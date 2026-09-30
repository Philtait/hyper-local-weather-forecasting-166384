# Busia weather data quality audit

Generated: 2026-09-30T19:59:33.065931+00:00

## Summary

- Retained **717,471 of 764,309 IoT readings** across **26 stations**; excluded 46,838 readings.

- Sensor flags: **0 nonzero**, **36,384 missing (5.07%)**. Zero flags do not guarantee complete or plausible measurements.

- IoT temperature: **160 outside 14–36°C** (0.0237% of observed temperatures); observed range **11.1–36.1°C**.

- Cumulative rain: **2 downward-step reset candidates**.

- Screened hourly IoT/Open-Meteo Pearson r: **0.875–0.912**, station median **0.899**.

- openmeteo `uv_index` is entirely missing at all stations.

- openmeteo `wind_gusts_10m` is entirely missing at `101990961244800083`, `101990961244800302`.

- chirps: 806 absent station/time slots across 26 stations; 31 distinct missing times from 2021-12-01 00:00:00 through 2021-12-31 00:00:00. See `missing_reference_time_slots.csv` for individual missing times.

## Scope and methods

- IoT row-level inclusive bounding box: latitude 0.1–0.7, longitude 34.1–34.6; exclude `test-spatial-check-device`. Missing/invalid coordinates are excluded. A device may have both retained and excluded rows. Reference-source completeness uses each supplied CSV in full.

- Identifiers are read as strings. No source records are edited or imputed. Counts and missingness use all retained IoT rows before temperature screening.

- IoT `received_at` is UTC; Open-Meteo naive timestamps are Africa/Nairobi (UTC+3), confirmed by the fetch script and archived API metadata, and are converted to UTC. Daily NASA/CHIRPS values retain their date labels. IoT coverage dates and active-day counts use Nairobi calendar days; timestamp columns use UTC.

- Missingness includes CSV nulls, unparseable numeric values, infinities, NASA fill values ≤−990 and CHIRPS fill values <−900. These categories are itemized in `numeric_conversion.csv`. Missing timestamps/rows are separate from missing cells.

- 14–36°C is an inclusive expected-range screen, not proof that every value outside it is a faulty sensor. NASA temperatures are daily means, whereas IoT/Open-Meteo are subdaily.

## 1. Record counts and date coverage

| dataset | records | stations | first_timestamp | last_timestamp | invalid_timestamps | duplicate_station_times_extra | duplicate_record_ids_extra |
| --- | --- | --- | --- | --- | --- | --- | --- |
| iot | 717,471 | 26 | 2025-08-09 21:15:13.979000+00:00 | 2026-09-14 17:45:02.416000+00:00 | 0 | 0 | 0.000 |
| openmeteo | 255,840 | 26 | 2025-07-31 21:00:00+00:00 | 2026-09-14 20:00:00+00:00 | 0 | 0 | — |
| nasa_power | 63,674 | 26 | 2020-01-01 00:00:00 | 2026-09-14 00:00:00 | 0 | 0 | — |
| chirps | 62,504 | 26 | 2020-01-01 00:00:00 | 2026-08-31 00:00:00 | 0 | 0 | — |

### IoT station coverage (Nairobi dates)

Active days contain at least one retained row, even if weather values are missing. Missing days are counted only between each station's first and last retained dates. No fixed IoT reporting interval is assumed.

