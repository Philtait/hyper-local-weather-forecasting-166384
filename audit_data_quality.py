"""Audit the four Busia weather CSVs without modifying source data.

Requires pandas and numpy. Run: python audit_data_quality.py
Outputs a Markdown report and supporting CSVs in reports/data_quality/.
"""

import argparse
from pathlib import Path

import numpy as np
import pandas as pd  # pyright: ignore[reportMissingModuleSource]


TEST_DEVICE = "test-spatial-check-device"
LOCAL_TZ = "Africa/Nairobi"
WEATHER = {
    "iot": [
        "air_temperature", "air_humidity", "barometric_pressure", "wind_speed",
        "peak_wind_gust", "wind_direction", "rain_accumulation", "rain_gauge",
        "light_intensity", "uv_index",
    ],
    "openmeteo": [
        "temperature_2m", "relative_humidity_2m", "surface_pressure",
        "wind_speed_10m", "wind_gusts_10m", "wind_direction_10m",
        "precipitation", "uv_index",
    ],
    "nasa_power": ["T2M", "RH2M", "PRECTOTCORR", "ALLSKY_SFC_SW_DWN", "WS2M", "WD2M"],
    "chirps": ["rainfall_mm"],
}
FILES = {
    "iot": "all_readings.csv", "openmeteo": "openmeteo_busia.csv",
    "nasa_power": "nasa_power_busia.csv", "chirps": "chirps_busia.csv",
}


def percent(numerator, denominator):
    return 100 * numerator / denominator if denominator else np.nan


def markdown(frame):
    """Small Markdown table renderer, avoiding an optional tabulate dependency."""
    def cell(value):
        if pd.isna(value):
            return "—"
        if isinstance(value, (float, np.floating)):
            return f"{value:,.3f}"
        if isinstance(value, (int, np.integer)):
            return f"{value:,}"
        return str(value).replace("|", "\\|")

    lines = ["| " + " | ".join(frame.columns) + " |",
             "| " + " | ".join(["---"] * len(frame.columns)) + " |"]
    lines.extend("| " + " | ".join(cell(v) for v in row) + " |"
                 for row in frame.itertuples(index=False, name=None))
    return "\n".join(lines)


def normalize_numeric(frame, columns, source):
    """Separate empty values, parse failures, infinities and provider fill values."""
    result = []
    for column in columns:
        original = frame[column]
        numeric = pd.to_numeric(original, errors="coerce")
        invalid = original.notna() & numeric.isna()
        infinite = numeric.notna() & ~np.isfinite(numeric)
        sentinel = pd.Series(False, index=frame.index)
        if column in WEATHER[source]:
            if source == "nasa_power":
                sentinel = numeric.le(-990)
            elif source == "chirps":
                sentinel = numeric.lt(-900)
        frame[column] = numeric.mask(infinite | sentinel)
        result.append({
            "dataset": source, "variable": column,
            "invalid_numeric": int(invalid.sum()),
            "nonfinite": int(infinite.sum()), "provider_fill_values": int(sentinel.sum()),
        })
    return result


