"""Imarika weather preprocessing (pandas is the only third-party dependency).

Run ``python preprocess.py`` from any directory. Inputs and outputs live in the
project's data/ directory; override it with ``--data-dir PATH`` if needed.

Timestamps are stored in UTC. Calendar features and daily joins use Nairobi
dates. The unified table has one row per station/hour within that station's
retained IoT span, including empty hours. Weather variables use hourly means;
rainfall increments use hourly sums (mm), not means. Source prefixes distinguish
IoT, Open-Meteo, NASA POWER and CHIRPS measurements.
"""

import argparse
from math import cos, pi, sin
from pathlib import Path

import pandas as pd  # pyright: ignore[reportMissingModuleSource]


# =============================================================================
# Configuration and shared input helpers
# =============================================================================
DATA_DIR = Path(__file__).resolve().parent / "data"
LOCAL_TZ = "Africa/Nairobi"
MAX_INTERPOLATION_GAP = pd.Timedelta(hours=6)
TEST_DEVICE = "test-spatial-check-device"
IOT_WEATHER = [
    "air_temperature", "air_humidity", "barometric_pressure", "wind_speed",
    "peak_wind_gust", "wind_direction", "rain_accumulation", "rain_gauge",
    "light_intensity", "uv_index",
]
OPENMETEO_WEATHER = [
    "temperature_2m", "relative_humidity_2m", "surface_pressure",
    "wind_speed_10m", "wind_gusts_10m", "wind_direction_10m",
    "precipitation", "uv_index",
]
NASA_WEATHER = ["T2M", "RH2M", "PRECTOTCORR", "ALLSKY_SFC_SW_DWN", "WS2M", "WD2M"]
# Exact UTC transitions from reports/data_quality/rain_reset_events.csv. Splitting
# at midnight on these dates would incorrectly split before the actual decrease.
KNOWN_RESETS = {
    "101990961244800102": pd.Timestamp("2026-05-02T05:44:20.644Z"),
    "101990961244800222": pd.Timestamp("2026-02-27T05:56:59.561Z"),
}


def read_source(path):
    frame = pd.read_csv(path, dtype={"device_id": "string"}, low_memory=False)
    frame["device_id"] = (
        frame.device_id.str.replace(r'''["'“”‘’]''', "", regex=True).str.strip()
    )
    if frame.device_id.isna().any() or frame.device_id.eq("").any():
        raise ValueError(f"{path.name}: missing device_id")
    return frame


def numeric(frame, columns):
    for column in columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce").replace(
            [float("inf"), float("-inf")], float("nan")
        )


def require_valid_times(frame, column, source):
    if frame[column].isna().any():
        raise ValueError(f"{source}: {frame[column].isna().sum()} invalid {column} values")


# =============================================================================
# 1. FILTER: Busia bounding box, test device, and missing sensor-error flags
# =============================================================================
def filter_iot(frame):
    frame = frame.copy()
    numeric(frame, ["latitude", "longitude", "sensor_error"])
    inside = frame.latitude.between(0.1, 0.7) & frame.longitude.between(34.1, 34.6)
    busia = frame.loc[inside & frame.device_id.ne(TEST_DEVICE)].copy()
    filtered = busia.loc[busia.sensor_error.notna()].copy()
    print(f"IoT input: {len(frame):,}; Busia/non-test: {len(busia):,}")
    print(f"Removed missing sensor_error: {len(busia) - len(filtered):,}; retained: {len(filtered):,}")
    if filtered.empty:
        raise ValueError("No IoT records remain after filtering")

    numeric(filtered, IOT_WEATHER + ["altitude", "rssi", "snr"])
    filtered["received_at"] = pd.to_datetime(
        filtered.received_at, format="mixed", utc=True, errors="coerce"
    )
    require_valid_times(filtered, "received_at", "IoT")
    if filtered.duplicated(["device_id", "received_at"]).any():
        raise ValueError("IoT has duplicate station/timestamps; resolve these before differencing")
    return filtered.sort_values(["device_id", "received_at"]).reset_index(drop=True)


# =============================================================================
# 2. RAIN ACCUMULATION: segment counters, then derive per-reading rainfall
# =============================================================================
def derive_rainfall(frame):
    parts = []
    for station, group in frame.groupby("device_id", sort=True):
        group = group.sort_values("received_at").copy()
        # Skip missing counter cells only to detect a decrease; actual rainfall
        # uses adjacent-reading differences below, leaving missing pairs unknown.
        previous_counter = group.rain_accumulation.ffill().shift()
        reset = (group.rain_accumulation - previous_counter).lt(-1e-6)
        if station in KNOWN_RESETS:
            boundary = KNOWN_RESETS[station]
            reset |= group.received_at.ge(boundary) & group.received_at.shift().lt(boundary)
        reset.iloc[0] = False
        group["rain_reset"] = reset
        group["rain_segment"] = reset.cumsum().astype("int64")
        segments = group.groupby("rain_segment", sort=False)
        group["rainfall_mm"] = segments.rain_accumulation.diff().clip(lower=0)
        # This convention also sets the station's first retained reading to zero.
        group.loc[segments.cumcount().eq(0), "rainfall_mm"] = 0.0
        parts.append(group)
    result = pd.concat(parts, ignore_index=True)
    print(f"Rain counter transitions segmented: {int(result.rain_reset.sum()):,}")
    return result


