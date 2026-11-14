"""Write-once cache files.

A cached raster is named after the dataset and the exact grid it was pulled on, so the same
field (or any request that snaps to the same grid) finds it again. Files are written to a
temporary name first and renamed when complete, so an interrupted pull never leaves a
half-written file that looks valid. Delete a file (or a folder) to pull it again.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import numpy as np

from ..dataanal import raster_io as rio


def grid_key(grid: rio.Grid) -> str:
    t = grid.transform
    epsg = grid.crs.to_epsg()
    if grid.crs.is_geographic:
        return f"epsg{epsg}_{t.a:g}deg_{t.c:.4f}_{t.f:.4f}_{grid.width}x{grid.height}"
    return f"epsg{epsg}_{t.a:g}m_{t.c:.0f}_{t.f:.0f}_{grid.width}x{grid.height}"


def _tmp(path: Path) -> Path:
    return path.with_name(f".part-{os.getpid()}-{path.name}")


def cached_file(path: Path, build: Callable[[Path], None]) -> Path:
    """Return `path`, first calling build(tmp_path) to create it if it does not exist."""
    path = Path(path)
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp(path)
    try:
        build(tmp)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()
    return path


def cached_raster(path: Path, grid: rio.Grid, build: Callable[[], np.ndarray], *, description: str, units: str,
                  tags: dict | None = None, dtype: str = "float32", nodata=np.nan, band_names=None) -> Path:
    """A GeoTIFF on `grid` holding build()'s array, pulled only if not cached yet."""
    stamp = {"FETCHED_UTC": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}

    def write(tmp: Path):
        data = build()
        if dtype != "float32":
            data = np.where(np.isfinite(data), data, nodata)
        rio.write(tmp, data, grid, description=description, units=units, dtype=dtype, nodata=nodata,
                  band_names=band_names, tags={**stamp, **(tags or {})})

    return cached_file(path, write)


def cached_json(path: Path, build: Callable[[], dict]) -> dict:
    def write(tmp: Path):
        tmp.write_text(json.dumps(build(), indent=2), encoding="utf-8")

    return json.loads(cached_file(path, write).read_text(encoding="utf-8"))
