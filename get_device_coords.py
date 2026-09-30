"""
Quick script — get coordinates for all 31 devices.
Runs in seconds, independent of the main extraction.
"""

import requests, json, os
from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("ANGA_API_KEY")
BASE    = "https://api.wirelessplanet.co.ke"
HEADERS = {"X-API-Key": API_KEY, "Accept": "application/json"}

resp = requests.get(f"{BASE}/api/v1/devices", headers=HEADERS, timeout=30)
devices = resp.json()["data"]

# Busia County bounding box
LAT_MIN, LAT_MAX = 0.1,  0.7
LON_MIN, LON_MAX = 34.1, 34.6

print(f"Total devices: {len(devices)}\n")
print(f"{'#':<4} {'DeviceId':<25} {'Lat':>10} {'Lon':>10} {'Alt':>6}  Status")
print("-" * 70)

busia_stations   = []
outside_stations = []

for i, d in enumerate(devices, 1):
    did = d["deviceId"]
    lat = d["location"]["latitude"]
    lon = d["location"]["longitude"]
    alt = d["location"]["altitude"]

    in_busia = (LAT_MIN <= lat <= LAT_MAX) and (LON_MIN <= lon <= LON_MAX)
    flag = "✓ Busia" if in_busia else "✗ OUTSIDE"

    print(f"{i:<4} {did:<25} {lat:>10.6f} {lon:>10.6f} {alt:>6}  {flag}")

    entry = {
        "device_id": did,
        "lat": lat,
        "lon": lon,
        "altitude": alt,
        "dev_eui": d.get("devEui"),
        "status": d.get("status"),
        "created_at": d.get("createdAt"),
    }

    if in_busia:
        busia_stations.append(entry)
    else:
        outside_stations.append(entry)

print("-" * 70)
print(f"\nIn Busia County:  {len(busia_stations)} stations")
print(f"Outside Busia:    {len(outside_stations)} stations")

if outside_stations:
    print("\nExcluded stations:")
    for s in outside_stations:
        print(f"  {s['device_id']}  lat={s['lat']:.4f}  lon={s['lon']:.4f}")

# Save Busia-only list for use in Open-Meteo script
with open("data/busia_stations.json", "w") as f:
    json.dump(busia_stations, f, indent=2)

print(f"\nBusia station list saved → data/busia_stations.json")