# =============================================================================
# 3. UNIT CONVERSION: IoT pressure Pa -> hPa
# =============================================================================
def convert_units(frame):
    frame = frame.copy()
    # Keep the original column name; barometric_pressure now has units of hPa.
    frame["barometric_pressure"] = frame.barometric_pressure / 100.0
    return frame


# =============================================================================
# 4. TEMPORAL INTERPOLATION: elapsed-time linear interpolation, short gaps only
# =============================================================================
def interpolate_short_gaps(values):
    """Fill internal NaNs only when bounding observations are <6 hours apart.

    The DatetimeIndex must be sorted and unique. ``limit=24`` would be incorrect
    for irregular observations: the limit here is elapsed time, not row count.
    Leading/trailing NaNs and gaps exactly six hours long remain NaN.
    """
    timestamps = pd.Series(values.index, index=values.index)
    observed_times = timestamps.where(values.notna())
    bracket_span = observed_times.bfill() - observed_times.ffill()
    eligible = values.isna() & bracket_span.lt(MAX_INTERPOLATION_GAP)
    interpolated = values.interpolate(method="time", limit_area="inside")
    return values.where(~eligible, interpolated)


def interpolate_iot(frame):
    columns = IOT_WEATHER + ["rainfall_mm"]
    before = frame[columns].isna().sum()
    parts = []
    for _, group in frame.groupby("device_id", sort=True):
        group = group.sort_values("received_at").set_index("received_at").copy()
        for column in columns:
            if column in ("rain_accumulation", "rainfall_mm"):
                # A missing value must never be interpolated across a reset.
                for _, segment in group.groupby("rain_segment", sort=False):
                    group.loc[segment.index, column] = interpolate_short_gaps(segment[column])
            else:
                group[column] = interpolate_short_gaps(group[column])
        parts.append(group.reset_index())
    result = pd.concat(parts, ignore_index=True)
    print("Cells filled by short-gap interpolation:")
    print((before - result[columns].isna().sum()).to_string())
    return result


# =============================================================================
# 5. FEATURE ENGINEERING: local calendar and cyclical features
# =============================================================================
def add_time_features(frame, time_column):
    frame = frame.copy()
    local = frame[time_column].dt.tz_convert(LOCAL_TZ)
    frame["hour_of_day"] = local.dt.hour
    frame["day_of_week"] = local.dt.dayofweek  # Monday=0, Sunday=6
    frame["month"] = local.dt.month
    frame["day_of_year"] = local.dt.dayofyear
    frame["is_rainy_season"] = frame.month.isin([3, 4, 5, 10, 11, 12]).astype("int8")
    # Lookup tables avoid a Python trig call for every reading. Only pandas and
    # the standard-library math module are needed (no numpy/sklearn imports).
    frame["sin_hour"] = frame.hour_of_day.map({h: sin(2 * pi * h / 24) for h in range(24)})
    frame["cos_hour"] = frame.hour_of_day.map({h: cos(2 * pi * h / 24) for h in range(24)})
    # Annual phase starts at zero on January 1; leap years use a 366-day period.
    for label, function in [("sin_doy", sin), ("cos_doy", cos)]:
        regular = frame.day_of_year.map({d: function(2 * pi * (d - 1) / 365) for d in range(1, 367)})
        leap = frame.day_of_year.map({d: function(2 * pi * (d - 1) / 366) for d in range(1, 367)})
        frame[label] = regular.where(~local.dt.is_leap_year, leap)
    return frame


# =============================================================================
# 6. UNIFIED DATASET: hourly IoT + hourly Open-Meteo + daily NASA/CHIRPS
# =============================================================================
def hourly_iot(clean):
    parts = []
    mean_columns = ["latitude", "longitude", "altitude"] + IOT_WEATHER
    for station, group in clean.groupby("device_id", sort=True):
        hourly = group.set_index("received_at").resample("h")
        result = hourly[mean_columns].mean()
        # All-missing rainfall hours must stay NaN instead of becoming dry hours.
        result["rainfall_mm"] = hourly.rainfall_mm.sum(min_count=1)
        result["reading_count"] = hourly.size()
        result["rainfall_observation_count"] = hourly.rainfall_mm.count()
        result["sensor_error"] = hourly.sensor_error.max()
        result["rain_reset_count"] = hourly.rain_reset.sum()
        result = result.add_prefix("iot_").rename_axis("hour").reset_index()
        result.insert(0, "device_id", station)
        parts.append(result)
    return pd.concat(parts, ignore_index=True)


