"""
Imarika — Open-Meteo Historical Extractor (Final)
Pulls hourly reanalysis data for all 26 real Busia County stations.
Variables chosen to match IoT sensorData fields exactly.
No API key required.
"""

import requests
import json
import csv
import os
import time
from datetime import date
from dotenv import load_dotenv

load_dotenv()

# ── Config ────────────────────────────────────────────────────
START_DATE = "2025-08-01"
END_DATE   = date.today().isoformat()
OUT_DIR    = "data/openmeteo"
CSV_OUT    = "data/openmeteo_busia.csv"
COORDS_FILE = "data/busia_stations.json"

os.makedirs(OUT_DIR, exist_ok=True)

# ── Load Busia stations, drop test device ─────────────────────
with open(COORDS_FILE) as f:
    all_stations = json.load(f)

stations = [
    s for s in all_stations
    if s["device_id"] != "test-spatial-check-device"
]

print(f"Stations loaded: {len(all_stations)} Busia → {len(stations)} after dropping test device")

# ── Variables — matched to your sensorData fields ─────────────
#
#  Open-Meteo field          IoT sensorData field       Notes
#  ─────────────────────────────────────────────────────────────
#  temperature_2m          → airTemperature             °C
#  relative_humidity_2m    → airHumidity                %
#  surface_pressure        → barometricPressure         hPa (IoT is Pa, ÷100)
#  wind_speed_10m          → windSpeed                  m/s
#  wind_gusts_10m          → peakWindGust               m/s
#  wind_direction_10m      → windDirectionSensor        degrees
#  precipitation           → rainfall per hour          mm (IoT needs differencing)
#  uv_index                → uvIndex                    0-11 scale
#
VARIABLES = ",".join([
    "temperature_2m",
    "relative_humidity_2m",
    "surface_pressure",
    "wind_speed_10m",
    "wind_gusts_10m",
    "wind_direction_10m",
    "precipitation",
    "uv_index",
])

BASE_URL = "https://archive-api.open-meteo.com/v1/archive"

CSV_FIELDS = [
    "device_id", "latitude", "longitude", "altitude",
    "timestamp",
    "temperature_2m",
    "relative_humidity_2m",
    "surface_pressure",
    "wind_speed_10m",
    "wind_gusts_10m",
    "wind_direction_10m",
    "precipitation",
    "uv_index",
]

# ── Fetch one station with retry ──────────────────────────────
def fetch_station(station):
    did  = station["device_id"]
    lat  = station["lat"]
    lon  = station["lon"]
    alt  = station["altitude"]
    save = os.path.join(OUT_DIR, f"{did}.json")

    # Resumable — skip if already saved
    if os.path.exists(save):
        with open(save) as f:
            data = json.load(f)
        n = len(data["hourly"]["time"])
        print(f"  Already saved — {n:,} records. Skipping.")
        return data

    params = {
        "latitude":           lat,
        "longitude":          lon,
        "hourly":             VARIABLES,
        "start_date":         START_DATE,
        "end_date":           END_DATE,
        "timezone":           "Africa/Nairobi",
        "wind_speed_unit":    "ms",
        "precipitation_unit": "mm",
    }

    for attempt in range(3):
        try:
            r = requests.get(BASE_URL, params=params, timeout=120)
            r.raise_for_status()
            data = r.json()

            if "hourly" not in data:
                print(f"  ERROR — unexpected response: {json.dumps(data)[:200]}")
                return None

            with open(save, "w") as f:
                json.dump(data, f)

            n = len(data["hourly"]["time"])
            print(f"  {n:,} hourly records saved → {save}")
            return data

        except requests.exceptions.Timeout:
            wait = 15 * (attempt + 1)
            print(f"  Timeout (attempt {attempt+1}/3) — retrying in {wait}s...")
            time.sleep(wait)

        except Exception as e:
            print(f"  ERROR: {e}")
            return None

    print(f"  FAILED after 3 attempts — {did}")
    return None

# ── Flatten API response to CSV rows ─────────────────────────
def flatten(station, data):
    if not data or "hourly" not in data:
        return []

    h    = data["hourly"]
    n    = len(h["time"])
    rows = []

    var_keys = [k for k in CSV_FIELDS
                if k not in ("device_id","latitude","longitude","altitude","timestamp")]

    for i in range(n):
        row = {
            "device_id": station["device_id"],
            "latitude":  station["lat"],
            "longitude": station["lon"],
            "altitude":  station["altitude"],
            "timestamp": h["time"][i],
        }
        for k in var_keys:
            row[k] = h.get(k, [None] * n)[i]
        rows.append(row)

    return rows

# ── Main ──────────────────────────────────────────────────────
print("=" * 60)
print("Open-Meteo Historical Extractor — Busia County")
print(f"Range : {START_DATE} → {END_DATE}")
print(f"Stations : {len(stations)}")
print(f"Variables: temperature, humidity, pressure, wind,")
print(f"           gusts, direction, precipitation, UV")
print("=" * 60)

all_rows     = []
failed       = []

for i, station in enumerate(stations, 1):
    did = station["device_id"]
    print(f"\n[{i}/{len(stations)}] {did}  "
          f"({station['lat']:.4f}, {station['lon']:.4f})")

    data = fetch_station(station)
    if data:
        rows = flatten(station, data)
        all_rows.extend(rows)
    else:
        failed.append(did)

    time.sleep(0.5)

# ── Write combined CSV ────────────────────────────────────────
print(f"\nWriting CSV → {CSV_OUT}")
with open(CSV_OUT, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
    writer.writeheader()
    writer.writerows(all_rows)

# ── Summary ───────────────────────────────────────────────────
expected_per_station = 9840  # ~13 months hourly
print(f"\n{'='*60}")
print(f"DONE")
print(f"Stations attempted : {len(stations)}")
print(f"Stations failed    : {len(failed)}")
print(f"Total rows written : {len(all_rows):,}")
print(f"Expected per station: ~{expected_per_station:,} hourly records")
print(f"CSV saved → {CSV_OUT}")
if failed:
    print(f"\nFailed devices (re-run script to retry):")
    for d in failed:
        print(f"  {d}")
print("="*60)