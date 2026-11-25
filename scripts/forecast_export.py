"""Export real SARIMA 7-day station forecasts to deploy_data/ for the dashboard.

Start the long run in its own terminal (not inside an editor or AI session):

    venv\\Scripts\\python.exe scripts\\forecast_export.py > reports\\forecast_export.log 2>&1

Other modes:
    --step1             read-only: print the station status table and AS-OF time
    --time-stations N   with --step1: time the forecast of N active stations
    --assemble-only     write deploy_data/ from the per-station results saved so far
    --deadline HH:MM    local time after which no new station is started (default 16:00)

Method (also written to deploy_data/README.md):
* Input data/all_readings_refresh.csv. preprocess.filter_iot, derive_rainfall,
  convert_units, interpolate_iot and hourly_iot; merge_sources is not used and
  no data/ file is written.
* AS-OF is the newest reading in the network. A station is active if its last
  reading is within 48 hours of AS-OF.
* For each active station, SARIMA with the fixed orders in
  reports/sarima_best_params.csv (seasonal (1,1,1,24)) is fitted to the last
  120 days of complete hours before AS-OF, for air temperature, wind speed and
  rainfall, to the end of observing day 7 after the AS-OF date. Observing day
  D starts 08:00 EAT (05:00 UTC). Rainfall is clipped at 0. The stations are
  off overnight, so only 08:00-00:00 EAT (05:00-20:59 UTC) is published, and
  only where every published hour of day was observed at least 5 times in
  training (see deploy_data/README.md). Timestamps are ISO UTC with Z.
* Each station's result is saved as soon as it finishes, so a stopped run
  resumes where it stopped (for the same AS-OF).
"""

import os

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")

import argparse
import json
import platform
import sys
import time
import traceback
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import preprocess  # noqa: E402
from geo import contains, features_at, load_boundaries, sub_county  # noqa: E402

INPUT = ROOT / "data" / "all_readings_refresh.csv"
DEPLOY = ROOT / "deploy_data"
PARTS = ROOT / "reports" / "forecast_export_parts"
PARAMS = ROOT / "reports" / "sarima_best_params.csv"
LOCAL_TZ = "Africa/Nairobi"
ACTIVE_WITHIN = pd.Timedelta(hours=48)
WINDOW_HOURS = 120 * 24
FORECAST_DAYS = 7
HISTORY_DAYS = 14
# The stations switch off overnight (no readings 22:00-04:59 UTC since May 2026;
# the LoRaWAN frame counter restarts each morning). Forecasts are published only
# for the on-hours, and observed days run 05:00-05:00 UTC (08:00 in Nairobi).
ON_HOURS = list(range(5, 22))
# Published forecast hours: 05:00-20:59 UTC (08:00-00:00 EAT). The horizon still runs to
# 21:59 UTC so saved runs stay valid; the 21:00 UTC hour is not published.
FORECAST_HOURS = list(range(5, 21))
MIN_HOUR_READINGS = 5
OBSERVING_WINDOW = "05:00-20:59"
LOCAL_WINDOW = "08:00-00:00 EAT"
DAY_START = pd.Timedelta(hours=5)
BRIDGE = np.timedelta64(12, "h")
EXTRA_SENSORS = ["light_intensity", "uv_index", "barometric_pressure", "peak_wind_gust", "wind_direction"]
VARIATION_DAYS = 5
DAILY_COLUMNS = ["device_id", "date", "rain_mm", "rain_upper", "temp_min", "temp_max", "wind_max",
                 "observing_window_utc", "local_window"]
STATUS_COLUMNS = ["device_id", "latitude", "longitude", "last_reading_utc", "last_seen_utc", "active",
                  "reported_today", "reason", "coverage_14d", "area", "forecast_status"]
MAXITER = 200
RETRY_MAXITER = 1000
WIND_FAILED = "wind forecast failed to converge"
FORECAST_STATUSES = {"ok", "partial"}
NOT_AVAILABLE = "forecast not available"
# Forecast target -> hourly pipeline column.
TARGETS = {"air_temperature": "iot_air_temperature", "wind_speed": "iot_wind_speed",
           "rainfall_mm": "iot_rainfall_mm"}


def package_versions():
    import sklearn
    import statsmodels
    return {"python": platform.python_version(), "pandas": pd.__version__, "numpy": np.__version__,
            "statsmodels": statsmodels.__version__, "scikit-learn": sklearn.__version__}


def load_orders(path=PARAMS):
    params = pd.read_csv(path, dtype={"device_id": "string"})
    orders = {}
    for target in TARGETS:
        row = params.loc[params.variable.eq(target)].iloc[0]
        orders[target] = (int(row.p), int(row.d), int(row.q))
    return orders


