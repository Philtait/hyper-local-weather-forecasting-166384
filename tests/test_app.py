"""Dashboard navigation, absent-file handling, forecast export pages and test SMS."""

import tempfile
import unittest
from pathlib import Path

import pandas as pd
from streamlit.testing.v1 import AppTest

from app import FORECAST_CAVEATS, NOT_GENERATED, PAGES, ROOT, parse_audit, station_coordinates
from insights import generate_insights


def write_fixture(root):
    """Small synthetic forecast export in a temporary folder (tests only)."""
    deploy = root / "deploy_data"
    deploy.mkdir()
    dates = pd.date_range("2026-11-24", periods=7, freq="D").strftime("%Y-%m-%d")
    forecast = pd.concat([
        pd.DataFrame({"device_id": "A", "date": dates, "rain_mm": [0, 0, 5, 25, 55, 0, 0],
                      "rain_upper": [0, 1, 8, 40, 80, 2, 0]}),
        pd.DataFrame({"device_id": "B", "date": dates, "rain_mm": [0.0] * 7, "rain_upper": [0.5] * 7}),
    ]).assign(temp_min=16.0, temp_max=29.0, wind_max=4.0)
    forecast.to_csv(deploy / "forecast_daily.csv", index=False)
    pd.DataFrame({
        "device_id": ["A", "B"], "latitude": [0.45, 0.30], "longitude": [34.2, 34.4],
        "last_reading_utc": ["2026-11-23T18:00:00Z", "2026-09-07T16:02:00Z"],
        "active": [True, False], "reason": ["", "No readings since 7 Sep 2026"], "area": ["Nambale", None],
    }).to_csv(deploy / "station_status.csv", index=False)
    pd.DataFrame(generate_insights(forecast)).to_csv(deploy / "insights_latest.csv", index=False)
    (deploy / "README.md").write_text("Synthetic test export.\n", encoding="utf-8")
    return deploy


def script(deploy, data=None, reports=None):
    """App script with deploy_data (and optionally data/ and reports/) redirected.

    SMS credentials are forced absent and the SDK is replaced by a mock that
    fails if touched, so no test can reach the network or read real secrets.
    """
    patches = [f"patch.object(app, 'DEPLOY', Path({str(deploy)!r}))",
               "patch.object(sms_sender, 'load_credentials', return_value=None)",
               "patch.dict(sys.modules, {'africastalking': NO_NETWORK})"]
    if data:
        patches.append(f"patch.object(app, 'DATA', Path({str(data)!r}))")
    if reports:
        patches.append(f"patch.object(app, 'REPORTS', Path({str(reports)!r}))")
    return ("import sys\nfrom pathlib import Path\nfrom unittest.mock import MagicMock, patch\n"
            "import app, sms_sender\n"
            "NO_NETWORK = MagicMock()\n"
            "NO_NETWORK.initialize.side_effect = AssertionError('network access in a test')\n"
            f"with {', '.join(patches)}:\n    app.main()\n")


def values(elements):
    return [element.value for element in elements]


