"""Datasets used by both paths, cached in the global cache.

  IMERG V07 half-hourly rain  NASA/GPM_L3/IMERG_V07           band precipitation (mm/h)
  Landsat 8/9 C2 L2           LANDSAT/LC0{8,9}/C02/T1_L2       monthly NDVI medians + MNDWI water
  HydroBASINS level 12        WWF/HydroSHEDS/v1/Basins/hybas_12   sub-basins around the field
"""
from __future__ import annotations

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import ee
import numpy as np

from ..dataanal import raster_io as rio
from . import cache, ee_io

IMERG = "NASA/GPM_L3/IMERG_V07"
LANDSAT = ("LANDSAT/LC08/C02/T1_L2", "LANDSAT/LC09/C02/T1_L2")
HYBAS = "WWF/HydroSHEDS/v1/Basins/hybas_12"
STEP = np.timedelta64(30, "m")

# ============================================================================ IMERG


def half_months(start, end) -> list[tuple[np.datetime64, np.datetime64]]:
    """Fixed half-month pieces [1st, 16th) and [16th, next 1st) covering days [start, end).
    Each holds <= 768 half-hours, under the 1,024-band request limit, and fixed boundaries
    make every piece reusable from the cache."""
    start, end = np.datetime64(start, "D"), np.datetime64(end, "D")
    month = start.astype("datetime64[M]")
    out = []
    while month.astype("datetime64[D]") < end:
        first = month.astype("datetime64[D]")
        nxt = (month + 1).astype("datetime64[D]")
        for a, b in ((first, first + 15), (first + 15, nxt)):
            if b > start and a < end:
                out.append((a, b))
        month += 1
    return out


def _imerg_piece(grid: rio.Grid, a: np.datetime64, b: np.datetime64, cache_dir: Path):
    """One half-month of IMERG -> (times datetime64[m], rate (T, h, w)); cached once complete."""
    path = Path(cache_dir) / "imerg" / cache.grid_key(grid) / f"{str(a).replace('-', '')}.npz"
    if path.exists():
        z = np.load(path)
        return z["times"].astype("datetime64[m]"), z["rate"]
    ee_io.init()
    col = (ee.ImageCollection(IMERG).filterDate(str(a), str(b)).select("precipitation").sort("system:time_start"))
    ms = np.array(ee_io.retry(col.aggregate_array("system:time_start").getInfo), dtype="int64")
    times = ms.astype("datetime64[ms]").astype("datetime64[m]")
    rate = (ee_io.pixels(col.toBands().toFloat().unmask(0, False), grid) if times.size
            else np.zeros((0, grid.height, grid.width), np.float32))
    if rate.shape[0] != times.size:
        raise RuntimeError(f"IMERG {a}: {times.size} timestamps but {rate.shape[0]} bands")
    complete = np.datetime64(b, "m") <= np.datetime64(datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=2), "m")
    if complete:
        def write(tmp: Path):
            with open(tmp, "wb") as f:
                np.savez_compressed(f, times=times.astype("int64"), rate=rate)
        cache.cached_file(path, write)
    return times, rate


