"""Pure-Python helpers for the boundary file: loading and point-in-polygon.

Coordinates follow GeoJSON order (longitude, latitude). No third-party
geometry packages are used.
"""

import json
from pathlib import Path

BOUNDARIES = Path(__file__).resolve().parent / "assets" / "kenya_boundaries.geojson"


def polygons(geometry):
    """Yield each polygon (a list of rings) of a Polygon or MultiPolygon."""
    if not geometry:
        return
    if geometry.get("type") == "Polygon":
        yield geometry["coordinates"]
    elif geometry.get("type") == "MultiPolygon":
        yield from geometry["coordinates"]


def in_ring(lon, lat, ring):
    """Ray casting: True if the point lies inside the closed ring."""
    inside = False
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        if (y1 > lat) != (y2 > lat):
            crossing = x1 + (lat - y1) * (x2 - x1) / (y2 - y1)
            if lon < crossing:
                inside = not inside
    return inside


def contains(geometry, lon, lat):
    """True if the point is inside an outer ring and outside its holes."""
    for rings in polygons(geometry):
        if rings and in_ring(lon, lat, [tuple(point[:2]) for point in rings[0]]):
            if not any(in_ring(lon, lat, [tuple(point[:2]) for point in hole]) for hole in rings[1:]):
                return True
    return False


def centre(geometry):
    """Mean of the outer-ring vertices of the largest polygon (a rough centre)."""
    rings = max(polygons(geometry), key=lambda rings: len(rings[0]))
    points = rings[0]
    return (sum(point[0] for point in points) / len(points),
            sum(point[1] for point in points) / len(points))


def load_boundaries(path=BOUNDARIES):
    """Return the GeoJSON features, or [] if the file is missing or unreadable."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return [feature for feature in data.get("features", []) if feature.get("geometry")]
    except (OSError, ValueError, AttributeError):
        return []


def features_at(features, level):
    return [feature for feature in features if feature.get("properties", {}).get("level") == level]


def sub_county(features, lon, lat):
    """Name of the sub-county containing the point, or None."""
    try:
        lon, lat = float(lon), float(lat)
    except (TypeError, ValueError):
        return None
    for feature in features_at(features, "sub-county"):
        if contains(feature["geometry"], lon, lat):
            return feature["properties"].get("name")
    return None
