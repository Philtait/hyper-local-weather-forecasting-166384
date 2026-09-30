"""
Imarika — NASA POWER Daily Data Extractor
Fetches daily agricultural meteorological data for all 26
Busia County stations from 2020-01-01 to today.

Community: AG (Agriculture)
No API key required.
"""

import requests
import json
import csv
import os
import time
from datetime import date

# ── Config ────────────────────────────────────────────────────
START_DATE    = "20200101"
END_DATE      = date.today().strftime("%Y%m%d")
STATIONS_FILE = "data/busia_stations.json"
OUT_DIR       = "data/nasa_power"
CSV_OUT       = "data/nasa_power_busia.csv"

os.makedirs(OUT_DIR, exist_ok=True)

# ── Parameters ────────────────────────────────────────────────
# T2M              → airTemperature cross-validation
# RH2M             → airHumidity cross-validation
# PRECTOTCORR      → corrected precipitation (third rainfall ref)
# ALLSKY_SFC_SW_DWN→ solar radiation (lightIntensity proxy)
# WS2M             → windSpeed cross-validation
# WD2M             → windDirection cross-validation
PARAMETERS = "T2M,RH2M,PRECTOTCORR,ALLSKY_SFC_SW_DWN,WS2M,WD2M"

BASE_URL = "https://power.larc.nasa.gov/api/temporal/daily/point"

CSV_FIELDS = [
    "date",
    "device_id",
    "latitude",
    "longitude",
    "T2M",               # temperature °C
    "RH2M",              # relative humidity %
    "PRECTOTCORR",       # precipitation mm/day
    "ALLSKY_SFC_SW_DWN", # solar radiation MJ/m²/day
    "WS2M",              # wind speed m/s
    "WD2M",              # wind direction degrees
]

# ── Load Busia stations ───────────────────────────────────────
with open(STATIONS_FILE) as f:
    all_stations = json.load(f)

stations = [
    s for s in all_stations
    if s["device_id"] != "test-spatial-check-device"
]

print(f"Stations  : {len(stations)}")
print(f"Parameters: {PARAMETERS}")
print(f"Date range: {START_DATE} → {END_DATE}")
print(f"API calls : {len(stations)} (one per station, full range per call)\n")

# ── Fetch one station ─────────────────────────────────────────
def fetch_station(station):
    did  = station["device_id"]
    lat  = station["lat"]
    lon  = station["lon"]
    save = os.path.join(OUT_DIR, f"{did}.json")

    # Resumable — skip if already saved
    if os.path.exists(save):
        print(f"  Already saved — loading from disk")
        with open(save) as f:
            return json.load(f)

    params = {
        "parameters": PARAMETERS,
        "community":  "AG",
        "longitude":  lon,
        "latitude":   lat,
        "start":      START_DATE,
        "end":        END_DATE,
        "format":     "JSON",
    }

    for attempt in range(3):
        try:
            r = requests.get(BASE_URL, params=params, timeout=120)

            # Rate limit hit
            if r.status_code == 429:
                wait = 30 * (attempt + 1)
                print(f"  Rate limited — waiting {wait}s...")
                time.sleep(wait)
                continue

            r.raise_for_status()
            data = r.json()

            # Check for API-level error
            if "properties" not in data:
                print(f"  Unexpected response: {json.dumps(data)[:200]}")
                return None

            # Save raw response
            with open(save, "w") as f:
                json.dump(data, f)

            n = len(data["properties"]["parameter"]["T2M"])
            print(f"  {n:,} daily records saved → {save}")
            return data

        except requests.exceptions.Timeout:
            wait = 20 * (attempt + 1)
            print(f"  Timeout (attempt {attempt+1}/3) — retrying in {wait}s...")
            time.sleep(wait)

        except Exception as e:
            print(f"  ERROR: {e}")
            return None

    print(f"  FAILED after 3 attempts")
    return None

# ── Flatten response to CSV rows ──────────────────────────────
def flatten(station, data):
    if not data or "properties" not in data:
        return []

    params = data["properties"]["parameter"]
    # All parameter dicts have the same date keys
    dates  = sorted(params["T2M"].keys())
    rows   = []

    for d in dates:
        row = {
            "date":      d,          # format: YYYYMMDD
            "device_id": station["device_id"],
            "latitude":  station["lat"],
            "longitude": station["lon"],
        }
        for p in ["T2M","RH2M","PRECTOTCORR",
                  "ALLSKY_SFC_SW_DWN","WS2M","WD2M"]:
            val = params[p].get(d)
            # NASA POWER uses -999 as fill value for missing data
            if val is not None and val <= -990:
                val = None
            row[p] = val
        rows.append(row)

    return rows

# ── Main ──────────────────────────────────────────────────────
print("=" * 60)
print("NASA POWER Daily Extractor — Busia County")
print("=" * 60)

all_rows = []
failed   = []

for i, station in enumerate(stations, 1):
    did = station["device_id"]
    print(f"\n[{i}/{len(stations)}] {did}")
    print(f"  Coords: ({station['lat']:.4f}, {station['lon']:.4f})")

    data = fetch_station(station)

    if data:
        rows = flatten(station, data)
        all_rows.extend(rows)
        print(f"  Flattened to {len(rows):,} rows")
    else:
        failed.append(did)

    # Polite delay between stations — avoid rate limiting
    time.sleep(2)

# ── Write CSV ─────────────────────────────────────────────────
print(f"\nWriting CSV → {CSV_OUT}")
with open(CSV_OUT, "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
    writer.writeheader()
    writer.writerows(all_rows)

# ── Summary ───────────────────────────────────────────────────
print(f"\n{'='*60}")
print(f"DONE")
print(f"Stations attempted : {len(stations)}")
print(f"Stations failed    : {len(failed)}")
print(f"Total rows written : {len(all_rows):,}")
print(f"Date format in CSV : YYYYMMDD (convert to date during preprocessing)")
print(f"Missing data flag  : None (originally -999 in NASA POWER)")
print(f"CSV saved → {CSV_OUT}")
if failed:
    print(f"\nFailed — re-run script to retry:")
    for d in failed:
        print(f"  {d}")
print("="*60)