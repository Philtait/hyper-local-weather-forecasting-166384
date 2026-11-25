"""Website data builder: synthetic build, status precedence, synthetic guard, NEVER SHOW scan.

No network is used. Run: python -m pytest -q tests/test_build_site_data.py
"""

import csv
import json
import re
import subprocess
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import build_site_data as builder  # noqa: E402
from app import STATUS  # noqa: E402
from insights import generate_insights, load_config, sms_text  # noqa: E402

WEB = ROOT / "web"
EIGHTEEN_DIGITS = re.compile(r"(?<!\d)\d{18}(?!\d)")
# Words and phrases the site must never show (case-insensitive substrings).
NEVER_SHOW = [
    "soil temperature", "soil moisture", "moisture", "evapotranspiration", "evapo-trans", "readiness",
    "crop model", "agronomic model", "cloud cover", "feels like", "feels-like", "precipitation likelihood",
    "chance of rain", "sms balance", "delivery rate", "shortcode", "short code", "subscriber", "reply stop",
    "sender id", "sender name", "issued by", "county agro-met", "field protocol", "valid through",
    "live telemetry", "dismiss", "confidence interval", "envelope", "busia central station",
]
# Technical terms: never on the page; SARIMA and LSTM may appear once each in model_results.json.
JARGON = ["SARIMA", "LSTM", "RMSE", "AIC"]


def string_values(node):
    """All string values (not keys) in a JSON document."""
    if isinstance(node, dict):
        for value in node.values():
            yield from string_values(value)
    elif isinstance(node, list):
        for value in node:
            yield from string_values(value)
    elif isinstance(node, str):
        yield node


def synthetic_build(folder):
    with tempfile.TemporaryDirectory() as deploy:
        builder.synthetic_deploy(Path(deploy), builder.load_boundaries(builder.BOUNDARIES), date(2026, 11, 25))
        builder.build(deploy, folder, ROOT / "reports", synthetic=True)
    return {name: json.loads((Path(folder) / name).read_text(encoding="utf-8"))
            for name in ["meta.json", "stations.json", "scenarios.json", "model_results.json", "data_quality.json"]}


def write_deploy(folder, stations):
    """stations: {device_id: (active, [7 rain values])}."""
    start = date(2026, 12, 1)
    forecast, status = [], []
    for index, (device_id, (active, rain)) in enumerate(stations.items()):
        status.append([device_id, 0.40 + index * 0.01, 34.25, "2026-11-30T06:00:00Z", active, ""])
        for offset, value in enumerate(rain):
            forecast.append([device_id, (start + timedelta(days=offset)).isoformat(), value, value, 15.0, 27.0, 3.0])
    pd.DataFrame(forecast, columns=builder.DEPLOY_FILES["forecast_daily.csv"]).to_csv(folder / "forecast_daily.csv", index=False)
    pd.DataFrame(status, columns=builder.DEPLOY_FILES["station_status.csv"]).to_csv(folder / "station_status.csv", index=False)
    pd.DataFrame(columns=builder.DEPLOY_FILES["insights_latest.csv"]).to_csv(folder / "insights_latest.csv", index=False)
    (folder / "README.md").write_text("test export\n", encoding="utf-8")


class SyntheticBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.tmp.name)
        cls.data = synthetic_build(cls.out)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_meta_is_flagged_synthetic_and_counts_match_the_stations(self):
        meta, stations = self.data["meta.json"], self.data["stations.json"]["stations"]
        self.assertIs(meta["synthetic"], True)
        self.assertEqual(meta["as_of"], "2026-11-25")
        self.assertEqual(meta["counts"]["stations_total"], len(stations))
        self.assertEqual(meta["counts"]["stations_reporting"], sum(s["active"] for s in stations))
        self.assertEqual(meta["counts"]["stations_with_alerts"],
                         sum(s["status"]["code"] in {"warning", "watch", "planting", "delay"} for s in stations))
        self.assertEqual(meta["boundary_file"], "data/kenya_boundaries.geojson")
        self.assertTrue((self.out / "kenya_boundaries.geojson").is_file())

    def test_every_status_appears_and_uses_the_app_labels(self):
        stations = self.data["stations.json"]["stations"]
        self.assertEqual({s["status"]["code"] for s in stations}, set(STATUS))
        for station in stations:
            label, colour = STATUS[station["status"]["code"]]
            self.assertEqual((station["status"]["label"], station["status"]["colour"]), (label, colour))
            self.assertRegex(station["label"], r"^Station \w{3,}$")
            self.assertIn("area", station)
            self.assertNotIn("sub_county", station)

    def test_not_reporting_stations_have_no_forecast(self):
        for station in self.data["stations.json"]["stations"]:
            if not station["active"]:
                self.assertEqual(station["forecast"], [])
                self.assertEqual(station["insights"], [])
                self.assertIsNone(station["latest"])
                self.assertTrue(station["last_reading_utc"])
            else:
                self.assertEqual(len(station["forecast"]), 7)

    def test_insights_carry_basis_caveat_and_sms_text_within_one_message(self):
        limit = load_config()["sms"]["max_chars"]
        for station in self.data["stations.json"]["stations"]:
            for item in station["insights"]:
                for key in ["key", "basis", "caveat", "sms_text", "action", "title"]:
                    self.assertTrue(item[key], key)
                self.assertLessEqual(len(item["sms_text"]), limit)

    def test_scenarios_come_from_the_real_engine(self):
        scenarios = {s["key"]: s for s in self.data["scenarios.json"]["scenarios"]}
        self.assertEqual(set(scenarios), {"dry", "normal", "planting", "heavy"})
        self.assertEqual(scenarios["heavy"]["status"]["code"], "warning")
        self.assertEqual(scenarios["planting"]["status"]["code"], "planting")
        self.assertIsNone(scenarios["dry"]["sms_text"])
        heavy = scenarios["heavy"]
        frame = pd.DataFrame({"device_id": "Scenario", "date": heavy["dates"], "rain_mm": heavy["rain_mm"],
                              "rain_upper": heavy["rain_mm"], "temp_min": float("nan"), "temp_max": float("nan"),
                              "wind_max": float("nan")})
        expected = {sms_text(item, area="Scenario") for item in generate_insights(frame)}
        self.assertEqual({item["sms_text"] for item in heavy["insights"]}, expected)

    def test_model_results_match_the_report_file_and_show_no_lstm_numbers(self):
        models = self.data["model_results.json"]
        with (ROOT / "reports" / "sarima_results.csv").open(newline="", encoding="utf-8") as handle:
            rows = {row["variable"]: row for row in csv.DictReader(handle) if row["device_id"].isdigit()}
        for item in models["sarima"]["variables"]:
            self.assertEqual(item["source"], "reports/sarima_results.csv")
            self.assertAlmostEqual(item["typical_error"], float(rows[item["variable"]]["rmse"]), places=4)
            self.assertAlmostEqual(item["skill_vs_persistence"], float(rows[item["variable"]]["skill_score"]), places=3)
        self.assertFalse([value for value in models["lstm"].values() if isinstance(value, (int, float))])

    def test_every_data_quality_number_has_a_source(self):
        quality = self.data["data_quality.json"]
        for item in quality["audit"] + quality["fixes"]:
            self.assertTrue(item["source"])
        self.assertTrue(all("#40" in item["source"] for item in quality["fixes"]))

    def test_no_raw_device_numbers_or_upper_estimate_in_the_data(self):
        for name, document in self.data.items():
            text = json.dumps(document)
            self.assertIsNone(EIGHTEEN_DIGITS.search(text), name)
            self.assertNotIn("rain_upper", text, name)