# =============================================================================
# Preparation (read-only)
# =============================================================================
def stamp(value):
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def observing_day(time):
    """Observing day D of a UTC timestamp, or None outside the on-hours.

    Day D runs 08:00 EAT on D to 00:59 EAT on D+1, i.e. 05:00-21:59 UTC on D.
    """
    return time.date() if time.hour in ON_HOURS else None


def day_start(day):
    """08:00 EAT (05:00 UTC) on observing day D."""
    return pd.Timestamp(day, tz="UTC") + DAY_START


def day_end(day):
    """01:00 EAT on D+1 (22:00 UTC on D): end of observing day D, exclusive."""
    return pd.Timestamp(day, tz="UTC") + pd.Timedelta(hours=ON_HOURS[-1] + 1)


def morning_start(time):
    """08:00 EAT on the Nairobi date of `time` (the previous day's 08:00 before 08:00 EAT)."""
    local = time.tz_convert(LOCAL_TZ)
    morning = local.normalize() + pd.Timedelta(hours=8)
    if local < morning:
        morning -= pd.Timedelta(days=1)
    return morning.tz_convert("UTC")


def horizon(as_of):
    """Last complete observed hour, forecast steps and the 7 forecast dates.

    Forecast dates are the 7 Africa/Nairobi days after the AS-OF day. Date D
    covers the on-hours D 05:00-21:59 UTC, so the run ends at D7 21:59 UTC.
    """
    last_obs = as_of.floor("h") - pd.Timedelta(hours=1)
    local_day = as_of.tz_convert(LOCAL_TZ).normalize()
    dates = [(local_day + pd.Timedelta(days=offset)).date() for offset in range(1, FORECAST_DAYS + 1)]
    last_hour = pd.Timestamp(dates[-1], tz="UTC") + pd.Timedelta(hours=ON_HOURS[-1])
    steps = int((last_hour - last_obs) / pd.Timedelta(hours=1))
    return last_obs, steps, dates


def area_name(features, longitude, latitude):
    name = sub_county(features, longitude, latitude)
    if name:
        return name
    county = features_at(features, "county")
    if county and contains(county[0]["geometry"], float(longitude), float(latitude)):
        return "Busia County"
    return "Outside Busia County"


def station_status(filtered, raw, as_of, now):
    last = filtered.groupby("device_id").tail(1).set_index("device_id")
    seen = raw.loc[raw.device_id.isin(last.index)].groupby("device_id").received_at.max()
    today_start = morning_start(now)
    features = load_boundaries()
    status = pd.DataFrame({
        "device_id": last.index,
        "latitude": last.latitude.round(6).values,
        "longitude": last.longitude.round(6).values,
        "last_reading_utc": last.received_at.map(stamp).values,
        "last_seen_utc": seen.reindex(last.index).map(stamp).values,
        "active": (last.received_at >= as_of - ACTIVE_WITHIN).values,
        "reported_today": (seen.reindex(last.index) >= today_start).values,
    })
    age_days = (as_of - last.received_at).dt.total_seconds().values / 86400
    status["reason"] = [
        "" if active else f"No readings since {when[:10]} ({age:.0f} days before AS-OF)"
        for active, when, age in zip(status.active, status.last_reading_utc, age_days)]
    status["area"] = [area_name(features, lon, lat) for lon, lat in zip(status.longitude, status.latitude)]
    return status.reset_index(drop=True)


def counter_rain(times, counter, start, end):
    """Rain counted in [start, end) from the rainAccumulation counter, or None.

    The end value is the last counter reading in the interval. The start value
    is the last counter reading in the 12 hours before the interval (this
    bridges the nightly shutdown), else the first reading in the interval.
    None when the interval has no counter reading or the counter went down
    (a reset). Rain while the station is switched off is not counted.
    """
    known = ~np.isnan(counter)
    times, counter = times[known], counter[known]
    inside = (times >= start) & (times < end)
    if not inside.any():
        return None
    before = np.nonzero((times < start) & (times >= start - BRIDGE))[0]
    values = counter[inside]
    if before.size:
        values = np.concatenate([[counter[before[-1]]], values])
    if (np.diff(values) < -1e-6).any():
        return None
    return float(values[-1] - values[0])


def sensor_value(reading, column):
    value = reading[column]
    if pd.isna(value) or (pd.notna(reading.sensor_error) and reading.sensor_error != 0):
        return None
    if column == "barometric_pressure":
        value = value / 100.0  # Pa -> hPa, as preprocess.convert_units
        return None if value < preprocess.PRESSURE_MIN_HPA else round(value, 1)
    if column == "uv_index" and value < 0:
        return None  # a UV index cannot be negative
    return value