| device_id | records | first_date | last_date | active_days | days_without_records_within_span | median_interval_minutes | max_gap_hours |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 101990961244800001 | 30,739 | 2025-08-10 | 2026-09-07 | 345 | 49 | 15.106 | 376.247 |
| 101990961244800005 | 25,356 | 2025-08-12 | 2026-09-04 | 310 | 79 | 15.106 | 881.375 |
| 101990961244800061 | 31,001 | 2025-08-10 | 2026-09-06 | 354 | 39 | 15.107 | 325.357 |
| 101990961244800083 | 33,783 | 2025-08-10 | 2026-09-14 | 357 | 44 | 15.106 | 325.951 |
| 101990961244800101 | 31,471 | 2025-08-10 | 2026-09-14 | 364 | 37 | 15.106 | 326.410 |
| 101990961244800102 | 31,858 | 2025-08-10 | 2026-09-08 | 344 | 51 | 15.106 | 396.916 |
| 101990961244800113 | 31,984 | 2025-08-10 | 2026-09-08 | 359 | 36 | 15.107 | 325.478 |
| 101990961244800131 | 16,754 | 2025-11-30 | 2026-09-14 | 218 | 71 | 15.106 | 396.465 |
| 101990961244800184 | 30,932 | 2025-08-10 | 2026-09-07 | 329 | 65 | 15.106 | 375.846 |
| 101990961244800201 | 26,166 | 2025-08-10 | 2026-09-08 | 310 | 85 | 15.106 | 402.386 |
| 101990961244800207 | 30,144 | 2025-08-10 | 2026-09-14 | 358 | 43 | 15.106 | 328.645 |
| 101990961244800222 | 16,270 | 2025-08-10 | 2026-09-07 | 255 | 139 | 15.106 | 354.844 |
| 101990961244800246 | 26,733 | 2025-08-10 | 2026-09-07 | 292 | 102 | 15.106 | 1,500.855 |
| 101990961244800270 | 19,418 | 2025-08-10 | 2026-09-14 | 269 | 132 | 15.106 | 371.490 |
| 101990961244800281 | 24,426 | 2025-08-10 | 2026-09-07 | 324 | 70 | 15.105 | 661.539 |
| 101990961244800302 | 28,972 | 2025-08-10 | 2026-09-14 | 346 | 55 | 15.106 | 325.764 |
| 101990961244800306 | 31,885 | 2025-08-10 | 2026-09-07 | 359 | 35 | 15.106 | 323.975 |
| 101990961244800317 | 33,975 | 2025-08-10 | 2026-09-06 | 352 | 41 | 15.106 | 325.886 |
| 101990961244800336 | 28,438 | 2025-08-10 | 2026-09-06 | 341 | 52 | 15.106 | 327.374 |
| 101990961244800342 | 26,685 | 2025-08-10 | 2026-09-14 | 306 | 95 | 15.106 | 376.600 |
| 101990961244800348 | 31,773 | 2025-08-10 | 2026-09-06 | 348 | 45 | 15.106 | 326.575 |
| 101990961244800361 | 16,942 | 2025-08-10 | 2026-08-05 | 223 | 138 | 15.107 | 2,665.104 |
| 101990961244800375 | 21,138 | 2025-08-10 | 2026-08-28 | 314 | 70 | 15.106 | 399.877 |
| 101990961244800389 | 31,387 | 2025-08-10 | 2026-09-05 | 342 | 50 | 15.106 | 325.707 |
| 101990961244800393 | 28,922 | 2025-08-10 | 2026-09-08 | 356 | 39 | 15.106 | 375.262 |
| 101990961244800395 | 30,319 | 2025-08-10 | 2026-09-14 | 351 | 50 | 15.106 | 326.169 |

Full timestamps, gap counts, duplicate counts and reference-source coverage are in `station_coverage.csv`. Filter outcomes, including partially excluded stations, are in `filter_counts.csv`.

### Missing reference time slots within station spans

| dataset | missing_time_slots | max_missing_slots_per_station |
| --- | --- | --- |
| chirps | 806.000 | 31.000 |
| nasa_power | 0.000 | 0.000 |
| openmeteo | 0.000 | 0.000 |

- chirps: 806 absent station/time slots across 26 stations; 31 distinct missing times from 2021-12-01 00:00:00 through 2021-12-31 00:00:00. See `missing_reference_time_slots.csv` for individual missing times.

## 2. Sensor error flags

| sensor_error | records | pct | meaning |
| --- | --- | --- | --- |
| 0.000 | 681,087 | 94.929 | zero (no error reported) |
| — | 36,384 | 5.071 | missing/unknown |

The extractor maps `sensorData.err` to `sensor_error`. Without a device error-code dictionary, only zero/nonzero/missing semantics are used; missing flags are not treated as healthy readings.

- 41,888 rows have all ten IoT weather variables missing.

- 10,889 zero-flag rows have at least one missing weather variable, including 5,677 with no temperature.

- 0 missing-flag rows contain a temperature.

| device_id | records | zero_flags | nonzero_flags | missing_flags | missing_flag_pct | all_weather_missing |
| --- | --- | --- | --- | --- | --- | --- |
| 101990961244800001 | 30,739 | 29,606 | 0 | 1,133 | 3.686 | 1,350 |
| 101990961244800005 | 25,356 | 24,804 | 0 | 552 | 2.177 | 763 |
| 101990961244800061 | 31,001 | 29,979 | 0 | 1,022 | 3.297 | 1,261 |
| 101990961244800083 | 33,783 | 31,372 | 0 | 2,411 | 7.137 | 2,654 |
| 101990961244800101 | 31,471 | 29,418 | 0 | 2,053 | 6.523 | 2,295 |
| 101990961244800102 | 31,858 | 29,249 | 0 | 2,609 | 8.189 | 2,835 |
| 101990961244800113 | 31,984 | 29,795 | 0 | 2,189 | 6.844 | 2,429 |
| 101990961244800131 | 16,754 | 15,649 | 0 | 1,105 | 6.595 | 1,291 |
| 101990961244800184 | 30,932 | 28,454 | 0 | 2,478 | 8.011 | 2,689 |
| 101990961244800201 | 26,166 | 25,452 | 0 | 714 | 2.729 | 902 |
| 101990961244800207 | 30,144 | 28,350 | 0 | 1,794 | 5.951 | 2,018 |
| 101990961244800222 | 16,270 | 15,627 | 0 | 643 | 3.952 | 801 |
| 101990961244800246 | 26,733 | 26,037 | 0 | 696 | 2.604 | 866 |
| 101990961244800270 | 19,418 | 18,497 | 0 | 921 | 4.743 | 1,069 |
| 101990961244800281 | 24,426 | 23,283 | 0 | 1,143 | 4.679 | 1,340 |
| 101990961244800302 | 28,972 | 28,124 | 0 | 848 | 2.927 | 1,080 |
| 101990961244800306 | 31,885 | 28,931 | 0 | 2,954 | 9.265 | 3,192 |
| 101990961244800317 | 33,975 | 32,378 | 0 | 1,597 | 4.701 | 1,829 |
| 101990961244800336 | 28,438 | 26,805 | 0 | 1,633 | 5.742 | 1,850 |
| 101990961244800342 | 26,685 | 25,975 | 0 | 710 | 2.661 | 917 |
| 101990961244800348 | 31,773 | 30,827 | 0 | 946 | 2.977 | 1,167 |
| 101990961244800361 | 16,942 | 15,755 | 0 | 1,187 | 7.006 | 1,391 |
| 101990961244800375 | 21,138 | 20,475 | 0 | 663 | 3.137 | 836 |
| 101990961244800389 | 31,387 | 29,807 | 0 | 1,580 | 5.034 | 1,797 |
| 101990961244800393 | 28,922 | 27,551 | 0 | 1,371 | 4.740 | 1,606 |
| 101990961244800395 | 30,319 | 28,887 | 0 | 1,432 | 4.723 | 1,660 |

## 3. Missing values per weather variable