def load_references(data_dir):
    meteo = read_source(data_dir / "openmeteo_busia.csv")
    numeric(meteo, OPENMETEO_WEATHER)
    # CSV times have no offset; the fetch script explicitly requested Nairobi.
    meteo["hour"] = pd.to_datetime(meteo.timestamp, errors="coerce").dt.tz_localize(
        LOCAL_TZ
    ).dt.tz_convert("UTC")
    require_valid_times(meteo, "hour", "Open-Meteo")
    if not meteo.hour.eq(meteo.hour.dt.floor("h")).all():
        raise ValueError("Open-Meteo contains non-hour-aligned timestamps")
    meteo = meteo[["device_id", "hour"] + OPENMETEO_WEATHER].rename(
        columns={column: f"openmeteo_{column}" for column in OPENMETEO_WEATHER}
    )

    nasa = read_source(data_dir / "nasa_power_busia.csv")
    numeric(nasa, NASA_WEATHER)
    nasa[NASA_WEATHER] = nasa[NASA_WEATHER].mask(nasa[NASA_WEATHER].le(-990))
    nasa["date"] = pd.to_datetime(nasa.date.astype(str), format="%Y%m%d", errors="coerce")
    require_valid_times(nasa, "date", "NASA POWER")
    nasa = nasa[["device_id", "date"] + NASA_WEATHER].rename(
        columns={column: f"nasa_{column}" for column in NASA_WEATHER}
    )

    chirps = read_source(data_dir / "chirps_busia.csv")
    numeric(chirps, ["rainfall_mm"])
    chirps["rainfall_mm"] = chirps.rainfall_mm.mask(chirps.rainfall_mm.lt(-900))
    chirps["date"] = pd.to_datetime(chirps.date, format="%Y-%m-%d", errors="coerce")
    require_valid_times(chirps, "date", "CHIRPS")
    chirps = chirps[["device_id", "date", "rainfall_mm"]].rename(
        columns={"rainfall_mm": "chirps_rainfall_mm"}
    )
    return meteo, nasa, chirps


def merge_sources(hourly, meteo, nasa, chirps):
    # Left joins preserve IoT coverage even where a reference variable/day is
    # unavailable. Validation prevents duplicate keys from multiplying rows.
    unified = hourly.merge(meteo, on=["device_id", "hour"], how="left", validate="one_to_one")
    unified["date"] = unified.hour.dt.tz_convert(LOCAL_TZ).dt.tz_localize(None).dt.normalize()
    unified = unified.merge(nasa, on=["device_id", "date"], how="left", validate="many_to_one")
    unified = unified.merge(chirps, on=["device_id", "date"], how="left", validate="many_to_one")
    # Recompute features on hourly timestamps rather than averaging calendar or
    # cyclical columns from the individual IoT readings.
    return add_time_features(unified, "hour").sort_values(["device_id", "hour"]).reset_index(drop=True)


# =============================================================================
# 7. OUTPUT: cleaned readings, unified hourly table, and printed summaries
# =============================================================================
def print_summary(name, frame, time_column):
    print(f"\n{name}: {len(frame):,} rows; {frame.device_id.nunique()} stations")
    print(f"UTC coverage: {frame[time_column].min()} -> {frame[time_column].max()}")
    print("Per-station record counts and UTC coverage:")
    print(frame.groupby("device_id")[time_column].agg(["size", "min", "max"]).to_string())
    print("Missing values by column:")
    missing = frame.isna().sum().rename("missing").to_frame()
    missing["percent"] = (missing.missing / len(frame) * 100).round(3)
    print(missing.to_string())


def main(data_dir=DATA_DIR):
    clean = filter_iot(read_source(data_dir / "all_readings.csv"))
    clean = derive_rainfall(clean)
    clean = convert_units(clean)
    clean = interpolate_iot(clean)
    clean = add_time_features(clean, "received_at")
    meteo, nasa, chirps = load_references(data_dir)
    unified = merge_sources(hourly_iot(clean), meteo, nasa, chirps)

    clean_path = data_dir / "iot_clean.csv"
    unified_path = data_dir / "unified_dataset.csv"
    clean.to_csv(clean_path, index=False)
    unified.to_csv(unified_path, index=False)
    print_summary("Clean IoT", clean, "received_at")
    print_summary("Unified hourly", unified, "hour")
    print(f"\nHourly slots with no IoT readings: {int(unified.iot_reading_count.eq(0).sum()):,}")
    print("Pressure columns barometric_pressure / iot_barometric_pressure are in hPa.")
    print("Daily NASA/CHIRPS values repeat across each Nairobi day's hourly rows.")
    print(f"Saved: {clean_path}\nSaved: {unified_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DATA_DIR)
    main(parser.parse_args().data_dir)
