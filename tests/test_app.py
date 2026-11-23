"""Dashboard navigation, absent-file handling, and faithful persistence/audit data."""

import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd
from streamlit.testing.v1 import AppTest

from app import ROOT, demo_figure, parse_audit, read_unified_cached, seasonal_persistence, station_coordinates


class DashboardTests(unittest.TestCase):
    def test_persistence_repeats_clock_hours_including_gaps(self):
        index = pd.date_range("2026-01-01", periods=200, freq="h", tz="UTC")
        series = pd.Series(np.arange(200, dtype=float), index=index).drop(index[185])
        actual, forecast = seasonal_persistence(series)
        self.assertEqual(len(actual), 168)
        self.assertEqual(len(forecast), 24)
        self.assertEqual(forecast.index.min(), index[-1] + pd.Timedelta(hours=1))
        self.assertEqual(forecast.index.max(), index[-1] + pd.Timedelta(hours=24))
        np.testing.assert_allclose(forecast.to_numpy(), series.reindex(index[-24:]).to_numpy(), equal_nan=True)
        self.assertTrue(pd.isna(forecast.iloc[9]))
        figure = demo_figure(actual, forecast, "Temperature", "°C")
        self.assertEqual(figure.data[1].line.dash, "dot")
        self.assertEqual(figure.data[1].name, "Persistence baseline forecast")

    def test_seven_day_window_is_identical_for_mixed_timezone_strings(self):
        index = pd.date_range(end="2026-09-14 17:00:00Z", periods=240, freq="h")
        baseline = pd.Series(np.arange(240, dtype=float), index=index)
        actual, forecast = seasonal_persistence(baseline)
        zones = ["UTC", "Africa/Nairobi", "America/New_York", "Asia/Kolkata"]
        timestamps = [time.tz_convert(zones[i % len(zones)]).isoformat() for i, time in enumerate(index)]
        strings = pd.Series(baseline.to_numpy(), index=timestamps).iloc[::-1]
        converted, predicted = seasonal_persistence(strings)
        pd.testing.assert_series_equal(actual, converted)
        pd.testing.assert_series_equal(forecast, predicted)
        self.assertEqual(converted.index.min(), pd.Timestamp("2026-09-07 18:00:00Z"))
        self.assertEqual(converted.index.max(), pd.Timestamp("2026-09-14 17:00:00Z"))
        self.assertEqual(converted.notna().sum(), 168)

    def test_trailing_placeholders_do_not_move_window_past_available_readings(self):
        index = pd.date_range(end="2026-09-16 17:00:00Z", periods=240, freq="h")
        series = pd.Series(np.arange(240, dtype=float), index=index)
        series.loc[series.index > pd.Timestamp("2026-09-14 17:00:00Z")] = np.nan
        actual, forecast = seasonal_persistence(series)
        self.assertEqual(actual.index.max(), pd.Timestamp("2026-09-14 17:00:00Z"))
        self.assertEqual(actual.notna().sum(), 168)
        self.assertEqual(forecast.index.min(), pd.Timestamp("2026-09-14 18:00:00Z"))

    def test_csv_loader_handles_mixed_offsets_before_filtering(self):
        index = pd.date_range(end="2026-09-14 17:00:00Z", periods=200, freq="h")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "unified.csv"
            pd.DataFrame({
                "device_id": ["101990961244800083"] * len(index),
                "timestamp": [time.tz_convert("Africa/Nairobi" if i % 2 else "UTC").isoformat()
                              for i, time in enumerate(index)],
                "air_temperature": np.arange(len(index), dtype=float),
            }).to_csv(path, index=False)
            frame = read_unified_cached(str(path), (path.stat().st_mtime_ns, path.stat().st_size))
        actual, _ = seasonal_persistence(frame.set_index("timestamp").air_temperature)
        self.assertEqual(actual.notna().sum(), 168)
        self.assertEqual(actual.index.max(), pd.Timestamp("2026-09-14 17:00:00Z"))

    def test_registry_filter_removes_test_device_and_outside_coordinates(self):
        records = [
            {"device_id": "'good'", "lat": .4, "lon": 34.3},
            {"device_id": "test-spatial-check-device", "lat": .4, "lon": 34.3},
            {"device_id": "outside", "lat": 1, "lon": 34.3},
            {"device_id": "missing", "lat": None, "lon": 34.3},
        ]
        result = station_coordinates(records)
        self.assertEqual(result.device_id.tolist(), ["good"])

    def test_audit_metrics_preserve_report_findings(self):
        audit = (ROOT / "reports/data_quality/report.md").read_text(encoding="utf-8")
        metrics = parse_audit(audit)
        self.assertAlmostEqual(metrics["zero_flag_pct"], 94.93, places=2)
        self.assertEqual(metrics["correlation"], "0.875–0.912")
        self.assertEqual(metrics["reset_candidates"], 2)
        self.assertTrue(all(value is None for value in parse_audit("unrecognized report").values()))

    def test_all_five_pages_render_and_live_demo_controls_work(self):
        app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
        self.assertFalse(app.exception)
        self.assertEqual([metric.value for metric in app.metric], ["26", "764,309", "13 months", "4", "1,146,327"])
        for page in ["Data Collection", "Model Results", "Live Demo", "About"]:
            app.sidebar.radio[0].set_value(page).run()
            self.assertFalse(app.exception, f"Page failed: {page}: {app.exception}")
            if page == "Data Collection":
                self.assertEqual(app.metric[0].value, "26")
                self.assertEqual(app.metric[1].value, "94.93%")
            if page == "Model Results":
                self.assertEqual(len(app.tabs), 5)
                app.selectbox[0].set_value("ALL_STATIONS_MEAN").run()
                self.assertFalse(app.exception)
                self.assertTrue(any("station(s)" in message.value for message in app.info))
            if page == "Live Demo":
                self.assertEqual(len(app.selectbox[0].options), 26)
                self.assertEqual(app.metric[1].value, "168")
                self.assertTrue(any("Observed readings: 4 / 168" in caption.value for caption in app.caption))
                self.assertTrue(any("14 Sep 2026" in caption.value for caption in app.caption))
                app.selectbox[1].set_value("rainfall_mm").run()
                self.assertFalse(app.exception)
                app.selectbox[0].set_value("101990961244800361").run()
                self.assertFalse(app.exception)

    def test_missing_artifacts_show_warnings_without_page_exceptions(self):
        with tempfile.TemporaryDirectory() as directory:
            code = (
                "from pathlib import Path\nfrom unittest.mock import patch\nimport app\n"
                f"with patch.object(app, 'DATA', Path({directory!r})), patch.object(app, 'REPORTS', Path({directory!r})):\n"
                "    app.main()\n"
            )
            app = AppTest.from_string(code, default_timeout=30).run()
            for page in ["Data Collection", "Model Results", "Live Demo"]:
                app.sidebar.radio[0].set_value(page).run()
                self.assertFalse(app.exception, page)
                self.assertGreater(len(app.warning), 0)


if __name__ == "__main__":
    unittest.main()
