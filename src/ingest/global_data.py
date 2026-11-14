"""Global-path datasets, cached in the global cache.

  FABDEM 30 m bare-earth DEM      projects/sat-io/open-datasets/FABDEM (Earth Engine)
  SoilGrids 2.0, 250 m            projects/soilgrids-isric/{sand,silt,clay,soc}_mean (Earth Engine)
  Depth to bedrock, 250 m         ISRIC BDRICM (Shangguan et al. 2017), read over HTTP
  ESA WorldCover v200, 10 m       ESA/WorldCover/v200 (Earth Engine)

FABDEM is CC BY-NC-SA 4.0 (non-commercial): "FABDEM is produced using Copernicus WorldDEM-30
(c) DLR e.V."
"""
from __future__ import annotations

from pathlib import Path

import ee
import numpy as np
import rasterio
from rasterio.crs import CRS
from rasterio.warp import Resampling
from rasterio.windows import Window, from_bounds

from ..dataanal import raster_io as rio
from . import cache, ee_io

FABDEM = "projects/sat-io/open-datasets/FABDEM"
SOILGRIDS = "projects/soilgrids-isric/{}_mean"
WORLDCOVER = "ESA/WorldCover/v200"
BDRICM_URL = "https://files.isric.org/soilgrids/former/2017-03-10/data/BDRICM_M_250m_ll.tif"


def _ee_raster(dataset: str, grid: rio.Grid, cache_dir: Path, make_image, **write) -> Path:
    def build():
        ee_io.init()
        return ee_io.fetch(make_image(), grid)

    return cache.cached_raster(Path(cache_dir) / dataset / f"{cache.grid_key(grid)}.tif", grid, build, **write)


def dem(grid: rio.Grid, cache_dir: Path) -> Path:
    print("Elevation: FABDEM 30 m")
    return _ee_raster("fabdem", grid, cache_dir,
                      lambda: ee.ImageCollection(FABDEM).map(lambda im: im.resample("bilinear")).mosaic().select([0]),
                      description="FABDEM bare-earth elevation", units="m",
                      tags={"RUSLE_ROLE": "dem", "SOURCE": FABDEM})


def soil_texture(grid: rio.Grid, cache_dir: Path) -> Path:
    """Sand, silt, clay and organic carbon (%), 0-30 cm depth-weighted means."""
    print("Soil: SoilGrids 2.0 texture and organic carbon")

    def image():
        def top30(prop, to_pct):
            img = ee.Image(SOILGRIDS.format(prop))
            mean = img.select(0).multiply(5).add(img.select(1).multiply(10)).add(img.select(2).multiply(15)).divide(30)
            return mean.multiply(to_pct)
        # g/kg -> % (x 0.1); SOC dg/kg -> % (x 0.01)
        return ee.Image.cat([top30("sand", 0.1), top30("silt", 0.1), top30("clay", 0.1), top30("soc", 0.01)])

    return _ee_raster("soilgrids", grid, cache_dir, image, description="SoilGrids 2.0 0-30 cm: sand, silt, clay, OC",
                      units="%", band_names=["sand", "silt", "clay", "organic_carbon"],
                      tags={"RUSLE_ROLE": "soil_texture", "SOURCE": "SoilGrids 2.0 (ISRIC)"})


def soil_depth(grid: rio.Grid, cache_dir: Path) -> Path:
    """Depth to bedrock (m), censored at 2 m, read from ISRIC's global 250 m GeoTIFF."""
    print("Soil depth: ISRIC depth to bedrock (BDRICM)")

    def build():
        w, s, e, n = ee_io.lonlat_bounds(grid)
        # ISRIC's server is sometimes very slow (minutes); give up after ~1 min per request and
        # let the caller fall back to the default depth instead of stalling the whole run.
        env = dict(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR", CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif",
                   GDAL_HTTP_MAX_RETRY="2", GDAL_HTTP_RETRY_DELAY="2", GDAL_HTTP_TIMEOUT="60",
                   GDAL_HTTP_CONNECTTIMEOUT="20")
        with rasterio.Env(**env), rasterio.open("/vsicurl/" + BDRICM_URL) as src:
            win = from_bounds(w - 0.01, s - 0.01, e + 0.01, n + 0.01, src.transform).round_offsets().round_lengths()
            win = win.intersection(Window(0, 0, src.width, src.height))
            data = src.read(1, window=win).astype(np.float32)
            data[data == src.nodata] = np.nan
            transform = src.window_transform(win)
        return rio.align(data / 100.0, transform, CRS.from_epsg(4326), grid, Resampling.bilinear)

    return cache.cached_raster(Path(cache_dir) / "bedrock_depth" / f"{cache.grid_key(grid)}.tif", grid, build,
                               description="depth to bedrock, censored at 2 m (Shangguan et al. 2017)", units="m",
                               tags={"RUSLE_ROLE": "soil_depth", "SOURCE": "ISRIC BDRICM 250 m"})


def landcover(grid: rio.Grid, cache_dir: Path) -> Path:
    print("Land cover: ESA WorldCover 2021")
    return _ee_raster("worldcover", grid, cache_dir,
                      lambda: ee.ImageCollection(WORLDCOVER).mosaic().select("Map"),
                      description="ESA WorldCover v200 (2021) land-cover class", units="class", dtype="uint8",
                      nodata=255, tags={"RUSLE_ROLE": "landcover", "LANDCOVER_SCHEME": "worldcover",
                                        "SOURCE": WORLDCOVER})
