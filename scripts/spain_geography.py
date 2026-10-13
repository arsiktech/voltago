"""Spain land filter using the attributed IGN/geoBoundaries simplified boundary.

The vendored geometry includes mainland, islands, Ceuta, Melilla and Llivia.
It is not a geocoder: explicit upstream country fields remain stronger evidence.
"""
import json
import math
from collections import defaultdict
from functools import lru_cache
from pathlib import Path

BOUNDARY = Path(__file__).resolve().parents[1] / "data" / "spain_boundary.geojson"
LATITUDE_BUCKETS_PER_DEGREE = 20


@lru_cache(maxsize=1)
def _edge_index():
    data = json.loads(BOUNDARY.read_text(encoding="utf-8"))
    buckets = defaultdict(list)
    for feature in data["features"]:
        geometry = feature["geometry"]
        polygons = geometry["coordinates"] if geometry["type"] == "MultiPolygon" else [geometry["coordinates"]]
        for polygon in polygons:
            for ring in polygon:
                for start, end in zip(ring, ring[1:]):
                    x1, y1 = start[:2]
                    x2, y2 = end[:2]
                    if y1 == y2:
                        continue
                    low, high = sorted((y1, y2))
                    for bucket in range(math.floor(low * LATITUDE_BUCKETS_PER_DEGREE), math.floor(high * LATITUDE_BUCKETS_PER_DEGREE) + 1):
                        buckets[bucket].append((x1, y1, x2, y2))
    return dict(buckets)


def point_in_spain(lat, lon):
    if lat is None or lon is None or not math.isfinite(lat) or not math.isfinite(lon):
        return False
    if not (27.5 <= lat <= 44.0 and -18.3 <= lon <= 4.4):
        return False
    inside = False
    for x1, y1, x2, y2 in _edge_index().get(math.floor(lat * LATITUDE_BUCKETS_PER_DEGREE), ()):
        if (y1 > lat) != (y2 > lat) and lon < (x2 - x1) * (lat - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside
