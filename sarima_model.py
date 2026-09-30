"""Station-wise SARIMA baseline for the Imarika hourly weather dataset.

Usage:
    python sarima_model.py                         # representative station, then all others
    python sarima_model.py --resume                # resume matching completed checkpoints
    python sarima_model.py --stations 101990961244800083
    python sarima_model.py --targets air_temperature --maxiter 100

Dependencies: pandas, numpy, statsmodels, matplotlib.

Methodology:
* Preserve hourly gaps as NaN; SARIMAX handles missing observations in its filter.
* Split hours 70/15/15 chronologically BEFORE ADF or model fitting.
* ADF uses the longest contiguous observed TRAINING block, avoiding compression
  of multi-hour outages into artificial adjacent observations. d is handled by
  SARIMAX itself: no manual/double differencing of the target.
* "Validation AIC" is -2 * conditional validation log-likelihood + 2*k, using
  parameters estimated on training only and states updated through validation.
  This is a held-out AIC-style score, NOT ordinary in-sample training AIC.
* Refit the selected order on train+validation. Test predictions are prior-state
  one-step forecasts; the Kalman filter then consumes each observed test value.
  Parameters are fixed during test evaluation. No future exogenous data is used.
* All metrics compare actual/model/persistence on the SAME finite timestamps.
  Persistence is exactly the preceding hourly observation (no forward fill).
  MAPE excludes zero actuals for every target, including rainfall; it is percent.
* ALL_STATIONS_MEAN rows are unweighted means of successful station metrics,
  not pooled RMSE. Counts disclose failed models and unavailable metrics.
"""

import os

# Avoid BLAS oversubscription on this long sequential grid search. Explicit
# environment settings supplied by the caller take precedence.
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

import argparse
import gc
import hashlib
import itertools
import json
import logging
import time
import warnings
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import statsmodels
from statsmodels.tsa.statespace.sarimax import SARIMAX
from statsmodels.tsa.statespace.kalman_filter import (
    MEMORY_NO_FILTERED, MEMORY_NO_GAIN, MEMORY_NO_PREDICTED_COV, MEMORY_NO_SMOOTHING,
)
from statsmodels.tsa.stattools import adfuller


# =============================================================================
# Configuration, schema aliases, and chronological data preparation
# =============================================================================
ROOT = Path(__file__).resolve().parent
REPRESENTATIVE = "101990961244800083"
SEASONAL_ORDER = (1, 1, 1, 24)
TARGET_ALIASES = {
    "air_temperature": ["air_temperature", "iot_air_temperature"],
    "air_humidity": ["air_humidity", "iot_air_humidity"],
    "barometric_pressure_hpa": ["barometric_pressure_hpa", "iot_barometric_pressure"],
    "wind_speed": ["wind_speed", "iot_wind_speed"],
    "rainfall_mm": ["rainfall_mm", "iot_rainfall_mm"],
}
UNITS = {
    "air_temperature": "Temperature (°C)", "air_humidity": "Relative humidity (%)",
    "barometric_pressure_hpa": "Pressure (hPa)", "wind_speed": "Wind speed (m/s)",
    "rainfall_mm": "Hourly rainfall (mm)",
}
METRICS = ["rmse", "mae", "mape", "rmse_naive", "skill_score"]
LOG = logging.getLogger("imarika.sarima")


def load_dataset(path, targets):
    columns = pd.read_csv(path, nrows=0).columns
    time_column = next((name for name in ["timestamp", "hour", "received_at"] if name in columns), None)
    if time_column is None:
        raise ValueError("Dataset needs timestamp, hour, or received_at")
    selected = {}
    for target in targets:
        source = next((name for name in TARGET_ALIASES[target] if name in columns), None)
        if source is None:
            raise ValueError(f"Missing target {target}; accepted columns: {TARGET_ALIASES[target]}")
        selected[source] = target
    frame = pd.read_csv(path, usecols=["device_id", time_column] + list(selected), dtype={"device_id": "string"})
    frame = frame.rename(columns={time_column: "timestamp", **selected})
    frame["device_id"] = frame.device_id.str.replace(r'''["'“”‘’]''', "", regex=True).str.strip()
    frame["timestamp"] = pd.to_datetime(frame.timestamp, format="mixed", utc=True, errors="coerce")
    if frame.timestamp.isna().any() or frame.device_id.isna().any() or frame.device_id.eq("").any():
        raise ValueError("Missing/invalid station identifiers or timestamps")
    if not frame.timestamp.eq(frame.timestamp.dt.floor("h")).all():
        raise ValueError("Input must already be aligned to hourly timestamps")
    if frame.duplicated(["device_id", "timestamp"]).any():
        raise ValueError("Duplicate station/hour keys in unified dataset")
    for target in targets:
        frame[target] = pd.to_numeric(frame[target], errors="coerce").replace([np.inf, -np.inf], np.nan)
    LOG.info("Schema mapping: %s; timestamp=%s (UTC)", selected, time_column)
    return frame.sort_values(["device_id", "timestamp"])


def chronological_split(series):
    train_end = int(len(series) * 0.70)
    validation_end = train_end + int(len(series) * 0.15)
    if train_end < 3 * 24 or validation_end == train_end or validation_end == len(series):
        raise ValueError("Not enough hourly slots for a 70/15/15 seasonal split")
    return series.iloc[:train_end], series.iloc[train_end:validation_end], series.iloc[validation_end:]


# =============================================================================
# 1. Stationarity: training-only ADF and a single nonseasonal d decision
# =============================================================================
def stationarity_test(train):
    present = train.notna()
    blocks = present.ne(present.shift()).cumsum()
    lengths = train[present].groupby(blocks[present]).size()
    if lengths.empty:
        raise ValueError("Training target has no observed values")
    sample = train.loc[present & blocks.eq(lengths.idxmax())]
    result = {
        "adf_statistic": np.nan, "adf_pvalue": np.nan, "adf_lags": np.nan,
        "adf_sample_hours": len(sample), "adf_sample_start": str(sample.index.min()),
        "adf_sample_end": str(sample.index.max()), "adf_status": "ok",
    }
    # Constant full training series is already stationary; ADF is undefined.
    if train.dropna().nunique() <= 1:
        result.update(d=0, adf_status="constant_training_series")
        return result
    if len(sample) < 3 * 24 or sample.nunique() <= 1:
        result.update(d=1, adf_status="insufficient_contiguous_variation_fallback_d1")
        LOG.warning("ADF needs >=72 contiguous, nonconstant training observations; using d=1")
        return result
    try:
        statistic, pvalue, lags, _, critical, _ = adfuller(sample, maxlag=24, autolag="AIC")
        result.update(adf_statistic=float(statistic), adf_pvalue=float(pvalue),
                      adf_lags=int(lags), adf_critical_5pct=float(critical["5%"]),
                      d=int(pvalue >= 0.05))
    except Exception as error:
        LOG.exception("ADF failed; conservatively using d=1")
        result.update(d=1, adf_status=f"failed_fallback_d1: {error}")
    return result


# =============================================================================
# 2-3. Training fits and nine-candidate validation grid search
# =============================================================================
def fit_model(series, order, maxiter):
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        model = SARIMAX(
            series, order=order, seasonal_order=SEASONAL_ORDER, trend="n",
            enforce_stationarity=False, enforce_invertibility=False,
        )
        fitted = model.fit(disp=False, method="lbfgs", maxiter=maxiter,
                           low_memory=True, cov_type="none")
    messages = " | ".join(dict.fromkeys(str(w.message) for w in caught))
    if messages:
        LOG.warning("Fit %s: %s", order, messages)
    if not fitted.mle_retvals.get("converged", False):
        raise RuntimeError(f"Optimizer did not converge within {maxiter} iterations: {messages}")
    if not np.isfinite(fitted.params).all() or not np.isfinite(fitted.llf):
        raise ValueError("Nonfinite fitted parameters or training likelihood")
    return fitted