def imerg_series(grid: rio.Grid, start, end, cache_dir: Path, workers: int = 8):
    """Half-hourly rain rate (mm/h) for days [start, end) on `grid`.

    Returns (times, rate): a regular 30-min axis and (T, h, w) values, NaN where IMERG has
    no image for a time step."""
    pieces = half_months(start, end)
    t0 = np.datetime64(start, "D").astype("datetime64[m]")
    n = int((np.datetime64(end, "D").astype("datetime64[m]") - t0) // STEP)
    rate = np.full((n, grid.height, grid.width), np.nan, np.float32)
    with ThreadPoolExecutor(workers) as pool:
        for i, (times, values) in enumerate(pool.map(lambda p: _imerg_piece(grid, *p, cache_dir), pieces), 1):
            slot = ((times - t0) // STEP).astype(int)
            ok = (slot >= 0) & (slot < n)
            rate[slot[ok]] = values[ok]
            if len(pieces) > 4:
                print(f"    IMERG {i}/{len(pieces)} half-months", end="\r")
    if len(pieces) > 4:
        print()
    return t0 + np.arange(n) * STEP, rate


def precip_year_files(grid: rio.Grid, years, cache_dir: Path, out_dir: Path) -> list[Path]:
    """precip_<year>.npz analysis inputs (rain rate, one band per half hour) for whole years.
    .npz rather than GeoTIFF: GDAL takes ~15 s to write or read 17,520 bands, numpy ~0.1 s."""
    years = list(years)
    print(f"Rain: IMERG {years[0]}-{years[-1]} on {grid.width} x {grid.height} cells of 0.1 deg")
    times, rate = imerg_series(grid, f"{years[0]}-01-01", f"{years[-1] + 1}-01-01", cache_dir)
    year_of = times.astype("datetime64[Y]").astype(int) + 1970
    paths = []
    for y in years:
        sel = year_of == y
        paths.append(rio.write_npz(Path(out_dir) / f"precip_{y}.npz", rate[sel], grid, tags={
            "RUSLE_ROLE": "precip", "SOURCE": IMERG, "TIME_START": f"{y}-01-01T00:00:00Z", "TIME_STEP_MINUTES": 30,
            "UNITS": "mm/h", "DESCRIPTION": "IMERG V07 half-hourly precipitation rate"}))
    return paths


# ============================================================================ Landsat NDVI


def _landsat_indices(img: ee.Image) -> ee.Image:
    # QA_PIXEL bits 0-5: fill, dilated cloud, cirrus, cloud, cloud shadow, snow.
    clear = img.select("QA_PIXEL").bitwiseAnd(0b111111).eq(0)
    sr = img.select(["SR_B3", "SR_B4", "SR_B5", "SR_B6"]).multiply(2.75e-5).add(-0.2)
    ndvi = sr.normalizedDifference(["SR_B5", "SR_B4"]).toFloat().rename("ndvi")
    mndwi = sr.normalizedDifference(["SR_B3", "SR_B6"]).rename("mndwi")
    return ee.Image(ndvi.addBands(mndwi).updateMask(clear).copyProperties(img, ["system:time_start"]))


def _ndvi_image(geom: ee.Geometry, start: str, end: str) -> ee.Image:
    """ndvi_01..ndvi_12 (calendar-month medians, gaps filled with the all-period median),
    ndvi_annual, water (MNDWI > 0) and n_obs (clear observations)."""
    col = (ee.ImageCollection(LANDSAT[0]).merge(ee.ImageCollection(LANDSAT[1]))
           .filterBounds(geom).filterDate(start, end).map(_landsat_indices))
    annual = col.median()
    empty = ee.ImageCollection([ee.Image.constant(0).toFloat().rename("ndvi").updateMask(0)])
    monthly = [col.select("ndvi").filter(ee.Filter.calendarRange(m, m, "month")).merge(empty).median()
               .unmask(annual.select("ndvi")).rename(f"ndvi_{m:02d}") for m in range(1, 13)]
    return ee.Image.cat(monthly + [annual.select("ndvi").rename("ndvi_annual"),
                                   annual.select("mndwi").gt(0).rename("water"),
                                   col.select("ndvi").count().rename("n_obs")])


def landsat_inputs(grid: rio.Grid, years, cache_dir: Path, out_dir: Path) -> list[Path]:
    """ndvi.tif (12 monthly medians) and water.tif analysis inputs for `years`."""
    y0, y1 = min(years), max(years)
    path = Path(cache_dir) / "landsat_ndvi" / f"{cache.grid_key(grid)}_{y0}-{y1}.tif"
    print(f"Vegetation: Landsat 8/9 monthly NDVI {y0}-{y1}")

    def build():
        ee_io.init()
        return ee_io.fetch(_ndvi_image(ee_io.region(grid, 0.01), f"{y0}-01-01", f"{y1 + 1}-01-01"), grid, tile=256)

    stack = rio.read(cache.cached_raster(
        path, grid, build,
        description="Landsat 8/9 C2 L2: NDVI Jan..Dec medians, annual median, water, clear obs", units="mixed",
        tags={"SOURCE": ", ".join(LANDSAT), "TIME_RANGE": f"{y0}-{y1}"})).data
    out_dir = Path(out_dir)
    return [
        rio.write(out_dir / "ndvi.tif", stack[:12], grid, description="Landsat 8/9 C2 L2 NDVI, calendar-month medians",
                  units="NDVI", band_names=[f"ndvi_{m:02d}" for m in range(1, 13)],
                  tags={"RUSLE_ROLE": "ndvi", "SOURCE": ", ".join(LANDSAT), "TIME_RANGE": f"{y0} to {y1}"}),
        rio.write(out_dir / "water.tif", np.nan_to_num(stack[13]), grid, dtype="uint8", nodata=255,
                  description="water (Landsat MNDWI > 0)", units="mask", tags={"RUSLE_ROLE": "water", "SOURCE": "Landsat"}),
    ]


# ============================================================================ HydroBASINS


def basins(polygon_lonlat, cache_dir: Path) -> dict:
    """Level-12 HydroBASINS sub-basins touching the field: ids, areas, lon/lat bounds."""
    coords = [[round(x, 6), round(y, 6)] for x, y in polygon_lonlat]
    key = hashlib.sha1(json.dumps(coords).encode()).hexdigest()[:16]

    def build():
        ee_io.init()
        fc = ee.FeatureCollection(HYBAS).filterBounds(ee.Geometry.Polygon([coords], "EPSG:4326", False))
        info = ee_io.retry(lambda: ee.Dictionary({
            "ids": fc.aggregate_array("HYBAS_ID"), "areas": fc.aggregate_array("SUB_AREA"),
            "bounds": fc.geometry().bounds(1).coordinates().get(0)}).getInfo())
        lons, lats = zip(*info["bounds"])
        return {"ids": info["ids"], "sub_area_km2": info["areas"], "bounds_lonlat": [min(lons), min(lats), max(lons), max(lats)]}

    return cache.cached_json(Path(cache_dir) / "hydrobasins" / f"{key}.json", build)