def history_and_latest(filtered, hourly, status, as_of):
    morning = morning_start(as_of)
    today = morning.date()  # observing day D: 05:00 UTC on D is 08:00 EAT on D
    days =[today - timedelta(days=offset) for offset in range(HISTORY_DAYS - 1, -1, -1)]
    rows, latest, coverage = [], [], {}
    raw_hours = filtered.assign(hour=filtered.received_at.dt.floor("h"))
    window_start = as_of - pd.Timedelta(hours=HISTORY_DAYS * 24)
    for device_id in status.device_id:
        station = filtered.loc[filtered.device_id.eq(device_id)]
        times = station.received_at.dt.tz_localize(None).to_numpy()
        counter = station.rain_accumulation.to_numpy(dtype=float)
        hours = raw_hours.loc[raw_hours.device_id.eq(device_id), "hour"]
        recent = hours.loc[(hours + pd.Timedelta(hours=1) > window_start) & (hours <= as_of)]
        coverage[device_id] = round(min(recent.nunique() / (HISTORY_DAYS * 24), 1.0), 3)
        hourly_station = hourly.loc[hourly.device_id.eq(device_id)]
        hourly_day = hourly_station.hour.map(observing_day)
        hour_day = hours.map(observing_day)
        for day in days:
            start = day_start(day)
            end = min(day_end(day), as_of + pd.Timedelta(microseconds=1))
            total = counter_rain(times, counter, np.datetime64(start.tz_localize(None)),
                                 np.datetime64(end.tz_localize(None)))
            same_day = hourly_station.loc[hourly_day.eq(day)]
            rows.append({
                "device_id": device_id, "date": day.isoformat(),
                "rain_mm": None if total is None else round(total, 1),
                "temp_min": same_day.iot_air_temperature.min(),
                "temp_max": same_day.iot_air_temperature.max(),
                "humidity_mean": same_day.iot_air_humidity.mean(),
                "readings_received": int(hours.loc[hour_day.eq(day)].nunique()),
            })
        last = station.iloc[-1]
        # Counter now minus the counter at the first reading since 08:00 EAT today.
        in_today = station.loc[station.received_at >= morning].rain_accumulation.dropna()
        since_morning = (round(float(last.rain_accumulation - in_today.iloc[0]), 1)
                         if len(in_today) and pd.notna(last.rain_accumulation)
                         and last.received_at >= morning else None)
        latest.append({
            "device_id": device_id,
            "reading_utc": stamp(last.received_at),
            "air_temperature": last.air_temperature, "air_humidity": last.air_humidity,
            "wind_speed": last.wind_speed, "rain_today_mm": rows[-1]["rain_mm"],
            "age_hours": round((as_of - last.received_at).total_seconds() / 3600, 2),
            **{column: sensor_value(last, column) for column in EXTRA_SENSORS},
            "rain_since_morning_mm": since_morning,
        })
    history = pd.DataFrame(rows)
    for column in ["temp_min", "temp_max", "humidity_mean"]:
        history[column] = history[column].round(1)
    return history, pd.DataFrame(latest), coverage


def prepare(path=INPUT, now=None):
    now = now or pd.Timestamp.now(tz="UTC")
    raw = preprocess.read_source(path)
    raw_times = raw.assign(received_at=pd.to_datetime(raw.received_at, format="mixed", utc=True,
                                                      errors="coerce"))
    filtered = preprocess.filter_iot(raw)
    preprocess.numeric(filtered, ["air_temperature", "air_humidity", "wind_speed"])
    as_of = filtered.received_at.max()
    status = station_status(filtered, raw_times, as_of, now)
    # Only the last 120 days (plus margin) feed the forecasts.
    recent = filtered.loc[filtered.received_at >= as_of - pd.Timedelta(days=125)].reset_index(drop=True)
    derived = preprocess.convert_units(preprocess.derive_rainfall(recent))
    hourly = preprocess.hourly_iot(preprocess.interpolate_iot(derived))
    history, latest, coverage = history_and_latest(filtered, hourly, status, as_of)
    status["coverage_14d"] = status.device_id.map(coverage).fillna(0.0)
    return {"as_of": as_of, "now": now, "status": status, "hourly": hourly, "history": history,
            "latest": latest}


