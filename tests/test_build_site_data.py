"""Website data builder: synthetic build, status precedence, synthetic guard, NEVER SHOW scan.

No network is used. Run: python -m pytest -q tests/test_build_site_data.py
"""

import csv
import json
import re
import shutil
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
    # real data: no "right now" for old readings, no unverified units, no export jargon
    "right now", "reporting now", "km/h", "lux", "light level", "light intensity", "as-of",
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
            for name in ["meta.json", "stations.json", "model_results.json", "data_quality.json"]}


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
        cls.files = {path.name for path in cls.out.iterdir()}

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

    def test_no_test_scenarios_are_built(self):
        self.assertNotIn("scenarios.json", self.files)
        self.assertFalse((WEB / "data" / "scenarios.json").exists(), "web/data/scenarios.json must be removed")

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
            self.assertEqual(station["days_with_data_14d"], sum(day["readings_received"] > 0 for day in history))
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
        spraying = {s["id"]: next(o for o in s["operations"] if o["key"] == "spraying")["state"]
                    for s in stations if s["active"] and s["operations"]}
        # Without history_daily.csv the charts get no history and no day count.
        self.assertTrue(all(s["history"] == [] and s["days_with_data_14d"] is None for s in stations))
        self.assertEqual(spraying["004"], "hold")
        # An all-dry week does not change from day to day, so it gets no field-work advice at all.
        flat = next(s for s in stations if s["id"] == "005")
        self.assertTrue(flat["flat"])
        self.assertEqual(flat["operations"], [])
        self.assertEqual(flat["advice_note"], builder.FLAT_NOTE)


class FlatForecastTests(unittest.TestCase):
    """A forecast with almost no day-to-day change gives no planting or rain-delay advice anywhere."""

    def build(self, stations, variation=None):
        with tempfile.TemporaryDirectory() as deploy, tempfile.TemporaryDirectory() as out:
            folder = Path(deploy)
            write_deploy(folder, stations)
            if variation is not None:  # the export's forecast_variation_mm column
                rows = [[device, "planting_window", "advisory", "2026-12-01", "m", "b", "2026-11-30", "c", value]
                        for device, value in variation.items()]
                pd.DataFrame(rows, columns=builder.DEPLOY_FILES["insights_latest.csv"] + ["forecast_variation_mm"]).to_csv(
                    folder / "insights_latest.csv", index=False)
            builder.build(deploy, out, ROOT / "reports")
            stations = json.loads((Path(out) / "stations.json").read_text(encoding="utf-8"))["stations"]
            meta = json.loads((Path(out) / "meta.json").read_text(encoding="utf-8"))
        return {s["id"]: s for s in stations}, meta

    def test_flat_forecast_drops_planting_and_delay_but_keeps_flood_alerts(self):
        got, meta = self.build({
            "101990961244800001": (True, [6, 6, 6, 6, 6, 6, 6]),        # would be planting: flat, so none
            "101990961244800002": (True, [25, 25, 25.5, 25, 25, 25, 25]),  # flat, but heavy rain still counts
            "101990961244800003": (True, [5, 6, 5, 4, 0, 0, 0]),        # changes: planting as before
        })
        self.assertEqual(got["001"]["status"]["code"], "none")
        self.assertTrue(got["001"]["flat"])
        self.assertEqual(got["001"]["operations"], [])
        self.assertIsNone(got["001"]["planting"])
        for station in got.values():
            if station["flat"]:
                self.assertFalse(any(item["type"] in builder.ADVICE_TYPES for item in station["insights"]))
                self.assertFalse(any(day["planting"] or day["alert"] == "delay" for day in station["forecast"]))
        self.assertEqual(got["002"]["status"]["code"], "watch")
        self.assertEqual(got["003"]["status"]["code"], "planting")
        self.assertFalse(got["003"]["flat"])
        self.assertEqual(meta["counts"]["stations_flat_forecast"], 2)

    def test_variation_from_the_export_wins(self):
        got, _ = self.build({"101990961244800003": (True, [5, 6, 5, 4, 0, 0, 0])}, variation={"101990961244800003": 0.5})
        self.assertTrue(got["003"]["flat"])
        self.assertEqual(got["003"]["status"]["code"], "none")


