"""Tests for scripts/forecast_export.py and the committed deploy_data/ export."""

import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT))
import forecast_export as fe  # noqa: E402

DEPLOY = ROOT / "deploy_data"
SCHEMAS = {
    "forecast_hourly.csv": ["device_id", "time_utc", "air_temperature", "wind_speed", "rainfall_mm",
                            "rainfall_upper"],
    "forecast_daily.csv": fe.DAILY_COLUMNS,
    "station_status.csv": fe.STATUS_COLUMNS,
    "latest_readings.csv": ["device_id", "reading_utc", "air_temperature", "air_humidity", "wind_speed",
                            "rain_today_mm", "age_hours", "light_intensity", "uv_index",
                            "barometric_pressure", "peak_wind_gust", "wind_direction",
                            "rain_since_morning_mm"],
    "history_daily.csv": ["device_id", "date", "rain_mm", "temp_min", "temp_max", "humidity_mean",
                          "readings_received"],
    "insights_latest.csv": ["device_id", "type", "severity", "start_date", "message", "basis", "as_of",
                            "caveat", "forecast_variation_mm"],
    "forecast_run_log.csv": ["device_id", "status", "seconds", "errors", "notes", "python", "pandas",
                             "numpy", "statsmodels", "scikit-learn", "as_of"],
}


def ts(text):
    return np.datetime64(pd.Timestamp(text).tz_localize(None))


# ----------------------------------------------------------------------------- unit tests
def test_horizon_ends_at_the_last_on_hour_of_the_seventh_nairobi_day():
    as_of = pd.Timestamp("2026-11-25T05:30:00Z")  # Wed 25 Nov, 08:30 in Nairobi
    last_obs, steps, dates = fe.horizon(as_of)
    assert last_obs == pd.Timestamp("2026-11-25T04:00:00Z")
    assert dates[0] == date(2026, 11, 26) and dates[-1] == date(2026, 12, 2) and len(dates) == 7
    assert last_obs + pd.Timedelta(hours=steps) == pd.Timestamp("2026-12-02T21:00:00Z")


def test_observing_day_runs_0800_to_0059_eat():
    # Day D = 08:00 EAT on D to 00:59 EAT on D+1 = 05:00-21:59 UTC on D.
    assert fe.observing_day(pd.Timestamp("2026-11-25T04:59Z")) is None  # 07:59 EAT
    assert fe.observing_day(pd.Timestamp("2026-11-25T05:00Z")) == date(2026, 11, 25)  # 08:00 EAT
    assert fe.observing_day(pd.Timestamp("2026-11-25T21:59Z")) == date(2026, 11, 25)  # 00:59 EAT 26th
    assert fe.observing_day(pd.Timestamp("2026-11-25T22:00Z")) is None  # 01:00 EAT 26th
    assert fe.day_start(date(2026, 11, 25)) == pd.Timestamp("2026-11-25T05:00Z")
    assert fe.day_end(date(2026, 11, 25)) == pd.Timestamp("2026-11-25T22:00Z")


def test_morning_start_is_0800_eat_today():
    assert fe.morning_start(pd.Timestamp("2026-11-25T11:00Z")) == pd.Timestamp("2026-11-25T05:00Z")
    # 00:30 EAT on the 26th: 08:00 EAT on the 26th has not come yet.
    assert fe.morning_start(pd.Timestamp("2026-11-25T21:30Z")) == pd.Timestamp("2026-11-25T05:00Z")
    assert fe.morning_start(pd.Timestamp("2026-11-26T04:00Z")) == pd.Timestamp("2026-11-25T05:00Z")


def test_counter_rain_bridges_the_overnight_gap():
    times = np.array([ts("2026-11-19T20:00"), ts("2026-11-20T05:10"), ts("2026-11-20T12:00"),
                      ts("2026-11-20T20:30")])
    counter = np.array([100.0, 101.0, 103.5, 104.0])
    start, end = ts("2026-11-20T05:00"), ts("2026-11-21T05:00")
    # Start value is the evening reading before the shutdown (within 12 h).
    assert fe.counter_rain(times, counter, start, end) == 4.0
    # A missing counter inside the day is skipped, not fatal.
    assert fe.counter_rain(times, np.array([100.0, np.nan, 103.5, 104.0]), start, end) == 4.0
    # No counter reading in the day: empty, never 0.
    assert fe.counter_rain(times, counter, ts("2026-11-22T05:00"), ts("2026-11-23T05:00")) is None
    assert fe.counter_rain(times, np.array([100.0, np.nan, np.nan, np.nan]), start, end) is None
    # A counter reset (decrease) leaves the day empty.
    assert fe.counter_rain(times, np.array([100.0, 101.0, 2.0, 3.0]), start, end) is None
    # A start reading older than 12 h is not used: count from the first reading of the day.
    old = np.array([ts("2026-11-18T20:00"), *times[1:]])
    assert fe.counter_rain(old, counter, start, end) == 3.0


