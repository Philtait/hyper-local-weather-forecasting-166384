"""Imarika: multi-output, next-hour LSTM weather baseline (TensorFlow/Keras).

Run with the project's virtual environment:
    venv/Scripts/python.exe lstm_model.py
    venv/Scripts/python.exe lstm_model.py --runs single --epochs 1 --output-dir reports/lstm_smoke

Default execution trains a new model on the representative station, then a NEW
model/scaler on all 26 stations. All-station outputs use the requested names in
reports/; single-station outputs are in reports/lstm_single_station/.

Methodology:
* Split the selected stations' shared UTC hourly timeline 70/15/15. Shared date
  cutoffs prevent cross-station temporal leakage in a jointly trained network.
  Individual station row counts can differ from 70/15/15 because coverage differs.
* Forward-fill input features causally within each station, never backward-fill.
  Do not fill target labels. Train/validation require five observed target values;
  test windows may have partially observed targets, scored separately by variable.
* Windows contain exactly the preceding 24 hours, never the target hour. Boundary
  windows can use earlier splits' history; stations are never mixed in a window.
* Fit one shared per-feature StandardScaler on complete forward-filled TRAINING
  rows only. Targets use the matching first five feature means/scales.
* Test predictions use observed history as it becomes available (rolling one-hour
  forecasts, not a recursive long-horizon forecast). Persistence uses the original
  preceding hourly target. Metrics use common finite actual/prediction/persistence
  pairs per variable; MAPE excludes zero actuals for ALL variables.
* SARIMA skill uses the user-supplied fixed RMSE values, which are not rescored on
  these test timestamps. All-station results include both pooled and station-wise
  metrics; pooled RMSE is not an unweighted average of station RMSE values.
"""

import argparse
import gc
import hashlib
import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path

os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler


# =============================================================================
# Configuration: dataset aliases, ten input features, and five output variables
# =============================================================================
ROOT = Path(__file__).resolve().parent
REPRESENTATIVE = "101990961244800083"
LOOKBACK = 24
TARGET_ALIASES = {
    "air_temperature": ["air_temperature", "iot_air_temperature"],
    "air_humidity": ["air_humidity", "iot_air_humidity"],
    "barometric_pressure_hpa": ["barometric_pressure_hpa", "iot_barometric_pressure"],
    "wind_speed": ["wind_speed", "iot_wind_speed"],
    "rainfall_mm": ["rainfall_mm", "iot_rainfall_mm"],
}
TARGETS = list(TARGET_ALIASES)
CALENDAR_FEATURES = ["sin_hour", "cos_hour", "sin_doy", "cos_doy", "is_rainy_season"]
FEATURES = TARGETS + CALENDAR_FEATURES
SARIMA_RMSE = dict(zip(TARGETS, [1.2416, 5.2116, 0.3277, 0.5059, 0.0830]))
UNITS = ["Temperature (°C)", "Relative humidity (%)", "Pressure (hPa)", "Wind speed (m/s)", "Rainfall (mm)"]
LOG = logging.getLogger("imarika.lstm")


def clean_ids(values):
    return values.astype("string").str.replace(r'''["'“”‘’]''', "", regex=True).str.strip()


def write_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(path)