class LatestReadingTests(unittest.TestCase):
    """Latest readings carry only values with a documented unit; wind, gust and light are left out."""

    def test_latest_fields(self):
        device = "101990961244800001"
        with tempfile.TemporaryDirectory() as deploy, tempfile.TemporaryDirectory() as out:
            folder = Path(deploy)
            write_deploy(folder, {device: (True, [5, 6, 5, 4, 0, 0, 0])})
            columns = builder.DEPLOY_FILES["latest_readings.csv"] + [
                "light_intensity", "uv_index", "barometric_pressure", "peak_wind_gust", "wind_direction", "rain_since_morning_mm"]
            pd.DataFrame([[device, "2026-11-30T05:55:00Z", 21.2, 85, 1.4, 3.0, 2.0, 110000, -1, 873.2, 2.5, 185, 1.2]],
                         columns=columns).to_csv(folder / "latest_readings.csv", index=False)
            builder.build(deploy, out, ROOT / "reports")
            station = json.loads((Path(out) / "stations.json").read_text(encoding="utf-8"))["stations"][0]
            meta = json.loads((Path(out) / "meta.json").read_text(encoding="utf-8"))
        latest = station["latest"]
        self.assertEqual(latest["time_utc"], "2026-11-30T05:55:00Z")
        self.assertEqual(latest["rain_since_morning_mm"], 1.2)
        self.assertEqual(latest["pressure_hpa"], 873)
        self.assertIsNone(latest["uv_index"])                       # negative UV is not a reading
        for key in latest:
            self.assertNotRegex(key, "wind|gust|light")             # units not verified
        self.assertEqual(meta["headline"], "1 of 1 stations sent data in the last 2 days.")
        self.assertEqual(meta["latest_reading_utc"], "2026-11-30T06:00:00Z")


class HistoryFileTests(unittest.TestCase):
    """history_daily.csv is read when the export has it; days with data are counted from it."""

    def test_history_rows_and_days_with_data_come_from_the_export(self):
        device = "101990961244800083"
        with tempfile.TemporaryDirectory() as deploy, tempfile.TemporaryDirectory() as out:
            folder = Path(deploy)
            write_deploy(folder, {device: (True, [0, 0, 0, 0, 0, 0, 0])})
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
        self.assertEqual(station["days_with_data_14d"], 2)            # days with at least one reading


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


    def test_every_sms_never_shows_banned_words(self):
        """Every SMS the demo can send (latest readings, crop guide, alert; en and sw), built by the shared
        JS builder exactly as it is sent, is scanned too."""
        guide, stations = WEB / "data" / "crop_guide.json", WEB / "data" / "stations.json"
        node = shutil.which("node")
        if not guide.is_file() or not stations.is_file() or not node:
            self.skipTest("web/data or node is missing")
        script = (
            "const b = require(process.argv[1]); const data = {stations: require(process.argv[2]).stations,"
            " guide: require(process.argv[3])}; const out = [];"
            "for (const s of data.stations) for (const lang of b.LANGS) {"
            " for (const scenario of ['latest', 'alert']) { const m = b.buildMessage(data, {scenario, station: s.id, lang});"
            "  if (m) out.push([scenario, m.text]); }"
            " for (const crop of b.CROPS) { const m = b.buildMessage(data, {scenario: 'crop_guide', station: s.id, crop, lang});"
            "  if (m) out.push(['crop_guide', m.text]); } }"
            "process.stdout.write(JSON.stringify(out));"
        )
        result = subprocess.run([node, "-e", script, str(WEB / "js" / "sms-message.js"), str(stations), str(guide)],
                                capture_output=True, text=True, encoding="utf-8", check=True)
        messages = json.loads(result.stdout)
        self.assertTrue({"latest", "crop_guide"} <= {kind for kind, _ in messages})
        for kind, text in messages:
            lower = text.lower()
            for phrase in NEVER_SHOW + PAGE_ONLY + ["test scenario", "scenario"]:
                self.assertNotIn(phrase, lower, f"{kind} SMS contains {phrase!r}: {text}")
            for word in JARGON + ["AI", "now", "live", "sasa"]:
                self.assertIsNone(re.search(rf"\b{word}\b", text, 0 if word.isupper() else re.I),
                                  f"{kind} SMS contains {word}: {text}")
            self.assertIsNone(re.search(r"\{(RAIN|TEMP)\}|\bF0\d\b|°", text), text)
            self.assertIsNone(EIGHTEEN_DIGITS.search(text), text)
            self.assertLessEqual(len(text), 306)
            if kind == "latest":  # the reading's date and time are always there
                self.assertRegex(text, r"(Last reading|Kipimo cha mwisho) \w+ \d{1,2} \w+, \d{2}:\d{2}")

    def test_footer_is_one_quiet_line_and_credits_live_on_contact_only(self):
        """The footer never carries the AI images note or the map and boundary credits again;
        those sit in "Data and credits" on the Contact page and in the map's own attribution control."""
        footer_banned = ["generated with ai", "images are illustrative", "illustrative", "openstreetmap",
                         "geoboundaries", "cc by", "map data"]
        layout = (SRC / "layout.html").read_text(encoding="utf-8")
        footers = [re.search(r"<footer.*?</footer>", layout, re.S).group(0)]
        for path in sorted(WEB.glob("*.html")):
            footers.append(re.search(r"<footer.*?</footer>", path.read_text(encoding="utf-8"), re.S).group(0))
        strings = json.loads((WEB / "i18n" / "en.json").read_text(encoding="utf-8"))
        footers.append(" ".join(string_values(strings["footer"])))
        for footer in footers:
            for phrase in footer_banned:
                self.assertNotIn(phrase, footer.lower(), footer[:120])
            self.assertLessEqual(len(re.findall(r"<p\b", footer)), 1)  # one line
        self.assertEqual(strings["footer"]["owner"], "Philip Tait Lemaron")  # one string, order editable
        credit = "Illustrative images were generated with AI."
        for path in sorted(WEB.glob("*.html")):
            text = path.read_text(encoding="utf-8")
            if path.name == "contact.html":
                self.assertIn('id="credits-title"', text)
                for phrase in ["iLabAfrica Anga Weather Network", "© OpenStreetMap contributors",
                               "CC BY 3.0 IGO; IEBC and OCHA ROSEA", credit]:
                    self.assertIn(phrase, text)
            else:
                self.assertNotIn(credit, text, path.name)
                self.assertNotIn("credits-title", text, path.name)
        map_js = (WEB / "js" / "map.js").read_text(encoding="utf-8")
        self.assertIn("OpenStreetMap</a> contributors", map_js)        # the map keeps its own attribution
        self.assertIn('addAttribution("Boundaries: geoBoundaries")', map_js)
        self.assertNotRegex((WEB / "css" / "styles.css").read_text(encoding="utf-8"),
                            r"leaflet-control-attribution \{[^}]*display: none")

    def test_sms_page_shows_no_now_live_or_test_scenario(self):
        """Visible words only: i18n strings and page text (tags, attributes and scripts removed)."""
        strings = json.loads((WEB / "i18n" / "en.json").read_text(encoding="utf-8"))
        texts = list(string_values(strings))
        for path in [WEB / "sms.html", SRC / "pages" / "sms.html"]:
            html = re.sub(r"<script.*?</script>", " ", path.read_text(encoding="utf-8"), flags=re.S)
            texts.append(re.sub(r"<[^>]+>", " ", html))
        for text in texts:
            self.assertIsNone(re.search(r"\b(now|live)\b", text, re.I), text[:120])
            self.assertNotIn("scenario", text.lower(), text[:120])
        sms_js = (WEB / "js" / "sms.js").read_text(encoding="utf-8")
        self.assertNotIn("scenarios.json", sms_js)
        self.assertNotIn("scenarios.json", (WEB / "sw.js").read_text(encoding="utf-8"))