# =============================================================================
# Forecasting (one process per station)
# =============================================================================
def forecast_station(device_id, frame, last_obs, steps, orders):
    """Fit the three targets for one station; return (hourly forecast, log)."""
    from sarima_model import fit_model

    started = time.time()
    index = pd.date_range(last_obs - pd.Timedelta(hours=WINDOW_HOURS - 1), last_obs, freq="h")
    frame = frame.set_index("hour").reindex(index)
    future = pd.date_range(last_obs + pd.Timedelta(hours=1), periods=steps, freq="h")
    result = pd.DataFrame({"device_id": device_id, "time_utc": future})
    errors, notes = [], []
    for target, column in TARGETS.items():
        series = frame[column].astype(float)
        observed = int(series.notna().sum())
        if observed < 14 * 24:
            errors.append(f"{target}: only {observed} observed hours in the 120-day window")
            continue
        try:
            try:
                fitted = fit_model(series, orders[target], MAXITER)
            except RuntimeError:  # not converged: one retry with more iterations, same order
                fitted = fit_model(series, orders[target], RETRY_MAXITER)
                notes.append(f"{target}: converged only with maxiter {RETRY_MAXITER}")
            # fit_model uses low_memory=True, which gives NaN prediction intervals.
            # Re-filter with the same fitted parameters (no re-estimation) to get them.
            prediction = fitted.model.filter(fitted.params).get_forecast(steps)
            mean = np.asarray(prediction.predicted_mean, dtype=float)
            upper = np.asarray(prediction.conf_int(alpha=0.05), dtype=float)[:, 1]
        except Exception as error:  # recorded in the run log, never fatal
            errors.append(f"{target}: {type(error).__name__}: {error}")
            continue
        if target == "rainfall_mm":
            result["rainfall_mm"] = np.clip(mean, 0, None)
            result["rainfall_upper"] = np.clip(upper, 0, None)
        elif target == "wind_speed":
            result["wind_speed"] = np.clip(mean, 0, None)
        else:
            result[target] = mean
    # Temperature and rainfall are required. A wind-only failure keeps the
    # other forecasts and leaves wind empty ("partial").
    essential = {"air_temperature", "rainfall_mm"} <= set(result.columns)
    if "wind_speed" not in result.columns:
        result["wind_speed"] = np.nan
        notes.append(WIND_FAILED)
    status = "ok" if not errors else ("partial" if essential else "error")
    log = {"device_id": device_id, "status": status,
           "seconds": round(time.time() - started, 1), "errors": " | ".join(errors),
           "notes": " | ".join(notes)}
    return (None if status == "error" else result), log


def run_one(device_id, frame, last_obs, steps, orders, part_dir):
    try:
        result, log = forecast_station(device_id, frame, last_obs, steps, orders)
    except Exception:
        result, log = None, {"device_id": device_id, "status": "error", "seconds": None,
                             "errors": traceback.format_exc(limit=3).replace("\n", " "), "notes": ""}
    if result is not None:
        result.to_csv(part_dir / f"{device_id}.csv", index=False)
    (part_dir / f"{device_id}.json").write_text(json.dumps(log), encoding="utf-8")
    return log


def part_folder(as_of, steps):
    folder = PARTS / f"{as_of.strftime('%Y%m%dT%H%M%SZ')}_{steps}h"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


# =============================================================================
# Export
# =============================================================================
def on_hours(hourly):
    """Keep only published forecast hours (05:00-20:59 UTC, 08:00-00:00 EAT)."""
    times = pd.to_datetime(hourly.time_utc, utc=True)
    return hourly.loc[times.dt.hour.isin(FORECAST_HOURS)].reset_index(drop=True)


def daily_from_hourly(hourly, dates):
    """Daily values over the 16 published hours of each forecast date (UTC date = D)."""
    frame = on_hours(hourly)
    frame["date"] = pd.to_datetime(frame.time_utc, utc=True).dt.date
    frame = frame.loc[frame.date.isin(dates)]
    groups = frame.groupby(["device_id", "date"])
    hours = len(FORECAST_HOURS)
    # min_count: a day with any missing hourly value stays empty, never 0.
    daily = pd.DataFrame({
        "rain_mm": groups.rainfall_mm.sum(min_count=hours),
        "rain_upper": groups.rainfall_upper.sum(min_count=hours),
        "temp_min": np.nan,  # overnight low not observable: stations are off at night
        "temp_max": groups.air_temperature.max(),
        "wind_max": groups.wind_speed.max(), "hours": groups.size()}).reset_index()
    if not daily.hours.eq(hours).all():
        raise ValueError(f"A forecast day does not have {hours} on-hour values")
    daily["date"] = daily.date.astype(str)
    daily = daily.drop(columns="hours").round(1)
    daily["observing_window_utc"] = OBSERVING_WINDOW
    daily["local_window"] = LOCAL_WINDOW
    return daily