def test_daily_uses_only_the_16_published_hours_and_leaves_temp_min_empty():
    hours = pd.date_range("2026-11-26T00:00Z", periods=48, freq="h")
    hourly = pd.DataFrame({"device_id": "A", "time_utc": hours, "air_temperature": 20.0,
                           "wind_speed": 1.0, "rainfall_mm": 0.5, "rainfall_upper": 1.0})
    hourly.loc[hours.hour == 23, "rainfall_mm"] = np.nan  # night values are ignored
    daily = fe.daily_from_hourly(hourly, [date(2026, 11, 26), date(2026, 11, 27)])
    assert list(daily.columns) == fe.DAILY_COLUMNS
    assert list(daily.date) == ["2026-11-26", "2026-11-27"]
    assert list(daily.rain_mm) == [8.0, 8.0] and list(daily.rain_upper) == [16.0, 16.0]
    assert daily.temp_min.isna().all() and daily.observing_window_utc.eq("05:00-20:59").all()
    assert daily.local_window.eq("08:00-00:00 EAT").all()
    # A missing on-hour value leaves the day empty, never 0.
    hourly.loc[10, "rainfall_upper"] = np.nan
    daily = fe.daily_from_hourly(hourly, [date(2026, 11, 26), date(2026, 11, 27)])
    assert daily.rain_upper.isna().tolist() == [True, False]


def test_on_hours_keeps_0500_to_2059_utc():
    hours = pd.date_range("2026-11-26T00:00Z", periods=24, freq="h")
    kept = fe.on_hours(pd.DataFrame({"time_utc": hours}))
    assert sorted(pd.to_datetime(kept.time_utc).dt.hour) == list(range(5, 21))


def test_coverage_guard_withholds_hours_never_seen_in_training():
    last_obs = pd.Timestamp("2026-11-25T04:00Z")
    hours = pd.date_range(last_obs - pd.Timedelta(hours=fe.WINDOW_HOURS - 1), last_obs, freq="h")
    frame = pd.DataFrame({"hour": hours, "iot_air_temperature": 20.0, "iot_wind_speed": 1.0,
                          "iot_rainfall_mm": 0.0})
    part = pd.DataFrame({"device_id": "A", "time_utc": ["2026-11-25T05:00:00Z"], "air_temperature": 20.0,
                         "wind_speed": 1.0, "rainfall_mm": 0.1, "rainfall_upper": 1.0})
    log = {"device_id": "A", "status": "ok", "notes": ""}
    kept, new_log = fe.coverage_guard(part, log, fe.hour_counts(frame, last_obs))
    assert kept is not None and new_log["status"] == "ok"
    # Rain never observed at 20:00 UTC: the station is not published.
    gap = frame.assign(iot_rainfall_mm=frame.iot_rainfall_mm.where(frame.hour.dt.hour != 20))
    kept, new_log = fe.coverage_guard(part, log, fe.hour_counts(gap, last_obs))
    assert kept is None and new_log["status"] == "insufficient data" and "[20]" in new_log["notes"]
    # Fewer than the minimum: 4 observations at 06:00 UTC is still too few.
    sparse = frame.assign(iot_air_temperature=frame.iot_air_temperature.where(
        (frame.hour.dt.hour != 6) | (frame.hour >= last_obs - pd.Timedelta(days=4))))
    assert fe.hour_counts(sparse, last_obs)["air_temperature"][6] == 4
    assert fe.coverage_guard(part, log, fe.hour_counts(sparse, last_obs))[0] is None
    # Wind alone short: published as partial with wind empty.
    windless = frame.assign(iot_wind_speed=frame.iot_wind_speed.where(frame.hour.dt.hour != 7))
    kept, new_log = fe.coverage_guard(part, log, fe.hour_counts(windless, last_obs))
    assert new_log["status"] == "partial" and kept.wind_speed.isna().all()


