"""Temporal isolation, scaling, observed labels, metrics, and Keras integration."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

from lstm_model import (
    FEATURES, TARGETS, build_model, calendar_values, evaluate, inverse_targets,
    make_datasets, metric_row, prepare_data, run_experiment, save_scaler, split_cutoffs,
)


def sample_frame():
    parts = []
    timeline = pd.date_range("2025-01-01", periods=240, freq="h", tz="UTC")
    for station, offset in [("A", 0), ("B", 1000)]:
        frame = calendar_values(timeline)
        for column, target in enumerate(TARGETS):
            frame[target] = np.arange(len(timeline), dtype=float) + offset + column
        frame["device_id"] = station
        frame["timestamp"] = timeline
        parts.append(frame.reset_index(drop=True))
    return pd.concat(parts, ignore_index=True)


class LstmPreparationTests(unittest.TestCase):
    def test_windows_never_cross_stations_or_include_the_target_hour(self):
        data = prepare_data(sample_frame())
        for split, endpoints in data.endpoints.items():
            self.assertTrue(data.timestamp[endpoints].is_monotonic_increasing)
            for endpoint in endpoints:
                self.assertTrue((data.device_id[endpoint - 24:endpoint] == data.device_id[endpoint]).all())
                self.assertEqual(data.timestamp[endpoint] - data.timestamp[endpoint - 24], pd.Timedelta(hours=24))
                self.assertLess(data.timestamp[endpoint - 1], data.timestamp[endpoint])
                if split == "train":
                    self.assertLess(data.timestamp[endpoint], data.validation_start)
                elif split == "validation":
                    self.assertGreaterEqual(data.timestamp[endpoint], data.validation_start)
                    self.assertLess(data.timestamp[endpoint], data.test_start)
                else:
                    self.assertGreaterEqual(data.timestamp[endpoint], data.test_start)
        first_test = data.endpoints["test"][0]
        self.assertEqual(data.timestamp[first_test], data.test_start)
        self.assertLess(data.timestamp[first_test - 24], data.test_start)

    def test_scaler_and_training_sequences_ignore_validation_and_test_values(self):
        frame = sample_frame()
        original = prepare_data(frame)
        changed = frame.copy()
        changed.loc[changed.timestamp >= original.validation_start, TARGETS] += 100000
        altered = prepare_data(changed)
        np.testing.assert_array_equal(original.scaler.mean_, altered.scaler.mean_)
        np.testing.assert_array_equal(original.scaler.scale_, altered.scaler.scale_)
        expected = frame.loc[frame.timestamp < original.validation_start, FEATURES].mean().to_numpy()
        np.testing.assert_allclose(original.scaler.mean_, expected)
        for endpoint in original.endpoints["train"]:
            np.testing.assert_array_equal(original.features[endpoint - 24:endpoint], altered.features[endpoint - 24:endpoint])
        train_endpoints = original.endpoints["train"]
        np.testing.assert_array_equal(original.targets[train_endpoints], altered.targets[train_endpoints])

    def test_forward_fill_is_causal_and_missing_labels_are_not_ground_truth(self):
        frame = sample_frame()
        frame.loc[0, TARGETS] = np.nan
        frame.loc[30, "air_temperature"] = np.nan
        data = prepare_data(frame)
        restored = inverse_targets(data.features[:, :5], data.scaler)
        self.assertTrue(np.isnan(restored[0]).all())
        self.assertAlmostEqual(restored[30, 0], 29, places=4)
        self.assertTrue(np.isnan(data.actual[30, 0]))
        self.assertNotIn(30, data.endpoints["train"])
        self.assertNotIn(24, data.endpoints["train"])
        self.assertIn(25, data.endpoints["train"])
        self.assertEqual(data.imputed_input_cells[31], 1)

    def test_reindex_preserves_hour_gaps_and_global_cutoffs(self):
        frame = sample_frame()
        frame = frame.drop(index=[40, 41, 42])
        # Later deployment of B does not create its own, later training cutoff.
        frame = frame.loc[~(frame.device_id.eq("B") & frame.timestamp.lt(frame.timestamp.min() + pd.Timedelta(hours=30)))]
        data = prepare_data(frame)
        self.assertEqual(len(data.timestamp), 240 + 210)
        self.assertTrue(np.isnan(data.actual[40:43]).all())
        self.assertEqual((data.validation_start, data.test_start), split_cutoffs(frame))
        for station in ["A", "B"]:
            endpoints = data.endpoints["train"]
            selected = endpoints[data.device_id[endpoints] == station]
            self.assertLess(data.timestamp[selected].max(), data.validation_start)

    def test_saved_scaler_and_target_inverse_transformation(self):
        data = prepare_data(sample_frame())
        rows = data.endpoints["test"]
        restored = inverse_targets(data.targets[rows], data.scaler)
        np.testing.assert_allclose(restored, data.actual[rows], atol=0.0001)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "lstm_scaler.json"
            save_scaler(data, path)
            saved = json.loads(path.read_text())
        self.assertEqual(saved["feature_order"], FEATURES)
        self.assertEqual(saved["target_order"], TARGETS)
        np.testing.assert_allclose(saved["scale"], data.scaler.scale_)

    def test_paired_metrics_zero_mape_and_fixed_sarima_reference(self):
        row, mask = metric_row(np.array([0., 2., 4., np.nan, 7.]),
                               np.array([1., 3., 2., 5., 0.]),
                               np.array([1., 0., 2., 4., np.nan]), "rainfall_mm")
        self.assertEqual(mask.tolist(), [True, True, True, False, False])
        self.assertAlmostEqual(row["rmse_lstm"], np.sqrt(2))
        self.assertAlmostEqual(row["mape"], 50)
        self.assertAlmostEqual(row["skill_vs_sarima"], 1 - np.sqrt(2) / .0830)
        self.assertAlmostEqual(row["skill_vs_persistence"], 1 - np.sqrt(2) / np.sqrt(3))
        self.assertEqual(row["mape_hours"], 2)
        zero, _ = metric_row(np.array([0.]), np.array([1.]), np.array([0.]), "rainfall_mm")
        self.assertTrue(np.isnan(zero["mape"]))
        self.assertTrue(np.isnan(zero["skill_vs_persistence"]))

    def test_test_missing_labels_remain_missing_in_predictions(self):
        frame = sample_frame()
        frame.loc[220, "rainfall_mm"] = np.nan
        data = prepare_data(frame)
        self.assertIn(220, data.endpoints["test"])
        predicted = np.zeros((len(data.endpoints["test"]), 5))
        results, predictions = evaluate(data, predicted, "test")
        self.assertEqual(len(results), 15)  # five targets x pooled/two stations
        rainfall = predictions.loc[predictions.variable.eq("rainfall_mm")]
        missing = rainfall.loc[rainfall.device_id.eq("A") & rainfall.timestamp.eq(data.timestamp[220])]
        self.assertTrue(missing.actual.isna().all())
        self.assertFalse(missing.used_for_metrics.any())

    def test_training_failure_is_logged_and_saved_without_propagating(self):
        tensorflow = MagicMock()
        tensorflow.__version__ = "test"
        model = MagicMock()
        model.fit.side_effect = RuntimeError("training failed")
        args = SimpleNamespace(overwrite=False, data_sha256="test", script_sha256="test",
                               epochs=50, batch_size=64, seed=42)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            with patch("lstm_model.build_model", return_value=model), patch(
                "lstm_model.make_datasets", return_value={"train": None, "validation": None}
            ), self.assertLogs("imarika.lstm", level="ERROR"):
                result = run_experiment(sample_frame(), "failure_test", output, args, tensorflow)
            status = json.loads((output / "lstm_run.json").read_text())
        self.assertEqual(result["status"], "failed")
        self.assertEqual(status["error"], "training failed")
        self.assertGreaterEqual(tensorflow.keras.backend.clear_session.call_count, 2)


@unittest.skipUnless(importlib.util.find_spec("tensorflow"), "TensorFlow not installed in this interpreter")
class KerasIntegrationTests(unittest.TestCase):
    def test_exact_architecture_batches_training_and_checkpoint_roundtrip(self):
        import tensorflow as tf

        tf.keras.utils.set_random_seed(7)
        data = prepare_data(sample_frame())
        datasets = make_datasets(data, tf, 64)
        x, y = next(iter(datasets["train"]))
        self.assertEqual(tuple(x.shape), (64, 24, 10))
        self.assertEqual(tuple(y.shape), (64, 5))
        first = data.endpoints["train"][0]
        np.testing.assert_allclose(x[0].numpy(), data.features[first - 24:first])
        np.testing.assert_allclose(y[0].numpy(), data.targets[first])
        model = build_model(tf)
        self.assertEqual([layer.units for layer in model.layers], [128, 64, 32, 5])
        self.assertEqual(model.layers[0].dropout, .2)
        self.assertEqual(model.layers[1].dropout, .2)
        self.assertTrue(model.layers[0].return_sequences)
        self.assertFalse(model.layers[1].return_sequences)
        self.assertAlmostEqual(float(model.optimizer.learning_rate.numpy()), .001)
        history = model.fit(datasets["train"], validation_data=datasets["validation"], epochs=1, verbose=0, shuffle=False)
        self.assertTrue(np.isfinite(history.history["val_loss"]).all())
        forecast = model.predict(datasets["test"], verbose=0)
        self.assertEqual(forecast.shape, (len(data.endpoints["test"]), 5))
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model.keras"
            model.save(path)
            restored = tf.keras.models.load_model(path)
            np.testing.assert_allclose(forecast, restored.predict(datasets["test"], verbose=0), atol=1e-6)
        tf.keras.backend.clear_session()


if __name__ == "__main__":
    unittest.main()