def forecast_variation(daily, dates):
    """Max minus min of daily rain_mm over forecast days 1-5, per station."""
    first = {day.isoformat() for day in dates[:VARIATION_DAYS]}
    rain = daily.loc[daily.date.isin(first)].groupby("device_id").rain_mm
    return (rain.max() - rain.min()).round(1)


def readme(data, logs, versions, steps):
    as_of = data["as_of"]
    status = data["status"]
    ok = sum(log["status"] in FORECAST_STATUSES for log in logs)
    lines = [
        "# deploy_data",
        "",
        "Real station data and SARIMA forecasts for the Imarika dashboard and website.",
        "Built by `scripts/forecast_export.py` from `data/all_readings_refresh.csv`.",
        "",
        f"- AS-OF (newest reading in the network): {stamp(as_of)} "
        f"({as_of.tz_convert(LOCAL_TZ).strftime('%Y-%m-%d %H:%M')} EAT)",
        f"- Stations: {len(status)}; active (last reading within 48 h of AS-OF): "
        f"{int(status.active.sum())}; forecasts produced: {ok}",
        f"- Generated: {stamp(data['now'])} ({data['now'].tz_convert(LOCAL_TZ).strftime('%Y-%m-%d %H:%M')}"
        " EAT); used for `reported_today`",
        "- Packages: " + ", ".join(f"{name} {version}" for name, version in versions.items()),
        "",
        "## Times",
        "",
        "All timestamps are ISO 8601 UTC with a Z suffix. EAT is Africa/Nairobi time, UTC+3, with no",
        "daylight saving. Observing day D starts at 08:00 EAT (05:00 UTC) on D and is labelled",
        "with date D. Forecasts cover 08:00-00:00 EAT on D (05:00-20:59 UTC on D). The history day",
        "(history_daily.csv) also includes readings up to 00:59 EAT on D+1 (21:59 UTC on D) where a",
        "station sent them. rain_since_morning_mm and reported_today count from 08:00 EAT.",
        "",
        "## Stations are off overnight",
        "",
        "Since May 2026 no in-county station has reported between 01:00 and 07:59 EAT (22:00-04:59",
        "UTC); the LoRaWAN frame counter restarts each morning, so the stations switch off overnight",
        "(probably power; not confirmed). Consequences in this export:",
        "",
        "- Forecasts are published only for 08:00-00:00 EAT (05:00-20:59 UTC). There is no",
        "  overnight forecast and no overnight low (temp_min is empty). The 00:00-00:59 EAT hour is",
        "  left out because two stations never reported in it during the training window.",
        f"- Guard: every published hour of day must have at least {MIN_HOUR_READINGS} observed hours in the",
        "  station's 120-day training window. If temperature or rainfall falls short, the station's",
        "  forecast is not published (run log status \"insufficient data\", forecast_status \"not",
        "  available\"); if only wind falls short, wind is left empty (\"partial\").",
        "- Rain that falls while a station is off is NOT counted (the rain counter does not advance).",
        "  In Aug-Dec 2025, when stations reported all night, 13.3% of rain fell 01:00-06:59 EAT",
        "  (22:00-03:59 UTC), so observed and forecast totals are lower than full-day totals.",
        "",
        "## Method",
        "",
        "- Preprocessing for the forecasts: `preprocess.filter_iot`, `derive_rainfall`, `convert_units`,",
        "  `interpolate_iot`, `hourly_iot` (no `merge_sources`), on the last 125 days of readings.",
        "- Model: SARIMA with the fixed orders in `reports/sarima_best_params.csv`, chosen for station",
        "  101990961244800083 and applied unchanged to every station; seasonal order (1,1,1,24).",
        "  Temperature (1,0,0), wind speed (1,0,1), rainfall (1,0,0). Humidity and pressure are not",
        "  forecast. Fitted per station on the last 120 days of complete hours before AS-OF",
        f"  (gaps left as missing), lbfgs, at most {MAXITER} iterations. The 95% upper bounds come",
        "  from one Kalman filter pass with the fitted parameters (no re-estimation).",
        f"- Horizon: {steps} hourly steps from the last complete hour before AS-OF, to the end of",
        "  01:00 EAT after day 7 (21:59 UTC on day 7). Day 1 is the Nairobi date after the AS-OF",
        "  date. Only 08:00-00:00 EAT (05:00-20:59 UTC) is published.",
        "- Rainfall and wind forecasts are clipped at 0. No LSTM output is used anywhere.",
        "- The SARIMA skill was tested one hour ahead only. Over 7 days the forecast largely repeats",
        "  the daily cycle of the last observed days, so daily values change little from day to day",
        "  (see forecast_variation_mm in insights_latest.csv).",
        "",
        "## Files",
        "",
        "Units: temperature degC; humidity %; pressure hPa (raw Pa / 100); wind direction degrees",
        "(0-359); UV index has no unit; rain mm. Wind speed and gust: unit not verified from the",
        "data. light_intensity: unit unverified (midday values near 110,000, maximum 200,000).",
        "",
        "`forecast_hourly.csv`: published hours only (08:00-00:00 EAT, 05:00-20:59 UTC). device_id;",
        "time_utc (start of the hour, ISO UTC with Z); air_temperature; wind_speed; rainfall_mm (mm",
        "in the hour); rainfall_upper (upper 95% prediction bound for the hour, clipped at 0).",
        "Stations with a forecast only.",
        "",
        "`forecast_daily.csv`: device_id; date (observing day D, days 1-7 after the AS-OF date);",
        "rain_mm (expected rain in the 16 hours 08:00-00:00 EAT on D, 05:00-20:59 UTC on D);",
        "rain_upper (sum of the 16 hourly upper 95% bounds: a cautious high",
        "estimate, not a daily prediction interval); temp_min (always empty: no overnight data);",
        "temp_max (highest hourly forecast); wind_max (highest hourly mean wind, not a gust; empty",
        f"when the wind fit failed); observing_window_utc (\"{OBSERVING_WINDOW}\"); local_window",
        f"(\"{LOCAL_WINDOW}\").",
        "",
        "`station_status.csv`: every in-county station after `filter_iot`. device_id; latitude,",
        "longitude (last reading); last_reading_utc (last valid reading); last_seen_utc (last row of",
        "any kind, including rows `filter_iot` drops); active (last valid reading within 48 h of",
        "AS-OF); reported_today (a row has arrived since 08:00 EAT (05:00 UTC) on the generation",
        "date; before 08:00 EAT, since 08:00 EAT the previous day);",
        "reason (empty when active and forecast); coverage_14d (share of the 14 x 24 hours before",
        "AS-OF with at least one raw reading, 0 to 1); area (sub-county from",
        "`assets/kenya_boundaries.geojson` by point-in-polygon, else \"Busia County\" or \"Outside",
        "Busia County\"); forecast_status (ok, partial, not available, inactive).",
        "",
        "`latest_readings.csv`: every station, values from its last valid reading. device_id;",
        "reading_utc (ISO UTC with Z); air_temperature; air_humidity; wind_speed; rain_today_mm",
        "(history_daily rain_mm of the AS-OF observing day); age_hours (AS-OF minus reading_utc);",
        "light_intensity; uv_index (negative values left empty); barometric_pressure (hPa; below",
        "850 left empty); peak_wind_gust; wind_direction; rain_since_morning_mm (rain counter at the",
        "last reading minus the counter at the first reading since 08:00 EAT (05:00 UTC) on the",
        "AS-OF Nairobi date; empty if the last reading is earlier). Sensor fields are empty when",
        "the reading reports a sensor error or no value.",
        "",
        f"`history_daily.csv`: every station, the {HISTORY_DAYS} observing days up to the AS-OF day",
        "(day D = 08:00 EAT on D to 00:59 EAT on D+1, 05:00-21:59 UTC on D; the last day ends at",
        "AS-OF). device_id; date (D); rain_mm (rainAccumulation counter at the last reading of the",
        "day minus the counter at the last reading in the 12 h before 08:00 EAT, which bridges the",
        "overnight shutdown, else the first reading of the day; EMPTY when the day has no counter",
        "reading or the counter went down; rain while the station is off is not counted);",
        "temp_min, temp_max, humidity_mean (hourly pipeline values in the day: the lowest",
        "temperature is from the on-hours, not the overnight low); readings_received (hours 0 to",
        "17 of the day with at least one raw reading after `filter_iot`, before interpolation;",
        "the rare readings between 01:00 and 07:59 EAT belong to no observing day).",
        "",
        "`insights_latest.csv`: output of `insights.generate_insights` on `forecast_daily.csv`,",
        f"plus forecast_variation_mm (max minus min of daily rain_mm over forecast days 1-{VARIATION_DAYS}",
        "for that station).",
        "",
        "`forecast_run_log.csv`: one row per station. status (ok; partial = temperature and",
        "rainfall published but wind failed or was withheld, so wind_speed and wind_max are empty;",
        "error = model failure; insufficient data = withheld by the guard; inactive; not run).",
        "error, insufficient data and not run give forecast_status \"not available\" and reason",
        "\"forecast not available\" in station_status.csv;",
        "seconds (fit time); errors; notes (for example a fit that converged only on a retry with",
        f"maxiter {RETRY_MAXITER}, same order); package versions and AS-OF.",
        "",
        "## Station notes",
        "",
    ]
    noted = [log for log in logs if log["status"] not in ("ok", "inactive")]
    lines += [f"- {log['device_id']}: {log['status']}; {log['notes'] or NOT_AVAILABLE}" for log in noted]
    lines += [] if noted else ["- None: every active station has a full forecast."]
    lines += [""]
    return "\n".join(lines)