class DashboardTests(unittest.TestCase):
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

    def test_real_pages_render_without_deploy_data(self):
        with tempfile.TemporaryDirectory() as empty:
            app = AppTest.from_string(script(empty), default_timeout=30).run()
            self.assertFalse(app.exception)
            self.assertEqual(values(app.metric), ["26", "764,309", "13 months", "4", "1,146,327"])
            for page in PAGES[1:]:
                app.sidebar.radio[0].set_value(page).run()
                self.assertFalse(app.exception, f"Page failed: {page}: {app.exception}")
                if page == "Data Collection" and (ROOT / "data/busia_stations.json").is_file():
                    self.assertEqual(app.metric[0].value, "26")
                    self.assertEqual(app.metric[1].value, "94.93%")
                if page == "Model Results":
                    self.assertEqual(len(app.tabs), 5)
                    app.selectbox[0].set_value("ALL_STATIONS_MEAN").run()
                    self.assertFalse(app.exception)
                    self.assertTrue(any("station(s)" in message for message in values(app.info)))
                if page in ("Forecast and Advisories", "Network Status"):
                    self.assertTrue(any(NOT_GENERATED in message for message in values(app.info)))

    def test_forecast_page_renders_from_synthetic_deploy_data(self):
        with tempfile.TemporaryDirectory() as directory:
            deploy = write_fixture(Path(directory))
            app = AppTest.from_string(script(deploy), default_timeout=30).run()
            app.sidebar.radio[0].set_value("Forecast and Advisories").run()
            self.assertFalse(app.exception)
            self.assertFalse(any(NOT_GENERATED in message for message in values(app.info)))
            picker = app.selectbox(key="forecast_station")
            self.assertEqual(picker.options, ["Nambale · Station A", "Station B (0.300°N, 34.400°E)"])
            metrics = {metric.label: metric.value for metric in app.metric}
            self.assertEqual(metrics["Forecast as of"], "23 Nov 2026")
            self.assertEqual(metrics["Advisories"], "4")
            for caveat in FORECAST_CAVEATS:
                self.assertTrue(any(caveat in message for message in values(app.info)), caveat)
            text = " ".join(values(app.markdown))
            self.assertIn("Flood alert · warning", text)
            self.assertIn("Rain delay · advisory", text)
            self.assertEqual(len(app.tabs), 3)
            self.assertFalse(any("differs" in message for message in values(app.warning)))
            picker.set_value("B").run()
            self.assertFalse(app.exception)
            self.assertTrue(any("No advisories" in message for message in values(app.success)))

    def test_network_status_counts_from_synthetic_deploy_data(self):
        with tempfile.TemporaryDirectory() as directory:
            deploy = write_fixture(Path(directory))
            app = AppTest.from_string(script(deploy), default_timeout=30).run()
            app.sidebar.radio[0].set_value("Network Status").run()
            self.assertFalse(app.exception)
            metrics = {metric.label: metric.value for metric in app.metric}
            self.assertEqual((metrics["Active stations"], metrics["Inactive stations"]), ("1", "1"))
            self.assertEqual(metrics["Stations listed"], "2")

    def test_no_crash_when_deploy_data_and_data_are_missing(self):
        with tempfile.TemporaryDirectory() as empty:
            app = AppTest.from_string(script(empty, data=empty, reports=empty), default_timeout=30).run()
            self.assertFalse(app.exception)
            for page in PAGES:
                app.sidebar.radio[0].set_value(page).run()
                self.assertFalse(app.exception, page)
                if page == "Data Collection":
                    self.assertTrue(any("busia_stations.json" in message for message in values(app.info)))
                if page == "Model Results":
                    self.assertGreater(len(app.warning), 0)
                if page in ("Forecast and Advisories", "Network Status"):
                    self.assertTrue(any(NOT_GENERATED in message for message in values(app.info)))
            app.sidebar.radio[0].set_value("Forecast and Advisories").run()
            app.radio(key="advisory_mode").set_value("Scenario").run()
            self.assertFalse(app.exception)
            self.assertTrue(any("Scenario, not a forecast" in message for message in values(app.warning)))

    def test_scenario_mode_produces_expected_advisory(self):
        with tempfile.TemporaryDirectory() as empty:
            app = AppTest.from_string(script(empty), default_timeout=30).run()
            app.sidebar.radio[0].set_value("Forecast and Advisories").run()
            app.radio(key="advisory_mode").set_value("Scenario").run()
            self.assertTrue(any("no advisory" in message for message in values(app.success)))
            app.number_input(key="scenario_rain_3").set_value(55.0).run()
            self.assertFalse(app.exception)
            text = " ".join(values(app.markdown))
            self.assertIn("Flood alert · warning", text)
            self.assertNotIn("Rain delay", text)
            self.assertTrue(any(value.startswith("Scenario: Flood warning") for value in values(app.text_area)))
            self.assertEqual(len(app.button), 0)  # scenario mode never offers sending

    def test_test_sms_needs_valid_number_and_falls_back_to_preview(self):
        with tempfile.TemporaryDirectory() as directory:
            deploy = write_fixture(Path(directory))
            app = AppTest.from_string(script(deploy), default_timeout=30).run()
            app.sidebar.radio[0].set_value("Forecast and Advisories").run()
            preview = app.text_area(key="sms_preview").value
            self.assertLess(len(preview), 160)
            app.text_input(key="sms_phone").set_value("0712345678").run()
            app.button(key="sms_confirm").click().run()
            self.assertTrue(any("+254" in message for message in values(app.error)))
            app.text_input(key="sms_phone").set_value("+254712345678").run()
            app.button(key="sms_confirm").click().run()
            self.assertFalse(app.exception)
            self.assertTrue(any("not configured" in message for message in values(app.warning)))
            self.assertIn(preview, values(app.code))


if __name__ == "__main__":
    unittest.main()
