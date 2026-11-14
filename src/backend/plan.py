"""Analysis grids and the fixed analysis period for one field.

Each dataset is pulled over the area it needs:
  dem_grid    the HydroBASINS level-12 sub-basin(s) under the field, clipped to 5 km around it
              and never less than 1 km around it: slope length and wetness depend on all the
              land draining into the field
  field_grid  the field plus 500 m: soil, land cover, NDVI, and the analysis outputs
  rain_grid   the 0.1 deg IMERG cells over the field plus one cell on every side
Grids are snapped to whole multiples of the resolution, so the same field always maps to the
same cache files and field_grid sits exactly on dem_grid's lattice.

The analysis version and period live in analysis_version.json at the repository root, shared
with anything else that reads saved analyses (the time slider covers that period).
"""
from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from affine import Affine
from rasterio.crs import CRS
from rasterio.warp import transform as warp_transform
from rasterio.warp import transform_bounds

from ..dataanal import raster_io as rio
from ..ingest import shared
from . import region

_SHARED = json.loads((Path(__file__).resolve().parents[2] / "analysis_version.json").read_text(encoding="utf-8"))
VERSION = int(_SHARED["version"])  # a saved analysis with another version is redone
CLIMATE_YEARS = tuple(_SHARED["climate_years"])  # the analysis period (R, cover, landslide record); t_range only picks views
RES_M = {"us": 10.0, "global": 30.0}
FIELD_MARGIN_M = 500.0
DEM_MIN_MARGIN_M = 1000.0
DEM_MAX_MARGIN_M = 5000.0
RAIN_RES_DEG = 0.1


def request_key(polygon_lonlat, path: str) -> dict:
    """The part of a plan's fingerprint known without the HydroBASINS lookup. The grids follow
    from it (and from the cached sub-basins), so a saved analysis with the same key is current."""
    return {"version": VERSION, "path": path, "res_m": RES_M[path], "climate_years": list(CLIMATE_YEARS),
            "polygon": [[round(x, 6), round(y, 6)] for x, y in polygon_lonlat]}


@dataclass
class Plan:
    path: str  # "us" or "global"
    crs: CRS
    res: float
    field_lonlat: list
    field_xy: np.ndarray  # (N, 2) vertices in `crs`
    dem_grid: rio.Grid
    field_grid: rio.Grid
    rain_grid: rio.Grid
    climate_years: tuple
    basins: dict

    def fingerprint(self) -> dict:
        """Saved as analysis/plan.json; the views rebuild the field and rain grid from it."""
        return {**request_key(self.field_lonlat, self.path), "climate_years": list(self.climate_years),
                "dem_grid": self.dem_grid.as_dict(), "field_grid": self.field_grid.as_dict(),
                "rain_grid": self.rain_grid.as_dict()}


def _expand(b, m):
    return b[0] - m, b[1] - m, b[2] + m, b[3] + m


def snapped_grid(crs: CRS, bounds, res: float) -> rio.Grid:
    xmin, ymin, xmax, ymax = bounds
    x0, x1 = math.floor(xmin / res) * res, math.ceil(xmax / res) * res
    y0, y1 = math.floor(ymin / res) * res, math.ceil(ymax / res) * res
    return rio.Grid(crs, Affine(res, 0, x0, 0, -res, y1), round((x1 - x0) / res), round((y1 - y0) / res))


def rain_grid(lonlat_bounds, res: float = RAIN_RES_DEG, margin: int = 1) -> rio.Grid:
    """IMERG-aligned grid (cell edges on multiples of `res`) around lon/lat bounds."""
    k = round(1 / res)
    w, s, e, n = lonlat_bounds
    x0, x1 = math.floor(w * k + 1e-9) - margin, math.ceil(e * k - 1e-9) + margin
    y0, y1 = math.floor(s * k + 1e-9) - margin, math.ceil(n * k - 1e-9) + margin
    return rio.Grid(CRS.from_epsg(4326), Affine(res, 0, x0 / k, 0, -res, y1 / k), x1 - x0, y1 - y0)


def make(req, path: str) -> Plan:
    lons, lats = np.array(req.polygon).T
    crs = region.utm_crs(float(lons.mean()), float(lats.mean()))
    xs, ys = warp_transform("EPSG:4326", crs, lons.tolist(), lats.tolist())
    field_xy = np.column_stack([xs, ys])
    fb = (min(xs), min(ys), max(xs), max(ys))
    res = RES_M[path]

    basins = shared.basins(req.polygon, req.global_cache)
    near = _expand(fb, DEM_MIN_MARGIN_M)
    if basins.get("ids"):
        bb = transform_bounds("EPSG:4326", crs, *basins["bounds_lonlat"])
        far = _expand(fb, DEM_MAX_MARGIN_M)
        bb = (max(bb[0], far[0]), max(bb[1], far[1]), min(bb[2], far[2]), min(bb[3], far[3]))
        dem_bounds = (min(bb[0], near[0]), min(bb[1], near[1]), max(bb[2], near[2]), max(bb[3], near[3]))
    else:
        dem_bounds = _expand(fb, 3000.0)

    return Plan(path=path, crs=crs, res=res, field_lonlat=list(req.polygon), field_xy=field_xy,
                dem_grid=snapped_grid(crs, dem_bounds, res),
                field_grid=snapped_grid(crs, _expand(fb, FIELD_MARGIN_M), res),
                rain_grid=rain_grid((lons.min(), lats.min(), lons.max(), lats.max())),
                climate_years=CLIMATE_YEARS, basins=basins)