def hour_counts(frame, last_obs):
    """Observed hours per UTC hour of day in the 120-day training window, per target."""
    start = last_obs - pd.Timedelta(hours=WINDOW_HOURS - 1)
    window = frame.loc[(frame.hour >= start) & (frame.hour <= last_obs)]
    hours = window.hour.dt.hour
    return {target: window[column].notna().groupby(hours).sum().reindex(range(24), fill_value=0)
            for target, column in TARGETS.items()}


def coverage_guard(part, log, counts):
    """Withhold forecasts for hours the model never (or hardly) saw in training.

    Every published hour must have at least MIN_HOUR_READINGS observed hours at
    that hour of day in the training window. Temperature or rainfall short:
    the station is not published (status "insufficient data"). Wind short:
    wind is emptied and the station is "partial".
    """
    log = dict(log)
    thin = {target: [hour for hour in FORECAST_HOURS if counts[target][hour] < MIN_HOUR_READINGS]
            for target in TARGETS}
    notes = [log["notes"]] if log.get("notes") else []
    if thin["air_temperature"] or thin["rainfall_mm"]:
        short = sorted(set(thin["air_temperature"]) | set(thin["rainfall_mm"]))
        notes.append(f"fewer than {MIN_HOUR_READINGS} training readings at UTC hours {short}")
        log.update(status="insufficient data", notes=" | ".join(notes))
        return None, log
    if thin["wind_speed"] and part.wind_speed.notna().any():
        part = part.assign(wind_speed=np.nan)
        notes.append(f"wind withheld: fewer than {MIN_HOUR_READINGS} training readings at UTC hours "
                     f"{thin['wind_speed']}")
        log.update(status="partial", notes=" | ".join(notes))
    return part, log


