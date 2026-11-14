"""Earth Engine access for the harness: pulls the pipeline's input GeoTIFFs.

`download_inputs(cfg)` writes dem.tif, soil_type.tif, aoi.tif, ndvi.tif, water.tif and
precip_<year>.tif in the format src.dataanal.raster_io expects.

Datasets (all read through Google Earth Engine):
  NASA GPM IMERG V07, half-hourly    NASA/GPM_L3/IMERG_V07          band `precipitation` (mm/h)
  ERA5-Land static fields            ECMWF/ERA5_LAND/STATIC         band `soil_type` (FAO texture class)
  Landsat 8/9 Collection 2 Level 2   LANDSAT/LC0{8,9}/C02/T1_L2     SR_B3..SR_B6 + QA_PIXEL
  FABDEM (30 m bare-earth DEM)       projects/sat-io/open-datasets/FABDEM
  HydroBASINS (study-area outline)   WWF/HydroSHEDS/v1/Basins/hybas_<level>
"""
from __future__ import annotations

import dataclasses
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import ee
import numpy as np
from affine import Affine
from numpy.lib import recfunctions as rfn
from pyproj import Transformer
from rasterio.crs import CRS

from ..dataanal import raster_io as rio

IMERG = "NASA/GPM_L3/IMERG_V07"
ERA5_LAND_STATIC = "ECMWF/ERA5_LAND/STATIC"
FABDEM = "projects/sat-io/open-datasets/FABDEM"
LANDSAT = ("LANDSAT/LC08/C02/T1_L2", "LANDSAT/LC09/C02/T1_L2")
HYBAS = "WWF/HydroSHEDS/v1/Basins/hybas_{}"

NODATA = -9999.0


def init(project: str | None) -> None:
    if not project:
        raise RuntimeError(
            "No Earth Engine Cloud project set. Run `earthengine authenticate` once, then set "
            "the EE_PROJECT environment variable to your Cloud project ID."
        )
    ee.Initialize(project=project)


@dataclass(frozen=True)
class Grid:
    """North-up raster grid: top-left corner (x0, y0) and square pixels of size `res`."""

    crs: str
    x0: float
    y0: float
    res: float
    width: int
    height: int

    @property
    def transform(self) -> Affine:
        return Affine(self.res, 0.0, self.x0, 0.0, -self.res, self.y0)

    def centers(self) -> tuple[np.ndarray, np.ndarray]:
        xs = self.x0 + (np.arange(self.width) + 0.5) * self.res
        ys = self.y0 - (np.arange(self.height) + 0.5) * self.res
        return xs, ys

    def as_dict(self) -> dict:
        return dataclasses.asdict(self)


# --------------------------------------------------------------------------- study area


def study_area(point, max_area_km2, bbox=None) -> tuple[ee.Geometry, dict]:
    """`bbox` (W, S, E, N) if given, else the largest HydroBASINS sub-basin containing
    `point` whose area is <= max_area_km2."""
    if bbox is not None:
        return ee.Geometry.Rectangle(list(bbox), "EPSG:4326", False), {"source": "bbox"}
    pt = ee.Geometry.Point(list(point))
    for level in range(4, 13):
        feat = ee.Feature(ee.FeatureCollection(HYBAS.format(level)).filterBounds(pt).first())
        props = feat.toDictionary(["HYBAS_ID", "SUB_AREA", "UP_AREA"]).getInfo()
        if props["SUB_AREA"] <= max_area_km2:
            return feat.geometry(), {"source": f"HydroBASINS level {level}", **props}
    raise ValueError(f"No HydroBASINS sub-basin <= {max_area_km2} km2 contains {point}")


def lonlat_bounds(geom: ee.Geometry) -> tuple[float, float, float, float]:
    ring = geom.bounds(1).coordinates().get(0).getInfo()
    lons, lats = zip(*ring)
    return min(lons), min(lats), max(lons), max(lats)