class StatusPrecedenceTests(unittest.TestCase):
    """Precedence: not reporting, very heavy rain, heavy rain, planting, rain delay, none."""

    def test_status_follows_the_streamlit_precedence(self):
        cases = {
            "101990961244800001": (True, [0, 0, 60, 25, 6, 5, 4], "warning"),   # warning beats watch and planting
            "101990961244800002": (True, [2, 0, 25, 0, 0, 0, 0], "watch"),      # watch beats delay
            "101990961244800003": (True, [5, 6, 5, 4, 0, 0, 0], "planting"),    # planting beats delay
            "101990961244800004": (True, [3, 0, 0, 0, 0, 0, 0], "delay"),
            "101990961244800005": (True, [0, 0, 0, 0, 0, 0, 0], "none"),
            "101990961244800006": (False, [0, 0, 80, 0, 0, 0, 0], "offline"),  # not reporting beats everything
        }
        with tempfile.TemporaryDirectory() as deploy, tempfile.TemporaryDirectory() as out:
            write_deploy(Path(deploy), {key: (active, rain) for key, (active, rain, _) in cases.items()})
            builder.build(deploy, out, ROOT / "reports")
            stations = json.loads((Path(out) / "stations.json").read_text(encoding="utf-8"))["stations"]
            meta = json.loads((Path(out) / "meta.json").read_text(encoding="utf-8"))
        got = {station["id"]: station["status"]["code"] for station in stations}
        self.assertEqual(got, {key[-3:]: expected for key, (_, _, expected) in cases.items()})
        self.assertIs(meta["synthetic"], False)
        spraying = {s["id"]: next(o for o in s["operations"] if o["key"] == "spraying")["state"] for s in stations if s["active"]}
        self.assertEqual(spraying["004"], "hold")
        self.assertEqual(spraying["005"], "go")


class SyntheticGuardTests(unittest.TestCase):
    """A committed web/data/meta.json must never be synthetic."""

    @staticmethod
    def git(*args):
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)

    def test_committed_or_staged_meta_is_not_synthetic(self):
        if self.git("rev-parse", "--is-inside-work-tree").returncode != 0:
            self.skipTest("not a git checkout")
        for ref in ["HEAD:web/data/meta.json", ":web/data/meta.json"]:  # last commit, then the index
            shown = self.git("show", ref)
            if shown.returncode == 0:
                self.assertIs(json.loads(shown.stdout).get("synthetic"), False,
                              f"{ref} is synthetic preview data and must not be committed")


class NeverShowTests(unittest.TestCase):
    """Scan the site's HTML, scripts and data for words it must never show."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        data_dir = WEB / "data"
        if not (data_dir / "meta.json").is_file():
            synthetic_build(cls.tmp.name)
            data_dir = Path(cls.tmp.name)
        cls.data_dir = data_dir
        cls.pages = sorted(WEB.glob("*.html")) + sorted(WEB.glob("*.js")) + sorted((WEB / "js").glob("*.js")) \
            + sorted((WEB / "api").glob("*.js")) + [WEB / "manifest.webmanifest"]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_pages_and_scripts_never_show_banned_words_or_jargon(self):
        for path in self.pages:
            text = path.read_text(encoding="utf-8")
            lower = text.lower()
            for phrase in NEVER_SHOW:
                self.assertNotIn(phrase, lower, f"{path.name} contains {phrase!r}")
            for word in JARGON:
                self.assertIsNone(re.search(rf"\b{word}\b", text), f"{path.name} contains {word}")
            self.assertIsNone(EIGHTEEN_DIGITS.search(text), path.name)

    def test_data_never_shows_banned_words_and_names_models_once(self):
        for path in sorted(self.data_dir.glob("*.json")):
            values = list(string_values(json.loads(path.read_text(encoding="utf-8"))))
            joined = "\n".join(values)
            for phrase in NEVER_SHOW:
                self.assertNotIn(phrase, joined.lower(), f"{path.name} contains {phrase!r}")
            for word in JARGON:
                count = len(re.findall(rf"\b{word}\b", joined))
                allowed = 1 if path.name == "model_results.json" and word in {"SARIMA", "LSTM"} else 0
                self.assertLessEqual(count, allowed, f"{path.name} names {word} {count} times")
            self.assertIsNone(EIGHTEEN_DIGITS.search(joined), path.name)


if __name__ == "__main__":
    unittest.main()