def export(data, part_dir, steps, dates, versions, last_obs):
    status = data["status"]
    hourly_data = data["hourly"]
    logs, hourly_parts = [], []
    active = set(status.loc[status.active, "device_id"])
    for device_id in status.device_id:
        log_file = part_dir / f"{device_id}.json"
        if device_id not in active:
            log = {"device_id": device_id, "status": "inactive", "seconds": None,
                   "errors": "", "notes": "not forecast: station not reporting"}
        elif log_file.is_file():
            log = json.loads(log_file.read_text(encoding="utf-8"))
            if log["status"] in FORECAST_STATUSES:
                part = pd.read_csv(part_dir / f"{device_id}.csv", dtype={"device_id": "string"})
                counts = hour_counts(hourly_data.loc[hourly_data.device_id.eq(device_id)], last_obs)
                part, log = coverage_guard(part, log, counts)
                if part is not None:
                    hourly_parts.append(part)
        else:
            log = {"device_id": device_id, "status": "not run", "seconds": None, "errors": "", "notes": ""}
        logs.append(log)
    # Active stations without any forecast stay active; the reason says why nothing is shown.
    by_status = {log["device_id"]: log["status"] for log in logs}
    missing = {device_id for device_id, value in by_status.items() if value not in ("ok", "partial", "inactive")}
    status = status.copy()
    status.loc[status.device_id.isin(missing), "reason"] = NOT_AVAILABLE
    status["forecast_status"] = status.device_id.map(
        lambda device_id: {"ok": "ok", "partial": "partial", "inactive": "inactive"}.get(
            by_status[device_id], "not available"))
    status = status[STATUS_COLUMNS]
    columns = ["device_id", "time_utc", "air_temperature", "wind_speed", "rainfall_mm", "rainfall_upper"]
    hourly = (pd.concat(hourly_parts, ignore_index=True) if hourly_parts
              else pd.DataFrame(columns=columns))[columns]
    daily = (daily_from_hourly(hourly, dates) if hourly_parts else pd.DataFrame(columns=DAILY_COLUMNS))
    hourly = on_hours(hourly)
    from insights import generate_insights
    insight_columns = ["device_id", "type", "severity", "start_date", "message", "basis", "as_of", "caveat"]
    insights = pd.DataFrame(generate_insights(daily) if len(daily) else [], columns=insight_columns)
    insights["forecast_variation_mm"] = insights.device_id.map(forecast_variation(daily, dates))

    DEPLOY.mkdir(exist_ok=True)
    hourly["time_utc"] = pd.to_datetime(hourly.time_utc, utc=True).map(stamp)
    hourly.round(2).to_csv(DEPLOY / "forecast_hourly.csv", index=False)
    daily.to_csv(DEPLOY / "forecast_daily.csv", index=False)
    status.to_csv(DEPLOY / "station_status.csv", index=False)
    data["latest"].to_csv(DEPLOY / "latest_readings.csv", index=False)
    data["history"].to_csv(DEPLOY / "history_daily.csv", index=False)
    insights.to_csv(DEPLOY / "insights_latest.csv", index=False)
    run_log = pd.DataFrame(logs)
    for name, version in versions.items():
        run_log[name] = version
    run_log["as_of"] = data["as_of"].strftime("%Y-%m-%dT%H:%M:%SZ")
    run_log.to_csv(DEPLOY / "forecast_run_log.csv", index=False)
    (DEPLOY / "README.md").write_text(readme(data, logs, versions, steps), encoding="utf-8")
    print("\nRun log summary:")
    print(run_log.status.value_counts().to_string())
    print(f"\nWrote {DEPLOY}:")
    for path in sorted(DEPLOY.iterdir()):
        print(f"  {path.name:24} {path.stat().st_size:>10,} bytes")


