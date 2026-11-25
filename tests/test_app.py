"""Dashboard: weather pages from a synthetic export, plain language, missing files, SMS."""

import json
import re
import tempfile
import unittest
from pathlib import Path

import pandas as pd
from streamlit.testing.v1 import AppTest

from app import (
    NOT_GENERATED, ROOT, STATUS, TECH_PAGES, WEATHER_PAGES, build_network, parse_audit, prepare_status,
    station_coordinates, station_names, status_key, summary_sentence,
)
from geo import sub_county
from insights import generate_insights

STATION_A, STATION_B, STATION_C, STATION_D = (
    "101990961244800083", "101990961244800113", "101990961244800222", "101990961244800999")


def square(x0, y0, x1, y1):
    return {"type": "Polygon", "coordinates": [[[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]]}


def write_geojson(path):
    """Tiny test boundaries: Kenya and Busia squares, two sub-county squares."""
    features = [
        ("country", "Kenya", square(33.9, -4.7, 41.9, 5.0)),
        ("county", "Busia", square(34.1, 0.2, 34.6, 0.7)),
        ("sub-county", "Nambale", square(34.2, 0.4, 34.3, 0.5)),
        ("sub-county", "Butula", square(34.3, 0.3, 34.4, 0.4)),
    ]
    path.write_text(json.dumps({"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {"level": level, "name": name}, "geometry": geometry}
        for level, name, geometry in features]}), encoding="utf-8")
    return path


def write_fixture(root):
    """Small synthetic forecast export in a temporary folder (tests only)."""
    deploy = root / "deploy_data"
    deploy.mkdir()
    dates = pd.date_range("2026-11-24", periods=7, freq="D").strftime("%Y-%m-%d")
    rain = {STATION_A: [0, 0, 5, 25, 55, 0, 0], STATION_B: [0.0] * 7, STATION_C: [0.0] * 7,
            STATION_D: [0, 0, 0, 6.5, 6.5, 7, 0]}
    forecast = pd.concat([pd.DataFrame({"device_id": device, "date": dates, "rain_mm": values,
                                        "rain_upper": [value * 1.5 for value in values]})
                          for device, values in rain.items()]).assign(temp_min=16.0, temp_max=29.0, wind_max=4.0)
    forecast.to_csv(deploy / "forecast_daily.csv", index=False)
    pd.DataFrame({
        "device_id": [STATION_A, STATION_B, STATION_C, STATION_D],
        "latitude": [0.45, 0.35, 0.33, 0.65], "longitude": [34.25, 34.35, 34.32, 34.55],
        "last_reading_utc": ["2026-11-23T18:00:00Z", "2026-11-23T17:45:00Z", "2026-09-07T16:02:00Z",
                             "2026-11-23T18:10:00Z"],
        "active": [True, True, False, True], "reason": ["", "", "No readings since 7 Sep 2026", ""],
    }).to_csv(deploy / "station_status.csv", index=False)
    pd.DataFrame(generate_insights(forecast)).to_csv(deploy / "insights_latest.csv", index=False)
    pd.DataFrame({
        "device_id": [STATION_A, STATION_B, STATION_C],
        "reading_utc": ["2026-11-23T18:00:00Z", "2026-11-23T17:45:00Z", None],
        "air_temperature": [22.4, 23.0, None], "air_humidity": [81.0, 77.0, None],
        "wind_speed": [1.8, 2.2, None], "rain_today_mm": [3.2, 0.0, None], "age_hours": [0.2, 0.4, None],
    }).to_csv(deploy / "latest_readings.csv", index=False)
    (deploy / "README.md").write_text("Synthetic test export.\n", encoding="utf-8")
    return deploy


def script(deploy, boundaries=None, data=None, reports=None):
    """App script with deploy_data, boundaries, data/ and reports/ redirected.

    SMS credentials are forced absent and the SDK is replaced by a mock that
    fails if touched, so no test can reach the network or read real secrets.
    """
    patches = [f"patch.object(app, 'DEPLOY', Path({str(deploy)!r}))",
               "patch.object(sms_sender, 'load_credentials', return_value=None)",
               "patch.dict(sys.modules, {'africastalking': NO_NETWORK})"]
    for name, value in [("BOUNDARIES", boundaries), ("DATA", data), ("REPORTS", reports)]:
        if value:
            patches.append(f"patch.object(app, {name!r}, Path({str(value)!r}))")
    return ("import sys\nfrom pathlib import Path\nfrom unittest.mock import MagicMock, patch\n"
            "import app, sms_sender\n"
            "NO_NETWORK = MagicMock()\n"
            "NO_NETWORK.initialize.side_effect = AssertionError('network access in a test')\n"
            f"with {', '.join(patches)}:\n    app.main()\n")


