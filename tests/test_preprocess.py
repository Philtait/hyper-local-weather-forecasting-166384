"""Behavioral checks for irregular gaps, reset boundaries and timezone joins."""

import tempfile
import unittest
from pathlib import Path

import pandas as pd  # pyright: ignore[reportMissingModuleSource]

from preprocess import (
    IOT_WEATHER, add_time_features, derive_rainfall, hourly_iot,
    interpolate_iot, interpolate_short_gaps, merge_sources, read_source,
)


class PreprocessingTests(unittest.TestCase):
    def test_irregular_interpolation_uses_elapsed_time_and_strict_six_hour_limit(self):
        times = pd.to_datetime([
            "2026-01-01 00:00Z", "2026-01-01 01:00Z", "2026-01-01 01:30Z",
            "2026-01-01 03:00Z", "2026-01-01 04:00Z", "2026-01-01 09:00Z",
            "2026-01-01 10:00Z", "2026-01-01 16:00Z", "2026-01-01 17:00Z",
        ])
        series = pd.Series([None, 10, None, 30, None, 90, None, 160, None], index=times)
        result = interpolate_short_gaps(series)
        self.assertEqual(result.iloc[2], 15)  # elapsed-time, not equal-row interpolation
        self.assertTrue(result.iloc[[0, 4, 6, 8]].isna().all())

    def test_rain_resets_and_missing_counter_pairs(self):
        frame = pd.DataFrame({
            "device_id": ["A"] * 6 + ["B"] * 2,
            "received_at": list(pd.date_range("2026-01-01", periods=6, freq="h", tz="UTC"))
                           + list(pd.date_range("2026-01-01", periods=2, freq="h", tz="UTC")),
            "rain_accumulation": [100, 102, 1, 3, None, 5, 20, 23],
        })
        result = derive_rainfall(frame)
        self.assertEqual(result.rain_segment.tolist(), [0, 0, 1, 1, 1, 1, 0, 0])
        self.assertEqual(result.rainfall_mm.iloc[:4].tolist(), [0, 2, 0, 2])
        self.assertTrue(result.rainfall_mm.iloc[4:6].isna().all())
        self.assertEqual(result.rainfall_mm.iloc[6:].tolist(), [0, 3])

    def test_interpolation_does_not_cross_stations_or_rain_segments(self):
        frame = pd.DataFrame({
            "device_id": ["A"] * 4 + ["B"] * 2,
            "received_at": list(pd.date_range("2026-01-01", periods=4, freq="h", tz="UTC"))
                           + list(pd.date_range("2026-01-01", periods=2, freq="h", tz="UTC")),
            "rain_segment": [0, 0, 1, 1, 0, 0],
        })
        for column in IOT_WEATHER + ["rainfall_mm"]:
            frame[column] = [10, None, 30, 40, None, 100]
        result = interpolate_iot(frame)
        self.assertEqual(result.air_temperature.iloc[1], 20)
        self.assertTrue(pd.isna(result.rain_accumulation.iloc[1]))
        self.assertTrue(pd.isna(result.rainfall_mm.iloc[1]))
        self.assertTrue(pd.isna(result.air_temperature.iloc[4]))

    def test_hourly_rain_is_summed_and_empty_hours_remain_missing(self):
        clean = pd.DataFrame({
            "device_id": ["A"] * 3,
            "received_at": pd.to_datetime(["2026-01-01 00:10Z", "2026-01-01 00:40Z", "2026-01-01 02:00Z"]),
            "rainfall_mm": [1.0, 2.0, None], "rain_reset": [False] * 3,
            "sensor_error": [0] * 3,
        })
        for column in IOT_WEATHER + ["latitude", "longitude", "altitude"]:
            clean[column] = [10.0, 20.0, 30.0]
        result = hourly_iot(clean)
        self.assertEqual(len(result), 3)
        self.assertEqual(result.iot_air_temperature.iloc[0], 15)
        self.assertEqual(result.iot_rainfall_mm.iloc[0], 3)
        self.assertTrue(result.iot_rainfall_mm.iloc[1:].isna().all())
        self.assertEqual(result.iot_reading_count.tolist(), [2, 0, 1])

    def test_daily_join_uses_nairobi_date_and_preserves_unmatched_rows(self):
        hours = pd.to_datetime(["2026-02-28 20:00Z", "2026-02-28 21:00Z"])
        hourly = pd.DataFrame({"device_id": ["A", "A"], "hour": hours})
        meteo = pd.DataFrame({"device_id": ["A"], "hour": hours[:1], "openmeteo_temperature_2m": [22]})
        nasa = pd.DataFrame({"device_id": ["A"], "date": pd.to_datetime(["2026-03-01"]), "nasa_T2M": [23]})
        chirps = pd.DataFrame({"device_id": ["A"], "date": pd.to_datetime(["2026-02-28"]), "chirps_rainfall_mm": [5]})
        result = merge_sources(hourly, meteo, nasa, chirps)
        self.assertEqual(len(result), 2)
        self.assertEqual(result.hour_of_day.tolist(), [23, 0])
        self.assertEqual(result.is_rainy_season.tolist(), [0, 1])
        self.assertEqual(result.nasa_T2M.iloc[1], 23)
        self.assertTrue(pd.isna(result.nasa_T2M.iloc[0]))
        self.assertTrue(pd.isna(result.openmeteo_temperature_2m.iloc[1]))
        with self.assertRaises(pd.errors.MergeError):
            merge_sources(hourly, meteo, pd.concat([nasa, nasa]), chirps)

    def test_device_quotes_are_removed_without_losing_identifier_precision(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.csv"
            pd.DataFrame({"device_id": [" '101990961244800102' ", '"101990961244800222"']}).to_csv(path, index=False)
            result = read_source(path)
        self.assertEqual(result.device_id.tolist(), ["101990961244800102", "101990961244800222"])

    def test_leap_year_features_are_finite_and_cyclical(self):
        frame = pd.DataFrame({"time": pd.to_datetime(["2024-12-31 21:00Z", "2024-02-29 09:00Z"])})
        result = add_time_features(frame, "time")
        self.assertEqual(result.day_of_year.tolist(), [1, 60])
        self.assertEqual(result.hour_of_day.tolist(), [0, 12])
        for row in result.itertuples():
            self.assertAlmostEqual(row.sin_hour ** 2 + row.cos_hour ** 2, 1)
            self.assertAlmostEqual(row.sin_doy ** 2 + row.cos_doy ** 2, 1)


if __name__ == "__main__":
    unittest.main()
