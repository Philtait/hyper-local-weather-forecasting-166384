"""Check forecast causality, missing-hour handling, scoring, and data splits."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
from statsmodels.tsa.statespace.sarimax import SARIMAX

from sarima_model import (
    chronological_split, conditional_validation_aic, evaluation_metrics,
    filter_next_block, fit_model, grid_search, load_dataset, one_step_predictions, stationarity_test,
)


class SarimaTests(unittest.TestCase):
    def test_chronological_split_includes_missing_hours(self):
        index = pd.date_range("2025-01-01", periods=200, freq="h", tz="UTC")
        series = pd.Series(np.arange(200, dtype=float), index=index)
        series.iloc[100:120] = np.nan
        train, validation, test = chronological_split(series)
        self.assertEqual([len(train), len(validation), len(test)], [140, 30, 30])
        self.assertTrue(train.index.max() < validation.index.min() < test.index.min())
        self.assertEqual(train.isna().sum(), 20)
        pd.testing.assert_series_equal(pd.concat([train, validation, test]), series)

    def test_adf_uses_longest_contiguous_training_block(self):
        series = pd.Series(np.random.default_rng(2).normal(size=220),
                           index=pd.date_range("2025-01-01", periods=220, freq="h", tz="UTC"))
        series.iloc[100:120] = np.nan
        result = stationarity_test(series)
        self.assertEqual(result["adf_sample_hours"], 100)
        self.assertEqual(result["adf_sample_start"], str(series.index[0]))
        self.assertEqual(result["d"], 0)
        constant = stationarity_test(series.fillna(1) * 0)
        self.assertEqual(constant["d"], 0)
        self.assertEqual(constant["adf_status"], "constant_training_series")

    def test_one_step_forecasts_do_not_consume_current_or_future_values(self):
        train_index = pd.date_range("2025-01-01", periods=100, freq="h", tz="UTC")
        train = pd.Series(np.linspace(1, 3, 100), index=train_index)
        fitted = SARIMAX(train, order=(1, 0, 0), trend="n").filter([0.7, 1.0])
        test_index = pd.date_range(train_index[-1] + pd.Timedelta(hours=1), periods=4, freq="h")
        test = pd.Series([10.0, 20.0, np.nan, 40.0], index=test_index)
        predicted = one_step_predictions(fitted, test)
        np.testing.assert_allclose(predicted, [2.1, 7.0, 14.0, 9.8], atol=1e-8)
        changed = test.copy()
        changed.iloc[1] = 999.0
        changed_predictions = one_step_predictions(fitted, changed)
        np.testing.assert_allclose(changed_predictions.iloc[:2], predicted.iloc[:2])
        self.assertNotEqual(changed_predictions.iloc[2], predicted.iloc[2])

    def test_low_memory_training_can_score_and_predict_held_out_hours(self):
        index = pd.date_range("2025-01-01", periods=200, freq="h", tz="UTC")
        y = pd.Series(np.random.default_rng(7).normal(size=200), index=index)
        y.iloc[30:35] = np.nan
        with patch("sarima_model.SEASONAL_ORDER", (0, 0, 0, 0)):
            fitted = fit_model(y.iloc[:150], (1, 0, 0), 100)
        validation = y.iloc[150:180].copy()
        validation.iloc[5] = np.nan
        score, likelihood, k = conditional_validation_aic(fitted, validation)
        self.assertTrue(np.isfinite(score))
        self.assertAlmostEqual(score, -2 * likelihood + 2 * k)
        # Independently compute conditional likelihood by filtering the entire
        # chronological sequence at fixed parameters and slicing held-out terms.
        full = SARIMAX(pd.concat([y.iloc[:150], validation]), order=(1, 0, 0),
                       enforce_stationarity=False, enforce_invertibility=False).filter(fitted.params)
        self.assertAlmostEqual(likelihood, float(full.llf_obs[150:].sum()), places=7)
        test = y.iloc[180:]
        final_validation_state = filter_next_block(fitted, validation)
        prediction = one_step_predictions(final_validation_state, test)
        self.assertEqual(len(prediction), len(test))
        self.assertTrue(np.isfinite(prediction).all())

    def test_metrics_use_common_timestamps_and_exclude_zero_mape(self):
        actual = pd.Series([0.0, 2.0, 4.0, np.nan, 5.0])
        predicted = pd.Series([1.0, 3.0, 2.0, 0.0, 6.0])
        naive = pd.Series([1.0, 0.0, 2.0, 4.0, np.nan])
        scores, used = evaluation_metrics(actual, predicted, naive)
        self.assertEqual(used.tolist(), [True, True, True, False, False])
        self.assertAlmostEqual(scores["rmse"], np.sqrt(2))
        self.assertAlmostEqual(scores["mae"], 4 / 3)
        self.assertAlmostEqual(scores["mape"], 50)
        self.assertAlmostEqual(scores["skill_score"], 1 - np.sqrt(2) / np.sqrt(3))
        self.assertEqual(scores["mape_hours"], 2)
        zero, _ = evaluation_metrics(pd.Series([0.0]), pd.Series([1.0]), pd.Series([0.0]))
        self.assertTrue(np.isnan(zero["mape"]))
        self.assertTrue(np.isnan(zero["skill_score"]))

    def test_grid_search_continues_after_failed_fit(self):
        class Fitted:
            aic = 10.0

        effects = [RuntimeError("bad fit")] + [Fitted() for _ in range(8)]
        scores = [(float(value), -1.0, 3) for value in [8, 6, 4, 2, 3, 4, 5, 6]]
        with tempfile.TemporaryDirectory() as directory:
            with patch("sarima_model.fit_model", side_effect=effects), patch(
                "sarima_model.conditional_validation_aic", side_effect=scores
            ), self.assertLogs("imarika.sarima", level="ERROR"):
                best = grid_search(pd.Series(dtype=float), pd.Series(dtype=float), 0, 100, Path(directory))
            rows = pd.read_csv(Path(directory) / "grid_search.csv")
        self.assertEqual(len(rows), 9)
        self.assertEqual(best["successful_candidates"], 8)
        self.assertEqual((best["p"], best["d"], best["q"]), (1, 0, 1))

    def test_actual_pipeline_schema_maps_to_requested_targets(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "unified.csv"
            pd.DataFrame({"device_id": ["'101990961244800083'"], "hour": ["2025-08-10T06:00:00Z"],
                          "iot_air_temperature": [22.0], "iot_barometric_pressure": [880.0]}).to_csv(path, index=False)
            loaded = load_dataset(path, ["air_temperature", "barometric_pressure_hpa"])
        self.assertEqual(loaded.device_id.iloc[0], "101990961244800083")
        self.assertEqual(loaded.barometric_pressure_hpa.iloc[0], 880.0)
        self.assertEqual(str(loaded.timestamp.dt.tz), "UTC")


if __name__ == "__main__":
    unittest.main()