def filter_next_block(fitted, observations):
    """Continue filtering at fixed parameters from the previous terminal state.

    Low-memory fits do not expose the full public predicted_state history that
    SARIMAXResults.extend requires. The filter still retains its terminal state
    internally. Transfer just that state/covariance into a cloned model, which
    is equivalent to extend without allocating full training-state histories.
    """
    model = fitted.model.clone(observations)
    state = fitted.filter_results.predicted_state[..., -1].copy()
    covariance = fitted.filter_results.predicted_state_cov[..., -1].copy()
    model.ssm.initialize_known(state, covariance)
    # This block already has a trained prior, so no diffuse-initialization burn-in.
    model.loglikelihood_burn = 0
    # Retain predicted means: statsmodels needs them to reconstruct predictions
    # at missing observation times. Only the large covariance history is omitted.
    memory = MEMORY_NO_FILTERED | MEMORY_NO_GAIN | MEMORY_NO_PREDICTED_COV | MEMORY_NO_SMOOTHING
    return model.filter(fitted.params, cov_type="none", conserve_memory=memory)


def conditional_validation_aic(fitted, validation):
    if validation.notna().sum() < 24:
        raise ValueError("Validation needs at least 24 observed hours")
    # Each likelihood contribution is evaluated BEFORE observing that hour.
    evaluated = filter_next_block(fitted, validation)
    observed = validation.notna().to_numpy()
    contributions = np.asarray(evaluated.llf_obs)[observed]
    predictions = np.asarray(evaluated.get_prediction().predicted_mean)[observed]
    if not np.isfinite(contributions).all() or not np.isfinite(predictions).all():
        raise ValueError("Nonfinite validation predictive likelihood/forecast")
    loglikelihood = float(contributions.sum())
    parameter_count = len(fitted.params)
    return -2 * loglikelihood + 2 * parameter_count, loglikelihood, parameter_count


def grid_search(train, validation, d, maxiter, checkpoint_dir):
    candidates = []
    best = None
    for p, q in itertools.product([0, 1, 2], repeat=2):
        order = (p, d, q)
        started = time.perf_counter()
        row = {"p": p, "d": d, "q": q, "P": 1, "D": 1, "Q": 1, "s": 24}
        try:
            LOG.info("  Fitting candidate order=%s seasonal=%s", order, SEASONAL_ORDER)
            fitted = fit_model(train, order, maxiter)
            score, likelihood, k = conditional_validation_aic(fitted, validation)
            row.update(status="ok", validation_aic=score, validation_loglikelihood=likelihood,
                       parameter_count=k, train_aic=float(fitted.aic), error="")
            if best is None or score < best["validation_aic"]:
                best = row.copy()
            LOG.info("  Candidate %s validation AIC=%.3f (training AIC=%.3f)", order, score, fitted.aic)
            del fitted
        except Exception as error:
            row.update(status="failed", error=str(error), validation_aic=np.nan)
            LOG.exception("  Candidate %s failed; continuing grid", order)
        row["elapsed_seconds"] = time.perf_counter() - started
        candidates.append(row)
        pd.DataFrame(candidates).to_csv(checkpoint_dir / "grid_search.csv", index=False)
        gc.collect()
    if best is None:
        raise RuntimeError("All nine SARIMA candidates failed; see grid_search.csv and sarima.log")
    best["successful_candidates"] = sum(row["status"] == "ok" for row in candidates)
    return best


