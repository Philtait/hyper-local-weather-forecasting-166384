"""
Imarika — CHIRPS Daily Rainfall Extractor (Final Version)
Downloads Africa daily GeoTIFF files from UCSB, extracts values
at 26 Busia station coordinates. Parallel downloads, fully resumable.
"""

import os
import re
import gzip
import json
import csv
import time
import shutil
import requests
import numpy as np
import rasterio
from datetime import date, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from tqdm import tqdm

# ── Config ────────────────────────────────────────────────────
START_DATE    = date(2020, 1, 1)
END_DATE      = date.today()
STATIONS_FILE = "data/busia_stations.json"
OUT_DIR       = "data/chirps"
TEMP_DIR      = "data/chirps/temp"
DONE_DIR      = "data/chirps/done"      # extracted CSVs per day
CSV_OUT       = "data/chirps_busia.csv"
WORKERS       = 4                        # parallel downloads

BASE_URL = (
    "https://data.chc.ucsb.edu/products/CHIRPS-2.0/"
    "africa_daily/tifs/p05/{year}/"
    "chirps-v2.0.{year}.{month:02d}.{day:02d}.tif.gz"
)

os.makedirs(TEMP_DIR, exist_ok=True)
os.makedirs(DONE_DIR, exist_ok=True)

# ── Load stations ─────────────────────────────────────────────
with open(STATIONS_FILE) as f:
    all_stations = json.load(f)

stations = [
    s for s in all_stations
    if s["device_id"] != "test-spatial-check-device"
]

# ── Build date list ───────────────────────────────────────────
all_dates = [
    START_DATE + timedelta(days=i)
    for i in range((END_DATE - START_DATE).days + 1)
]

# Resumable — skip dates already extracted
pending_dates = [
    d for d in all_dates
    if not os.path.exists(os.path.join(DONE_DIR, f"{d}.csv"))
]

print(f"Stations      : {len(stations)}")
print(f"Total days    : {len(all_dates):,}")
print(f"Already done  : {len(all_dates) - len(pending_dates):,}")
print(f"Still needed  : {len(pending_dates):,}")
print(f"Parallel workers: {WORKERS}")

if len(pending_dates) == 0:
    print("\nAll days already extracted — building final CSV")
else:
    est_min = len(pending_dates) * 3.5 / WORKERS / 60
    print(f"Estimated time: ~{est_min:.0f} minutes")

print()

# ── Find nearest station ──────────────────────────────────────
def nearest_station(grid_lat, grid_lon):
    best, best_dist = None, 999
    for s in stations:
        dist = ((s["lat"] - grid_lat)**2 + (s["lon"] - grid_lon)**2)**0.5
        if dist < best_dist:
            best_dist = dist
            best = s
    return best if best_dist <= 0.05 else None

# ── Process one day ───────────────────────────────────────────
def process_day(target_date):
    """Download, extract values, save per-day CSV. Returns (date, n_rows, error)."""
    date_str  = str(target_date)
    done_path = os.path.join(DONE_DIR, f"{date_str}.csv")
    gz_path   = os.path.join(TEMP_DIR, f"chirps_{date_str}.tif.gz")
    tif_path  = os.path.join(TEMP_DIR, f"chirps_{date_str}.tif")

    # Skip if already done
    if os.path.exists(done_path):
        return date_str, -1, None

    url = BASE_URL.format(
        year=target_date.year,
        month=target_date.month,
        day=target_date.day
    )

    # Download
    for attempt in range(3):
        try:
            r = requests.get(url, timeout=60, stream=True)
            if r.status_code == 404:
                return date_str, 0, "404"
            r.raise_for_status()
            with open(gz_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=65536):
                    f.write(chunk)
            break
        except Exception as e:
            if attempt == 2:
                return date_str, 0, str(e)
            time.sleep(2 ** attempt)

    # Decompress
    try:
        with gzip.open(gz_path, "rb") as f_in:
            with open(tif_path, "wb") as f_out:
                shutil.copyfileobj(f_in, f_out)
        os.remove(gz_path)
    except Exception as e:
        for p in [gz_path, tif_path]:
            if os.path.exists(p):
                os.remove(p)
        return date_str, 0, f"decompress: {e}"

    # Extract station values
    rows = []
    try:
        with rasterio.open(tif_path) as src:
            for s in stations:
                try:
                    row_idx, col_idx = src.index(s["lon"], s["lat"])
                    window = rasterio.windows.Window(col_idx, row_idx, 1, 1)
                    val    = float(src.read(1, window=window)[0][0])
                    if val < -900:
                        val = None
                    else:
                        val = round(val, 2)
                except Exception:
                    val = None

                rows.append({
                    "date":        date_str,
                    "device_id":   s["device_id"],
                    "latitude":    s["lat"],
                    "longitude":   s["lon"],
                    "rainfall_mm": val,
                })
    except Exception as e:
        os.remove(tif_path)
        return date_str, 0, f"rasterio: {e}"

    os.remove(tif_path)

    # Save per-day CSV
    with open(done_path, "w", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=["date","device_id","latitude","longitude","rainfall_mm"]
        )
        writer.writeheader()
        writer.writerows(rows)

    return date_str, len(rows), None

# ── Parallel extraction ───────────────────────────────────────
CSV_FIELDS = ["date","device_id","latitude","longitude","rainfall_mm"]

if pending_dates:
    print("="*60)
    print("Extracting CHIRPS data")
    print("="*60)

    failed = []

    with ThreadPoolExecutor(max_workers=WORKERS) as executor:
        futures = {
            executor.submit(process_day, d): d
            for d in pending_dates
        }

        with tqdm(total=len(pending_dates), unit="day",
                  desc="CHIRPS") as pbar:
            for future in as_completed(futures):
                date_str, n_rows, error = future.result()
                if error and error != "404":
                    failed.append((date_str, error))
                pbar.update(1)

    if failed:
        print(f"\n{len(failed)} days failed:")
        for d, e in failed[:10]:
            print(f"  {d}: {e}")
        print("Re-run script to retry failed days")

# ── Combine all per-day CSVs into final CSV ───────────────────
print(f"\nBuilding final CSV → {CSV_OUT}")

done_files = sorted([
    f for f in os.listdir(DONE_DIR)
    if f.endswith(".csv")
])

total_rows = 0
with open(CSV_OUT, "w", newline="", encoding="utf-8") as out:
    writer = csv.DictWriter(out, fieldnames=CSV_FIELDS)
    writer.writeheader()

    for fname in tqdm(done_files, desc="Combining", unit="day"):
        fpath = os.path.join(DONE_DIR, fname)
        with open(fpath, newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                writer.writerow(row)
                total_rows += 1

print(f"\n{'='*60}")
print(f"DONE")
print(f"Days extracted : {len(done_files):,}")
print(f"Total rows     : {total_rows:,}")
print(f"Expected rows  : ~{len(all_dates) * len(stations):,}")
print(f"CSV → {CSV_OUT}")
print("="*60)