def goto(app, page):
    key = "nav_weather" if page in WEATHER_PAGES else "nav_tech"
    return app.sidebar.radio(key=key).set_value(page).run()


def values(elements):
    return [element.value for element in elements]


def strip_customdata(node):
    if isinstance(node, dict):
        return {key: strip_customdata(value) for key, value in node.items() if key != "customdata"}
    if isinstance(node, list):
        return [strip_customdata(value) for value in node]
    return node


def visible_text(app):
    """All text a viewer can see on the current page, including chart text."""
    parts = []
    for kind in ["title", "header", "subheader", "markdown", "caption", "info", "warning", "success",
                 "error", "code", "text"]:
        parts += [str(value) for value in values(getattr(app, kind))]
    for widget in ["selectbox", "number_input", "text_input", "toggle", "radio", "button"]:
        for element in getattr(app, widget):
            parts.append(str(element.label))
            parts += [str(option) for option in getattr(element, "options", [])]
    parts += [str(element.label) for element in app.expander]
    for chart in app.get("plotly_chart"):
        parts.append(json.dumps(strip_customdata(json.loads(chart.proto.spec))))
    return " ".join(parts)


FORBIDDEN = [r"soil temp", r"moisture", r"evapo", r"readiness", r"crop model", r"crop selection",
             r"cloud cover", r"feels[- ]like", r"precipitation likelihood", r"sms balance", r"delivery rate",
             r"shortcode", r"subscriber", r"reply stop", r"busiaweather", r"meteorological department",
             r"\bkmd\b", r"ministry", r"agro-met", r"official"]
JARGON = [r"SARIMA", r"LSTM", r"RMSE", r"\bAIC\b", r"envelope", r"confidence interval", r"device_id", r"\d{18}"]


class StatusRuleTests(unittest.TestCase):
    def test_status_precedence(self):
        warning = {"type": "flood_alert", "severity": "warning"}
        watch = {"type": "flood_alert", "severity": "watch"}
        planting = {"type": "planting_window", "severity": "advisory"}
        delay = {"type": "rain_delay", "severity": "advisory"}
        self.assertEqual(status_key([delay, planting, watch, warning], True), "warning")
        self.assertEqual(status_key([delay, planting, watch], True), "watch")
        self.assertEqual(status_key([delay, planting], True), "planting")
        self.assertEqual(status_key([delay], True), "delay")
        self.assertEqual(status_key([], True), "none")
        self.assertEqual(status_key([warning], False), "offline")

    def test_plain_language_status_labels(self):
        self.assertEqual([label for label, _ in STATUS.values()], [
            "Very heavy rain expected", "Heavy rain expected", "Good rain for planting",
            "Rain coming: delay spraying", "No alerts this week", "Not reporting"])
        self.assertEqual(len({colour for _, colour in STATUS.values()}), 6)

    def test_point_in_polygon_labels(self):
        with tempfile.TemporaryDirectory() as directory:
            from geo import load_boundaries
            features = load_boundaries(write_geojson(Path(directory) / "b.geojson"))
        self.assertEqual(sub_county(features, 34.25, 0.45), "Nambale")
        self.assertEqual(sub_county(features, 34.35, 0.35), "Butula")
        self.assertIsNone(sub_county(features, 34.55, 0.65))
        self.assertIsNone(sub_county(features, None, 0.45))
        status = prepare_status(pd.DataFrame({
            "device_id": [STATION_A, STATION_D], "latitude": [0.45, 0.65], "longitude": [34.25, 34.55],
            "last_reading_utc": ["2026-11-23T18:00:00Z"] * 2, "active": [True, True], "reason": ["", ""]}))
        names = station_names([STATION_A, STATION_D], status, features)
        self.assertEqual(names, {STATION_A: "Station 083, Nambale", STATION_D: "Station 999"})

    def test_hole_in_polygon_is_outside(self):
        from geo import contains
        donut = {"type": "Polygon", "coordinates": [
            [[0, 0], [4, 0], [4, 4], [0, 4], [0, 0]], [[1, 1], [3, 1], [3, 3], [1, 3], [1, 1]]]}
        self.assertTrue(contains(donut, 0.5, 0.5))
        self.assertFalse(contains(donut, 2, 2))
        self.assertFalse(contains(donut, 5, 5))

    def test_summary_sentence_counts_reporting_and_heavy_rain(self):
        with tempfile.TemporaryDirectory() as directory:
            deploy = write_fixture(Path(directory))
            forecast = pd.read_csv(deploy / "forecast_daily.csv", dtype={"device_id": str})
            status = prepare_status(pd.read_csv(deploy / "station_status.csv", dtype={"device_id": str}))
        network = build_network(forecast.assign(date=pd.to_datetime(forecast.date).dt.date), status, [])
        self.assertEqual(dict(zip(network.device_id, network.key)),
                         {STATION_A: "warning", STATION_B: "none", STATION_C: "offline", STATION_D: "planting"})
        self.assertEqual(summary_sentence(network),
                         "Busia County: 3 of 4 stations are reporting. 1 station expects heavy rain this week.")


