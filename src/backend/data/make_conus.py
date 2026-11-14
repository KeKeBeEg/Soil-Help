"""Regenerate conus.geojson: the 48 contiguous states + DC as one simplified outline.

    python -m src.backend.data.make_conus

Source: Census TIGER 2018 states (Earth Engine TIGER/2018/States), dissolved and simplified
to ~1 km, which is plenty for deciding which data path a farm field takes.
"""
from __future__ import annotations

import json
from pathlib import Path

import ee

from ...ingest import ee_io

NOT_CONUS = ["02", "15", "60", "66", "69", "72", "78"]  # AK, HI, AS, GU, MP, PR, VI


def _polygons(g: dict) -> list:
    if g["type"] == "Polygon":
        return [g["coordinates"]]
    if g["type"] == "MultiPolygon":
        return g["coordinates"]
    if g["type"] == "GeometryCollection":
        return [p for sub in g["geometries"] for p in _polygons(sub)]
    return []  # stray lines and points from the dissolve


def main():
    ee_io.init()
    states = ee.FeatureCollection("TIGER/2018/States").filter(ee.Filter.inList("STATEFP", NOT_CONUS).Not())
    outline = states.geometry(1000).dissolve(1000).simplify(1000)
    polygons = _polygons(ee_io.retry(outline.getInfo))
    path = Path(__file__).with_name("conus.geojson")
    fc = {"type": "FeatureCollection", "features": [{
        "type": "Feature", "properties": {"name": "CONUS", "source": "TIGER/2018/States, simplified 1 km"},
        "geometry": {"type": "MultiPolygon", "coordinates": polygons}}]}
    path.write_text(json.dumps(fc, separators=(",", ":")), encoding="utf-8")
    print(f"wrote {path} ({path.stat().st_size / 1024:.0f} KB, {len(polygons)} polygons)")


if __name__ == "__main__":
    main()
