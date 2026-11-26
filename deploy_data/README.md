# deploy_data

Real station data and SARIMA forecasts for the Imarika dashboard and website.
Built by `scripts/forecast_export.py` from `data/all_readings_refresh.csv`.

- AS-OF (newest reading in the network): 2026-11-26T04:44:15Z (2026-11-26 07:44 EAT)
- Stations: 26; active (last reading within 48 h of AS-OF): 12; forecasts produced: 7
- Generated: 2026-11-26T05:39:34Z (2026-11-26 08:39 EAT); used for `reported_today`
- Packages: python 3.12.10, pandas 2.3.3, numpy 2.5.3, statsmodels 0.14.5, scikit-learn 1.9.1

## Times

All timestamps are ISO 8601 UTC with a Z suffix. EAT is Africa/Nairobi time, UTC+3, with no
daylight saving. Observing day D starts at 08:00 EAT (05:00 UTC) on D and is labelled
with date D. Forecasts cover 08:00-00:00 EAT on D (05:00-20:59 UTC on D). The history day
(history_daily.csv) also includes readings up to 00:59 EAT on D+1 (21:59 UTC on D) where a
station sent them. rain_since_morning_mm and reported_today count from 08:00 EAT.

## Stations are off overnight

Since May 2026 no in-county station has reported between 01:00 and 07:59 EAT (22:00-04:59
UTC); the LoRaWAN frame counter restarts each morning, so the stations switch off overnight
(probably power; not confirmed). Consequences in this export:

- Forecasts are published only for 08:00-00:00 EAT (05:00-20:59 UTC). There is no
  overnight forecast and no overnight low (temp_min is empty). The 00:00-00:59 EAT hour is
  left out because two stations never reported in it during the training window.
