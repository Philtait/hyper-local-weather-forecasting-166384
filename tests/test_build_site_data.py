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
SRC = ROOT / "site_src"
EIGHTEEN_DIGITS = re.compile(r"(?<!\d)\d{18}(?!\d)")
# Words and phrases the site must never show (case-insensitive substrings).
NEVER_SHOW = [
    "soil temperature", "soil moisture", "moisture", "evapotranspiration", "evapo-trans", "readiness",
    "crop model", "agronomic model", "cloud cover", "feels like", "feels-like", "precipitation likelihood",
    "chance of rain", "sms balance", "delivery rate", "shortcode", "short code", "subscriber", "reply stop",
    "sender id", "sender name", "issued by", "county agro-met", "field protocol", "valid through",
    "live telemetry", "dismiss", "confidence interval", "envelope", "busia central station",
    # removed from the site: the sample-data banner and presentation mode
    "sample data", "presentation mode", "synthetic",
]
# The pages show no model names or accuracy figures at all.
PAGE_ONLY = ["neural network", "accuracy", "skill score", "typical error"]
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

    def test_history_covers_14_days_with_unknown_days_never_zero(self):
        stations = self.data["stations.json"]["stations"]
        gaps = 0
        for station in stations:
            history = station["history"]
            self.assertEqual(len(history), builder.HISTORY_DAYS, station["id"])
            self.assertEqual(history[-1]["date"], self.data["meta.json"]["as_of"])
            for day in history:
                if day["readings_received"] == 0:
                    gaps += 1
                    self.assertIsNone(day["rain_mm"])
                    self.assertIsNone(day["humidity_mean"])
            self.assertEqual(station["coverage_14d"], sum(day["readings_received"] > 0 for day in history))
            if not station["active"]:
                self.assertEqual(station["forecast"], [])
        self.assertGreater(gaps, 0, "synthetic history should include days without data")

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
        # Without history_daily.csv the charts get no history and no coverage count.
        self.assertTrue(all(s["history"] == [] and s["coverage_14d"] is None for s in stations))
        self.assertEqual(spraying["004"], "hold")
        self.assertEqual(spraying["005"], "go")


class HistoryFileTests(unittest.TestCase):
    """history_daily.csv and coverage_14d are read when the export has them."""

    def test_history_rows_and_coverage_come_from_the_export(self):
        device = "101990961244800083"
        with tempfile.TemporaryDirectory() as deploy, tempfile.TemporaryDirectory() as out:
            folder = Path(deploy)
            write_deploy(folder, {device: (True, [0, 0, 0, 0, 0, 0, 0])})
            status = pd.read_csv(folder / "station_status.csv", dtype={"device_id": "string"})
            status["coverage_14d"] = 11
            status.to_csv(folder / "station_status.csv", index=False)
            rows = [[device, "2026-11-30", 4.2, 15.1, 27.3, 81, 24], [device, "2026-11-29", None, None, None, None, 0],
                    [device, "2026-11-28", 0.0, 15.0, 26.0, 70, 12]]
            pd.DataFrame(rows, columns=builder.HISTORY_COLUMNS).to_csv(folder / builder.HISTORY_FILE, index=False)
            builder.build(deploy, out, ROOT / "reports")
            station = json.loads((Path(out) / "stations.json").read_text(encoding="utf-8"))["stations"][0]
        history = {day["date"]: day for day in station["history"]}
        self.assertEqual(len(history), builder.HISTORY_DAYS)
        self.assertEqual(history["2026-11-30"]["rain_mm"], 4.2)
        self.assertIsNone(history["2026-11-29"]["rain_mm"])           # no readings: unknown, not zero
        self.assertEqual(history["2026-11-28"]["rain_mm"], 0.0)       # a measured dry day stays zero
        self.assertIsNone(history["2026-11-20"]["rain_mm"])           # absent from the file: unknown
        self.assertEqual(station["coverage_14d"], 11)                 # the export's value wins


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
            + sorted((WEB / "api").glob("*.js")) + sorted((WEB / "i18n").glob("*.json")) \
            + sorted(SRC.rglob("*.html")) + [WEB / "manifest.webmanifest", WEB / "contact.json"]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_pages_and_scripts_never_show_banned_words_or_jargon(self):
        for path in self.pages:
            text = path.read_text(encoding="utf-8")
            lower = text.lower()
            for phrase in NEVER_SHOW + PAGE_ONLY:
                self.assertNotIn(phrase, lower, f"{path.name} contains {phrase!r}")
            for word in JARGON:
                self.assertIsNone(re.search(rf"\b{word}\b", text), f"{path.name} contains {word}")
            self.assertIsNone(EIGHTEEN_DIGITS.search(text), path.name)

    def test_data_never_shows_banned_words_and_names_models_once(self):
        for path in sorted(self.data_dir.glob("*.json")):
            values = list(string_values(json.loads(path.read_text(encoding="utf-8"))))
            joined = "\n".join(values)
            for phrase in [p for p in NEVER_SHOW if p != "synthetic"]:  # the flag is a key, never a value
                self.assertNotIn(phrase, joined.lower(), f"{path.name} contains {phrase!r}")
            for word in JARGON:
                count = len(re.findall(rf"\b{word}\b", joined))
                allowed = 1 if path.name == "model_results.json" and word in {"SARIMA", "LSTM"} else 0
                self.assertLessEqual(count, allowed, f"{path.name} names {word} {count} times")
            self.assertIsNone(EIGHTEEN_DIGITS.search(joined), path.name)


