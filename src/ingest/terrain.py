"""Fine terrain for the water simulation (US path only): USGS 3DEP 1 m lidar where it exists,
3DEP 1/3 arc-second (~10 m) everywhere else.

The soil-loss and landslide analysis stays on the 10 m grid: slope length and wetness need the
whole sub-basin, which at 1 m would be up to 100 million cells. Surface water needs the small
relief a 10 m model smooths away (furrows, ditches, terraces, road embankments), so the field
grid (the field plus 500 m) is pulled again at 1 m. Per cell:

  a USGS 1 m lidar DEM covers it     its elevation                               source 1
  no 1 m project covers it           3DEP ~10 m (the analysis DEM), bilinear     source 2

working_save/terrain/
  dem.tif          elevation (m, NAVD88) on the fine grid
  dem_source.tif   1 = USGS 3DEP 1 m lidar, 2 = 3DEP ~10 m resampled (fallback), 255 = none
  dem_context.tif  the ~10 m analysis DEM over the whole sub-basin: what drains into the 1 m
                   area from outside it (used by src/backend/water.py)
  terrain.json     grids, resolution, share of the field and of the grid on lidar, sources

The fine grid has the field grid's footprint and origin, so each 10 m analysis cell holds
exactly (10 / res)^2 fine cells. A field too large for MAX_CELLS at 1 m is pulled at 2 m, then
5 m. Both DEMs are bare earth on NAVD88, but where lidar meets the fallback the surface can step
by a few decimetres. The terrain is redone when the field grid changes, with --refresh, or when
the last 1 m pull failed (that run used the fallback everywhere). The ~10 m DEM comes from
working_save/inputs/dem.tif when it is there, else from the US cache (or Earth Engine). If even
that cannot be had (e.g. no Earth Engine login), the terrain is skipped and the run goes on.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from affine import Affine
from rasterio.features import geometry_mask
from rasterio.warp import transform as warp_transform

from ..dataanal import raster_io as rio
from . import us_data

VERSION = 2  # bump when the grid, the merge or the files change (2: dem_context.tif)
FINE_RES_M = (1.0, 2.0, 5.0)  # finest first; the first that fits MAX_CELLS is used
MAX_CELLS = 16_000_000  # 4 x 4 km at 1 m, 64 MB of float32
SOURCE_LIDAR, SOURCE_FALLBACK, SOURCE_NONE = 1, 2, 255
SOURCES = {
    SOURCE_LIDAR: f"USGS 3DEP 1 m lidar DEM ({us_data.DEM_3DEP_1M})",
    SOURCE_FALLBACK: f"USGS 3DEP 1/3 arc-second (~10 m), bilinear ({us_data.DEM_3DEP})",
}
DEM_FILE, SOURCE_FILE, CONTEXT_FILE, META_FILE = "dem.tif", "dem_source.tif", "dem_context.tif", "terrain.json"  # META last


def fine_grid(field_grid: rio.Grid) -> rio.Grid | None:
    """The field grid's footprint at the finest resolution in FINE_RES_M that divides its cell
    size and keeps the grid under MAX_CELLS; None if none does."""
    t = field_grid.transform
    for res in FINE_RES_M:
        k = field_grid.xres / res
        if k < 1 or abs(k - round(k)) > 1e-9:
            continue
        k = round(k)
        width, height = field_grid.width * k, field_grid.height * k
        if width * height <= MAX_CELLS:
            return rio.Grid(field_grid.crs, Affine(res, 0, t.c, 0, -res, t.f), width, height)
    return None


def merge(lidar, fallback) -> tuple[np.ndarray, np.ndarray]:
    """Elevation (lidar where finite, else the fallback) and the source code of each cell."""
    has_lidar = np.isfinite(lidar)
    elevation = np.where(has_lidar, lidar, fallback).astype(np.float32)
    source = np.where(has_lidar, SOURCE_LIDAR, np.where(np.isfinite(fallback), SOURCE_FALLBACK, SOURCE_NONE))
    return elevation, source.astype(np.uint8)


def _field_mask(polygon_lonlat, grid: rio.Grid) -> np.ndarray:
    lons, lats = np.array(polygon_lonlat, float).T
    xs, ys = warp_transform("EPSG:4326", grid.crs, lons.tolist(), lats.tolist())
    ring = np.column_stack([xs, ys])
    ring = np.vstack([ring, ring[:1]])
    return geometry_mask([{"type": "Polygon", "coordinates": [ring.tolist()]}], out_shape=(grid.height, grid.width),
                         transform=grid.transform, all_touched=True, invert=True)


def _first_line(err: Exception) -> str:
    text = str(err).strip()
    return text.splitlines()[0][:200] if text else type(err).__name__


def _fallback_dem(analysis_dir: Path, dem_grid: rio.Grid, us_cache) -> rio.Raster:
    """The analysis's ~10 m DEM: the run's own input copy if it is on `dem_grid`, else 3DEP from
    the US cache (pulled from Earth Engine if not cached)."""
    saved = analysis_dir.parent / "inputs" / "dem.tif"
    if saved.exists():
        dem = rio.read(saved)
        if rio.grid_of(dem).same_as(dem_grid):
            return dem
    return rio.read(us_data.dem(dem_grid, us_cache))


def _is_current(out_dir: Path, key: dict) -> bool:
    meta_path = out_dir / META_FILE
    if not all((out_dir / f).exists() for f in (META_FILE, DEM_FILE, SOURCE_FILE, CONTEXT_FILE)):
        return False
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    if any(meta.get(k) != v for k, v in key.items()):
        return False
    if meta.get("lidar_pull_failed"):
        print("The saved fine terrain used the ~10 m fallback because the 1 m pull failed: retrying it")
        return False
    return True


def ensure(analysis_dir, out_dir, us_cache, refresh: bool = False) -> dict | None:
    """Write working_save/terrain for the analysis in `analysis_dir` unless it is current.
    Returns terrain.json's content, or None for the global path or a field too large."""
    stamp = json.loads((Path(analysis_dir) / "plan.json").read_text(encoding="utf-8"))
    if stamp.get("path") != "us":
        return None
    if "field_grid" not in stamp or "dem_grid" not in stamp:
        print("Fine terrain skipped: plan.json has no grids (rerun with --refresh)")
        return None
    grid = fine_grid(rio.grid_from_dict(stamp["field_grid"]))
    if grid is None:
        print(f"Fine terrain skipped: the field grid exceeds {MAX_CELLS:,} cells even at {FINE_RES_M[-1]:g} m")
        return None
    out_dir = Path(out_dir)
    key = {"version": VERSION, "field_grid": stamp["field_grid"], "grid": grid.as_dict()}
    if not refresh and _is_current(out_dir, key):
        return json.loads((out_dir / META_FILE).read_text(encoding="utf-8"))

    print(f"Fine terrain: {grid.width} x {grid.height} cells at {grid.xres:g} m")
    try:
        context = _fallback_dem(Path(analysis_dir), rio.grid_from_dict(stamp["dem_grid"]), us_cache)
        fallback = rio.align_raster(context, grid)[0]
    except Exception as err:  # noqa: BLE001 - no Earth Engine login, network or server failure
        print(f"  Fine terrain skipped: the ~10 m elevation is not cached and could not be pulled ({_first_line(err)}). "
              "Log in with `earthengine authenticate`; the next run tries again")
        return None
    failed = None
    try:
        lidar = rio.read(us_data.dem_lidar(grid, us_cache)).data[0]
    except Exception as err:  # noqa: BLE001 - any network or server failure
        failed = _first_line(err)
        print(f"  USGS 1 m lidar unavailable ({failed}); using 3DEP ~10 m everywhere, retried on the next run")
        lidar = np.full((grid.height, grid.width), np.nan, np.float32)
    elevation, source = merge(lidar, fallback)

    field = _field_mask(stamp["polygon"], grid)
    on_lidar = source == SOURCE_LIDAR
    share_field = 100 * float(on_lidar[field].mean()) if field.any() else 0.0
    share_grid = 100 * float(on_lidar.mean())
    finite = elevation[np.isfinite(elevation)]
    rest = f"; the rest is 3DEP ~10 m resampled to {grid.xres:g} m" if share_grid < 100 else ""
    print(f"  1 m lidar covers {share_field:.0f}% of the field and {share_grid:.0f}% of the grid{rest}")

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / META_FILE).unlink(missing_ok=True)  # a half-written terrain is never taken as current
    rio.write(out_dir / DEM_FILE, elevation, grid, units="m",
              description="bare-earth elevation (NAVD88): USGS 3DEP 1 m lidar, gaps from 3DEP ~10 m",
              tags={"SOURCE": f"{us_data.DEM_3DEP_1M} + {us_data.DEM_3DEP}", "VERTICAL_DATUM": "NAVD88"})
    rio.write(out_dir / SOURCE_FILE, source, grid, units="code", dtype="uint8", nodata=SOURCE_NONE,
              description="elevation source per cell", tags={f"CODE_{k}": v for k, v in SOURCES.items()})
    rio.write(out_dir / CONTEXT_FILE, context.data[0], rio.grid_of(context), units="m",
              description="analysis DEM (~10 m) over the sub-basin: inflow into the fine grid",
              tags={"SOURCE": us_data.DEM_3DEP, "VERTICAL_DATUM": "NAVD88"})
    meta = {**key, "res_m": grid.xres, "vertical_datum": "NAVD88",
            "sources": {str(k): v for k, v in SOURCES.items()},
            "lidar_share_field_pct": round(share_field, 2), "lidar_share_grid_pct": round(share_grid, 2),
            "elevation_range_m": [round(float(finite.min()), 2), round(float(finite.max()), 2)] if finite.size else None,
            "lidar_pull_failed": failed,
            "created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    (out_dir / META_FILE).write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta
