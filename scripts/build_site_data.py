"""Build the showcase website's data files (web/data/*.json).

The website reads only these JSON files; every rule runs here in Python, using
the same functions as the Streamlit app (status precedence from app.py,
advisories and SMS text from insights.py, areas from geo.py).

Usage:
    python scripts/build_site_data.py --deploy-dir deploy_data --out web/data
    python scripts/build_site_data.py --synthetic --out web/data

--synthetic writes clearly fake forecast files to a temporary folder and runs
the normal build over them; meta.json then carries "synthetic": true and the
site shows a red banner. Model and data-quality numbers are read only from
files in --reports-dir (default reports/) or, for the preprocessing fixes,
from a merged pull request description; each number records its source.
"""

import argparse
import csv
import json
import random
import re
import shutil
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from app import (  # noqa: E402  (same precedence and wording as the Streamlit app)
    DEPLOY_FILES, LOCAL_TZ, RELIABILITY, STATUS, main_insight, prepare_forecast, prepare_latest,
    prepare_status, short_ids, status_key, what_to_do,
)
from geo import BOUNDARIES, load_boundaries, sub_county  # noqa: E402
from insights import generate_insights, load_config, sms_action, sms_text  # noqa: E402

ASSETS_README = ROOT / "assets" / "README.md"
REQUIRED = ["forecast_daily.csv", "station_status.csv", "insights_latest.csv", "README.md"]
# Optional: measured daily history for the station charts, and days with data in the last 14 days.
HISTORY_FILE = "history_daily.csv"
HISTORY_COLUMNS = ["device_id", "date", "rain_mm", "temp_min", "temp_max", "humidity_mean", "readings_received"]
HISTORY_DAYS = 14
# history_daily.csv counts hours 0 to 17 of each observing day (08:00 to 01:59 EAT): 18 is a full day.
HISTORY_HOURS = 18
# A forecast whose daily rain barely changes over days 1 to 5 (forecast_variation_mm in insights_latest.csv)
# is too flat to support planting or spraying advice; flood alerts still apply.
FLAT_LIMIT_MM = 1.0
FLAT_DAYS = 5
ADVICE_TYPES = {"planting_window", "rain_delay"}
FLAT_NOTE = ("The forecast shows no day-to-day change, so we are not giving planting or spraying advice.")
RECENT_HOURS = 48  # station_status.csv: active means a valid reading within 48 h of the newest reading
# latest_readings.csv columns shown on the site, with units documented in deploy_data/README.md.
# Wind speed, gust and light level are left out: their units are not verified.
LATEST_FIELDS = {"air_temperature": ("temperature_c", 1), "air_humidity": ("humidity_pct", 0),
                 "rain_since_morning_mm": ("rain_since_morning_mm", 1), "uv_index": ("uv_index", 0),
                 "barometric_pressure": ("pressure_hpa", 0)}
STATUS_CODES = list(STATUS)  # precedence order, worst first
ALERT_TITLES = {
    ("flood_alert", "warning"): "Very heavy rain",
    ("flood_alert", "watch"): "Heavy rain",
    ("planting_window", "advisory"): "Good rain for planting",
    ("rain_delay", "advisory"): "Rain coming: delay spraying",
}
INSIGHT_STATUS = {
    ("flood_alert", "warning"): "warning", ("flood_alert", "watch"): "watch",
    ("planting_window", "advisory"): "planting", ("rain_delay", "advisory"): "delay",
}
VARIABLES = {
    "air_temperature": ("Temperature", "°C"),
    "air_humidity": ("Humidity", "%"),
    "barometric_pressure_hpa": ("Air pressure", "hPa"),
    "wind_speed": ("Wind speed", "m/s"),
    "rainfall_mm": ("Rainfall", "mm per hour"),
}
# Counts stated in a merged pull request description (not in any reports/ file).
PR_FIX_SOURCE = "GitHub pull request #40, fix(preprocess): mask faulty pressure and rain after long gaps"
PR_FIXES = [
    {"label": "Faulty air-pressure values removed (below 850 hPa)", "value": 1683, "unit": "hourly values"},
    {"label": "Rain totals removed where the gauge was offline for more than 6 hours", "value": 388,
     "unit": "hourly values"},
    {"label": "Hours with more than 50 mm of rain", "value": "38 before, 20 after", "unit": None},
]
SOURCES = [
    {"name": "iLabAfrica weather stations", "detail": "SenseCAP S2120 stations in Busia County, Kenya"},
    {"name": "Open-Meteo", "detail": "Hourly weather data based on the ERA5 reanalysis"},
    {"name": "ERA5", "detail": "Copernicus Climate Change Service reanalysis, via Open-Meteo"},
    {"name": "NASA POWER", "detail": "Daily weather data, NASA Langley Research Center"},
    {"name": "CHIRPS", "detail": "Daily rainfall estimates, Climate Hazards Center, UC Santa Barbara"},
]