def test_forecast_variation_uses_days_one_to_five():
    dates = [date(2026, 11, 26) + pd.Timedelta(days=offset) for offset in range(7)]
    daily = pd.DataFrame({"device_id": "A", "date": [day.isoformat() for day in dates],
                          "rain_mm": [1.0, 4.0, 2.0, 2.0, 3.0, 50.0, 0.0]})
    assert fe.forecast_variation(daily, dates)["A"] == 3.0


def test_sensor_value_rules():
    reading = pd.Series({"sensor_error": 0.0, "uv_index": -1.2, "barometric_pressure": 87540.0,
                         "light_intensity": np.nan, "wind_direction": 180.0})
    assert fe.sensor_value(reading, "uv_index") is None
    assert fe.sensor_value(reading, "barometric_pressure") == 875.4
    assert fe.sensor_value(reading, "light_intensity") is None
    assert fe.sensor_value(reading, "wind_direction") == 180.0
    assert fe.sensor_value(pd.Series({**reading, "barometric_pressure": 44680.0}),
                           "barometric_pressure") is None
    assert fe.sensor_value(pd.Series({**reading, "sensor_error": 1.0}), "wind_direction") is None


def test_area_name_point_in_polygon():
    features = fe.load_boundaries()
    if not features:
        pytest.skip("assets/kenya_boundaries.geojson missing")
    names = {feature["properties"]["name"] for feature in fe.features_at(features, "sub-county")}
    assert fe.area_name(features, 34.304639, 0.391) in names
    assert fe.area_name(features, 36.8, -1.3) == "Outside Busia County"


def test_forecast_station_clips_rain_and_records_errors(monkeypatch):
    import sarima_model

    class Prediction:
        def __init__(self, steps):
            self.predicted_mean = np.full(steps, -0.3)
            self.steps = steps

        def conf_int(self, alpha):
            return np.column_stack([np.full(self.steps, -1.0), np.full(self.steps, -0.1)])

    class Fitted:
        params = None

        @property
        def model(self):
            return self

        def filter(self, params):
            return self

        def get_forecast(self, steps):
            return Prediction(steps)

    monkeypatch.setattr(sarima_model, "fit_model", lambda series, order, maxiter: Fitted())
    last_obs = pd.Timestamp("2026-11-25T04:00Z")
    hours = pd.date_range(last_obs - pd.Timedelta(hours=fe.WINDOW_HOURS - 1), last_obs, freq="h")
    frame = pd.DataFrame({"hour": hours, "iot_air_temperature": 20.0, "iot_wind_speed": 1.0,
                          "iot_rainfall_mm": 0.0})
    result, log = fe.forecast_station("A", frame, last_obs, 5, fe.load_orders())
    assert log["status"] == "ok"
    assert (result.rainfall_mm >= 0).all() and (result.rainfall_upper >= 0).all()
    assert (result.wind_speed >= 0).all()
    sparse = frame.assign(iot_rainfall_mm=np.nan)
    result, log = fe.forecast_station("A", sparse, last_obs, 5, fe.load_orders())
    assert result is None and log["status"] == "error" and "rainfall_mm" in log["errors"]
    # Wind alone failing keeps temperature and rain, with wind empty.
    no_wind = frame.assign(iot_wind_speed=np.nan)
    result, log = fe.forecast_station("A", no_wind, last_obs, 5, fe.load_orders())
    assert log["status"] == "partial" and fe.WIND_FAILED in log["notes"]
    assert result.wind_speed.isna().all() and result.rainfall_mm.notna().all()


# ----------------------------------------------------------------------------- export checks
needs_export = pytest.mark.skipif(not (DEPLOY / "forecast_run_log.csv").is_file(),
                                  reason="deploy_data/ has not been exported")


def read(name):
    return pd.read_csv(DEPLOY / name, dtype={"device_id": "string"}, keep_default_na=True)


def as_of():
    return pd.Timestamp(read("forecast_run_log.csv").as_of.iloc[0])


@needs_export
def test_export_schemas_and_sizes():
    for name, columns in SCHEMAS.items():
        assert list(read(name).columns) == columns, name
    for path in DEPLOY.iterdir():
        assert path.stat().st_size < 5 * 1024 * 1024, path.name
    assert (DEPLOY / "README.md").is_file()