class DashboardTests(unittest.TestCase):
    def test_registry_filter_removes_test_device_and_outside_coordinates(self):
        records = [
            {"device_id": "'good'", "lat": .4, "lon": 34.3},
            {"device_id": "test-spatial-check-device", "lat": .4, "lon": 34.3},
            {"device_id": "outside", "lat": 1, "lon": 34.3},
            {"device_id": "missing", "lat": None, "lon": 34.3},
        ]
        self.assertEqual(station_coordinates(records).device_id.tolist(), ["good"])

    def test_audit_metrics_preserve_report_findings(self):
        audit = (ROOT / "reports/data_quality/report.md").read_text(encoding="utf-8")
        metrics = parse_audit(audit)
        self.assertAlmostEqual(metrics["zero_flag_pct"], 94.93, places=2)
        self.assertEqual(metrics["correlation"], "0.875–0.912")
        self.assertEqual(metrics["reset_candidates"], 2)
        self.assertTrue(all(value is None for value in parse_audit("unrecognized report").values()))

    def test_technical_pages_render(self):
        with tempfile.TemporaryDirectory() as empty:
            app = AppTest.from_string(script(empty), default_timeout=30).run()
            self.assertFalse(app.exception)
            for page in TECH_PAGES:
                goto(app, page)
                self.assertFalse(app.exception, f"Page failed: {page}: {app.exception}")
                if page == "Project Overview":
                    self.assertEqual(values(app.metric), ["26", "764,309", "13 months", "4", "1,146,327"])
                if page == "Data Collection" and (ROOT / "data/busia_stations.json").is_file():
                    self.assertEqual(app.metric[0].value, "26")
                    self.assertEqual(app.metric[1].value, "94.93%")
                if page == "Model Results":
                    self.assertEqual(len(app.tabs), 5)
                if page == "About the project":
                    self.assertTrue(any("geoBoundaries (open licence)" in caption for caption in values(app.caption)))
            goto(app, "Weather Map")
            self.assertIsNone(app.session_state["nav_tech"])

    def test_weather_map_card_for_active_station(self):
        with tempfile.TemporaryDirectory() as directory:
            deploy = write_fixture(Path(directory))
            boundaries = write_geojson(Path(directory) / "b.geojson")
            app = AppTest.from_string(script(deploy, boundaries), default_timeout=30).run()
            self.assertFalse(app.exception)
            page = visible_text(app)
            self.assertIn("Imarika forecast, issued 23 November 2026", page)
            self.assertIn("Busia County: 3 of 4 stations are reporting. 1 station expects heavy rain this week.", page)
            self.assertIn("Boundaries: geoBoundaries (open licence)", page)
            picker = app.selectbox(key="station_choice")
            self.assertEqual(picker.options, ["Station 083, Nambale", "Station 113, Butula",
                                              "Station 222, Butula", "Station 999"])
            self.assertEqual(picker.value, STATION_A)
            self.assertIn("Very heavy rain expected", page)
            self.assertIn("If you are in a flood-prone area, move to higher ground.", page)
            self.assertIn("Rain: 55 mm", page)
            self.assertIn("Reading from 23 Nov 2026, 21:00 (Kenya time)", page)
            self.assertIn("20 mm needed over 5 days", page)
            expanders = [element.label for element in app.expander]
            self.assertIn("How reliable is this?", expanders)
            self.assertIn("Text message preview", expanders)
            self.assertIn("/160 characters. Preview. Sandbox only.", page)

    def test_weather_map_card_for_inactive_station(self):
        with tempfile.TemporaryDirectory() as directory:
            deploy = write_fixture(Path(directory))
            app = AppTest.from_string(script(deploy, write_geojson(Path(directory) / "b.geojson")),
                                      default_timeout=30).run()
            app.selectbox(key="station_choice").set_value(STATION_C).run()
            self.assertFalse(app.exception)
            page = visible_text(app)
            self.assertIn("Not reporting", page)
            self.assertIn("This station stopped reporting on 07 September 2026.", page)
            self.assertNotIn("Rain: 0 mm", page)
            self.assertIn("There is no alert to send for this station this week.", page)

    def test_farmer_pages_show_no_forbidden_labels_or_jargon(self):
        with tempfile.TemporaryDirectory() as directory:
            deploy = write_fixture(Path(directory))
            app = AppTest.from_string(script(deploy, write_geojson(Path(directory) / "b.geojson")),
                                      default_timeout=30).run()
            pages = []
            for station in [STATION_A, STATION_B, STATION_C, STATION_D]:
                app.selectbox(key="station_choice").set_value(station).run()
                pages.append(visible_text(app))
            goto(app, "What if? Try a rain forecast")
            app.number_input(key="scenario_rain_3").set_value(55.0).run()
            pages.append(visible_text(app))
            goto(app, "About this forecast")
            pages.append(visible_text(app))
            self.assertFalse(app.exception)
            for text in pages:
                for pattern in FORBIDDEN:
                    self.assertIsNone(re.search(pattern, text, re.IGNORECASE), pattern)
                for pattern in JARGON:
                    self.assertIsNone(re.search(pattern, text), pattern)

    def test_no_crash_when_deploy_data_and_data_are_missing(self):
        with tempfile.TemporaryDirectory() as empty:
            app = AppTest.from_string(script(empty, boundaries=Path(empty) / "none.geojson", data=empty,
                                             reports=empty), default_timeout=30).run()
            self.assertFalse(app.exception)
            self.assertTrue(any(NOT_GENERATED in message for message in values(app.info)))
            for page in WEATHER_PAGES + TECH_PAGES:
                goto(app, page)
                self.assertFalse(app.exception, page)
                if page == "Data Collection":
                    self.assertTrue(any("busia_stations.json" in message for message in values(app.info)))
                if page == "Model Results":
                    self.assertGreater(len(app.warning), 0)

    def test_map_falls_back_to_markers_when_boundaries_are_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            deploy = write_fixture(Path(directory))
            app = AppTest.from_string(script(deploy, boundaries=Path(directory) / "missing.geojson"),
                                      default_timeout=30).run()
            self.assertFalse(app.exception)
            self.assertTrue(any("only the stations are shown" in caption for caption in values(app.caption)))
            self.assertEqual(app.selectbox(key="station_choice").options[0], "Station 083")
            app.toggle(key="offline_map").set_value(True).run()
            self.assertFalse(app.exception)

    def test_offline_map_toggle_draws_outlines_without_tiles(self):
        with tempfile.TemporaryDirectory() as directory:
            deploy = write_fixture(Path(directory))
            app = AppTest.from_string(script(deploy, write_geojson(Path(directory) / "b.geojson")),
                                      default_timeout=30).run()
            online = json.loads(app.get("plotly_chart")[0].proto.spec)
            self.assertEqual(online["layout"]["map"]["style"], "carto-positron")
            app.toggle(key="offline_map").set_value(True).run()
            offline = json.loads(app.get("plotly_chart")[0].proto.spec)
            self.assertNotIn("map", offline["layout"])
            self.assertTrue(all(trace["type"] == "scatter" for trace in offline["data"]))
            legend = [trace.get("name") for trace in offline["data"] if trace.get("name")]
            self.assertEqual(legend, ["Very heavy rain expected", "Good rain for planting",
                                      "No alerts this week", "Not reporting"])

    def test_what_if_page_produces_expected_advisory(self):
        with tempfile.TemporaryDirectory() as empty:
            app = AppTest.from_string(script(empty), default_timeout=30).run()
            goto(app, "What if? Try a rain forecast")
            self.assertTrue(any("Scenario, not a forecast" in message for message in values(app.warning)))
            self.assertTrue(any("no alert" in message for message in values(app.success)))
            app.number_input(key="scenario_rain_3").set_value(55.0).run()
            self.assertFalse(app.exception)
            page = visible_text(app)
            self.assertIn("Very heavy rain expected", page)
            self.assertIn("Scenario: Flood warning", page)
            self.assertEqual(len(app.button), 0)  # the What if? page never offers sending

    def test_test_sms_needs_valid_number_and_falls_back_to_preview(self):
        with tempfile.TemporaryDirectory() as directory:
            deploy = write_fixture(Path(directory))
            boundaries = write_geojson(Path(directory) / "b.geojson")
            app = AppTest.from_string(script(deploy, boundaries), default_timeout=30).run()
            app.text_input(key="sms_phone").set_value("0712345678").run()
            app.button(key="sms_confirm").click().run()
            self.assertTrue(any("+254" in message for message in values(app.error)))
            app.text_input(key="sms_phone").set_value("+254712345678").run()
            app.button(key="sms_confirm").click().run()
            self.assertFalse(app.exception)
            self.assertTrue(any("not configured" in message for message in values(app.warning)))
            sent = values(app.code)
            self.assertEqual(len(sent), 1)
            self.assertLess(len(sent[0]), 160)
            self.assertTrue(sent[0].startswith("Station 083, Nambale: Flood warning"), sent[0])


if __name__ == "__main__":
    unittest.main()