| dataset | variable | records | missing_or_invalid | missing_pct |
| --- | --- | --- | --- | --- |
| iot | air_temperature | 717,471 | 42,061 | 5.862 |
| iot | air_humidity | 717,471 | 42,061 | 5.862 |
| iot | barometric_pressure | 717,471 | 47,134 | 6.569 |
| iot | wind_speed | 717,471 | 42,061 | 5.862 |
| iot | peak_wind_gust | 717,471 | 41,888 | 5.838 |
| iot | wind_direction | 717,471 | 47,134 | 6.569 |
| iot | rain_accumulation | 717,471 | 41,888 | 5.838 |
| iot | rain_gauge | 717,471 | 47,134 | 6.569 |
| iot | light_intensity | 717,471 | 42,061 | 5.862 |
| iot | uv_index | 717,471 | 42,061 | 5.862 |
| openmeteo | temperature_2m | 255,840 | 0 | 0.000 |
| openmeteo | relative_humidity_2m | 255,840 | 0 | 0.000 |
| openmeteo | surface_pressure | 255,840 | 0 | 0.000 |
| openmeteo | wind_speed_10m | 255,840 | 0 | 0.000 |
| openmeteo | wind_gusts_10m | 255,840 | 19,680 | 7.692 |
| openmeteo | wind_direction_10m | 255,840 | 0 | 0.000 |
| openmeteo | precipitation | 255,840 | 0 | 0.000 |
| openmeteo | uv_index | 255,840 | 255,840 | 100.000 |
| nasa_power | T2M | 63,674 | 78 | 0.122 |
| nasa_power | RH2M | 63,674 | 78 | 0.122 |
| nasa_power | PRECTOTCORR | 63,674 | 78 | 0.122 |
| nasa_power | ALLSKY_SFC_SW_DWN | 63,674 | 130 | 0.204 |
| nasa_power | WS2M | 63,674 | 78 | 0.122 |
| nasa_power | WD2M | 63,674 | 1,170 | 1.837 |
| chirps | rainfall_mm | 62,504 | 0 | 0.000 |

### Missing identifier, location, signal and flag fields

| dataset | variable | records | missing_or_invalid | missing_pct |
| --- | --- | --- | --- | --- |
| iot | dev_eui | 717,471 | 243,996 | 34.008 |
| iot | application_id | 717,471 | 243,996 | 34.008 |
| iot | sensor_error | 717,471 | 36,384 | 5.071 |
| iot | gateway_id | 717,471 | 243,996 | 34.008 |
| iot | rssi | 717,471 | 243,996 | 34.008 |
| iot | snr | 717,471 | 243,996 | 34.008 |

All fields, including those with zero missing values, are in `missing_values.csv`; station-level breakdowns are in `missing_values_by_station.csv`.

## 4. Temperature sanity check

| dataset | records | valid_temperature | missing_temperature | min_c | max_c | mean_c | below_14_c | above_36_c | outside_range | outside_pct_of_valid |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| iot | 717,471 | 675,410 | 42,061 | 11.100 | 36.100 | 22.854 | 157 | 3 | 160 | 0.024 |
| openmeteo | 255,840 | 255,840 | 0 | 14.700 | 35.600 | 22.055 | 0 | 0 | 0 | 0.000 |
| nasa_power | 63,674 | 63,596 | 78 | 17.730 | 27.370 | 21.988 | 0 | 0 | 0 | 0.000 |

### IoT stations with out-of-range temperatures

| device_id | valid_temperature | min_c | max_c | below_14_c | above_36_c | outside_pct_of_valid |
| --- | --- | --- | --- | --- | --- | --- |
| 101990961244800102 | 29,021 | 13.800 | 35.100 | 3 | 0 | 0.010 |
| 101990961244800222 | 15,468 | 14.200 | 36.100 | 0 | 2 | 0.013 |
| 101990961244800306 | 28,672 | 13.900 | 34.800 | 4 | 0 | 0.014 |
| 101990961244800317 | 32,117 | 13.000 | 35.300 | 13 | 0 | 0.040 |
| 101990961244800336 | 26,582 | 13.800 | 34.600 | 4 | 0 | 0.015 |
| 101990961244800348 | 30,598 | 11.100 | 35.300 | 114 | 0 | 0.373 |
| 101990961244800361 | 15,549 | 12.600 | 34.400 | 7 | 0 | 0.045 |
| 101990961244800393 | 27,309 | 13.800 | 35.100 | 4 | 0 | 0.015 |
| 101990961244800395 | 28,652 | 13.900 | 36.100 | 8 | 1 | 0.031 |

