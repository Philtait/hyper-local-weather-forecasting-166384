"""
Imarika — Full Historical Data Extractor
Pulls all readings from all 31 devices, saves raw JSON per device,
then combines into a single CSV. Resumable — skips already-saved devices.
"""

import requests, json, os, time, csv
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

API_KEY  = os.getenv("ANGA_API_KEY")
BASE     = "https://api.wirelessplanet.co.ke"
HEADERS  = {"X-API-Key": API_KEY, "Accept": "application/json"}

RAW_DIR  = "data/raw"          # one JSON file per device
CSV_PATH = "data/all_readings.csv"

os.makedirs(RAW_DIR, exist_ok=True)

# ── Helpers ───────────────────────────────────────────────────

def get(path, params=None, timeout=60, retries=3):
    """GET with retry and exponential backoff."""
    for attempt in range(retries):
        try:
            r = requests.get(
                BASE + path, headers=HEADERS,
                params=params, timeout=timeout
            )
            if "json" in r.headers.get("Content-Type", ""):
                return r.json()
            print(f"  Non-JSON response: {r.text[:100]}")
            return None
        except requests.exceptions.Timeout:
            wait = 2 ** attempt
            print(f"  Timeout on attempt {attempt+1}/{retries} — retrying in {wait}s...")
            time.sleep(wait)
        except Exception as e:
            print(f"  Error: {e}")
            return None
    print(f"  Failed after {retries} attempts: {path}")
    return None

def fetch_all_pages(device_id):
    """Pull every page of readings for one device. Returns list of records."""
    all_records = []
    page        = 1
    limit       = 500          # max safe page size
    total_pages = None

    while True:
        resp = get(
            f"/api/v1/readings/device/{device_id}",
            params={"limit": limit, "page": page},
            timeout=60
        )

        if not resp or not resp.get("success"):
            print(f"  Bad response on page {page}: {resp}")
            break

        records = resp.get("data", [])
        all_records.extend(records)

        # First page — learn total
        if total_pages is None:
            pagination  = resp.get("pagination", {})
            total       = pagination.get("total", 0)
            total_pages = pagination.get("totalPages", 1)
            print(f"  Total records: {total:,}  |  Pages: {total_pages}")

        print(f"  Page {page}/{total_pages} — fetched {len(records)} records "
              f"(running total: {len(all_records):,})")

        if page >= total_pages or not records:
            break

        page += 1
        time.sleep(0.3)        # polite delay between pages

    return all_records

def flatten_record(r, device_meta):
    """Extract only the fields we need into a flat dict."""
    s = r.get("sensorData", {})
    g = r.get("gateway",    {})
    l = r.get("location",   {})
    return {
        # Identifiers
        "record_id":            r.get("_id"),
        "device_id":            r.get("deviceId"),
        "dev_eui":              r.get("devEui"),
        "application_id":       r.get("applicationId"),
        # Timestamp
        "received_at":          r.get("receivedAt"),
        # Location (from device registry — stable)
        "latitude":             l.get("latitude",  device_meta.get("latitude")),
        "longitude":            l.get("longitude", device_meta.get("longitude")),
        "altitude":             l.get("altitude",  device_meta.get("altitude")),
        # Primary weather variables (for ML)
        "air_temperature":      s.get("airTemperature"),
        "air_humidity":         s.get("airHumidity"),
        "barometric_pressure":  s.get("barometricPressure"),
        "wind_speed":           s.get("windSpeed"),
        "peak_wind_gust":       s.get("peakWindGust"),
        "wind_direction":       s.get("windDirectionSensor"),
        "rain_accumulation":    s.get("rainAccumulation"),
        "rain_gauge":           s.get("rainGauge"),
        "light_intensity":      s.get("lightIntensity"),
        "uv_index":             s.get("uvIndex"),
        "sensor_error":         s.get("err"),
        # Signal quality (useful for QC)
        "gateway_id":           g.get("gatewayId"),
        "rssi":                 g.get("rssi"),
        "snr":                  g.get("snr"),
    }

# ── Main extraction loop ──────────────────────────────────────

print("=" * 60)
print("Imarika Data Extractor")
print(f"Started: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 60)

# Step 1: Get all devices
print("\n[1/3] Fetching device list...")
devices_resp = get("/api/v1/devices")
devices      = devices_resp["data"]
print(f"  Found {len(devices)} devices")

# Build device metadata lookup
device_meta = {
    d["deviceId"]: {
        "latitude":  d["location"]["latitude"],
        "longitude": d["location"]["longitude"],
        "altitude":  d["location"]["altitude"],
    }
    for d in devices
}

# Step 2: Extract per device
print(f"\n[2/3] Extracting readings ({len(devices)} devices)...")
total_extracted = 0
failed_devices  = []

for i, device in enumerate(devices, 1):
    did       = device["deviceId"]
    save_path = os.path.join(RAW_DIR, f"{did}.json")

    print(f"\n  [{i}/{len(devices)}] Device {did}")

    # Skip if already extracted (resumable)
    if os.path.exists(save_path):
        existing = json.load(open(save_path))
        print(f"  Already extracted — {len(existing):,} records on disk. Skipping.")
        total_extracted += len(existing)
        continue

    records = fetch_all_pages(did)

    if not records:
        print(f"  WARNING: No records returned for {did}")
        failed_devices.append(did)
        continue

    # Save raw JSON immediately
    with open(save_path, "w") as f:
        json.dump(records, f)

    total_extracted += len(records)
    print(f"  Saved {len(records):,} records → {save_path}")
    time.sleep(0.5)    # pause between devices

# Step 3: Combine all raw JSON into one CSV
print(f"\n[3/3] Building combined CSV → {CSV_PATH}")

CSV_FIELDS = [
    "record_id", "device_id", "dev_eui", "application_id",
    "received_at", "latitude", "longitude", "altitude",
    "air_temperature", "air_humidity", "barometric_pressure",
    "wind_speed", "peak_wind_gust", "wind_direction",
    "rain_accumulation", "rain_gauge",
    "light_intensity", "uv_index", "sensor_error",
    "gateway_id", "rssi", "snr",
]

row_count = 0
with open(CSV_PATH, "w", newline="", encoding="utf-8") as csvfile:
    writer = csv.DictWriter(csvfile, fieldnames=CSV_FIELDS)
    writer.writeheader()

    for did, meta in device_meta.items():
        raw_path = os.path.join(RAW_DIR, f"{did}.json")
        if not os.path.exists(raw_path):
            continue
        records = json.load(open(raw_path))
        for r in records:
            writer.writerow(flatten_record(r, meta))
            row_count += 1

print(f"\n{'='*60}")
print(f"DONE — {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print(f"Total records extracted: {total_extracted:,}")
print(f"Total rows in CSV:       {row_count:,}")
print(f"CSV saved to:            {CSV_PATH}")
if failed_devices:
    print(f"Failed devices:          {failed_devices}")
print("="*60)