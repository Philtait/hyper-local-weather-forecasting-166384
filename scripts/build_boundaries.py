"""Build assets/kenya_boundaries.geojson from geoBoundaries simplified releases.

Download the three simplified GeoJSON files listed in assets/README.md, then:

    python scripts/build_boundaries.py ADM0.geojson ADM1.geojson ADM2.geojson

Keeps the Kenya outline, Busia County and the ADM2 units whose centre lies in
Busia, rounds coordinates to 4 decimals, and adds "level" and "name" properties.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from geo import centre, contains  # noqa: E402

OUTPUT = Path(__file__).resolve().parents[1] / "assets" / "kenya_boundaries.geojson"


def rounded(value):
    if isinstance(value, list):
        return [rounded(item) for item in value]
    return round(value, 4)


def feature(source, level, name):
    geometry = {"type": source["geometry"]["type"], "coordinates": rounded(source["geometry"]["coordinates"])}
    return {"type": "Feature", "properties": {"level": level, "name": name}, "geometry": geometry}


def main(adm0, adm1, adm2, output=OUTPUT):
    read = lambda path: json.loads(Path(path).read_text(encoding="utf-8"))["features"]
    kenya = read(adm0)[0]
    busia = next(item for item in read(adm1) if item["properties"]["shapeName"] == "Busia")
    parts = [item for item in read(adm2) if contains(busia["geometry"], *centre(item["geometry"]))]
    features = [feature(kenya, "country", "Kenya"), feature(busia, "county", "Busia")]
    features += [feature(item, "sub-county", item["properties"]["shapeName"])
                 for item in sorted(parts, key=lambda item: item["properties"]["shapeName"])]
    output.parent.mkdir(exist_ok=True)
    output.write_text(json.dumps({"type": "FeatureCollection", "features": features},
                                 separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {output} ({output.stat().st_size:,} bytes): "
          f"{', '.join(item['properties']['name'] for item in features)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("adm0")
    parser.add_argument("adm1")
    parser.add_argument("adm2")
    args = parser.parse_args()
    main(args.adm0, args.adm1, args.adm2)