# =============================================================================
# Small helpers
# =============================================================================
def number(value, digits=1):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return round(value, digits) if pd.notna(value) and abs(value) != float("inf") else None


def nice_day(day):
    return f"{day:%a} {day.day} {day:%b}"


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def read_csv(path):
    frame = pd.read_csv(path, dtype={"device_id": "string"})
    frame["device_id"] = frame.device_id.str.replace(r'''["'“”‘’]''', "", regex=True).str.strip()
    return frame.loc[frame.device_id.notna() & frame.device_id.ne("")]


def boundary_credit(path=ASSETS_README):
    """Full credit from assets/README.md: headline, table rows and the citation."""
    text = path.read_text(encoding="utf-8")
    headline = next((line.strip() for line in text.splitlines() if line.startswith("Boundaries:")), "")
    rows = [[cell.strip() for cell in line.strip().strip("|").split("|")]
            for line in text.splitlines() if re.match(r"\|\s*ADM\d", line)]
    citation = " ".join(line.strip() for line in text.split("geoBoundaries: Runfola", 1)[1:2][0].splitlines()
                        if line.strip()) if "geoBoundaries: Runfola" in text else ""
    return {
        "headline": headline,
        "layers": [{"level": row[0], "name": row[1], "source": row[2], "licence": row[3]} for row in rows],
        "citation": ("geoBoundaries: Runfola" + citation) if citation else "",
        "source": "assets/README.md",
    }


# =============================================================================
# Plain-language explanations of each advisory
# =============================================================================
def plain_basis(item, days, config):
    kind, severity = item["type"], item["severity"]
    start = date.fromisoformat(item["start_date"])
    rain = {day: value for day, value in days}
    if kind == "flood_alert":
        threshold = config["flood_alert"][f"{severity}_mm"]
        word = "very heavy" if severity == "warning" else "heavy"
        amount = rain.get(start)
        amount_text = f"{amount:.0f} mm" if amount is not None else "a large amount"
        return (f"The forecast shows {amount_text} of rain on {nice_day(start)}. Imarika raises a {word} "
                f"rain alert when one day's rain reaches {threshold:g} mm, the level the Kenya "
                f"Meteorological Department describes as {word} rainfall.")
    if kind == "planting_window":
        rule = config["planting_window"]
        window = [start + timedelta(days=offset) for offset in range(rule["window_days"])]
        values = [rain.get(day) for day in window if rain.get(day) is not None]
        rainy = sum(value >= rule["rain_day_mm"] for value in values)
        return (f"About {sum(values):.0f} mm of rain is forecast over the {rule['window_days']} days from "
                f"{nice_day(window[0])} to {nice_day(window[-1])}, with rain on {rainy} of them. Imarika "
                f"counts at least {rule['min_total_mm']:g} mm over {rule['window_days']} days, with rain on "
                f"at least {rule['min_rain_days']} days, as good rain for planting, following published "
                f"definitions of the start of the rains. It cannot check for dry spells after the "
                f"{config['forecast']['horizon_days']}-day forecast.")
    if kind == "rain_delay":
        rule = config["rain_delay"]
        last = start + timedelta(days=rule["lookahead_days"])
        wet = [nice_day(day) for day, value in days
               if value is not None and value >= rule["rain_day_mm"] and start <= day <= last]
        return (f"Rain of {rule['rain_day_mm']:g} mm or more is forecast on {', '.join(wet)}, within the next "
                f"{rule['lookahead_days']} days. Spraying or harvesting just before rain is usually wasted "
                f"effort. This is Imarika's own rule, not an official standard.")
    raise ValueError(f"unknown insight type: {kind!r}")


def insight_key(item):
    return f"{item['type']}:{item['severity']}:{item['start_date']}"


def describe(item, days, area, config):
    pair = (item["type"], item["severity"])
    return {
        "key": insight_key(item),
        "type": item["type"],
        "severity": item["severity"],
        "status": INSIGHT_STATUS[pair],
        "title": ALERT_TITLES[pair],
        "start_date": item["start_date"],
        "action": sms_action(item, config),
        "message": item["message"],
        "basis": plain_basis(item, days, config),
        "caveat": item["caveat"],
        "sms_text": sms_text(item, area=area, config=config),
    }


def day_rows(rows, insights, config):
    """Seven daily tiles; 'alert' uses the same colouring rule as the Streamlit day tiles."""
    flood, delay = config["flood_alert"], config["rain_delay"]
    has_delay = any(item["type"] == "rain_delay" for item in insights)
    planting = next((item for item in insights if item["type"] == "planting_window"), None)
    window = set()
    if planting:
        first = date.fromisoformat(planting["start_date"])
        window = {first + timedelta(days=offset) for offset in range(config["planting_window"]["window_days"])}
    lookahead = rows.date.min() + timedelta(days=delay["lookahead_days"] - 1) if len(rows) else None
    out = []
    for row in rows.itertuples():
        rain, alert = number(row.rain_mm), None
        if rain is not None and rain >= flood["warning_mm"]:
            alert = "warning"
        elif rain is not None and rain >= flood["watch_mm"]:
            alert = "watch"
        elif has_delay and rain is not None and rain >= delay["rain_day_mm"] and row.date <= lookahead:
            alert = "delay"
        out.append({"date": row.date.isoformat(), "rain_mm": rain, "temp_min": number(row.temp_min),
                    "temp_max": number(row.temp_max), "alert": alert, "planting": row.date in window})
    return out


def planting_card(insights, config):
    """The planting card: the engine's advisory, or the Streamlit app's 'no planting rain' wording."""
    item = next((entry for entry in insights if entry["type"] == "planting_window"), None)
    if item:
        return {"status": "planting", "badge": STATUS["planting"][0], "text": sms_action(item, config),
                "detail": item["message"]}
    rule = config["planting_window"]
    return {"status": None, "badge": None, "detail": None,
            "text": (f"No good planting rain in the next {config['forecast']['horizon_days']} days: the rain "
                     f"does not reach {rule['min_total_mm']:g} mm over {rule['window_days']} days with at "
                     f"least {rule['min_rain_days']} rainy days.")}


def operations(rows, insights, as_of, config):
    """Two field-work tiles driven by the planting-window and rain-delay rules."""
    planting = next((entry for entry in insights if entry["type"] == "planting_window"), None)
    delay = next((entry for entry in insights if entry["type"] == "rain_delay"), None)
    rule = config["rain_delay"]
    out = [{"key": "planting", "title": "Planting", "state": "go" if planting else "wait",
            "text": (sms_action(planting, config).split(". ")[0] + "." if planting
                     else "Not enough rain is forecast for planting this week.")}]
    if delay:
        out.append({"key": "spraying", "title": "Spraying", "state": "hold", "text": sms_action(delay, config)})
    else:
        last = as_of + timedelta(days=rule["lookahead_days"])
        window = [number(row.rain_mm) for row in rows.itertuples() if as_of < row.date <= last]
        known = len(window) == rule["lookahead_days"] and all(value is not None for value in window)
        out.append({"key": "spraying", "title": "Spraying", "state": "go" if known else "unknown",
                    "text": (f"No day with {rule['rain_day_mm']:g} mm of rain or more in the next "
                             f"{rule['lookahead_days']} days." if known
                             else f"Not enough forecast data for the next {rule['lookahead_days']} days.")})
    return out


# =============================================================================
# Stations
# =============================================================================
def status_block(code):
    label, colour = STATUS[code]
    return {"code": code, "label": label, "colour": colour}


def iso_utc(value):
    if value is None or pd.isna(value):
        return None
    return pd.Timestamp(value).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")


def read_history(deploy_dir):
    """history_daily.csv as a frame with real dates, or None when the export does not have it yet."""
    path = Path(deploy_dir) / HISTORY_FILE
    if not path.is_file():
        return None
    frame = read_csv(path)
    missing = set(HISTORY_COLUMNS) - set(frame.columns)
    if missing:
        raise SystemExit(f"{HISTORY_FILE} is missing columns: {', '.join(sorted(missing))}")
    frame["date"] = pd.to_datetime(frame.date).dt.date
    return frame


def read_variation(deploy_dir):
    """forecast_variation_mm per device from insights_latest.csv, when the column exists."""
    frame = read_csv(Path(deploy_dir) / "insights_latest.csv")
    if "forecast_variation_mm" not in frame.columns:
        return {}
    values = pd.to_numeric(frame.forecast_variation_mm, errors="coerce")
    return {device: float(value) for device, value in zip(frame.device_id, values) if pd.notna(value)}


def variation_of(rows):
    """Same definition as the export: max minus min of daily rain over forecast days 1 to 5."""
    rain = pd.to_numeric(rows.sort_values("date").rain_mm.head(FLAT_DAYS), errors="coerce").dropna()
    return round(float(rain.max() - rain.min()), 1) if len(rain) else None


def read_extra(deploy_dir, name, columns):
    """Extra columns from an export file, keyed by device; empty when the file or columns are absent."""
    path = Path(deploy_dir) / name
    if not path.is_file():
        return {}
    frame = read_csv(path)
    wanted = [column for column in columns if column in frame.columns]
    return {row["device_id"]: {column: row[column] for column in wanted}
            for row in frame.drop_duplicates("device_id", keep="last").to_dict("records")}


def history_rows(history, device_id, as_of):
    """The last 14 days up to as_of. A day without readings has rain_mm None (unknown), never zero."""
    if history is None:
        return []
    rows = history.loc[history.device_id.eq(device_id)].set_index("date")
    out = []
    for offset in range(HISTORY_DAYS - 1, -1, -1):
        day = as_of - timedelta(days=offset)
        row = rows.loc[day] if day in rows.index else None
        if isinstance(row, pd.DataFrame):
            row = row.iloc[-1]
        received = int(row.readings_received) if row is not None and pd.notna(row.readings_received) else 0
        value = (lambda name, digits=1: number(row[name], digits)
                 if row is not None and received > 0 and pd.notna(row[name]) else None)
        out.append({"date": day.isoformat(), "rain_mm": value("rain_mm"), "temp_min": value("temp_min"),
                    "temp_max": value("temp_max"), "humidity_mean": value("humidity_mean", 0),
                    "readings_received": received})
    return out