def deadline_time(text):
    hour, minute = (int(part) for part in text.split(":"))
    return datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0)


def main(args):
    started = time.time()
    versions = package_versions()
    print("Packages:", versions, flush=True)
    data = prepare(args.input)
    as_of, status = data["as_of"], data["status"]
    last_obs, steps, dates = horizon(as_of)
    print(f"\nAS-OF {as_of.isoformat()} ({as_of.tz_convert(LOCAL_TZ).strftime('%a %d %b %Y %H:%M')} Nairobi)")
    print(f"Active stations: {int(status.active.sum())} of {len(status)}")
    print(f"Forecast dates: {dates[0]} to {dates[-1]}; {steps} hourly steps from {last_obs.isoformat()}")
    print(f"Preparation took {time.time() - started:.0f} s", flush=True)
    with pd.option_context("display.width", 200, "display.max_columns", 20):
        print(status.to_string(index=False), flush=True)

    orders = load_orders()
    active = list(status.loc[status.active, "device_id"])
    hourly = data["hourly"]
    frames = {device_id: hourly.loc[hourly.device_id.eq(device_id), ["hour", *TARGETS.values()]]
              for device_id in active}
    if args.step1:
        for device_id in active[:args.time_stations]:
            begin = time.time()
            result, log = forecast_station(device_id, frames[device_id], last_obs, steps, orders)
            print(f"Timing {device_id}: {time.time() - begin:.1f} s, status {log['status']} {log['errors']}",
                  flush=True)
        return

    part_dir = part_folder(as_of, steps)
    if not args.assemble_only:
        todo = [device_id for device_id in active
                if not (part_dir / f"{device_id}.json").is_file()
                or json.loads((part_dir / f"{device_id}.json").read_text())["status"] == "error"]
        print(f"\nForecasting {len(todo)} stations ({len(active) - len(todo)} already done) "
              f"with {args.workers} workers; no new station after {args.deadline}", flush=True)
        limit = deadline_time(args.deadline)
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            pending = set()
            queue = list(todo)
            while queue or pending:
                while queue and len(pending) < args.workers and datetime.now() < limit:
                    device_id = queue.pop(0)
                    pending.add(pool.submit(run_one, device_id, frames[device_id], last_obs, steps,
                                            orders, part_dir))
                if not pending:
                    print(f"Deadline {args.deadline} reached: {len(queue)} stations not run", flush=True)
                    break
                done, pending = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    log = future.result()
                    print(f"{datetime.now():%H:%M:%S} {log['device_id']} {log['status']} "
                          f"{log['seconds']} s {log['errors']}", flush=True)
    export(data, part_dir, steps, dates, versions, last_obs)
    print(f"\nDONE in {time.time() - started:.0f} s", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--input", type=Path, default=INPUT)
    parser.add_argument("--step1", action="store_true")
    parser.add_argument("--time-stations", type=int, default=2)
    parser.add_argument("--assemble-only", action="store_true")
    parser.add_argument("--deadline", default="16:00")
    parser.add_argument("--workers", type=int, default=max(1, min(10, (os.cpu_count() or 2) - 2)))
    main(parser.parse_args())
