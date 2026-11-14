"""Which data path a field takes, and its local projection."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import numpy as np
from matplotlib.path import Path as Ring
from rasterio.crs import CRS

CONUS_FILE = Path(__file__).with_name("data") / "conus.geojson"


@lru_cache(maxsize=1)
def _conus_rings() -> list[Ring]:
    fc = json.loads(CONUS_FILE.read_text(encoding="utf-8"))
    rings = []
    for feature in fc["features"]:
        g = feature["geometry"]
        polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
        rings += [Ring(np.asarray(poly[0])) for poly in polys]  # exterior rings; lakes don't matter here
    return rings


def in_conus(polygon) -> bool:
    """True if every vertex of the (lon, lat) polygon lies inside the contiguous US."""
    pts = np.asarray(polygon, float)
    inside = np.zeros(len(pts), bool)
    for ring in _conus_rings():
        inside |= ring.contains_points(pts)
    return bool(inside.all())


def data_path(polygon) -> str:
    return "us" if in_conus(polygon) else "global"


def utm_crs(lon: float, lat: float) -> CRS:
    zone = int((lon + 180) // 6) % 60 + 1
    return CRS.from_epsg((32600 if lat >= 0 else 32700) + zone)