class RealDataTests(unittest.TestCase):
    """web/data built from deploy_data: not synthetic, every station placed, times labelled EAT."""

    @classmethod
    def setUpClass(cls):
        if not (WEB / "data" / "meta.json").is_file():
            raise unittest.SkipTest("web/data is not built")
        cls.meta = json.loads((WEB / "data" / "meta.json").read_text(encoding="utf-8"))
        if cls.meta.get("synthetic"):
            raise unittest.SkipTest("web/data is a synthetic preview")
        cls.stations = json.loads((WEB / "data" / "stations.json").read_text(encoding="utf-8"))["stations"]

    def test_counts_match_the_stations(self):
        counts = self.meta["counts"]
        self.assertEqual(counts["stations_total"], len(self.stations))
        self.assertEqual(counts["stations_reporting"], sum(s["active"] for s in self.stations))
        self.assertTrue(all(s["lat"] is not None and s["area"] for s in self.stations))
        for s in self.stations:
            if not s["active"]:
                self.assertEqual(s["forecast"], [])
            for day in s["forecast"]:
                self.assertIsNone(day["temp_min"])  # no overnight low in the export
            if s["flat"]:
                self.assertNotIn(s["status"]["code"], {"planting", "delay"})

    def test_every_shown_time_is_labelled_eat(self):
        strings = json.loads((WEB / "i18n" / "en.json").read_text(encoding="utf-8"))
        for key in ["eat", "date_time", "measured_today", "measured_on", "last_reading"]:
            self.assertIn("EAT", strings["time"][key])
        self.assertIn("EAT", strings["home"]["hero_data"])
        for key in ["status_line_on", "status_line_off", "updated_today", "updated_on", "stale"]:
            self.assertIn("EAT", strings["map"][key], key)
        self.assertIn("EAT", strings["station"]["working_last"])