def utm_grid(bounds, res: float, buffer_m: float = 600.0) -> Grid:
    """Grid in the local UTM zone covering lon/lat `bounds` plus a buffer, snapped to `res`."""
    w, s, e, n = bounds
    zone = int((0.5 * (w + e) + 180) // 6) + 1
    epsg = (32600 if 0.5 * (s + n) >= 0 else 32700) + zone
    t = np.linspace(0, 1, 50)
    lon = np.concatenate([w + (e - w) * t, np.full(50, e), w + (e - w) * t, np.full(50, w)])
    lat = np.concatenate([np.full(50, s), s + (n - s) * t, np.full(50, n), s + (n - s) * t])
    x, y = Transformer.from_crs("EPSG:4326", f"EPSG:{epsg}", always_xy=True).transform(lon, lat)
    x0 = math.floor((x.min() - buffer_m) / res) * res
    x1 = math.ceil((x.max() + buffer_m) / res) * res
    y0 = math.floor((y.min() - buffer_m) / res) * res
    y1 = math.ceil((y.max() + buffer_m) / res) * res
    return Grid(f"EPSG:{epsg}", x0, y1, res, round((x1 - x0) / res), round((y1 - y0) / res))


def latlon_grid(bounds, res: float = 0.1, margin: int = 1) -> Grid:
    """IMERG-aligned 0.1 deg grid covering `bounds` plus `margin` pixels on every side."""
    w, s, e, n = bounds
    k = round(1 / res)
    x0, x1 = (math.floor(w * k) - margin) / k, (math.ceil(e * k) + margin) / k
    y0, y1 = (math.floor(s * k) - margin) / k, (math.ceil(n * k) + margin) / k
    return Grid("EPSG:4326", x0, y1, res, round((x1 - x0) * k), round((y1 - y0) * k))


# --------------------------------------------------------------------------- pixel transfer


def _pixels(image: ee.Image, grid: Grid, col0=0, row0=0, width=None, height=None) -> np.ndarray:
    """One computePixels call -> float32 array (bands, height, width)."""
    width, height = width or grid.width, height or grid.height
    request = {
        "expression": image,
        "fileFormat": "NUMPY_NDARRAY",
        "grid": {
            "dimensions": {"width": width, "height": height},
            "affineTransform": {
                "scaleX": grid.res, "shearX": 0, "translateX": grid.x0 + col0 * grid.res,
                "shearY": 0, "scaleY": -grid.res, "translateY": grid.y0 - row0 * grid.res,
            },
            "crsCode": grid.crs,
        },
    }
    for attempt in range(5):
        try:
            arr = ee.data.computePixels(request)
            break
        except ee.EEException as err:
            if attempt == 4:
                raise
            print(f"    retrying after Earth Engine error: {err}")
            time.sleep(3 * 2**attempt)
    data = rfn.structured_to_unstructured(arr) if arr.dtype.names else arr[..., None]
    return np.moveaxis(data.astype(np.float32), -1, 0)


def fetch(image: ee.Image, grid: Grid, tile: int = 512, workers: int = 8) -> np.ndarray:
    """Download `image` on `grid` in tiles -> float32 (bands, H, W), NaN where masked."""
    image = ee.Image(image).toFloat().unmask(NODATA, False)
    jobs = [
        (r, c, min(tile, grid.height - r), min(tile, grid.width - c))
        for r in range(0, grid.height, tile)
        for c in range(0, grid.width, tile)
    ]

    def run(job):
        r, c, h, w = job
        return job, _pixels(image, grid, c, r, w, h)

    out = None
    with ThreadPoolExecutor(workers) as pool:
        for i, ((r, c, h, w), block) in enumerate(pool.map(run, jobs), 1):
            if out is None:
                out = np.empty((block.shape[0], grid.height, grid.width), np.float32)
            out[:, r : r + h, c : c + w] = block
            print(f"    tile {i}/{len(jobs)}", end="\r")
    print()
    out[out == NODATA] = np.nan
    return out


# --------------------------------------------------------------------------- datasets


def static_image(geom: ee.Geometry) -> ee.Image:
    """Bands: FABDEM elevation (bilinear), ERA5-Land soil_type (nearest), basin mask (0/1)."""
    dem = (
        ee.ImageCollection(FABDEM)
        .map(lambda im: im.resample("bilinear"))
        .mosaic()
        .select([0], ["elevation"])
    )
    soil = ee.Image(ERA5_LAND_STATIC).select("soil_type")
    basin = ee.Image(0).paint(ee.FeatureCollection([ee.Feature(geom)]), 1).rename("basin")
    return ee.Image.cat([dem, soil, basin])


def _landsat_indices(img: ee.Image) -> ee.Image:
    # QA_PIXEL bits 0-5: fill, dilated cloud, cirrus, cloud, cloud shadow, snow.
    clear = img.select("QA_PIXEL").bitwiseAnd(0b111111).eq(0)
    sr = img.select(["SR_B3", "SR_B4", "SR_B5", "SR_B6"]).multiply(2.75e-5).add(-0.2)
    ndvi = sr.normalizedDifference(["SR_B5", "SR_B4"]).rename("ndvi")
    mndwi = sr.normalizedDifference(["SR_B3", "SR_B6"]).rename("mndwi")
    return ee.Image(ndvi.addBands(mndwi).updateMask(clear).copyProperties(img, ["system:time_start"]))


def ndvi_image(geom: ee.Geometry, start: str, end: str) -> ee.Image:
    """Bands ndvi_01..ndvi_12 (calendar-month medians, gaps filled with the all-period
    median), ndvi_annual, water (MNDWI > 0) and n_obs (clear observations)."""
    col = (
        ee.ImageCollection(LANDSAT[0]).merge(ee.ImageCollection(LANDSAT[1]))
        .filterBounds(geom)
        .filterDate(start, end)
        .map(_landsat_indices)
    )
    annual = col.median()
    # A fully masked placeholder keeps median() well-defined for a month with no scenes.
    empty = ee.ImageCollection([ee.Image.constant(0).toFloat().rename("ndvi").updateMask(0)])
    monthly = [
        col.select("ndvi")
        .filter(ee.Filter.calendarRange(m, m, "month"))
        .merge(empty)
        .median()
        .unmask(annual.select("ndvi"))
        .rename(f"ndvi_{m:02d}")
        for m in range(1, 13)
    ]
    return ee.Image.cat(
        monthly
        + [
            annual.select("ndvi").rename("ndvi_annual"),
            annual.select("mndwi").gt(0).rename("water"),
            col.select("ndvi").count().rename("n_obs"),
        ]
    )


def fetch_imerg(grid: Grid, start: datetime, end: datetime, cache_dir: Path,
                chunk_days: int = 10, workers: int = 8) -> tuple[np.ndarray, np.ndarray]:
    """Half-hourly IMERG rain rate on `grid` -> (times datetime64[ms] (T,), mm/h (T, H, W)).

    Each `chunk_days` window is one request (the collection stacked into bands) and is
    cached to disk, so an interrupted download resumes where it stopped."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    spans, t = [], start
    while t < end:
        spans.append((t, min(t + timedelta(days=chunk_days), end)))
        t = spans[-1][1]

    def run(span):
        t0, t1 = span
        path = cache_dir / f"imerg_{t0:%Y%m%d}_{t1:%Y%m%d}_{grid.x0}_{grid.y0}_{grid.width}x{grid.height}.npz"
        if path.exists():
            z = np.load(path)
            return z["times"], z["rate"]
        col = (
            ee.ImageCollection(IMERG)
            .filterDate(t0.strftime("%Y-%m-%dT%H:%M:%S"), t1.strftime("%Y-%m-%dT%H:%M:%S"))
            .select("precipitation")
            .sort("system:time_start")
        )
        times = np.array(col.aggregate_array("system:time_start").getInfo(), dtype="int64")
        rate = _pixels(col.toBands().toFloat().unmask(0, False), grid)
        if rate.shape[0] != times.size:
            raise RuntimeError(f"IMERG {t0:%Y-%m-%d}: {times.size} timestamps but {rate.shape[0]} bands")
        np.savez_compressed(path, times=times, rate=rate)
        return times, rate

    results = []
    with ThreadPoolExecutor(workers) as pool:
        for i, res in enumerate(pool.map(run, spans), 1):
            results.append(res)
            print(f"    IMERG chunk {i}/{len(spans)}", end="\r")
    print()
    times = np.concatenate([r[0] for r in results])
    rate = np.concatenate([r[1] for r in results])
    times, keep = np.unique(times, return_index=True)  # drop any duplicated time steps
    return times.astype("datetime64[ms]"), rate[keep]


# --------------------------------------------------------------------------- GeoTIFF export


def _rio_grid(g: Grid) -> rio.Grid:
    return rio.Grid(CRS.from_string(g.crs), g.transform, g.width, g.height)


def download_inputs(cfg, refresh: bool = False) -> list[Path]:
    """Fetch every pipeline input for `cfg` (a HarnessConfig) as GeoTIFFs; cached files are kept
    unless `refresh`. Returns the input paths."""
    init(cfg.ee_project)
    inputs, qa = cfg.inputs_dir, cfg.qa_dir
    inputs.mkdir(parents=True, exist_ok=True)
    qa.mkdir(parents=True, exist_ok=True)

    geom, aoi = study_area(cfg.aoi_point, cfg.aoi_max_area_km2, cfg.aoi_bbox)
    bounds = lonlat_bounds(geom)
    grid, rgrid = utm_grid(bounds, cfg.scale_m), latlon_grid(bounds)
    g30, g01 = _rio_grid(grid), _rio_grid(rgrid)
    (qa / "study_area.json").write_text(json.dumps(
        {**aoi, "bounds_lonlat": bounds, "grid_30m": g30.as_dict(), "grid_imerg": g01.as_dict()}, indent=2))
    print(f"Study area: {aoi}; 30 m grid {grid.width} x {grid.height} ({grid.crs}); "
          f"IMERG grid {rgrid.width} x {rgrid.height}")

    def stale(*names):
        return refresh or not all((inputs / n).exists() for n in names)

    if stale("dem.tif", "soil_type.tif", "aoi.tif"):
        print("Fetching FABDEM elevation, ERA5-Land soil type and the study-area mask")
        dem, soil, basin = fetch(static_image(geom), grid, workers=cfg.workers)
        rio.write(inputs / "dem.tif", dem, g30, description="FABDEM bare-earth elevation", units="m",
                  tags={"RUSLE_ROLE": "dem", "SOURCE": FABDEM})
        rio.write(inputs / "soil_type.tif", np.nan_to_num(soil, nan=255), g30, dtype="uint8", nodata=255,
                  description="ERA5-Land soil_type (FAO texture class)", units="class",
                  tags={"RUSLE_ROLE": "soil_type", "SOURCE": ERA5_LAND_STATIC})
        rio.write(inputs / "aoi.tif", np.nan_to_num(basin), g30, dtype="uint8", nodata=255,
                  description=f"study area ({aoi['source']})", units="mask", tags={"RUSLE_ROLE": "aoi"})

    if stale("ndvi.tif", "water.tif"):
        print("Fetching Landsat 8/9 monthly NDVI composites")
        stack = fetch(ndvi_image(geom, cfg.c_start, cfg.c_end), grid, tile=256, workers=cfg.workers)
        period = f"{cfg.c_start[:7]} to {np.datetime64(cfg.c_end, 'M') - 1}"
        rio.write(inputs / "ndvi.tif", stack[:12], g30, description="Landsat 8/9 C2 L2 NDVI, calendar-month medians",
                  units="NDVI", band_names=[f"ndvi_{m:02d}" for m in range(1, 13)],
                  tags={"RUSLE_ROLE": "ndvi", "SOURCE": ", ".join(LANDSAT), "TIME_RANGE": period})
        rio.write(inputs / "water.tif", np.nan_to_num(stack[13]), g30, dtype="uint8", nodata=255,
                  description="water (Landsat MNDWI > 0)", units="mask", tags={"RUSLE_ROLE": "water"})
        rio.write(qa / "landsat_clear_obs.tif", stack[14], g30, description="clear Landsat observations", units="count")
        obs = stack[14][np.isfinite(stack[14])]
        print(f"  clear Landsat observations per pixel: min {obs.min():.0f}, median {np.median(obs):.0f}")

    for year in range(cfg.start_year, cfg.end_year + 1):
        name = f"precip_{year}.tif"
        if not stale(name):
            continue
        print(f"Fetching IMERG V07 half-hourly precipitation for {year}")
        t0 = np.datetime64(f"{year}-01-01T00:00", "m")
        n = int((np.datetime64(f"{year + 1}-01-01T00:00", "m") - t0) // np.timedelta64(30, "m"))
        times, rate = fetch_imerg(rgrid, datetime(year, 1, 1), datetime(year + 1, 1, 1),
                                  cfg.data_dir / "imerg_cache", workers=cfg.workers)
        slot = ((times.astype("datetime64[m]") - t0) // np.timedelta64(30, "m")).astype(int)
        ok = (slot >= 0) & (slot < n)
        cube = np.full((n, rgrid.height, rgrid.width), np.nan, np.float32)  # gaps stay NaN
        cube[slot[ok]] = rate[ok]
        print(f"  {np.unique(slot[ok]).size} of {n} half-hours present")
        rio.write(inputs / name, cube, g01, description="IMERG V07 half-hourly precipitation rate", units="mm/h",
                  tags={"RUSLE_ROLE": "precip", "SOURCE": IMERG,
                        "TIME_START": f"{year}-01-01T00:00:00Z", "TIME_STEP_MINUTES": 30})

    return sorted(inputs.glob("*.tif"))