- Guard: every published hour of day must have at least 5 observed hours in the
  station's 120-day training window. If temperature or rainfall falls short, the station's
  forecast is not published (run log status "insufficient data", forecast_status "not
  available"); if only wind falls short, wind is left empty ("partial").
- Rain that falls while a station is off is NOT counted (the rain counter does not advance).
  In Aug-Dec 2025, when stations reported all night, 13.3% of rain fell 01:00-06:59 EAT
  (22:00-03:59 UTC), so observed and forecast totals are lower than full-day totals.

## Method

- Preprocessing for the forecasts: `preprocess.filter_iot`, `derive_rainfall`, `convert_units`,
  `interpolate_iot`, `hourly_iot` (no `merge_sources`), on the last 125 days of readings.
- Model: SARIMA with the fixed orders in `reports/sarima_best_params.csv`, chosen for station
  101990961244800083 and applied unchanged to every station; seasonal order (1,1,1,24).
  Temperature (1,0,0), wind speed (1,0,1), rainfall (1,0,0). Humidity and pressure are not
  forecast. Fitted per station on the last 120 days of complete hours before AS-OF
  (gaps left as missing), lbfgs, at most 200 iterations. The 95% upper bounds come
  from one Kalman filter pass with the fitted parameters (no re-estimation).
- Horizon: 186 hourly steps from the last complete hour before AS-OF, to the end of
  01:00 EAT after day 7 (21:59 UTC on day 7). Day 1 is the Nairobi date after the AS-OF
  date. Only 08:00-00:00 EAT (05:00-20:59 UTC) is published.
- Rainfall and wind forecasts are clipped at 0. No LSTM output is used anywhere.
- The SARIMA skill was tested one hour ahead only. Over 7 days the forecast largely repeats
  the daily cycle of the last observed days, so daily values change little from day to day
  (see forecast_variation_mm in insights_latest.csv).

## Files

Units: temperature degC; humidity %; pressure hPa (raw Pa / 100); wind direction degrees
(0-359); UV index has no unit; rain mm. Wind speed and gust: unit not verified from the
data. light_intensity: unit unverified (midday values near 110,000, maximum 200,000).

`forecast_hourly.csv`: published hours only (08:00-00:00 EAT, 05:00-20:59 UTC). device_id;
time_utc (start of the hour, ISO UTC with Z); air_temperature; wind_speed; rainfall_mm (mm
in the hour); rainfall_upper (upper 95% prediction bound for the hour, clipped at 0).
Stations with a forecast only.

`forecast_daily.csv`: device_id; date (observing day D, days 1-7 after the AS-OF date);
rain_mm (expected rain in the 16 hours 08:00-00:00 EAT on D, 05:00-20:59 UTC on D);
rain_upper (sum of the 16 hourly upper 95% bounds: a cautious high
estimate, not a daily prediction interval); temp_min (always empty: no overnight data);
temp_max (highest hourly forecast); wind_max (highest hourly mean wind, not a gust; empty
when the wind fit failed); observing_window_utc ("05:00-20:59"); local_window
("08:00-00:00 EAT").

`station_status.csv`: every in-county station after `filter_iot`. device_id; latitude,
longitude (last reading); last_reading_utc (last valid reading); last_seen_utc (last row of
any kind, including rows `filter_iot` drops); active (last valid reading within 48 h of
AS-OF); reported_today (a row has arrived since 08:00 EAT (05:00 UTC) on the generation
date; before 08:00 EAT, since 08:00 EAT the previous day);
reason (empty when active and forecast); coverage_14d (share of the 14 x 24 hours before
AS-OF with at least one raw reading, 0 to 1); area (sub-county from
`assets/kenya_boundaries.geojson` by point-in-polygon, else "Busia County" or "Outside
Busia County"); forecast_status (ok, partial, not available, inactive).

`latest_readings.csv`: every station, values from its last valid reading. device_id;
reading_utc (ISO UTC with Z); air_temperature; air_humidity; wind_speed; rain_today_mm
(history_daily rain_mm of the AS-OF observing day); age_hours (AS-OF minus reading_utc);
light_intensity; uv_index (negative values left empty); barometric_pressure (hPa; below
850 left empty); peak_wind_gust; wind_direction; rain_since_morning_mm (rain counter at the
last reading minus the counter at the first reading since 08:00 EAT (05:00 UTC) on the
AS-OF Nairobi date; empty if the last reading is earlier). Sensor fields are empty when
the reading reports a sensor error or no value.

`history_daily.csv`: every station, the 14 observing days up to the AS-OF day
(day D = 08:00 EAT on D to 00:59 EAT on D+1, 05:00-21:59 UTC on D; the last day ends at
AS-OF). device_id; date (D); rain_mm (rainAccumulation counter at the last reading of the
day minus the counter at the last reading in the 12 h before 08:00 EAT, which bridges the
overnight shutdown, else the first reading of the day; EMPTY when the day has no counter
reading or the counter went down; rain while the station is off is not counted);
temp_min, temp_max, humidity_mean (hourly pipeline values in the day: the lowest
temperature is from the on-hours, not the overnight low); readings_received (hours 0 to
17 of the day with at least one raw reading after `filter_iot`, before interpolation;
the rare readings between 01:00 and 07:59 EAT belong to no observing day).

`insights_latest.csv`: output of `insights.generate_insights` on `forecast_daily.csv`,
plus forecast_variation_mm (max minus min of daily rain_mm over forecast days 1-5
for that station).

`forecast_run_log.csv`: one row per station. status (ok; partial = temperature and
rainfall published but wind failed or was withheld, so wind_speed and wind_max are empty;
error = model failure; insufficient data = withheld by the guard; inactive; not run).
error, insufficient data and not run give forecast_status "not available" and reason
"forecast not available" in station_status.csv;
seconds (fit time); errors; notes (for example a fit that converged only on a retry with
maxiter 1000, same order); package versions and AS-OF.

## Station notes

- 101990961244800061: error; forecast not available
- 101990961244800113: error; forecast not available
- 101990961244800317: error; forecast not available
- 101990961244800336: error; forecast not available
- 101990961244800348: error; forecast not available