def build_stations(forecast, status, latest, features, config, history=None, variation=None, as_of=None,
                   extra_latest=None, extra_status=None):
    ids = sorted(set(forecast.device_id) | set(status.device_id))
    endings = short_ids(ids)
    stations = []
    for device_id in ids:
        known = status.loc[status.device_id.eq(device_id)]
        lat = number(known.latitude.iloc[0], 6) if len(known) else None
        lon = number(known.longitude.iloc[0], 6) if len(known) else None
        active = bool(known.is_active.iloc[0]) if len(known) else True
        rows = forecast.loc[forecast.device_id.eq(device_id)]
        try:
            insights = generate_insights(rows, config) if len(rows) else []
        except ValueError:
            insights = []
        spread = (variation or {}).get(device_id, variation_of(rows) if len(rows) else None)
        flat = bool(active and len(rows) and spread is not None and spread < FLAT_LIMIT_MM)
        if flat:  # no planting or rain-delay advice from a flat forecast; flood alerts still count
            insights = [item for item in insights if item["type"] not in ADVICE_TYPES]
        code = status_key(insights, active)
        area = sub_county(features, lon, lat) if lat is not None and lon is not None else None
        label = f"Station {endings[device_id]}"
        name = f"{label}, {area}" if area else label
        days = [(row.date, number(row.rain_mm)) for row in rows.itertuples()]
        main = main_insight(insights, code)
        ordered = ([main] if main else []) + [item for item in insights if item is not main]
        reading = None
        if latest is not None:
            match = latest.loc[latest.device_id.eq(device_id)]
            if len(match) and pd.notna(match.reading_time.iloc[0]):
                row = match.iloc[0]
                values = dict((extra_latest or {}).get(device_id, {}))
                values.update({"air_temperature": row.air_temperature, "air_humidity": row.air_humidity})
                reading = {"time_utc": iso_utc(row.reading_time)}
                for column, (key, digits) in LATEST_FIELDS.items():
                    reading[key] = number(values.get(column), digits)
                if reading["uv_index"] is not None and reading["uv_index"] < 0:
                    reading["uv_index"] = None
        past = history_rows(history, device_id, as_of) if as_of else []
        stations.append({
            "id": endings[device_id],
            "label": label,
            "name": name,
            "area": area,
            "lat": lat,
            "lon": lon,
            "active": active,
            "last_reading_utc": iso_utc(known.last_seen.iloc[0]) if len(known) else None,
            "status": status_block(code),
            "what_to_do": what_to_do(code, insights, config),
            "latest": reading if active else None,
            "forecast": day_rows(rows, insights, config) if active else [],
            "forecast_status": str((extra_status or {}).get(device_id, {}).get("forecast_status") or "")
            or ("ok" if active and len(rows) else "not available"),
            "insights": [describe(item, days, name, config) for item in ordered] if active else [],
            "flat": flat,
            "forecast_variation_mm": number(spread) if active and len(rows) else None,
            "advice_note": FLAT_NOTE if flat else None,
            "planting": planting_card(insights, config) if active and len(rows) and not flat else None,
            "operations": operations(rows, insights, rows.date.min() - timedelta(days=1), config)
            if active and len(rows) and not flat else [],
            "history": past,
            # Days in the last 14 with at least one reading, counted from history_daily.csv.
            "days_with_data_14d": sum(day["readings_received"] > 0 for day in past) if past else None,
        })
    return stations


# =============================================================================
# Model results and data quality: only numbers read from files
# =============================================================================
def model_results(reports):
    path = reports / "sarima_results.csv"
    source = "reports/sarima_results.csv"
    sarima = {"plain_name": "a statistical time-series model (SARIMA)",
              "use": "Used for the forecasts on this site.",
              "evaluation": "Tested one hour ahead at one station on the last 15% of the data, kept aside.",
              "evaluation_source": "sarima_model.py (causal one-step forecasts on hourly data)",
              "variables": []}
    if path.is_file():
        with path.open(newline="", encoding="utf-8") as handle:
            rows = [row for row in csv.DictReader(handle)
                    if row["status"] == "ok" and re.fullmatch(r"\d{6,}", row["device_id"] or "")]
        stations = sorted({row["device_id"] for row in rows})
        if len(stations) == 1:
            sarima["station"] = f"Station {stations[0][-3:]}"
        for row in rows:
            label, unit = VARIABLES.get(row["variable"], (row["variable"], ""))
            sarima["variables"].append({
                "variable": row["variable"], "label": label, "unit": unit,
                "typical_error": number(row["rmse"], 4), "average_error": number(row["mae"], 4),
                "persistence_error": number(row["rmse_naive"], 4),
                "skill_vs_persistence": number(row["skill_score"], 3),
                "test_hours_evaluated": int(float(row["evaluated_hours"])) if row["evaluated_hours"] else None,
                "source": source})
    return {
        "sarima": sarima,
        "lstm": {"plain_name": "a neural network (LSTM)",
                 "status": "Evaluated at one station; results under review; not used for live forecasts."},
        "notes": {"typical_error": "root mean square error on the test hours",
                  "skill_vs_persistence": "1 - error of the model / error of 'same as one hour ago'"},
    }


def data_quality(reports):
    folder = reports / "data_quality"
    items = []
    report = folder / "report.md"
    if report.is_file():
        match = re.search(r"Retained \*\*([\d,]+) of ([\d,]+) IoT readings\*\* across \*\*(\d+) stations\*\*",
                          report.read_text(encoding="utf-8"))
        if match:
            items.append({"label": "Station readings kept for Busia County", "value": int(match[1].replace(",", "")),
                          "of": int(match[2].replace(",", "")), "source": "reports/data_quality/report.md"})
            items.append({"label": "Weather stations in Busia County", "value": int(match[3]),
                          "source": "reports/data_quality/report.md"})
    errors = folder / "sensor_error_counts.csv"
    if errors.is_file():
        for row in csv.DictReader(errors.open(newline="", encoding="utf-8")):
            if row["sensor_error"] == "":
                items.append({"label": "Readings removed because the sensor's error flag was missing",
                              "value": int(row["records"]), "source": "reports/data_quality/sensor_error_counts.csv"})
    resets = folder / "rain_reset_events.csv"
    if resets.is_file():
        count = sum(1 for _ in csv.DictReader(resets.open(newline="", encoding="utf-8")))
        items.append({"label": "Rain gauge counter resets found and handled", "value": count,
                      "source": "reports/data_quality/rain_reset_events.csv"})
    correlations = folder / "temperature_correlations.csv"
    agreement = None
    if correlations.is_file():
        values = [float(row["screened_pearson_r"]) for row in csv.DictReader(correlations.open(newline="", encoding="utf-8"))
                  if row.get("screened_pearson_r")]
        if values:
            agreement = {"label": "Agreement between station temperatures and Open-Meteo (correlation, 1 = perfect)",
                         "min": round(min(values), 3), "max": round(max(values), 3),
                         "source": "reports/data_quality/temperature_correlations.csv"}
    fixes = [dict(item, source=PR_FIX_SOURCE) for item in PR_FIXES]
    other_fixes = ["Pressure converted from pascals to hectopascals.",
                   "Rainfall worked out from the gauge's running total, restarting at counter resets.",
                   "Gaps shorter than 6 hours filled by straight-line interpolation; longer gaps left empty."]
    return {"audit": items, "temperature_agreement": agreement, "fixes": fixes, "other_fixes": other_fixes,
            "other_fixes_source": "preprocess.py"}


# =============================================================================
# Synthetic preview data
# =============================================================================
def synthetic_history(status, today):
    """Fake daily history with gaps: partial days, a missing day, and silence after a station stops."""
    rng = random.Random(4940)
    rows = []
    for device_id, _lat, _lon, seen, active, _reason in status:
        last = datetime.fromisoformat(seen).date()
        gap = rng.randint(2, 11) if rng.random() < 0.4 else None
        for offset in range(HISTORY_DAYS - 1, -1, -1):
            day = today - timedelta(days=offset)
            if day > last or offset == gap:
                rows.append([device_id, day.isoformat(), None, None, None, None, 0])
                continue
            received = 24 if rng.random() < 0.8 else rng.randint(6, 22)
            if day == today:
                received = min(received, 8)
            rain = rng.choice([0, 0, 0, 0.4, 1.5, 3, 6, 9, 14, 23])
            low = round(rng.uniform(14.5, 17.0), 1)
            rows.append([device_id, day.isoformat(), round(rain * rng.uniform(0.8, 1.2), 1), low,
                         round(low + rng.uniform(9, 13), 1), round(rng.uniform(62, 88)), received])
    return rows