# =============================================================================
# 4-5. Final train+validation refit, causal one-step forecasts, and evaluation
# =============================================================================
def one_step_predictions(fitted, test):
    # get_forecast(len(test)) would be a fixed-origin, multi-step forecast.
    # Block filtering instead consumes test sequentially. Its information set is
    # 'predicted': y[t] is not yet used in the forecast for time t.
    evaluated = filter_next_block(fitted, test)
    prediction = evaluated.get_prediction(information_set="predicted").predicted_mean
    return pd.Series(np.asarray(prediction), index=test.index, name="predicted")


def evaluation_metrics(actual, predicted, naive):
    table = pd.DataFrame({"actual": actual, "predicted": predicted, "naive": naive})
    paired = np.isfinite(table).all(axis=1)
    valid = table.loc[paired]
    if valid.empty:
        raise ValueError("No common finite actual/model/persistence test observations")
    error = valid.predicted - valid.actual
    rmse = float(np.sqrt((error ** 2).mean()))
    naive_rmse = float(np.sqrt(((valid.naive - valid.actual) ** 2).mean()))
    nonzero = valid.actual.ne(0)
    mape = float((error.loc[nonzero].abs() / valid.loc[nonzero, "actual"].abs()).mean() * 100) if nonzero.any() else np.nan
    metrics = {
        "rmse": rmse, "mae": float(error.abs().mean()), "mape": mape,
        "rmse_naive": naive_rmse, "skill_score": 1 - rmse / naive_rmse if naive_rmse > 0 else np.nan,
        "test_hours": len(table), "test_observed_hours": int(actual.notna().sum()),
        "evaluated_hours": len(valid), "mape_hours": int(nonzero.sum()),
        "excluded_test_hours": int((~paired).sum()),
    }
    return metrics, paired


def save_plot(predictions, station, target, directory):
    directory.mkdir(parents=True, exist_ok=True)
    fig, axis = plt.subplots(figsize=(14, 4))
    axis.plot(predictions.timestamp, predictions.actual, label="Actual", linewidth=0.9)
    axis.plot(predictions.timestamp, predictions.predicted, label="One-step SARIMA", linewidth=0.8, alpha=0.8)
    axis.set(title=f"{station} — {target} (test set)", xlabel="Time (UTC)", ylabel=UNITS[target])
    axis.legend()
    axis.grid(alpha=0.2)
    fig.autofmt_xdate()
    fig.tight_layout()
    fig.savefig(directory / f"{station}_{target}.png", dpi=150)
    plt.close(fig)


# =============================================================================
# 6. Per-model checkpoints and aggregate CSV outputs (updated after every model)
# =============================================================================
def json_safe(record):
    return {key: None if isinstance(value, (float, np.floating)) and not np.isfinite(value)
            else value.item() if isinstance(value, np.generic) else value
            for key, value in record.items()}


def run_target(series, station, target, args, directory):
    started = time.perf_counter()
    identity = {"device_id": station, "variable": target}
    metrics = {**identity, "status": "failed", **{key: np.nan for key in METRICS}}
    params = {**identity, "status": "failed"}
    adf = {**identity}
    splits = []
    predictions_path = directory / "predictions.csv"
    try:
        train, validation, test = chronological_split(series)
        for name, values in [("train", train), ("validation", validation), ("test", test)]:
            row = {**identity, "split": name, "hours": len(values), "observed_hours": int(values.notna().sum()),
                   "start_utc": str(values.index.min()), "end_utc": str(values.index.max())}
            splits.append(row)
            LOG.info("  %s: %s hours (%s observed), %s -> %s", name, row["hours"], row["observed_hours"], row["start_utc"], row["end_utc"])
        adf.update(stationarity_test(train))
        LOG.info("  ADF statistic=%s p=%s n=%s d=%s status=%s", adf["adf_statistic"], adf["adf_pvalue"], adf["adf_sample_hours"], adf["d"], adf["adf_status"])
        best = grid_search(train, validation, adf["d"], args.maxiter, directory)
        params.update(best)
        params.update(adf_pvalue=adf["adf_pvalue"], adf_status=adf["adf_status"])
        order = (best["p"], best["d"], best["q"])
        LOG.info("  Best %s/%s: order=%s seasonal=%s validation AIC=%.3f", station, target, order, SEASONAL_ORDER, best["validation_aic"])
        # Fit exceptions, including the final refit, are contained per variable.
        final_fit = fit_model(pd.concat([train, validation]), order, args.maxiter)
        predicted = one_step_predictions(final_fit, test)
        naive = series.shift(1).reindex(test.index)
        scores, paired = evaluation_metrics(test, predicted, naive)
        predictions = pd.DataFrame({
            "device_id": station, "variable": target, "timestamp": test.index,
            "actual": test.to_numpy(), "predicted": predicted.to_numpy(),
            "naive": naive.to_numpy(), "used_for_metrics": paired.to_numpy(),
        })
        predictions.to_csv(predictions_path, index=False)
        metrics.update(scores, status="ok", error="")
        params.update(status="ok", final_train_aic=float(final_fit.aic))
        try:
            save_plot(predictions, station, target, args.output_dir / "sarima_plots")
        except Exception:
            LOG.exception("Plot failed for %s/%s; numerical results retained", station, target)
        LOG.info("  COMPLETED %s/%s RMSE=%.4f MAE=%.4f MAPE=%.3f%% skill=%.4f n=%s",
                 station, target, scores["rmse"], scores["mae"], scores["mape"], scores["skill_score"], scores["evaluated_hours"])
    except Exception as error:
        metrics["error"] = str(error)
        params.update(status="failed", error=str(error))
        LOG.exception("FAILED %s/%s; continuing with next variable", station, target)
    metrics["elapsed_seconds"] = time.perf_counter() - started
    checkpoint = {"metrics": json_safe(metrics), "params": json_safe(params),
                  "adf": json_safe(adf), "splits": splits}
    # A completed marker is atomic, so interruption cannot create a partial JSON
    # file that --resume mistakes for a completed station/variable.
    temporary = directory / "completed.tmp"
    temporary.write_text(json.dumps(checkpoint, indent=2, allow_nan=False), encoding="utf-8")
    temporary.replace(directory / "completed.json")
    gc.collect()
    return checkpoint


def write_reports(completed, args):
    rows = [checkpoint["metrics"] for checkpoint in completed]
    results = pd.DataFrame(rows)
    averages = []
    for target in args.targets:
        group = results.loc[results.variable.eq(target)]
        successful = group.loc[group.status.eq("ok")]
        if group.empty:
            continue
        average = {"device_id": "ALL_STATIONS_MEAN", "variable": target,
                   "status": "ok" if len(successful) else "no_successful_models",
                   "stations_completed": len(group), "stations_successful": len(successful),
                   "stations_requested": args.station_count}
        for metric in METRICS:
            values = pd.to_numeric(successful[metric], errors="coerce")
            average[metric] = values.mean()
            average[f"{metric}_station_count"] = int(values.notna().sum())
        averages.append(average)
    combined = pd.concat([results, pd.DataFrame(averages)], ignore_index=True)
    combined.to_csv(args.output_dir / "sarima_results.csv", index=False)
    pd.DataFrame([c["params"] for c in completed]).to_csv(args.output_dir / "sarima_best_params.csv", index=False)
    pd.DataFrame([c["adf"] for c in completed]).to_csv(args.output_dir / "sarima_adf.csv", index=False)
    pd.DataFrame([row for c in completed for row in c["splits"]]).to_csv(args.output_dir / "sarima_splits.csv", index=False)
    # Concatenate per-model CSVs without retaining all stations' predictions in RAM.
    destination = args.output_dir / "sarima_predictions.csv"
    with destination.open("w", encoding="utf-8", newline="") as output:
        output.write("device_id,variable,timestamp,actual,predicted,naive,used_for_metrics\n")
        for checkpoint in completed:
            identity = checkpoint["metrics"]
            if identity["status"] != "ok":
                continue
            path = args.output_dir / "sarima_checkpoints" / identity["device_id"] / identity["variable"] / "predictions.csv"
            with path.open(encoding="utf-8") as source:
                next(source)
                for line in source:
                    output.write(line)
    LOG.info("Current evaluation summary:\n%s", combined[["device_id", "variable", "status"] + METRICS].to_string(index=False, float_format=lambda x: f"{x:.4f}"))