All flagged IoT records are in `iot_temperature_outliers.csv`; per-station summaries for all temperature sources are in `temperature_summary.csv`.

## 5. Rain accumulation reset detection

Within each station, sort by UTC timestamp and compare consecutive finite cumulative readings. A decrease greater than 0.000001 mm is a reset candidate. Missing readings may be bridged; the elapsed gap is included. Identical same-time counter readings are collapsed; conflicting same-time readings are omitted and counted. A downward step can indicate a reset, rollover, replacement or bad reading; it does not prove which occurred.

| device_id | record_id | previous_time | time | previous_accumulation | rain_accumulation | drop_mm | gap_hours | previous_sensor_error | sensor_error |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 101990961244800102 | 69f58f3461a78ef3f7f4b2da | 2026-05-01 21:15:47.686000+00:00 | 2026-05-02 05:44:20.644000+00:00 | 1,836.420 | 1,836.166 | 0.254 | 8.476 | 0.000 | 0.000 |
| 101990961244800222 | 69a1322bea3d23d3be1509f8 | 2026-02-26 17:52:53.120000+00:00 | 2026-02-27 05:56:59.561000+00:00 | 1,069.848 | 1,069.594 | 0.254 | 12.068 | 0.000 | 0.000 |

Observed drops range from 0.254 to 0.254 mm. 0 candidates end at zero; small downward steps alone are not convincing evidence of a full counter reset.

Do not use negative cumulative differences as rainfall or interpret a downward-step magnitude as rainfall. Mark reset-crossing intervals unknown unless device reset/rollover semantics are verified. Counters already above zero at the beginning of coverage do not establish rainfall before the first observation.

## 6. IoT versus Open-Meteo temperature correlation

**Method:** collapse duplicate station/observation times by mean, average IoT temperature in UTC clock-hour bins `[hour, hour+1h)`, and inner-join the corresponding Open-Meteo hour by exact device ID and UTC hour. No interpolation or nearest-time matching. Each matched hour has equal weight, with at least one IoT observation; Pearson r requires at least three matched hours and nonconstant series. The IoT timestamp is gateway receipt time; sensor acquisition latency is not known. Open-Meteo hourly temperature is compared to the within-hour IoT mean, so their temporal support is not identical.

**Raw:** all finite temperatures, regardless of flag/range. **Screened:** only IoT temperatures in 14–36°C with `sensor_error == 0`. The reference retains all finite temperatures. Bias is IoT minus Open-Meteo in °C. Correlation reflects agreement including the diurnal cycle, and is not a calibration or anomaly-correlation test.