def synthetic_deploy(folder, features, today):
    """Fake but plausible deploy files covering every status. Never real forecasts."""
    rng = random.Random(49)
    areas = [feature for feature in features if feature["properties"].get("level") == "sub-county"]
    # Up to two fake stations per area, placed near the area's centre and kept inside it.
    from geo import centre, contains
    points = []
    for feature in areas:
        cx, cy = centre(feature["geometry"])
        for _ in range(2):
            for _attempt in range(40):
                lon, lat = cx + rng.uniform(-0.05, 0.05), cy + rng.uniform(-0.05, 0.05)
                if contains(feature["geometry"], lon, lat):
                    points.append((lat, lon))
                    break
    if not points:  # no boundaries: a fixed grid inside the Busia bounding box
        points = [(0.25 + 0.08 * (i % 5), 34.15 + 0.08 * (i // 5)) for i in range(12)]
    patterns = {
        "warning": [3, 12, 58, 24, 6, 2, 0], "watch": [0, 4, 26, 9, 3, 0, 0],
        "planting": [0, 0, 6, 5, 7, 4, 1], "delay": [0, 2, 0, 0, 0, 0, 0], "none": [0, 0, 0, 0, 0.4, 0, 0],
    }
    order = ["warning", "watch", "watch", "planting", "planting", "planting", "delay", "delay", "delay",
             "none", "none", "none", "offline", "offline"]
    start = today + timedelta(days=1)
    forecast, status, latest, now = [], [], [], datetime.now(timezone.utc).replace(microsecond=0)
    for index, (lat, lon) in enumerate(points):
        device_id = f"SYN{index + 1:03d}"
        kind = order[index % len(order)]
        active = kind != "offline"
        seen = now - (timedelta(days=rng.randint(9, 40)) if not active else timedelta(minutes=rng.randint(5, 50)))
        status.append([device_id, lat, lon, seen.isoformat(), active, "" if active else "No data received recently"])
        if not active:
            continue
        rain = [max(0.0, value + rng.uniform(-0.3, 0.3)) if value else 0.0 for value in patterns[kind]]
        for offset, value in enumerate(rain):
            low = round(rng.uniform(14.5, 17.0), 1)
            forecast.append([device_id, (start + timedelta(days=offset)).isoformat(), round(value, 1),
                             round(value * 1.6, 1), low, round(low + rng.uniform(10, 13), 1), round(rng.uniform(2, 6), 1)])
        latest.append([device_id, seen.isoformat(), round(rng.uniform(19, 27), 1), round(rng.uniform(55, 90)),
                       round(rng.uniform(0.5, 4), 1), round(rng.uniform(0, 3), 1), 0.3,
                       rng.randint(0, 9), round(rng.uniform(872, 878), 1), round(rng.uniform(0, 4), 1)])
    history = synthetic_history(status, today)
    tables = {"forecast_daily.csv": forecast, "station_status.csv": status, "latest_readings.csv": latest}
    extra = {"latest_readings.csv": ["uv_index", "barometric_pressure", "rain_since_morning_mm"]}
    for name, rows in tables.items():
        pd.DataFrame(rows, columns=DEPLOY_FILES[name] + extra.get(name, [])).to_csv(folder / name, index=False)
    pd.DataFrame(history, columns=HISTORY_COLUMNS).to_csv(folder / HISTORY_FILE, index=False)
    frame = pd.read_csv(folder / "forecast_daily.csv", dtype={"device_id": "string"})
    found = pd.DataFrame(generate_insights(frame)).reindex(columns=DEPLOY_FILES["insights_latest.csv"])
    spread = {device: variation_of(group) for device, group in frame.groupby("device_id")}
    found["forecast_variation_mm"] = found.device_id.map(spread)
    found.to_csv(folder / "insights_latest.csv", index=False)
    (folder / "README.md").write_text("SYNTHETIC PREVIEW DATA - NOT REAL FORECASTS\n", encoding="utf-8")


# =============================================================================
# Build
# =============================================================================
def build(deploy_dir, out_dir, reports_dir, synthetic=False):
    deploy_dir, out_dir, reports_dir = Path(deploy_dir), Path(out_dir), Path(reports_dir)
    missing = [name for name in REQUIRED if not (deploy_dir / name).is_file()]
    if missing:
        raise SystemExit(f"Missing files in {deploy_dir}: {', '.join(missing)}")
    config = load_config()
    features = load_boundaries(BOUNDARIES)
    forecast = prepare_forecast(read_csv(deploy_dir / "forecast_daily.csv"))
    status = prepare_status(read_csv(deploy_dir / "station_status.csv"))
    latest_path = deploy_dir / "latest_readings.csv"
    latest = prepare_latest(read_csv(latest_path)) if latest_path.is_file() else None
    if forecast.empty:
        raise SystemExit("forecast_daily.csv has no usable rows")
    as_of = forecast.date.min() - timedelta(days=1)

    history = read_history(deploy_dir)
    extra_latest = read_extra(deploy_dir, "latest_readings.csv", list(LATEST_FIELDS))
    extra_status = read_extra(deploy_dir, "station_status.csv", ["forecast_status"])
    stations = build_stations(forecast, status, latest, features, config, history, read_variation(deploy_dir), as_of,
                              extra_latest, extra_status)
    if history is None:
        print(f"Note: {HISTORY_FILE} is not in {deploy_dir}; the station charts will show the forecast only.")
    generated = sum(len(station["insights"]) for station in stations)
    exported = len(read_csv(deploy_dir / "insights_latest.csv"))
    if generated != exported:
        print(f"Note: {generated} advisories generated here, {exported} rows in insights_latest.csv "
              f"(the site uses the generated ones, like the Streamlit app).")

    out_dir.mkdir(parents=True, exist_ok=True)
    alert_codes = {"warning", "watch", "planting", "delay"}
    recent = sum(station["active"] for station in stations)
    seen = [station["last_reading_utc"] for station in stations if station["last_reading_utc"]]
    # Plain counts from the data; the engine's week summary is not used because flat forecasts give no advice.
    headline = f"{recent} of {len(stations)} stations sent data in the last 2 days."
    meta = {
        "synthetic": bool(synthetic),
        "as_of": as_of.isoformat(),
        "issued_line": f"Imarika forecast, issued {as_of.day} {as_of:%B %Y}",
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "timezone": LOCAL_TZ,
        "counts": {
            "stations_total": len(stations),
            "stations_reporting": recent,
            "stations_with_forecast": sum(bool(station["forecast"]) for station in stations),
            "stations_flat_forecast": sum(station["flat"] for station in stations),
            "stations_with_alerts": sum(station["status"]["code"] in alert_codes for station in stations),
            "stations_heavy_rain": sum(station["status"]["code"] in {"warning", "watch"} for station in stations),
            "alerts_total": generated,
        },
        "headline": headline,
        "latest_reading_utc": max(seen) if seen else None,
        "recent_hours": RECENT_HOURS,
        "history_hours_per_day": HISTORY_HOURS,
        "flat_note": FLAT_NOTE,
        "statuses": [dict(status_block(code), order=index) for index, code in enumerate(STATUS_CODES)],
        "thresholds": {"heavy_rain_mm": config["flood_alert"]["watch_mm"],
                       "very_heavy_rain_mm": config["flood_alert"]["warning_mm"],
                       "sms_max_chars": config["sms"]["max_chars"]},
        # The site does not show the upper rain estimate, so the line about it is left out.
        "reliability": [line for line in RELIABILITY if "high estimate" not in line],
        "boundary_file": "data/kenya_boundaries.geojson",
        "credits": {"boundaries": boundary_credit(), "data_sources": SOURCES,
                    "map_tiles": "Map data from OpenStreetMap contributors"},
        "source_dir": "synthetic" if synthetic else deploy_dir.name,
    }
    write_json(out_dir / "meta.json", meta)
    write_json(out_dir / "stations.json", {"synthetic": bool(synthetic), "stations": stations})
    write_json(out_dir / "model_results.json", model_results(reports_dir))
    write_json(out_dir / "data_quality.json", data_quality(reports_dir))
    if BOUNDARIES.is_file():
        shutil.copyfile(BOUNDARIES, out_dir / "kenya_boundaries.geojson")
    print(f"Wrote {out_dir}: {len(stations)} stations, {generated} advisories, as of {as_of}"
          + (" (SYNTHETIC)" if synthetic else ""))
    return meta


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--deploy-dir", default=str(ROOT / "deploy_data"))
    parser.add_argument("--out", default=str(ROOT / "web" / "data"))
    parser.add_argument("--reports-dir", default=str(ROOT / "reports"),
                        help="folder with sarima_results.csv and data_quality/ (read only)")
    parser.add_argument("--synthetic", action="store_true", help="write clearly fake preview data")
    args = parser.parse_args(argv)
    if args.synthetic:
        with tempfile.TemporaryDirectory() as folder:
            synthetic_deploy(Path(folder), load_boundaries(BOUNDARIES), date.today())
            return build(folder, args.out, args.reports_dir, synthetic=True)
    return build(args.deploy_dir, args.out, args.reports_dir)


if __name__ == "__main__":
    main()
