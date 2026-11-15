"""NASA datasets beyond IMERG rain and Landsat NDVI, pulled through Earth Engine (NASA POWER over
HTTP) and cached in the global cache like global_data.

  SMAP L4 soil moisture, 9 km        NASA/SMAP/SPL4SMGP/008
  Burn history: MODIS + FIRMS        MODIS/061/MCD64A1, FIRMS
  HLS NDVI, half-month series, 30 m  NASA/HLS/HLSL30/v002, HLSS30/v002
  MERRA-2 wind (and POWER point)     NASA/GSFC/MERRA/slv/2      (POWER: power.larc.nasa.gov)
  MERRA-2 snow and snow loss         NASA/GSFC/MERRA/lnd/2

Earth Engine login as in ee_io. Every layer is cached on the exact grid it was pulled on and is
written only when complete; a failed pull caches nothing.

HLS NDVI is an analysis input: hls_ndvi_input writes ndvi_series.tif (role ndvi_series), from
which the pipeline derives the cover factor of each half-month for the dated views. The other
layers are not analysis inputs yet (their RUSLE_ROLE tags are not in raster_io.ROLES), so
`collect` does not copy them into working_save/inputs; `pull_all` fills the caches on demand.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import ee
import numpy as np

from ..dataanal import raster_io as rio
from . import cache, ee_io, shared

SMAP = "NASA/SMAP/SPL4SMGP/008"
MCD64A1 = "MODIS/061/MCD64A1"
FIRMS = "FIRMS"
HLS = (("NASA/HLS/HLSL30/v002", "B5"), ("NASA/HLS/HLSS30/v002", "B8A"))  # (collection, NIR band)
MERRA_SLV = "NASA/GSFC/MERRA/slv/2"
MERRA_LND = "NASA/GSFC/MERRA/lnd/2"
POWER_URL = "https://power.larc.nasa.gov/api/temporal/daily/point"

WIND_EROSION_THRESHOLD = 6.0  # m/s at 10 m: above this bare, dry soil starts to move
HLS_FMASK_BAD = 0b011110      # cloud, cloud-adjacent, cloud shadow, snow/ice
MAX_HLS_PIECES = 1000         # computePixels band limit is 1,024
NEVER_BURNED = 99             # years_since_burn where nothing burned in the record


def _ee_raster(folder: str, name: str, grid: rio.Grid, cache_dir: Path, make_image, **write) -> Path:
    def build():
        ee_io.init()
        return ee_io.fetch(make_image(), grid)

    return cache.cached_raster(Path(cache_dir) / folder / f"{cache.grid_key(grid)}_{name}.tif", grid, build, **write)


def _span(start, end) -> tuple[str, str]:
    start, end = str(np.datetime64(start, "D")), str(np.datetime64(end, "D"))
    if end <= start:
        raise ValueError(f"end {end} must be after start {start}")
    return start, end


# ============================================================================ SMAP soil moisture


def _smap_image(start: str, end: str) -> ee.Image:
    col = ee.ImageCollection(SMAP).filterDate(start, end).select(["sm_surface", "sm_rootzone"])
    return ee.Image.cat([col.select("sm_surface").mean().rename("sm_surface_mean"),
                         col.select("sm_rootzone").mean().rename("sm_rootzone_mean"),
                         col.select("sm_rootzone").min().rename("sm_rootzone_min")])


def smap_soil_moisture(grid: rio.Grid, start, end, cache_dir: Path) -> Path:
    """Mean surface (0-5 cm) and root-zone (0-100 cm) soil moisture and the driest root-zone
    value (m3/m3) for days [start, end). 9 km source: on a field grid it is smooth, not detailed."""
    start, end = _span(start, end)
    print(f"Soil moisture: SMAP L4 {start} to {end}")
    return _ee_raster("smap", f"{start}_{end}", grid, cache_dir, lambda: _smap_image(start, end),
                      description="SMAP L4 volumetric soil moisture: surface mean, root-zone mean, root-zone minimum",
                      units="m3 m-3", band_names=["sm_surface_mean", "sm_rootzone_mean", "sm_rootzone_min"],
                      tags={"RUSLE_ROLE": "soil_moisture", "SOURCE": SMAP, "TIME_RANGE": f"{start} to {end}"})


# ============================================================================ burn history


def _fire_image(y0: int, y1: int) -> ee.Image:
    burn = ee.ImageCollection(MCD64A1).filterDate(f"{y0}-01-01", f"{y1 + 1}-01-01").select("BurnDate")

    def per_month(img):
        burned = img.gt(0)  # BurnDate: day of year, 0 = not burned, -1 = unmapped
        year = ee.Image.constant(ee.Date(img.get("system:time_start")).get("year")).toFloat()
        return ee.Image.cat([burned.rename("burned"), year.updateMask(burned).rename("year")])

    months = burn.map(per_month)
    last = months.select("year").max()
    fires = ee.ImageCollection(FIRMS).filterDate(f"{y0}-01-01", f"{y1 + 1}-01-01").select("confidence")
    return ee.Image.cat([months.select("burned").sum().unmask(0).rename("burn_count"),  # unburned cells are masked
                         last.unmask(0).rename("last_burn_year"),
                         ee.Image.constant(y1).subtract(last).unmask(NEVER_BURNED).rename("years_since_burn"),
                         fires.max().unmask(0).rename("fire_confidence_max")])


def fire_burn_history(grid: rio.Grid, years, cache_dir: Path) -> Path:
    """Burn count, last burn year and years since the last burn (MODIS MCD64A1, 500 m; 99 = never
    in the record), plus the strongest FIRMS active-fire confidence (%) over `years`."""
    y0, y1 = min(years), max(years)
    print(f"Fire: MODIS burned area and FIRMS {y0}-{y1}")
    return _ee_raster("fire", f"{y0}-{y1}", grid, cache_dir, lambda: _fire_image(y0, y1),
                      description="MODIS burned area and FIRMS active-fire history",
                      units="mixed", band_names=["burn_count", "last_burn_year", "years_since_burn", "fire_confidence_max"],
                      tags={"RUSLE_ROLE": "fire_history", "SOURCE": f"{MCD64A1}, {FIRMS}", "TIME_RANGE": f"{y0} to {y1}"})


# ============================================================================ HLS NDVI


def _hls_ndvi(col_id: str, nir: str, geom, start: str, end: str) -> ee.ImageCollection:
    def ndvi(img):
        clear = img.select("Fmask").bitwiseAnd(HLS_FMASK_BAD).eq(0)
        return img.normalizedDifference([nir, "B4"]).toFloat().rename("ndvi").updateMask(clear)  # plain float, as `empty`

    return ee.ImageCollection(col_id).filterBounds(geom).filterDate(start, end).map(ndvi)


def _hls_image(geom, pieces) -> ee.Image:
    # A fully masked image keeps a half-month without any scene as one (NaN) band.
    empty = ee.ImageCollection([ee.Image.constant(0).toFloat().rename("ndvi").updateMask(0)])
    bands = []
    for a, b in pieces:
        col = _hls_ndvi(HLS[0][0], HLS[0][1], geom, str(a), str(b)).merge(
            _hls_ndvi(HLS[1][0], HLS[1][1], geom, str(a), str(b)))
        bands.append(col.merge(empty).median().rename(f"ndvi_{a}"))
    return ee.Image.cat(bands)


def hls_ndvi_series(grid: rio.Grid, start, end, cache_dir: Path) -> Path:
    """Half-month NDVI medians from HLS Landsat + Sentinel-2 (30 m, cloud/shadow/snow masked) for
    days [start, end). Band descriptions are `ndvi_<first day of the half month>`; NaN = no clear view."""
    start, end = _span(start, end)
    pieces = shared.half_months(start, end)
    if len(pieces) > MAX_HLS_PIECES:
        raise ValueError(f"{len(pieces)} half-months exceeds {MAX_HLS_PIECES}; pull shorter ranges")
    print(f"Vegetation: HLS NDVI {start} to {end} ({len(pieces)} half-months)")
    return _ee_raster("hls_ndvi", f"{start}_{end}", grid, cache_dir, lambda: _hls_image(ee_io.region(grid, 0.01), pieces),
                      description="HLS L30 + S30 NDVI, half-month medians", units="NDVI",
                      band_names=[f"ndvi_{a}" for a, _ in pieces],
                      tags={"RUSLE_ROLE": "hls_ndvi", "SOURCE": ", ".join(c for c, _ in HLS), "TIME_RANGE": f"{start} to {end}"})


def hls_ndvi_input(grid: rio.Grid, years, cache_dir: Path, out_dir: Path, workers: int = 4) -> Path:
    """ndvi_series.tif analysis input: HLS NDVI for every half-month of `years`, pulled and cached
    one calendar year at a time (so a later, longer period reuses the years it already has)."""
    years = list(years)
    with ThreadPoolExecutor(workers) as pool:
        rasters = [rio.read(p) for p in pool.map(
            lambda y: hls_ndvi_series(grid, f"{y}-01-01", f"{y + 1}-01-01", cache_dir), years)]
    return rio.write(Path(out_dir) / "ndvi_series.tif", np.concatenate([r.data for r in rasters]), grid,
                     description="HLS L30 + S30 NDVI, half-month medians", units="NDVI",
                     band_names=[d for r in rasters for d in r.descriptions],
                     tags={"RUSLE_ROLE": "ndvi_series", "SOURCE": ", ".join(c for c, _ in HLS),
                           "TIME_RANGE": f"{years[0]} to {years[-1]}"})


# ============================================================================ wind


def wind_speed(u, v):
    return np.hypot(u, v)


def wind_stats(speed, threshold: float = WIND_EROSION_THRESHOLD) -> dict:
    """Summary of an hourly wind-speed series (m/s): mean, max, share of hours above `threshold`,
    and wind erosivity = mean of speed^3 over the hours above it (m3 s-3)."""
    speed = np.asarray(speed, float)
    above = speed > threshold
    return {"mean": float(np.nanmean(speed)), "max": float(np.nanmax(speed)), "frac_above": float(np.mean(above)),
            "erosivity": float(np.mean(np.where(above, speed**3, 0.0)))}


def _wind_image(start: str, end: str, threshold: float) -> ee.Image:
    col = ee.ImageCollection(MERRA_SLV).filterDate(start, end).select(["U10M", "V10M"])
    ws = col.map(lambda im: im.select("U10M").hypot(im.select("V10M")).rename("ws").copyProperties(im, ["system:time_start"]))
    return ee.Image.cat([
        ws.mean().rename("ws10m_mean"), ws.max().rename("ws10m_max"),
        ws.map(lambda im: im.gt(threshold)).mean().multiply(100).rename("pct_hours_above_threshold"),
        ws.map(lambda im: im.pow(3).multiply(im.gt(threshold))).mean().rename("wind_erosivity")])


def merra2_wind(grid: rio.Grid, start, end, cache_dir: Path, threshold: float = WIND_EROSION_THRESHOLD) -> Path:
    """MERRA-2 hourly 10 m wind (about 50 km) over [start, end): mean and maximum speed (m/s),
    % of hours above `threshold` and wind erosivity (mean of speed^3 over those hours, m3/s3)."""
    start, end = _span(start, end)
    print(f"Wind: MERRA-2 10 m {start} to {end}")
    return _ee_raster("merra2_wind", f"{start}_{end}", grid, cache_dir, lambda: _wind_image(start, end, threshold),
                      description=f"MERRA-2 10 m wind, erosion threshold {threshold:g} m/s", units="mixed",
                      band_names=["ws10m_mean", "ws10m_max", "pct_hours_above_threshold", "wind_erosivity"],
                      tags={"RUSLE_ROLE": "wind_climatic", "SOURCE": MERRA_SLV, "TIME_RANGE": f"{start} to {end}",
                            "WIND_THRESHOLD_MS": threshold})


def power_wind_daily(lon: float, lat: float, start, end, cache_dir: Path) -> dict:
    """NASA POWER daily 10 m wind at a point (m/s): {"dates", "WS10M", "WS10M_MAX"}, -999 fill -> None."""
    import requests

    start, end = _span(start, end)
    last = str(np.datetime64(end, "D") - 1)

    def build():
        params = {"parameters": "WS10M,WS10M_MAX", "community": "AG", "longitude": round(lon, 4), "latitude": round(lat, 4),
                  "start": start.replace("-", ""), "end": last.replace("-", ""), "format": "JSON"}
        r = requests.get(POWER_URL, params=params, timeout=60)
        r.raise_for_status()
        par = r.json()["properties"]["parameter"]
        dates = sorted(par["WS10M"])
        clean = lambda k: [None if par[k][d] == -999 else par[k][d] for d in dates]  # noqa: E731
        return {"dates": dates, "WS10M": clean("WS10M"), "WS10M_MAX": clean("WS10M_MAX"), "source": POWER_URL}

    return cache.cached_json(Path(cache_dir) / "power_wind" / f"{lon:.3f}_{lat:.3f}_{start}_{end}.json", build)


# ============================================================================ snow


def _snow_image(start: str, end: str) -> ee.Image:
    col = ee.ImageCollection(MERRA_LND).filterDate(start, end).select(["SNOMAS", "PRECSNOLAND"])
    swe = col.select("SNOMAS")  # kg m-2 = mm water equivalent
    noon = swe.filter(ee.Filter.calendarRange(12, 12, "hour")).sort("system:time_start")  # one per day
    items = noon.toList(noon.size())
    drops = ee.ImageCollection.fromImages(ee.List.sequence(1, noon.size().subtract(1)).map(
        lambda i: ee.Image(items.get(ee.Number(i).subtract(1))).subtract(ee.Image(items.get(i))).max(0)))
    return ee.Image.cat([swe.mean().rename("swe_mean_mm"), swe.max().rename("swe_max_mm"),
                         col.select("PRECSNOLAND").sum().multiply(3600).rename("snowfall_mm"),
                         drops.sum().rename("swe_loss_mm"),
                         noon.map(lambda im: im.gt(1)).mean().multiply(100).rename("pct_days_snow")])


def merra2_snow(grid: rio.Grid, start, end, cache_dir: Path) -> Path:
    """MERRA-2 snow (about 50 km) over [start, end): mean and peak snow water equivalent, snowfall,
    day-to-day SWE loss (mm, a lower bound on melt: it ignores melt offset by same-day snowfall)
    and % of days with more than 1 mm SWE."""
    start, end = _span(start, end)
    print(f"Snow: MERRA-2 {start} to {end}")
    return _ee_raster("merra2_snow", f"{start}_{end}", grid, cache_dir, lambda: _snow_image(start, end),
                      description="MERRA-2 snow water equivalent, snowfall and SWE loss", units="mm",
                      band_names=["swe_mean_mm", "swe_max_mm", "snowfall_mm", "swe_loss_mm", "pct_days_snow"],
                      tags={"RUSLE_ROLE": "snow_melt", "SOURCE": MERRA_LND, "TIME_RANGE": f"{start} to {end}"})


# ============================================================================ everything at once


def pull_all(grid: rio.Grid, start, end, cache_dir: Path) -> tuple[dict[str, Path], list[str]]:
    """Pull every layer for days [start, end) on `grid`. A layer that cannot be pulled (server
    trouble, no data) is skipped, not cached, and named in the returned list."""
    from .collect import _optional  # here, not at the top: collect imports this module

    years = range(int(str(np.datetime64(start, "Y"))), int(str(np.datetime64(np.datetime64(end, "D") - 1, "Y"))) + 1)
    layers = {"smap": lambda: smap_soil_moisture(grid, start, end, cache_dir),
              "fire": lambda: fire_burn_history(grid, years, cache_dir),
              "hls_ndvi": lambda: hls_ndvi_series(grid, start, end, cache_dir),
              "wind": lambda: merra2_wind(grid, start, end, cache_dir),
              "snow": lambda: merra2_snow(grid, start, end, cache_dir)}
    skipped: list[str] = []
    found = {name: _optional(pull, name, skipped) for name, pull in layers.items()}
    return {k: v for k, v in found.items() if v is not None}, skipped