def fingerprint(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


# =============================================================================
# 7. Orchestration: representative station first, then the remaining stations
# =============================================================================
def main(args):
    args.output_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        handlers=[logging.StreamHandler(), logging.FileHandler(args.output_dir / "sarima.log", encoding="utf-8")])
    data = load_dataset(args.data, args.targets)
    available = sorted(data.device_id.unique())
    stations = args.stations if args.stations else available
    unknown = set(stations) - set(available)
    if unknown:
        raise ValueError(f"Unknown stations: {sorted(unknown)}")
    stations = sorted(set(stations), key=lambda station: (station != REPRESENTATIVE, station))
    args.station_count = len(stations)
    configuration = {
        "data_sha256": fingerprint(args.data), "script_sha256": fingerprint(Path(__file__)),
        "stations": stations, "targets": args.targets, "maxiter": args.maxiter,
        "seasonal_order": list(SEASONAL_ORDER), "statsmodels_version": statsmodels.__version__,
        "selection": "conditional_validation_aic", "evaluation": "common_finite_model_naive_actual",
    }
    manifest_path = args.output_dir / "sarima_run.json"
    checkpoint_root = args.output_dir / "sarima_checkpoints"
    if manifest_path.exists():
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not args.resume:
            raise ValueError("Existing SARIMA run: use --resume or choose another --output-dir")
        if previous["configuration"] != configuration:
            raise ValueError("Resume configuration/data/script differs; use a new --output-dir")
    manifest = {"configuration": configuration, "status": "running", "completed_models": 0,
                "requested_models": len(stations) * len(args.targets)}
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    completed = []
    LOG.info("Starting %s stations x %s targets x 9 candidates; representative=%s", len(stations), len(args.targets), REPRESENTATIVE)
    for station_number, station in enumerate(stations, 1):
        station_data = data.loc[data.device_id.eq(station)].set_index("timestamp").asfreq("h")
        for target in args.targets:
            LOG.info("Station %s/%s %s: %s", station_number, len(stations), station, target)
            directory = checkpoint_root / station / target
            directory.mkdir(parents=True, exist_ok=True)
            saved = directory / "completed.json"
            if args.resume and saved.exists():
                checkpoint = json.loads(saved.read_text(encoding="utf-8"))
                LOG.info("Resuming completed %s/%s (status=%s)", station, target, checkpoint["metrics"]["status"])
            else:
                series = station_data[target].astype(float)
                checkpoint = run_target(series, station, target, args, directory)
            completed.append(checkpoint)
            write_reports(completed, args)
            manifest["completed_models"] = len(completed)
            manifest["failed_models"] = sum(c["metrics"]["status"] != "ok" for c in completed)
            manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    manifest["status"] = "completed" if not manifest.get("failed_models") else "completed_with_failures"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    LOG.info("Finished. Outputs: %s; failed models: %s", args.output_dir, manifest.get("failed_models", 0))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data", type=Path, default=ROOT / "data/unified_dataset.csv")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "reports")
    parser.add_argument("--stations", nargs="+", help="Default: all stations, representative first")
    parser.add_argument("--targets", nargs="+", choices=list(TARGET_ALIASES), default=list(TARGET_ALIASES))
    parser.add_argument("--maxiter", type=int, default=100)
    parser.add_argument("--resume", action="store_true", help="Reuse completed per-model checkpoints with the same configuration")
    arguments = parser.parse_args()
    if arguments.maxiter < 1:
        parser.error("--maxiter must be positive")
    main(arguments)