class PagesTests(unittest.TestCase):
    """Five pages share one header, footer and bottom navigation, built from site_src/."""

    PAGES = ["index.html", "how-it-works.html", "map.html", "sms.html", "contact.html"]

    def test_map_and_hero_polish(self):
        css = (WEB / "css" / "styles.css").read_text(encoding="utf-8")
        self.assertIn("scroll-margin-top", css)                      # sticky header never covers an anchor
        self.assertRegex(css, r"\.tiles-osm \.leaflet-tile-pane \{ filter: grayscale\(1\)")  # quiet basemap
        for token in ["--space-1: 8px", "--space-2: 16px", "--space-3: 24px", "--space-4: 40px", "--tap: 44px"]:
            self.assertIn(token, css)                                # one spacing scale, 44 px targets
        for rule in [".btn-primary {", ".btn-secondary {", ".btn:disabled", ".chip {", ".card {", ":focus-visible {"]:
            self.assertIn(rule, css)                                 # one shared set of components
        for dead in [".area-btn", ".btn-solid", ".btn-outline {", ".stale-note", ".footer-small"]:
            self.assertNotIn(dead, css)
        for state in ["today", "recent", "off"]:                    # three marker states, one per class
            self.assertRegex(css, rf"\.mk-{state}::before \{{")
            self.assertRegex(css, rf"\.dot-{state} \{{")
        self.assertNotIn(".mk-count", css)                           # no clustering
        self.assertRegex(css, r"\.panel \{[^}]*border-radius: var\(--radius-card\)")
        self.assertIn("--radius-card: 16px", css)
        map_js = (WEB / "js" / "map.js").read_text(encoding="utf-8")
        self.assertNotIn("days-small", map_js)
        self.assertIn('"#station=" + id', map_js)                   # map.html#station=113
        self.assertIn('href="station.html?id=', map_js)             # See full details

    def test_station_page(self):
        """station.html: not in the main nav (Weather map stays highlighted), three accessible tabs, cached offline."""
        page = (WEB / "station.html").read_text(encoding="utf-8")
        self.assertEqual(page.count('aria-current="page"'), 2)
        self.assertEqual(page.count('href="map.html" aria-current="page"'), 2)    # top nav and bottom nav
        for name in self.PAGES:
            self.assertNotIn('href="station.html"', (WEB / name).read_text(encoding="utf-8"), f"{name} links station.html in its nav")
        self.assertIn('role="tablist"', page)
        self.assertEqual(page.count('role="tab"'), 3)
        self.assertEqual(page.count('role="tabpanel"'), 3)
        for tab in ["latest", "days", "working"]:
            self.assertIn(f'id="tab-{tab}" data-tab="{tab}" aria-controls="panel-{tab}"', page)
        self.assertIn("Back to the map", page)
        scripts = re.findall(r'<script src="([^"]+)"', page)
        self.assertEqual(scripts[-4:], ["js/map-logic.js", "js/station-logic.js", "js/charts.js", "js/station.js"])
        worker = (WEB / "sw.js").read_text(encoding="utf-8")
        for name in ["station.html", "js/station.js", "js/station-logic.js", "js/charts.js", "js/map-logic.js"]:
            self.assertIn(f'"{name}"', worker, name)
        strings = json.loads((WEB / "i18n" / "en.json").read_text(encoding="utf-8"))["station"]
        self.assertEqual(strings["tab_latest"], "Latest readings")   # never "Now"
        self.assertEqual(strings["rain_measured"], "Rain was measured on {n} of the last {total} days (total {mm} mm).")
        self.assertIn("so a chart would be misleading", strings["sparse"])
        station_js = (WEB / "js" / "station.js").read_text(encoding="utf-8")
        self.assertIn('t("map.no_forecast")', station_js)                 # model failed: readings, no outlook strip
        self.assertIn("ArrowRight", station_js)                          # keyboard tabs
        self.assertIn('history.replaceState(null, "", "#" + state.tab)', station_js)

    def test_weather_map_page_layout(self):
        """Status line, chips, Map | List, map with legend and help note, list, one panel; no card grid."""
        page = (WEB / "map.html").read_text(encoding="utf-8")
        for marker in ['id="map-status"', 'id="area-chips"', 'data-view="map"', 'data-view="list"', 'id="map"',
                       'id="legend"', 'class="map-help"', 'id="station-list"', 'id="panel"']:
            self.assertIn(marker, page)
        for gone in ["station-groups", "all-title", "js/charts.js", "result-card"]:
            self.assertNotIn(gone, page)
        strings = json.loads((WEB / "i18n" / "en.json").read_text(encoding="utf-8"))["map"]
        self.assertEqual(strings["view_map"], "Map")
        self.assertEqual(strings["view_list"], "List")
        self.assertIn("{n} of {total} stations sent data in the last 2 days", strings["status_line"])
        self.assertEqual(strings["latest"], "Latest readings")
        self.assertEqual(strings["updated_today"], "Updated {time} EAT")
        self.assertEqual(strings["rain_window"], "Rain between 8 am and midnight (night rain is not measured)")
        self.assertIn("mostly repeats the recent daily pattern", strings["outlook_note"])
        self.assertEqual(strings["no_reading_today"], "No reading today")
        self.assertEqual(strings["after_dark"], "after dark")
        text = re.sub(r"<[^>]+>", " ", re.sub(r"<script.*?</script>", " ", page, flags=re.S))
        self.assertIsNone(re.search(r"\b(now|live)\b", text + " ".join(string_values(strings)), re.I))
        self.assertNotIn("wind", " ".join(string_values(strings)).lower())  # no wind or light level on the map
        self.assertNotIn("light", " ".join(string_values(strings)).lower())
        home_js = (WEB / "js" / "home.js").read_text(encoding="utf-8")
        self.assertIn('I.t("home.hero_data"', home_js)               # plain counts, not the engine's week line
        self.assertNotIn("headline_week", home_js)

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
            footer = re.search(r'<footer class="site-footer">.*?</footer>', text, re.S).group(0)
            self.assertIn("© 2026 Philip Tait Lemaron · Strathmore University final year project", footer, name)
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
        # Step 1: area chips, then stations that sent data; the rest behind a toggle, collapsed by default.
        self.assertIn('id="sms-areas"', sms)
        self.assertIn('id="sms-off-toggle" aria-expanded="false" aria-controls="sms-off" hidden', sms)
        self.assertIn('id="sms-off" hidden', sms)
        self.assertNotIn('loading="lazy"', sms)                       # the hero image is above the fold
        self.assertIn('class="page-hero-media"', sms)
        sms_js = (WEB / "js" / "sms.js").read_text(encoding="utf-8")
        self.assertIn('box.lang = ', sms_js)                          # Kiswahili text is marked lang="sw"
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