@needs_export
def test_export_has_no_negative_rain_and_realistic_upper_bounds():
    for name, columns in [("forecast_hourly.csv", ["rainfall_mm", "rainfall_upper"]),
                          ("forecast_daily.csv", ["rain_mm", "rain_upper"]),
                          ("history_daily.csv", ["rain_mm"]),
                          ("latest_readings.csv", ["rain_today_mm", "rain_since_morning_mm"])]:
        frame = read(name)
        for column in columns:
            assert not (frame[column] < 0).any(), f"{name}:{column}"
    hourly = read("forecast_hourly.csv")
    assert (hourly.rainfall_upper >= hourly.rainfall_mm).all()
    assert hourly.rainfall_upper.max() < 30, "hourly upper bound is not realistic"
    daily = read("forecast_daily.csv")
    assert (daily.rain_upper >= daily.rain_mm).all()
    assert not (daily.rain_upper > 150).any(), "daily rain_upper above 150 mm"


@needs_export
def test_forecasts_cover_only_on_hours_and_active_stations():
    status = read("station_status.csv")
    active = set(status.loc[status.active.astype(str).eq("True"), "device_id"])
    hourly = read("forecast_hourly.csv")
    assert set(pd.to_datetime(hourly.time_utc).dt.hour) <= set(fe.FORECAST_HOURS)
    for name in ["forecast_hourly.csv", "forecast_daily.csv", "insights_latest.csv"]:
        assert set(read(name).device_id) <= active, name
    log = read("forecast_run_log.csv")
    forecast = set(log.loc[log.status.isin(["ok", "partial"]), "device_id"])
    daily = read("forecast_daily.csv")
    assert forecast <= active and set(daily.device_id) == forecast
    assert set(log.device_id) == set(status.device_id)
    partial = set(log.loc[log.status.eq("partial"), "device_id"])
    assert daily.loc[daily.device_id.isin(partial), "wind_max"].isna().all()
    assert daily[["rain_mm", "rain_upper", "temp_max"]].notna().all().all()
    assert daily.temp_min.isna().all() and daily.observing_window_utc.eq("05:00-20:59").all()
    assert daily.local_window.eq("08:00-00:00 EAT").all()


@needs_export
def test_timestamps_are_iso_utc_with_z():
    pattern = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$"
    for name, columns in [("forecast_hourly.csv", ["time_utc"]), ("latest_readings.csv", ["reading_utc"]),
                          ("station_status.csv", ["last_reading_utc", "last_seen_utc"]),
                          ("forecast_run_log.csv", ["as_of"])]:
        for column in columns:
            assert read(name)[column].str.match(pattern).all(), f"{name}:{column}"


@needs_export
def test_station_status_fields():
    status = read("station_status.csv")
    log = read("forecast_run_log.csv").set_index("device_id")
    assert len(status) == 26 and status.device_id.is_unique
    assert status.area.notna().all() and status.reported_today.astype(str).isin(["True", "False"]).all()
    assert status.coverage_14d.between(0, 1).all()
    expected = {"ok": "ok", "partial": "partial", "inactive": "inactive",
                "error": "not available", "not run": "not available", "insufficient data": "not available"}
    assert status.forecast_status.tolist() == [expected[log.status[d]] for d in status.device_id]
    failed = status.forecast_status.eq("not available")
    assert status.loc[failed, "reason"].eq(fe.NOT_AVAILABLE).all()
    assert (pd.to_datetime(status.last_seen_utc) >= pd.to_datetime(status.last_reading_utc)).all()


@needs_export
def test_daily_dates_are_the_seven_nairobi_days_after_as_of():
    _, _, dates = fe.horizon(as_of())
    for _, group in read("forecast_daily.csv").groupby("device_id"):
        assert list(group.date) == [day.isoformat() for day in dates]
    today = fe.morning_start(as_of()).date().isoformat()
    for _, group in read("history_daily.csv").groupby("device_id"):
        assert len(group) == fe.HISTORY_DAYS and group.date.iloc[-1] == today


@needs_export
def test_history_rain_empty_only_without_data():
    history = read("history_daily.csv")
    assert history.readings_received.between(0, len(fe.ON_HOURS)).all()
    no_data = history.loc[history.readings_received.eq(0)]
    assert no_data.rain_mm.isna().all() and no_data.temp_min.isna().all()
    assert history.loc[history.rain_mm.notna(), "readings_received"].gt(0).all()


@needs_export
def test_latest_readings_sensor_fields():
    latest = read("latest_readings.csv")
    assert len(latest) == 26
    assert not (latest.uv_index < 0).any()
    assert not (latest.barometric_pressure < 850).any()
    assert latest.wind_direction.dropna().between(0, 360).all()
    insights = read("insights_latest.csv")
    assert insights.forecast_variation_mm.notna().all() and (insights.forecast_variation_mm >= 0).all()