def rain_audit(iot):
    """Detect drops between consecutive finite, timestamped cumulative readings."""
    valid = iot.dropna(subset=["time", "rain_accumulation"]).copy()
    # A conflicting same-time counter has no unambiguous ordering; omit that time.
    conflicts = valid.groupby(["device_id", "time"]).rain_accumulation.transform("nunique").gt(1)
    ambiguous_counts = valid.loc[conflicts].groupby("device_id").size()
    valid = valid.loc[~conflicts].drop_duplicates(["device_id", "time"])
    valid = valid.sort_values(["device_id", "time"])
    groups = valid.groupby("device_id")
    valid["previous_time"] = groups.time.shift()
    valid["previous_accumulation"] = groups.rain_accumulation.shift()
    valid["previous_sensor_error"] = groups.sensor_error.shift()
    valid["change_mm"] = valid.rain_accumulation - valid.previous_accumulation
    valid["gap_hours"] = (valid.time - valid.previous_time).dt.total_seconds() / 3600
    drops = valid.loc[valid.change_mm.lt(-1e-6)].copy()
    drops["drop_mm"] = -drops.change_mm
    events = drops[[
        "device_id", "record_id", "previous_time", "time", "previous_accumulation",
        "rain_accumulation", "drop_mm", "gap_hours", "previous_sensor_error", "sensor_error",
    ]]
    rows = []
    for station, group in iot.groupby("device_id"):
        comparisons = valid.loc[valid.device_id.eq(station)]
        events_here = drops.loc[drops.device_id.eq(station)]
        rows.append({
            "device_id": station, "records": len(group),
            "missing_rain_accumulation": int(group.rain_accumulation.isna().sum()),
            "negative_accumulation": int(group.rain_accumulation.lt(0).sum()),
            "ambiguous_same_time_rows_omitted": int(ambiguous_counts.get(station, 0)),
            "valid_comparisons": int(comparisons.change_mm.notna().sum()),
            "reset_candidates": len(events_here),
            "largest_drop_mm": events_here.drop_mm.max(),
            "min_accumulation_mm": group.rain_accumulation.min(),
            "max_accumulation_mm": group.rain_accumulation.max(),
        })
    return events, pd.DataFrame(rows)


def temperature_correlations(iot, meteo):
    meteo = meteo.dropna(subset=["time", "temperature_2m"]).copy()
    meteo["hour_utc"] = meteo.time.dt.floor("h")
    reference = meteo.groupby(["device_id", "hour_utc"]).temperature_2m.mean().rename("reference")
    results = pd.DataFrame(index=pd.Index(sorted(iot.device_id.unique()), name="device_id"))
    for variant in ["raw", "screened"]:
        valid = iot.dropna(subset=["time", "air_temperature"]).copy()
        if variant == "screened":
            valid = valid.loc[valid.air_temperature.between(14, 36) & valid.sensor_error.eq(0)]
        # Equal weight per unique observation time, then equal weight per matched hour.
        readings = valid.groupby(["device_id", "time"], as_index=False).air_temperature.mean()
        readings["hour_utc"] = readings.time.dt.floor("h")
        hourly = readings.groupby(["device_id", "hour_utc"]).air_temperature.agg(["mean", "size"])
        pairs = hourly.join(reference, how="inner").reset_index()
        rows = []
        for station in results.index:
            matched = pairs.loc[pairs.device_id.eq(station)]
            enough = len(matched) >= 3
            varying = matched["mean"].nunique() > 1 and matched.reference.nunique() > 1
            residual = matched["mean"] - matched.reference
            total_hours = int((hourly.index.get_level_values("device_id") == station).sum())
            rows.append({
                "device_id": station,
                f"{variant}_iot_hours": total_hours,
                f"{variant}_matched_hours": len(matched),
                f"{variant}_matched_pct": percent(len(matched), total_hours),
                f"{variant}_matched_readings": int(matched["size"].sum()),
                f"{variant}_pearson_r": matched["mean"].corr(matched.reference) if enough and varying else np.nan,
                f"{variant}_bias_c": residual.mean(),
                f"{variant}_mae_c": residual.abs().mean(),
                f"{variant}_rmse_c": np.sqrt((residual ** 2).mean()),
                f"{variant}_first_hour_utc": matched.hour_utc.min(),
                f"{variant}_last_hour_utc": matched.hour_utc.max(),
                f"{variant}_status": "ok" if enough and varying else "insufficient_pairs_or_constant_series",
            })
        results = results.join(pd.DataFrame(rows).set_index("device_id"))
    return results.reset_index()