def fingerprint(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


# =============================================================================
# 1. DATA PREPARATION: station whitelist, schema normalization, and hourly order
# =============================================================================
def load_dataset(path, registry_path):
    columns = pd.read_csv(path, nrows=0).columns
    timestamp = next((c for c in ["timestamp", "hour", "received_at"] if c in columns), None)
    if timestamp is None:
        raise ValueError("Dataset needs timestamp, hour, or received_at")
    aliases = {}
    for target, candidates in TARGET_ALIASES.items():
        source = next((c for c in candidates if c in columns), None)
        if source is None:
            raise ValueError(f"Missing {target}; accepted aliases: {candidates}")
        aliases[source] = target
    # Select only the ten model features. Reference reanalysis is not an input to
    # this requested architecture, and pressure in the unified CSV is already hPa.
    frame = pd.read_csv(path, usecols=["device_id", timestamp] + list(aliases) + CALENDAR_FEATURES,
                        dtype={"device_id": "string"}, low_memory=False)
    frame = frame.rename(columns={timestamp: "timestamp", **aliases})
    frame["device_id"] = clean_ids(frame.device_id)
    registry = pd.DataFrame(json.loads(registry_path.read_text(encoding="utf-8")))
    registry["device_id"] = clean_ids(registry.device_id)
    inside = (pd.to_numeric(registry.lat, errors="coerce").between(0.1, 0.7)
              & pd.to_numeric(registry.lon, errors="coerce").between(34.1, 34.6)
              & registry.device_id.ne("test-spatial-check-device"))
    whitelist = set(registry.loc[inside, "device_id"])
    if len(whitelist) != 26:
        raise ValueError(f"Expected 26 Busia stations in registry; found {len(whitelist)}")
    initial = len(frame)
    frame = frame.loc[frame.device_id.isin(whitelist)].copy()
    if set(frame.device_id) != whitelist:
        raise ValueError(f"Missing registered Busia stations: {sorted(whitelist - set(frame.device_id))}")
    frame["timestamp"] = pd.to_datetime(frame.timestamp, format="mixed", utc=True, errors="coerce")
    if frame.timestamp.isna().any() or not frame.timestamp.eq(frame.timestamp.dt.floor("h")).all():
        raise ValueError("Timestamps must be valid, hourly-aligned times")
    if frame.duplicated(["device_id", "timestamp"]).any():
        raise ValueError("Duplicate station/timestamp pairs")
    for column in FEATURES:
        frame[column] = pd.to_numeric(frame[column], errors="coerce").replace([np.inf, -np.inf], np.nan)
    LOG.info("Loaded %s/%s rows from 26 registered Busia stations; mapping=%s", len(frame), initial, aliases)
    return frame.sort_values(["device_id", "timestamp"]).reset_index(drop=True)


def calendar_values(index):
    """Deterministic local calendar values for hours inserted by reindexing."""
    local = index.tz_convert("Africa/Nairobi")
    hour = local.hour.to_numpy()
    annual_phase = (local.dayofyear.to_numpy() - 1) / np.where(local.is_leap_year, 366, 365)
    return pd.DataFrame({
        "sin_hour": np.sin(2 * np.pi * hour / 24), "cos_hour": np.cos(2 * np.pi * hour / 24),
        "sin_doy": np.sin(2 * np.pi * annual_phase), "cos_doy": np.cos(2 * np.pi * annual_phase),
        "is_rainy_season": np.isin(local.month, [3, 4, 5, 10, 11, 12]).astype(float),
    }, index=index)


def split_cutoffs(frame):
    timeline = pd.date_range(frame.timestamp.min(), frame.timestamp.max(), freq="h")
    train_end = int(len(timeline) * 0.70)
    val_end = train_end + int(len(timeline) * 0.15)
    if train_end <= LOOKBACK or val_end <= train_end or val_end >= len(timeline):
        raise ValueError("Insufficient hourly coverage for chronological 70/15/15 splits")
    return timeline[train_end], timeline[val_end]


@dataclass
class PreparedData:
    features: np.ndarray
    targets: np.ndarray
    actual: np.ndarray
    naive: np.ndarray
    timestamp: pd.DatetimeIndex
    device_id: np.ndarray
    imputed_input_cells: np.ndarray
    endpoints: dict
    scaler: StandardScaler
    validation_start: pd.Timestamp
    test_start: pd.Timestamp
    split_summary: pd.DataFrame
    missing_summary: pd.DataFrame


# =============================================================================
# 2-3. SEQUENCES AND NORMALIZATION: causal fill, train-only scaler, station windows
# =============================================================================
def prepare_data(frame):
    validation_start, test_start = split_cutoffs(frame)
    stations = []
    missing_rows = []
    scaler = StandardScaler()
    for station, group in frame.groupby("device_id", sort=True):
        hourly = group.set_index("timestamp")[FEATURES].asfreq("h")
        # Reconstruct only missing calendar cells from their own timestamps.
        hourly[CALENDAR_FEATURES] = hourly[CALENDAR_FEATURES].fillna(calendar_values(hourly.index))
        filled = hourly.ffill()  # station-local; uses only earlier observations
        training = filled.loc[filled.index < validation_start, FEATURES].dropna()
        if len(training):
            scaler.partial_fit(training)
        for feature in FEATURES:
            missing_rows.append({"device_id": station, "feature": feature, "hours": len(hourly),
                                 "missing_before_ffill": int(hourly[feature].isna().sum()),
                                 "missing_after_ffill": int(filled[feature].isna().sum())})
        stations.append((station, hourly, filled))
    if not hasattr(scaler, "mean_") or not np.isfinite(scaler.mean_).all():
        raise ValueError("No complete training feature rows available to fit StandardScaler")

    feature_arrays, target_arrays, actual_arrays, naive_arrays = [], [], [], []
    timestamps, device_ids, imputation_arrays = [], [], []
    endpoints = {name: [] for name in ["train", "validation", "test"]}
    summaries = []
    offset = 0
    for station, hourly, filled in stations:
        scaled = scaler.transform(filled).astype("float32")
        actual = hourly[TARGETS].to_numpy(dtype=float)
        target_scaled = ((actual - scaler.mean_[:5]) / scaler.scale_[:5]).astype("float32")
        naive = hourly[TARGETS].shift(1).to_numpy(dtype=float)
        feature_valid = np.isfinite(scaled).all(axis=1)
        # A prefix sum makes checking all 24 input hours O(n), without building a
        # giant (number_of_windows, 24, 10) copy in memory.
        positions = np.arange(LOOKBACK, len(hourly))
        bad_prefix = np.r_[0, np.cumsum(~feature_valid)]
        valid_window = (bad_prefix[positions] - bad_prefix[positions - LOOKBACK]) == 0
        complete_target = np.isfinite(actual).all(axis=1)
        any_target = np.isfinite(actual).any(axis=1)
        filled_cells = (hourly.isna() & filled.notna()).sum(axis=1).to_numpy()
        fill_prefix = np.r_[0, np.cumsum(filled_cells)]
        context_imputed = np.zeros(len(hourly), dtype="int32")
        context_imputed[positions] = fill_prefix[positions] - fill_prefix[positions - LOOKBACK]
        masks = {
            "train": hourly.index < validation_start,
            "validation": (hourly.index >= validation_start) & (hourly.index < test_start),
            "test": hourly.index >= test_start,
        }
        for name, mask in masks.items():
            eligible_label = any_target if name == "test" else complete_target
            keep = mask[positions] & valid_window & eligible_label[positions]
            local_endpoints = positions[keep]
            endpoints[name].append(local_endpoints + offset)
            dates = hourly.index[mask]
            summaries.append({
                "device_id": station, "split": name, "hourly_rows": int(mask.sum()),
                "complete_observed_targets": int((mask & complete_target).sum()),
                "sequences": len(local_endpoints),
                "first_hour_utc": dates.min(), "last_hour_utc": dates.max(),
                "first_target_utc": hourly.index[local_endpoints].min(),
                "last_target_utc": hourly.index[local_endpoints].max(),
            })
        feature_arrays.append(scaled)
        target_arrays.append(target_scaled)
        actual_arrays.append(actual)
        naive_arrays.append(naive)
        timestamps.extend(hourly.index)
        device_ids.extend([station] * len(hourly))
        imputation_arrays.append(context_imputed)
        offset += len(hourly)

    timestamp = pd.DatetimeIndex(timestamps)
    device_id = np.asarray(device_ids)
    indices = {}
    for name, parts in endpoints.items():
        combined = np.concatenate(parts).astype("int64")
        # Chronological across all stations, with deterministic station-ID ties.
        order = np.lexsort((device_id[combined], timestamp.asi8[combined]))
        indices[name] = combined[order]
        if not len(combined):
            raise ValueError(f"No valid {name} sequences after observed-target/window checks")
    return PreparedData(
        features=np.concatenate(feature_arrays), targets=np.concatenate(target_arrays),
        actual=np.concatenate(actual_arrays), naive=np.concatenate(naive_arrays),
        timestamp=timestamp, device_id=device_id, imputed_input_cells=np.concatenate(imputation_arrays),
        endpoints=indices, scaler=scaler, validation_start=validation_start, test_start=test_start,
        split_summary=pd.DataFrame(summaries), missing_summary=pd.DataFrame(missing_rows),
    )


def save_scaler(prepared, path):
    scaler = prepared.scaler
    write_json(path, {
        "class": "sklearn.preprocessing.StandardScaler", "feature_order": FEATURES,
        "target_order": TARGETS, "target_feature_indices": list(range(5)),
        "mean": scaler.mean_.tolist(), "scale": scaler.scale_.tolist(), "variance": scaler.var_.tolist(),
        "n_samples_seen": np.asarray(scaler.n_samples_seen_).tolist(), "lookback_hours": LOOKBACK,
        "fit_scope": "complete causally-forward-filled feature rows strictly before validation_start_utc",
        "validation_start_utc": prepared.validation_start.isoformat(),
        "test_start_utc": prepared.test_start.isoformat(),
        "input_missing_policy": "station-local forward fill, no backward fill; drop unresolved input windows",
        "label_missing_policy": "never impute; complete train/validation labels, per-variable observed test labels",
    })


def inverse_targets(values, scaler):
    return np.asarray(values) * scaler.scale_[:5] + scaler.mean_[:5]


def make_datasets(prepared, tf, batch_size):
    features = tf.convert_to_tensor(prepared.features, dtype=tf.float32)
    targets = tf.convert_to_tensor(prepared.targets, dtype=tf.float32)
    offsets = tf.range(-LOOKBACK, 0, dtype=tf.int64)
    options = tf.data.Options()
    options.deterministic = True
    options.threading.private_threadpool_size = 1
    options.threading.max_intra_op_parallelism = 1

    def supervised(indices):
        return tf.gather(features, indices[:, None] + offsets), tf.gather(targets, indices)

    def inputs_only(indices):
        return tf.gather(features, indices[:, None] + offsets)

    datasets = {}
    for name, indices in prepared.endpoints.items():
        dataset = tf.data.Dataset.from_tensor_slices(indices).batch(batch_size)
        dataset = dataset.map(inputs_only if name == "test" else supervised, num_parallel_calls=1)
        # No shuffle at any point. Batches stay in chronological order.
        datasets[name] = dataset.with_options(options).prefetch(1)
    return datasets


# =============================================================================
# 4. MODEL ARCHITECTURE: exactly the requested two LSTMs and two Dense layers
# =============================================================================
def build_model(tf):
    model = tf.keras.Sequential([
        tf.keras.layers.Input(shape=(LOOKBACK, len(FEATURES))),
        tf.keras.layers.LSTM(128, return_sequences=True, dropout=0.2),
        tf.keras.layers.LSTM(64, return_sequences=False, dropout=0.2),
        tf.keras.layers.Dense(32, activation="relu"),
        tf.keras.layers.Dense(len(TARGETS)),
    ], name="imarika_lstm")
    model.compile(optimizer=tf.keras.optimizers.Adam(learning_rate=0.001), loss="mse")
    return model


# =============================================================================
# 6. EVALUATION: physical units, paired persistence, and supplied SARIMA baseline
# =============================================================================
def metric_row(actual, predicted, naive, target):
    paired = np.isfinite(actual) & np.isfinite(predicted) & np.isfinite(naive)
    y, forecast, persistence = actual[paired], predicted[paired], naive[paired]
    nonzero = y != 0
    rmse = float(np.sqrt(np.mean((forecast - y) ** 2))) if len(y) else np.nan
    rmse_naive = float(np.sqrt(np.mean((persistence - y) ** 2))) if len(y) else np.nan
    return {
        "variable": target, "rmse_lstm": rmse,
        "mae": float(np.mean(np.abs(forecast - y))) if len(y) else np.nan,
        "mape": float(np.mean(np.abs((forecast[nonzero] - y[nonzero]) / y[nonzero])) * 100) if nonzero.any() else np.nan,
        "rmse_sarima": SARIMA_RMSE[target], "rmse_naive": rmse_naive,
        "skill_vs_sarima": 1 - rmse / SARIMA_RMSE[target],
        "skill_vs_persistence": 1 - rmse / rmse_naive if rmse_naive > 0 else np.nan,
        "test_sequences": len(actual), "observed_targets": int(np.isfinite(actual).sum()),
        "evaluated_hours": int(paired.sum()), "mape_hours": int(nonzero.sum()),
        "status": "ok" if len(y) else "no_paired_observations",
    }, paired


def evaluate(prepared, predictions, run_name):
    endpoints = prepared.endpoints["test"]
    if predictions.shape != (len(endpoints), len(TARGETS)):
        raise ValueError(f"Unexpected prediction shape: {predictions.shape}")
    if not np.isfinite(predictions).all():
        raise ValueError("LSTM produced nonfinite test predictions")
    actual, naive = prepared.actual[endpoints], prepared.naive[endpoints]
    identities = prepared.device_id[endpoints]
    results, long_predictions = [], []
    for column, target in enumerate(TARGETS):
        pooled, paired = metric_row(actual[:, column], predictions[:, column], naive[:, column], target)
        results.append({"run": run_name, "device_id": "POOLED", **pooled})
        for station in sorted(set(identities)):
            select = identities == station
            row, _ = metric_row(actual[select, column], predictions[select, column], naive[select, column], target)
            results.append({"run": run_name, "device_id": station, **row})
        long_predictions.append(pd.DataFrame({
            "run": run_name, "device_id": identities, "timestamp": prepared.timestamp[endpoints],
            "variable": target, "actual": actual[:, column], "predicted": predictions[:, column],
            "naive": naive[:, column], "used_for_metrics": paired,
            "imputed_input_cells": prepared.imputed_input_cells[endpoints],
        }))
    return pd.DataFrame(results), pd.concat(long_predictions, ignore_index=True)


# =============================================================================
# 7. PLOTS AND OUTPUT: show gaps, separate stations, training curves and CSVs
# =============================================================================
def plot_predictions(predictions, output_dir):
    output_dir.mkdir(parents=True, exist_ok=True)
    for target, unit in zip(TARGETS, UNITS):
        subset = predictions.loc[predictions.variable.eq(target)]
        stations = sorted(subset.device_id.unique())
        columns = 2 if len(stations) > 1 else 1
        rows = (len(stations) + columns - 1) // columns
        fig, axes = plt.subplots(rows, columns, figsize=(15, max(4, rows * 2.5)), squeeze=False)
        for axis, station in zip(axes.flat, stations):
            group = subset.loc[subset.device_id.eq(station)].set_index("timestamp").sort_index()
            # Restore empty hours for plotting so lines do not hide long outages.
            group = group.reindex(pd.date_range(group.index.min(), group.index.max(), freq="h"))
            axis.plot(group.index, group.actual, label="Actual", linewidth=0.8)
            axis.plot(group.index, group.predicted, label="LSTM", linewidth=0.8, alpha=0.8)
            axis.set_title(station, fontsize=9)
            axis.set_ylabel(unit)
            axis.grid(alpha=0.2)
            axis.tick_params(axis="x", rotation=25, labelsize=7)
        for axis in list(axes.flat)[len(stations):]:
            axis.set_visible(False)
        axes.flat[0].legend()
        fig.suptitle(f"{target}: rolling next-hour forecasts, UTC test timestamps")
        fig.tight_layout(rect=(0, 0, 1, 0.98))
        fig.savefig(output_dir / f"{target}.png", dpi=140)
        plt.close(fig)


def plot_history(history, path):
    fig, axis = plt.subplots(figsize=(9, 4))
    axis.plot(history.epoch, history.loss, label="Training loss")
    axis.plot(history.epoch, history.val_loss, label="Validation loss")
    axis.set(xlabel="Epoch", ylabel="MSE (standardized targets)", title="LSTM learning curve")
    axis.legend()
    axis.grid(alpha=0.2)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


# =============================================================================
# 5. TRAINING: early stopping, best-model checkpoint and per-epoch progress
# =============================================================================
def run_experiment(frame, run_name, directory, args, tf):
    directory.mkdir(parents=True, exist_ok=True)
    status_path = directory / "lstm_run.json"
    if status_path.exists() and not args.overwrite:
        LOG.error("Existing run in %s: choose another --output-dir or use --overwrite", directory)
        return {"run": run_name, "status": "skipped_existing", "directory": str(directory)}
    status = {
        "run": run_name, "status": "preparing", "stations": sorted(frame.device_id.unique().tolist()),
        "data_sha256": args.data_sha256, "script_sha256": args.script_sha256,
        "epochs_requested": args.epochs, "batch_size": args.batch_size, "seed": args.seed,
        "tensorflow_version": tf.__version__, "lookback": LOOKBACK, "features": FEATURES,
        "target_order": TARGETS, "sarima_rmse_supplied": SARIMA_RMSE,
        "split_policy": "shared 70/15/15 UTC hourly timeline; no shuffle",
        "evaluation_policy": "per-variable common observed target/prediction/original previous-hour target",
        "sarima_comparison": "fixed user-supplied RMSE; not rescored on LSTM test pairs",
    }
    write_json(status_path, status)
    try:
        tf.keras.backend.clear_session()
        tf.keras.utils.set_random_seed(args.seed)
        prepared = prepare_data(frame)
        save_scaler(prepared, directory / "lstm_scaler.json")
        prepared.split_summary.to_csv(directory / "lstm_splits.csv", index=False)
        prepared.missing_summary.to_csv(directory / "lstm_missing_values.csv", index=False)
        LOG.info("%s split cutoffs: validation=%s test=%s", run_name, prepared.validation_start, prepared.test_start)
        LOG.info("Split sizes and date coverage:\n%s", prepared.split_summary.to_string(index=False))
        LOG.info("Sequences: %s", {name: len(indices) for name, indices in prepared.endpoints.items()})
        datasets = make_datasets(prepared, tf, args.batch_size)
        model = build_model(tf)
        model.summary(print_fn=LOG.info)
        status.update(status="training", validation_start_utc=prepared.validation_start.isoformat(),
                      test_start_utc=prepared.test_start.isoformat(),
                      sequences={name: len(indices) for name, indices in prepared.endpoints.items()})
        write_json(status_path, status)
        checkpoint = directory / "lstm_model.keras"
        callbacks = [
            tf.keras.callbacks.EarlyStopping(monitor="val_loss", patience=10, restore_best_weights=True),
            tf.keras.callbacks.ModelCheckpoint(checkpoint, monitor="val_loss", save_best_only=True, mode="min"),
            tf.keras.callbacks.CSVLogger(directory / "lstm_history.csv", append=False),
            tf.keras.callbacks.TerminateOnNaN(),
        ]
        history_object = model.fit(
            datasets["train"], validation_data=datasets["validation"], epochs=args.epochs,
            callbacks=callbacks, verbose=1, shuffle=False,
        )
        history = pd.DataFrame(history_object.history)
        history.insert(0, "epoch", np.arange(1, len(history) + 1))
        history.to_csv(directory / "lstm_history.csv", index=False)
        if not np.isfinite(history[["loss", "val_loss"]].to_numpy()).all():
            raise ValueError("Training/validation loss became nonfinite")
        if not checkpoint.exists():
            raise RuntimeError("Training did not produce a best-model checkpoint")
        LOG.info("%s training curve:\n%s", run_name, history.to_string(index=False))
        # Load the actual best checkpoint, not necessarily the final epoch.
        model = tf.keras.models.load_model(checkpoint)
        predictions = inverse_targets(model.predict(datasets["test"], verbose=1), prepared.scaler)
        results, prediction_rows = evaluate(prepared, predictions, run_name)
        results.to_csv(directory / "lstm_results.csv", index=False)
        prediction_rows.to_csv(directory / "lstm_predictions.csv", index=False)
        try:
            plot_history(history, directory / "lstm_training_curve.png")
            plot_predictions(prediction_rows, directory / "lstm_plots")
        except Exception as error:
            status["plot_error"] = str(error)
            LOG.exception("Plot generation failed; model and numerical results retained")
        LOG.info("%s LSTM vs supplied SARIMA summary:\n%s", run_name,
                 results.loc[results.device_id.eq("POOLED"), [
                     "variable", "rmse_lstm", "mae", "mape", "rmse_sarima", "rmse_naive",
                     "skill_vs_sarima", "skill_vs_persistence", "evaluated_hours",
                 ]].to_string(index=False, float_format=lambda value: f"{value:.4f}"))
        status.update(status="completed", epochs_completed=len(history),
                      best_epoch=int(history.val_loss.idxmin() + 1), best_val_loss=float(history.val_loss.min()))
    except Exception as error:
        LOG.exception("%s failed; the next experiment can still run", run_name)
        status.update(status="failed", error=str(error))
    finally:
        write_json(status_path, status)
        tf.keras.backend.clear_session()
        gc.collect()
    return {"run": run_name, "status": status["status"], "directory": str(directory)}


# =============================================================================
# Orchestration: representative station first, then fresh all-station training
# =============================================================================
def main(args):
    args.output_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(), logging.FileHandler(args.output_dir / "lstm.log", encoding="utf-8")])
    try:
        import tensorflow as tf
    except ImportError:
        LOG.exception("TensorFlow is unavailable. Install requirements-lstm.txt in the active environment.")
        return
    tf.config.threading.set_intra_op_parallelism_threads(args.threads)
    tf.config.threading.set_inter_op_parallelism_threads(1)
    tf.keras.utils.set_random_seed(args.seed)
    tf.config.experimental.enable_op_determinism()
    for gpu in tf.config.list_physical_devices("GPU"):
        tf.config.experimental.set_memory_growth(gpu, True)
    LOG.info("TensorFlow %s; devices=%s", tf.__version__, tf.config.list_physical_devices())
    try:
        frame = load_dataset(args.data, args.stations_file)
    except Exception:
        LOG.exception("Data preparation failed")
        return
    args.data_sha256 = fingerprint(args.data)
    args.script_sha256 = fingerprint(Path(__file__))
    runs = []
    if args.runs in ("both", "single"):
        single = frame.loc[frame.device_id.eq(REPRESENTATIVE)].copy()
        runs.append(run_experiment(single, "single_station", args.output_dir / "lstm_single_station", args, tf))
    if args.runs in ("both", "all"):
        runs.append(run_experiment(frame, "all_stations", args.output_dir, args, tf))
    write_json(args.output_dir / "lstm_runs.json", runs)
    LOG.info("Experiment outcomes:\n%s", pd.DataFrame(runs).to_string(index=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", type=Path, default=ROOT / "data/unified_dataset.csv")
    parser.add_argument("--stations-file", type=Path, default=ROOT / "data/busia_stations.json")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "reports")
    parser.add_argument("--runs", choices=["both", "single", "all"], default="both")
    parser.add_argument("--epochs", type=int, default=50)
    parser.add_argument("--batch-size", type=int, default=64)
    parser.add_argument("--threads", type=int, default=4)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--overwrite", action="store_true", help="Explicitly replace previous outputs in this run directory")
    options = parser.parse_args()
    if min(options.epochs, options.batch_size, options.threads) < 1:
        parser.error("epochs, batch-size, and threads must be positive")
    main(options)