class PagesTests(unittest.TestCase):
    """Five pages share one header, footer and bottom navigation, built from site_src/."""

    PAGES = ["index.html", "how-it-works.html", "map.html", "sms.html", "contact.html"]

    def test_map_and_hero_polish(self):
        css = (WEB / "css" / "styles.css").read_text(encoding="utf-8")
        self.assertIn("scroll-margin-top", css)                      # sticky header never covers an anchor
        self.assertRegex(css, r"\.leaflet-tile-pane \{ filter: grayscale\(0\.7\)")
        self.assertRegex(css, r"\.area-btn \{[^}]*border-radius: 10px")
        self.assertNotRegex(css, r"\.mk-count \{[^}]*background")    # cluster colour comes from its status class
        map_js = (WEB / "js" / "map.js").read_text(encoding="utf-8")
        self.assertNotIn("days-small", map_js)                       # one row per day, no narrow tiles
        self.assertIn("mk-count mk-' + code", map_js)
        self.assertIn("zoomArea: true", map_js)                      # choosing an area zooms to it
        # A day's status checks codes in the same order as the engine's precedence.
        order = re.search(r"function dayStatus\(day\) \{(.*?)\n  \}", map_js, re.S).group(1)
        positions = [order.index('"' + code + '"') for code in ["warning", "watch", "planting", "delay", "none"]]
        self.assertEqual(positions, sorted(positions))
        home_js = (WEB / "js" / "home.js").read_text(encoding="utf-8")
        self.assertIn(r'replace(/\s+this week(\.?)\s*$/i, "$1")', home_js)

    def test_pages_are_up_to_date_with_their_sources(self):
        import build_pages
        self.assertEqual(build_pages.main(["--check"]), 0, "run python scripts/build_pages.py")

    def test_every_page_has_the_shared_layout_and_accessibility_basics(self):
        for name in self.PAGES:
            text = (WEB / name).read_text(encoding="utf-8")
            self.assertRegex(text, r'<html lang="\w+">', name)
            self.assertIn('class="skip-link" href="#main"', text, name)
            self.assertIn('<main id="main"', text, name)
            self.assertIn('class="site-header"', text, name)
            self.assertIn('class="bottom-nav"', text, name)
            self.assertEqual(text.count('aria-current="page"'), 2, name)  # top nav and bottom nav
            self.assertIn("Final-year project, Strathmore University. Philip Tait Lemaron.", text, name)
            self.assertIn("Images are illustrative and generated with AI.", text, name)
            self.assertIn("CC BY 3.0 IGO; IEBC and OCHA ROSEA", text, name)
            for target in self.PAGES:
                self.assertIn(f'href="{target}"', text, f"{name} does not link to {target}")

    def test_images_are_small_and_service_worker_caches_every_page(self):
        for name in ["about-field.webp", "station.webp", "farmer.webp"]:
            self.assertLess((WEB / "img" / name).stat().st_size, 150 * 1024, name)
        worker = (WEB / "sw.js").read_text(encoding="utf-8")
        for name in self.PAGES + ["img/icons.svg", "i18n/en.json", "img/about-field.webp"]:
            self.assertIn(f'"{name}"', worker, name)
        self.assertNotIn("tile.openstreetmap.org", worker)

    def test_no_contact_details_are_invented(self):
        email = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")
        for path in sorted(WEB.glob("*.html")) + sorted((WEB / "js").glob("*.js")) + [WEB / "i18n" / "en.json"]:
            self.assertIsNone(email.search(path.read_text(encoding="utf-8")), path.name)
        contact = json.loads((WEB / "contact.json").read_text(encoding="utf-8"))
        for key in ["email", "phone", "organisation", "address"]:
            self.assertIn(key, contact)

    def test_new_pages_carry_their_required_lines(self):
        sms = (WEB / "sms.html").read_text(encoding="utf-8")
        self.assertIn("See the SMS a farmer receives", sms)
        self.assertIn("Test mode: the message goes to a simulator, not a real phone.", sms)
        how = (WEB / "how-it-works.html").read_text(encoding="utf-8")
        for code in ["warning", "watch", "planting", "delay", "none", "offline"]:
            self.assertIn(f"badge st-{code}", how)
        self.assertIn("iLabAfrica Anga Weather Network", how)
        contact = (WEB / "contact.html").read_text(encoding="utf-8")
        self.assertIn("We do not store the details you type here.", contact)
        self.assertNotIn("action=", contact)  # mailto only: the form never posts to a server

    def test_home_numbers_are_not_hard_coded(self):
        text = (WEB / "index.html").read_text(encoding="utf-8")
        numbers = re.search(r'<ul class="numbers".*?</ul>', text, re.S).group(0)
        self.assertIsNone(re.search(r"<strong[^>]*>\s*\d", numbers))


if __name__ == "__main__":
    unittest.main()
