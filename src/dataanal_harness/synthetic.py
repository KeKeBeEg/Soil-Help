"""Synthetic pipeline inputs that follow the GeoTIFF contract of src.dataanal.raster_io.

Used to debug and test src.dataanal without Earth Engine. Terrain, NDVI and the masks sit on a
30 m UTM grid; soil type and precipitation sit on 0.1 deg lat/lon grids (like ERA5-Land and
IMERG), so the pipeline's reprojection runs exactly as it does with real data.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from affine import Affine
from pyproj import Transformer
from rasterio.crs import CRS

from ..dataanal import raster_io as rio

UTM = CRS.from_epsg(32637)
ORIGIN = (320_000.0, 1_250_000.0)  # top-left corner, UTM 37N (south of Lake Tana, Ethiopia)


def make_inputs(out_dir, years=(2010,), size_px: int = 400, seed: int = 0, extras: bool = False) -> list[Path]:
    """Synthetic inputs; `extras` adds the optional soil_texture, soil_depth and landcover layers."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    res = 30.0
    span = size_px * res
    g30 = rio.Grid(UTM, Affine(res, 0, ORIGIN[0], 0, -res, ORIGIN[1]), size_px, size_px)
    x, y = np.meshgrid(np.arange(size_px) * res, np.arange(size_px) * res)  # metres from top-left
    paths = []

    dem = 1900 + 350 * np.sin(x / 3000) * np.cos(y / 4200) + 120 * np.sin(x / 900 + y / 1300)
    paths.append(rio.write(out_dir / "dem.tif", dem, g30, description="synthetic elevation", units="m"))
    aoi = np.hypot(x - span / 2, y - span / 2) < 0.45 * span
    paths.append(rio.write(out_dir / "aoi.tif", aoi, g30, dtype="uint8", nodata=255,
                           description="synthetic study area", units="mask"))
    water = np.hypot(x - 0.3 * span, y - 0.6 * span) < 0.05 * span
    paths.append(rio.write(out_dir / "water.tif", water, g30, dtype="uint8", nodata=255,
                           description="synthetic lake", units="mask"))

    # Forest strips keep NDVI ~0.75 all year; cropland is bare at planting (May-Jun) and
    # greenest after the rains (Sep-Oct).
    forest = np.sin(x / 2500) > 0.3
    crop = 0.38 + 0.22 * np.sin(2 * np.pi * (np.arange(12) - 6.5) / 12)
    ndvi = np.stack([np.where(forest, 0.75, c) for c in crop])
    paths.append(rio.write(out_dir / "ndvi.tif", ndvi, g30, description="synthetic monthly NDVI", units="NDVI",
                           band_names=[f"ndvi_{m:02d}" for m in range(1, 13)], tags={"TIME_RANGE": "synthetic"}))
    if extras:
        # Sandy in the west, clayey in the east; thin soils on the high ground.
        clay = 10 + 35 * x / span
        sand = 55 - 30 * x / span
        texture = np.stack([sand, 100 - sand - clay, clay, np.full(x.shape, 2.0)])
        paths.append(rio.write(out_dir / "soil_texture.tif", texture, g30, description="synthetic sand, silt, clay, OC",
                               units="%", band_names=["sand", "silt", "clay", "organic_carbon"]))
        depth = np.clip(2.2 - (dem - dem.min()) / (dem.max() - dem.min()) * 1.8, 0.4, 2.0)
        paths.append(rio.write(out_dir / "soil_depth.tif", depth, g30, description="synthetic soil depth", units="m"))
        paths.append(rio.write(out_dir / "landcover.tif", np.where(forest, 10, 40), g30, dtype="uint8", nodata=255,
                               description="synthetic land cover", units="class",
                               tags={"LANDCOVER_SCHEME": "worldcover"}))

    # 0.1 deg lat/lon grid covering the UTM tile plus a one-pixel margin.
    to_ll = Transformer.from_crs(UTM, "EPSG:4326", always_xy=True)
    xs = [ORIGIN[0], ORIGIN[0] + span, ORIGIN[0], ORIGIN[0] + span]
    ys = [ORIGIN[1], ORIGIN[1], ORIGIN[1] - span, ORIGIN[1] - span]
    lon, lat = to_ll.transform(xs, ys)
    w, e = math.floor(min(lon) * 10) / 10 - 0.1, math.ceil(max(lon) * 10) / 10 + 0.1
    s, n = math.floor(min(lat) * 10) / 10 - 0.1, math.ceil(max(lat) * 10) / 10 + 0.1
    gll = rio.Grid(CRS.from_epsg(4326), Affine(0.1, 0, w, 0, -0.1, n), round((e - w) * 10), round((n - s) * 10))

    lon_c = w + (np.arange(gll.width) + 0.5) * 0.1
    soil = np.repeat(np.where(lon_c < (w + e) / 2, 2, 4)[None, :], gll.height, axis=0)
    paths.append(rio.write(out_dir / "soil_type.tif", soil, gll, dtype="uint8", nodata=255,
                           description="synthetic ERA5-Land soil_type", units="class"))

    scale = 0.8 + 0.4 * rng.random((gll.height, gll.width))
    for year in years:
        t0 = np.datetime64(f"{year}-01-01T00:00", "m")
        times = np.arange(t0, np.datetime64(f"{year + 1}-01-01T00:00", "m"), np.timedelta64(30, "m"))
        base = _storm_series(times, rng)
        paths.append(rio.write(out_dir / f"precip_{year}.tif", base[:, None, None] * scale, gll,
                               description="synthetic rain rate", units="mm/h",
                               tags={"TIME_START": f"{year}-01-01T00:00:00Z", "TIME_STEP_MINUTES": 30}))
    return paths


def _storm_series(times, rng):
    """Half-hourly rain rate (mm/h) made of discrete convective storms: ~0.5 storms/day in the
    Jun-Sep rainy season and ~0.05/day otherwise, 1-6 h long, fast rise and slow decay,
    gamma-distributed peak intensity (mean 10 mm/h). About 1,400 mm/yr."""
    month = times.astype("datetime64[M]").astype(int) % 12
    storms_per_day = np.where((month >= 5) & (month <= 8), 0.5, 0.05)
    rate = np.zeros(times.size)
    for s in np.flatnonzero(rng.random(times.size) < storms_per_day / 48):
        dur = int(rng.integers(2, 13))
        shape = np.interp(np.arange(dur), [0, 0.3 * (dur - 1), dur - 1], [0.3, 1.0, 0.1])
        seg = rate[s : s + dur]
        seg += rng.gamma(2.0, 5.0) * shape[: seg.size]
    return rate