def audit(data_dir, output_dir):
    output_dir.mkdir(parents=True, exist_ok=True)
    datasets = {
        name: pd.read_csv(data_dir / filename, dtype={"device_id": "string"}, low_memory=False)
        for name, filename in FILES.items()
    }
    raw = datasets["iot"]
    lat = pd.to_numeric(raw.latitude, errors="coerce")
    lon = pd.to_numeric(raw.longitude, errors="coerce")
    reason = pd.Series("retained", index=raw.index)
    reason.loc[~(lat.between(0.1, 0.7) & lon.between(34.1, 34.6))] = "outside_bbox"
    reason.loc[lat.isna() | lon.isna()] = "missing_or_invalid_coordinates"
    reason.loc[raw.device_id.eq(TEST_DEVICE).fillna(False)] = "excluded_test_device"
    filtering = raw.assign(filter_result=reason).groupby(
        ["device_id", "filter_result"], dropna=False
    ).size().rename("records").reset_index()
    datasets["iot"] = raw.loc[reason.eq("retained")].copy()
    input_count = len(raw)
    del raw

    missing_rows, missing_station_rows, conversion_rows = [], [], []
    summary_rows, coverage_rows, missing_slot_rows = [], [], []
    for name, frame in datasets.items():
        original_columns = list(frame.columns)
        raw_missing = frame.isna().sum()
        numeric_columns = ["latitude", "longitude"] + WEATHER[name]
        if "altitude" in frame:
            numeric_columns.append("altitude")
        if name == "iot":
            numeric_columns += ["sensor_error", "rssi", "snr"]
        conversion_rows.extend(normalize_numeric(frame, numeric_columns, name))
        for column in original_columns:
            n = int(frame[column].isna().sum())
            missing_rows.append({
                "dataset": name, "variable": column, "records": len(frame),
                "raw_missing": int(raw_missing[column]), "missing_or_invalid": n,
                "missing_pct": percent(n, len(frame)),
            })
        for station, group in frame.groupby("device_id"):
            for column in original_columns:
                missing_station_rows.append({
                    "dataset": name, "device_id": station, "variable": column,
                    "records": len(group), "missing_or_invalid": int(group[column].isna().sum()),
                    "missing_pct": percent(group[column].isna().sum(), len(group)),
                })
        if name == "iot":
            frame["time"] = pd.to_datetime(frame.received_at, format="mixed", utc=True, errors="coerce")
        elif name == "openmeteo":
            # Confirmed by fetch_openmeteo.py and the archived API responses.
            frame["time"] = pd.to_datetime(frame.timestamp, errors="coerce").dt.tz_localize(
                LOCAL_TZ
            ).dt.tz_convert("UTC")
        else:
            fmt = "%Y%m%d" if name == "nasa_power" else "%Y-%m-%d"
            frame["time"] = pd.to_datetime(frame.date.astype(str), format=fmt, errors="coerce")
        time_valid = frame.dropna(subset=["time"])
        summary_rows.append({
            "dataset": name, "records": len(frame), "stations": frame.device_id.nunique(),
            "first_timestamp": frame.time.min(), "last_timestamp": frame.time.max(),
            "invalid_timestamps": int(frame.time.isna().sum()),
            "duplicate_station_times_extra": int(time_valid.duplicated(["device_id", "time"]).sum()),
            "duplicate_record_ids_extra": int(frame.record_id.dropna().duplicated().sum()) if name == "iot" else np.nan,
        })
        for station, group in frame.groupby("device_id"):
            times = group.time.dropna().drop_duplicates().sort_values()
            dates = times.dt.tz_convert(LOCAL_TZ).dt.date if name in ("iot", "openmeteo") else times.dt.date
            span = (dates.max() - dates.min()).days + 1 if len(dates) else 0
            gaps = times.diff().dt.total_seconds() / 3600
            row = {
                "dataset": name, "device_id": station, "records": len(group),
                "first_timestamp": times.min(), "last_timestamp": times.max(),
                "first_date": dates.min() if len(dates) else None,
                "last_date": dates.max() if len(dates) else None,
                "active_days": dates.nunique(), "span_days": span,
                "days_without_records_within_span": span - dates.nunique(),
                "median_interval_minutes": gaps.median() * 60, "max_gap_hours": gaps.max(),
                "gaps_over_24h": int(gaps.gt(24).sum()),
                "duplicate_station_times_extra": int(group.time.dropna().duplicated().sum()),
            }
            if name != "iot":
                step = pd.Timedelta(hours=1) if name == "openmeteo" else pd.Timedelta(days=1)
                row["missing_time_slots_within_span"] = int((times.max() - times.min()) / step) + 1 - len(times) if len(times) else 0
                if len(times):
                    absent = pd.date_range(times.min(), times.max(), freq=step).difference(times)
                    missing_slot_rows.extend(
                        {"dataset": name, "device_id": station, "missing_timestamp": time}
                        for time in absent
                    )
            coverage_rows.append(row)

    iot = datasets["iot"]
    assert len(iot) == filtering.loc[filtering.filter_result.eq("retained"), "records"].sum()
    assert iot.latitude.between(0.1, 0.7).all() and iot.longitude.between(34.1, 34.6).all()
    assert not iot.device_id.eq(TEST_DEVICE).any()
    flags = iot.sensor_error
    flag_counts = flags.value_counts(dropna=False).rename_axis("sensor_error").reset_index(name="records")
    flag_counts["pct"] = flag_counts.records / len(iot) * 100
    flag_counts["meaning"] = np.select(
        [flag_counts.sensor_error.isna(), flag_counts.sensor_error.eq(0)],
        ["missing/unknown", "zero (no error reported)"], default="nonzero (error reported)",
    )
    flag_rows = []
    for station, group in iot.groupby("device_id"):
        zero = group.sensor_error.eq(0)
        missing = group.sensor_error.isna()
        flag_rows.append({
            "device_id": station, "records": len(group), "zero_flags": int(zero.sum()),
            "nonzero_flags": int((group.sensor_error.notna() & ~zero).sum()),
            "missing_flags": int(missing.sum()), "missing_flag_pct": percent(missing.sum(), len(group)),
            "all_weather_missing": int(group[WEATHER["iot"]].isna().all(axis=1).sum()),
            "zero_flag_any_weather_missing": int((zero & group[WEATHER["iot"]].isna().any(axis=1)).sum()),
            "zero_flag_missing_temperature": int((zero & group.air_temperature.isna()).sum()),
            "missing_flag_with_temperature": int((missing & group.air_temperature.notna()).sum()),
        })

    temp_rows = []
    for name, column in [("iot", "air_temperature"), ("openmeteo", "temperature_2m"), ("nasa_power", "T2M")]:
        frame = datasets[name]
        for station, group in [("ALL", frame)] + list(frame.groupby("device_id")):
            values = group[column]
            below, above = int(values.lt(14).sum()), int(values.gt(36).sum())
            temp_rows.append({
                "dataset": name, "device_id": station, "records": len(group),
                "valid_temperature": int(values.notna().sum()), "missing_temperature": int(values.isna().sum()),
                "min_c": values.min(), "max_c": values.max(), "mean_c": values.mean(),
                "below_14_c": below, "above_36_c": above, "outside_range": below + above,
                "outside_pct_of_valid": percent(below + above, values.notna().sum()),
            })
    outliers = iot.loc[iot.air_temperature.lt(14) | iot.air_temperature.gt(36), [
        "device_id", "record_id", "time", "air_temperature", "sensor_error", "latitude", "longitude",
    ]].sort_values(["device_id", "time"])
    events, rain_summary = rain_audit(iot)
    correlations = temperature_correlations(iot, datasets["openmeteo"])
    tables = {
        "filter_counts": filtering, "source_summary": pd.DataFrame(summary_rows),
        "station_coverage": pd.DataFrame(coverage_rows), "missing_values": pd.DataFrame(missing_rows),
        "missing_values_by_station": pd.DataFrame(missing_station_rows),
        "numeric_conversion": pd.DataFrame(conversion_rows), "sensor_error_counts": flag_counts,
        "sensor_error_by_station": pd.DataFrame(flag_rows), "temperature_summary": pd.DataFrame(temp_rows),
        "iot_temperature_outliers": outliers, "rain_reset_events": events,
        "rain_reset_by_station": rain_summary, "temperature_correlations": correlations,
        "missing_reference_time_slots": pd.DataFrame(
            missing_slot_rows, columns=["dataset", "device_id", "missing_timestamp"]
        ),
    }
    for name, table in tables.items():
        table.to_csv(output_dir / f"{name}.csv", index=False)
    write_report(tables, input_count, output_dir)
    print(f"Audit complete: {input_count:,} input IoT records -> {len(iot):,} records / {iot.device_id.nunique()} stations")
    print(f"Report: {output_dir / 'report.md'}")
    print(f"Temperature outliers: {len(outliers):,}; rain reset candidates: {len(events):,}")
    print(correlations[["device_id", "screened_matched_hours", "screened_pearson_r", "screened_bias_c"]].to_string(index=False))


def write_report(tables, input_count, output_dir):
    source = tables["source_summary"]
    coverage = tables["station_coverage"]
    iot_coverage = coverage.loc[coverage.dataset.eq("iot")]
    n = int(source.loc[source.dataset.eq("iot"), "records"].iloc[0])
    missing = tables["missing_values"]
    flags = tables["sensor_error_by_station"]
    temps = tables["temperature_summary"]
    temp_total = temps.loc[temps.device_id.eq("ALL")]
    temp_iot = temp_total.loc[temp_total.dataset.eq("iot")].iloc[0]
    corrs = tables["temperature_correlations"]
    rain = tables["rain_reset_events"]
    flag_missing = int(flags.missing_flags.sum())
    missing_weather = missing.loc[missing.apply(lambda row: row.variable in WEATHER[row.dataset], axis=1)]
    missing_slots = tables["missing_reference_time_slots"]
    gap_notes = []
    for name, group in missing_slots.groupby("dataset"):
        dates = group.missing_timestamp
        gap_notes.append(
            f"- {name}: {len(group):,} absent station/time slots across {group.device_id.nunique()} stations; "
            f"{dates.nunique()} distinct missing times from {dates.min()} through {dates.max()}. "
            "See `missing_reference_time_slots.csv` for individual missing times."
        )
    station_missing = tables["missing_values_by_station"]
    absent_station_variables = station_missing.loc[
        station_missing.missing_pct.eq(100)
        & station_missing.apply(lambda row: row.variable in WEATHER[row.dataset], axis=1)
    ]
    missing_notes = []
    for (name, variable), group in absent_station_variables.groupby(["dataset", "variable"]):
        station_names = ", ".join(f"`{station}`" for station in group.device_id)
        detail = "all stations" if len(group) == int(source.loc[source.dataset.eq(name), "stations"].iloc[0]) else station_names
        missing_notes.append(f"- {name} `{variable}` is entirely missing at {detail}.")
    report = [
        "# Busia weather data quality audit",
        f"Generated: {pd.Timestamp.now(tz='UTC').isoformat()}",
        "## Summary",
        f"- Retained **{n:,} of {input_count:,} IoT readings** across **{len(iot_coverage)} stations**; excluded {input_count - n:,} readings.",
        f"- Sensor flags: **{int(flags.nonzero_flags.sum()):,} nonzero**, **{flag_missing:,} missing ({percent(flag_missing, n):.2f}%)**. Zero flags do not guarantee complete or plausible measurements.",
        f"- IoT temperature: **{int(temp_iot.outside_range):,} outside 14–36°C** ({temp_iot.outside_pct_of_valid:.4f}% of observed temperatures); observed range **{temp_iot.min_c:.1f}–{temp_iot.max_c:.1f}°C**.",
        f"- Cumulative rain: **{len(rain)} downward-step reset candidates**.",
        f"- Screened hourly IoT/Open-Meteo Pearson r: **{corrs.screened_pearson_r.min():.3f}–{corrs.screened_pearson_r.max():.3f}**, station median **{corrs.screened_pearson_r.median():.3f}**.",
        *missing_notes,
        *gap_notes,
        "## Scope and methods",
        "- IoT row-level inclusive bounding box: latitude 0.1–0.7, longitude 34.1–34.6; exclude `test-spatial-check-device`. Missing/invalid coordinates are excluded. A device may have both retained and excluded rows. Reference-source completeness uses each supplied CSV in full.",
        "- Identifiers are read as strings. No source records are edited or imputed. Counts and missingness use all retained IoT rows before temperature screening.",
        "- IoT `received_at` is UTC; Open-Meteo naive timestamps are Africa/Nairobi (UTC+3), confirmed by the fetch script and archived API metadata, and are converted to UTC. Daily NASA/CHIRPS values retain their date labels. IoT coverage dates and active-day counts use Nairobi calendar days; timestamp columns use UTC.",
        "- Missingness includes CSV nulls, unparseable numeric values, infinities, NASA fill values ≤−990 and CHIRPS fill values <−900. These categories are itemized in `numeric_conversion.csv`. Missing timestamps/rows are separate from missing cells.",
        "- 14–36°C is an inclusive expected-range screen, not proof that every value outside it is a faulty sensor. NASA temperatures are daily means, whereas IoT/Open-Meteo are subdaily.",
        "## 1. Record counts and date coverage",
        markdown(source),
        "### IoT station coverage (Nairobi dates)",
        "Active days contain at least one retained row, even if weather values are missing. Missing days are counted only between each station's first and last retained dates. No fixed IoT reporting interval is assumed.",
        markdown(iot_coverage[["device_id", "records", "first_date", "last_date", "active_days", "days_without_records_within_span", "median_interval_minutes", "max_gap_hours"]]),
        "Full timestamps, gap counts, duplicate counts and reference-source coverage are in `station_coverage.csv`. Filter outcomes, including partially excluded stations, are in `filter_counts.csv`.",
        "### Missing reference time slots within station spans",
        markdown(coverage.loc[coverage.dataset.ne("iot")].groupby("dataset", as_index=False).agg(
            missing_time_slots=("missing_time_slots_within_span", "sum"),
            max_missing_slots_per_station=("missing_time_slots_within_span", "max"))),
        *gap_notes,
        "## 2. Sensor error flags",
        markdown(tables["sensor_error_counts"]),
        "The extractor maps `sensorData.err` to `sensor_error`. Without a device error-code dictionary, only zero/nonzero/missing semantics are used; missing flags are not treated as healthy readings.",
        f"- {int(flags.all_weather_missing.sum()):,} rows have all ten IoT weather variables missing.",
        f"- {int(flags.zero_flag_any_weather_missing.sum()):,} zero-flag rows have at least one missing weather variable, including {int(flags.zero_flag_missing_temperature.sum()):,} with no temperature.",
        f"- {int(flags.missing_flag_with_temperature.sum()):,} missing-flag rows contain a temperature.",
        markdown(flags[["device_id", "records", "zero_flags", "nonzero_flags", "missing_flags", "missing_flag_pct", "all_weather_missing"]]),
        "## 3. Missing values per weather variable",
        markdown(missing_weather[["dataset", "variable", "records", "missing_or_invalid", "missing_pct"]]),
        "### Missing identifier, location, signal and flag fields",
        markdown(missing.loc[~missing.index.isin(missing_weather.index) & missing.missing_or_invalid.gt(0), ["dataset", "variable", "records", "missing_or_invalid", "missing_pct"]]),
        "All fields, including those with zero missing values, are in `missing_values.csv`; station-level breakdowns are in `missing_values_by_station.csv`.",
        "## 4. Temperature sanity check",
        markdown(temp_total.drop(columns="device_id")),
        "### IoT stations with out-of-range temperatures",
        markdown(temps.loc[temps.dataset.eq("iot") & temps.device_id.ne("ALL") & temps.outside_range.gt(0), ["device_id", "valid_temperature", "min_c", "max_c", "below_14_c", "above_36_c", "outside_pct_of_valid"]]),
        "All flagged IoT records are in `iot_temperature_outliers.csv`; per-station summaries for all temperature sources are in `temperature_summary.csv`.",
        "## 5. Rain accumulation reset detection",
        "Within each station, sort by UTC timestamp and compare consecutive finite cumulative readings. A decrease greater than 0.000001 mm is a reset candidate. Missing readings may be bridged; the elapsed gap is included. Identical same-time counter readings are collapsed; conflicting same-time readings are omitted and counted. A downward step can indicate a reset, rollover, replacement or bad reading; it does not prove which occurred.",
        markdown(rain) if len(rain) else "No downward steps detected.",
        (f"Observed drops range from {rain.drop_mm.min():.3f} to {rain.drop_mm.max():.3f} mm. "
         f"{int(rain.rain_accumulation.eq(0).sum())} candidates end at zero; "
         "small downward steps alone are not convincing evidence of a full counter reset.") if len(rain) else "",
        "Do not use negative cumulative differences as rainfall or interpret a downward-step magnitude as rainfall. Mark reset-crossing intervals unknown unless device reset/rollover semantics are verified. Counters already above zero at the beginning of coverage do not establish rainfall before the first observation.",
        "## 6. IoT versus Open-Meteo temperature correlation",
        "**Method:** collapse duplicate station/observation times by mean, average IoT temperature in UTC clock-hour bins `[hour, hour+1h)`, and inner-join the corresponding Open-Meteo hour by exact device ID and UTC hour. No interpolation or nearest-time matching. Each matched hour has equal weight, with at least one IoT observation; Pearson r requires at least three matched hours and nonconstant series. The IoT timestamp is gateway receipt time; sensor acquisition latency is not known. Open-Meteo hourly temperature is compared to the within-hour IoT mean, so their temporal support is not identical.",
        "**Raw:** all finite temperatures, regardless of flag/range. **Screened:** only IoT temperatures in 14–36°C with `sensor_error == 0`. The reference retains all finite temperatures. Bias is IoT minus Open-Meteo in °C. Correlation reflects agreement including the diurnal cycle, and is not a calibration or anomaly-correlation test.",
        markdown(corrs[["device_id", "raw_matched_hours", "raw_pearson_r", "screened_matched_hours", "screened_pearson_r", "screened_bias_c", "screened_mae_c", "screened_rmse_c"]]),
        "Matching coverage, raw/screened metrics and first/last matched UTC hours are in `temperature_correlations.csv`.",
        "## Recommended handling",
        "- Keep separate indicators for missing flags, missing measurements and temperature-range violations; a zero error flag is insufficient quality control.",
        "- Investigate low-temperature events against nearby stations and raw payloads before deciding whether to discard genuine weather extremes.",
        "- Segment cumulative-rain series at the detected downward steps before deriving rainfall increments; inspect gaps at these transitions.",
        "- Address missing reference variables and absent daily records before combining sources. Do not fill an entirely missing variable with zero.",
        "- Use station-wise bias and error alongside r when assessing reference agreement; investigate low-correlation stations and longer reporting gaps.",
        "## Reproduction and outputs",
        "Run `python audit_data_quality.py` (requires pandas and numpy). Optional flags: `--data-dir PATH --output-dir PATH`.",
        "Supporting tables:",
        *[f"- `{name}.csv`" for name in tables],
    ]
    (output_dir / "report.md").write_text("\n\n".join(report) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("data"))
    parser.add_argument("--output-dir", type=Path, default=Path("reports/data_quality"))
    args = parser.parse_args()
    audit(args.data_dir, args.output_dir)