| device_id | raw_matched_hours | raw_pearson_r | screened_matched_hours | screened_pearson_r | screened_bias_c | screened_mae_c | screened_rmse_c |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 101990961244800001 | 6,429 | 0.903 | 6,429 | 0.903 | 0.342 | 1.498 | 1.957 |
| 101990961244800005 | 5,198 | 0.887 | 5,198 | 0.887 | -0.022 | 1.521 | 2.024 |
| 101990961244800061 | 7,014 | 0.902 | 7,014 | 0.902 | 0.105 | 1.281 | 1.753 |
| 101990961244800083 | 6,802 | 0.899 | 6,802 | 0.899 | 0.284 | 1.360 | 1.822 |
| 101990961244800101 | 6,747 | 0.910 | 6,747 | 0.910 | 0.260 | 1.395 | 1.847 |
| 101990961244800102 | 6,618 | 0.899 | 6,618 | 0.899 | -0.075 | 1.523 | 1.970 |
| 101990961244800113 | 6,882 | 0.898 | 6,882 | 0.898 | 0.105 | 1.388 | 1.852 |
| 101990961244800131 | 3,231 | 0.886 | 3,231 | 0.886 | -0.129 | 1.643 | 2.116 |
| 101990961244800184 | 6,355 | 0.899 | 6,355 | 0.899 | 0.238 | 1.363 | 1.829 |
| 101990961244800201 | 5,730 | 0.912 | 5,730 | 0.912 | -0.095 | 1.343 | 1.822 |
| 101990961244800207 | 6,440 | 0.910 | 6,440 | 0.910 | -0.146 | 1.360 | 1.842 |
| 101990961244800222 | 3,499 | 0.876 | 3,499 | 0.876 | 0.191 | 1.763 | 2.273 |
| 101990961244800246 | 5,738 | 0.879 | 5,738 | 0.879 | 0.157 | 1.508 | 2.069 |
| 101990961244800270 | 4,413 | 0.896 | 4,413 | 0.896 | 0.028 | 1.559 | 2.045 |
| 101990961244800281 | 4,694 | 0.875 | 4,694 | 0.875 | 0.330 | 1.511 | 1.987 |
| 101990961244800302 | 5,950 | 0.889 | 5,950 | 0.889 | -0.199 | 1.504 | 2.020 |
| 101990961244800306 | 6,583 | 0.904 | 6,583 | 0.904 | -0.253 | 1.474 | 1.935 |
| 101990961244800317 | 6,734 | 0.896 | 6,732 | 0.896 | -0.123 | 1.540 | 2.014 |
| 101990961244800336 | 6,125 | 0.901 | 6,125 | 0.901 | -0.256 | 1.511 | 1.989 |
| 101990961244800342 | 5,686 | 0.907 | 5,686 | 0.907 | -0.349 | 1.349 | 1.840 |
| 101990961244800348 | 6,280 | 0.892 | 6,261 | 0.894 | -0.288 | 1.633 | 2.127 |
| 101990961244800361 | 3,874 | 0.905 | 3,873 | 0.905 | -0.058 | 1.412 | 1.879 |
| 101990961244800375 | 4,434 | 0.875 | 4,434 | 0.875 | -0.262 | 1.559 | 2.128 |
| 101990961244800389 | 6,052 | 0.900 | 6,052 | 0.900 | 0.242 | 1.564 | 2.019 |
| 101990961244800393 | 6,398 | 0.906 | 6,398 | 0.906 | -0.403 | 1.450 | 1.937 |
| 101990961244800395 | 6,148 | 0.887 | 6,148 | 0.887 | -0.172 | 1.742 | 2.235 |

Matching coverage, raw/screened metrics and first/last matched UTC hours are in `temperature_correlations.csv`.

## Recommended handling

- Keep separate indicators for missing flags, missing measurements and temperature-range violations; a zero error flag is insufficient quality control.

- Investigate low-temperature events against nearby stations and raw payloads before deciding whether to discard genuine weather extremes.

- Segment cumulative-rain series at the detected downward steps before deriving rainfall increments; inspect gaps at these transitions.

- Address missing reference variables and absent daily records before combining sources. Do not fill an entirely missing variable with zero.

- Use station-wise bias and error alongside r when assessing reference agreement; investigate low-correlation stations and longer reporting gaps.

## Reproduction and outputs

Run `python audit_data_quality.py` (requires pandas and numpy). Optional flags: `--data-dir PATH --output-dir PATH`.

Supporting tables:

- `filter_counts.csv`

- `source_summary.csv`

- `station_coverage.csv`

- `missing_values.csv`

- `missing_values_by_station.csv`

- `numeric_conversion.csv`

- `sensor_error_counts.csv`

- `sensor_error_by_station.csv`

- `temperature_summary.csv`

- `iot_temperature_outliers.csv`

- `rain_reset_events.csv`

- `rain_reset_by_station.csv`

- `temperature_correlations.csv`

- `missing_reference_time_slots.csv`
