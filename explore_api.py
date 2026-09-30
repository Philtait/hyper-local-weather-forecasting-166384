import requests
import json
import os
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("ANGA_API_KEY")
BASE    = "https://api.wirelessplanet.co.ke"
HEADERS = {"X-API-Key": API_KEY, "Accept": "application/json"}

def get(path, params=None):
    r = requests.get(BASE + path, headers=HEADERS, params=params, timeout=15)
    return r.json() if "json" in r.headers.get("Content-Type", "") else None

# ── Step 1: Get all device IDs ────────────────────────────────
print("Fetching all devices...")
resp = get("/api/v1/devices")
devices = resp["data"]
print(f"Total devices: {len(devices)}\n")

# ── Step 2: Get stats for every device ───────────────────────
print(f"{'DeviceId':<25} {'Records':>8} {'First Reading':<26} {'Last Reading':<26} {'AvgTemp':>8} {'TotalRain':>12}")
print("-" * 115)

total_records   = 0
device_summary  = []

for d in devices:
    did   = d["deviceId"]
    lat   = d["location"]["latitude"]
    lon   = d["location"]["longitude"]

    stats_resp = get(f"/api/v1/readings/device/{did}/stats")
    if not stats_resp or not stats_resp.get("success"):
        print(f"{did:<25} ERROR fetching stats")
        continue

    s = stats_resp["data"]
    total_records += s.get("totalReadings", 0)

    device_summary.append({
        "deviceId":      did,
        "devEui":        d.get("devEui"),
        "latitude":      lat,
        "longitude":     lon,
        "altitude":      d["location"]["altitude"],
        "status":        d.get("status"),
        "totalReadings": s.get("totalReadings", 0),
        "firstReading":  s.get("firstReading", "N/A"),
        "lastReading":   s.get("lastReading",  "N/A"),
        "avgTemp":       round(s.get("avgTemperature", 0), 2),
        "totalRainfall": round(s.get("totalRainfall",  0), 2),
        "avgHumidity":   round(s.get("avgHumidity",    0), 2),
        "avgPressure":   round(s.get("avgPressure",    0), 2),
        "maxWindGust":   s.get("maxWindGust", 0),
        "minTemp":       s.get("minTemperature", 0),
        "maxTemp":       s.get("maxTemperature", 0),
    })

    print(
        f"{did:<25} "
        f"{s.get('totalReadings', 0):>8,} "
        f"{str(s.get('firstReading','N/A'))[:25]:<26} "
        f"{str(s.get('lastReading','N/A'))[:25]:<26} "
        f"{s.get('avgTemperature', 0):>8.1f} "
        f"{s.get('totalRainfall', 0):>12.1f}"
    )

print("-" * 115)
print(f"{'TOTAL':<25} {total_records:>8,}")
print(f"\nEstimated file size: ~{total_records * 0.5 / 1024:.0f} MB (rough estimate)")

# ── Step 3: Flag devices outside Busia County ─────────────────
# Busia County bounding box: lat 0.1–0.7 N, lon 34.1–34.6 E
print("\n⚠  Devices outside Busia County bounding box:")
for d in device_summary:
    lat, lon = d["latitude"], d["longitude"]
    if not (0.1 <= lat <= 0.7 and 34.1 <= lon <= 34.6):
        print(f"  {d['deviceId']}  lat={lat:.4f}  lon={lon:.4f}  — likely NOT in Busia")

# ── Step 4: Check pagination ceiling ─────────────────────────
print("\nChecking max page size...")
test = get("/api/v1/readings", params={"limit": 1000, "page": 1})
if test and "pagination" in test:
    p = test["pagination"]
    returned = len(test.get("data", []))
    print(f"Asked for 1000 — got {returned} records back")
    print(f"Pagination object: {json.dumps(p, indent=2)}")