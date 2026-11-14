"""Earth Engine access: lazy login and tiled pixel downloads with computePixels.

The project comes from the EE_PROJECT environment variable, else the team project. Run
`earthengine authenticate` once on a new machine.
"""
from __future__ import annotations

import io
import os
import time
from concurrent.futures import ThreadPoolExecutor

import ee
import numpy as np
from numpy.lib import recfunctions as rfn

from ..dataanal import raster_io as rio

DEFAULT_PROJECT = "spaceapps-514713"
NODATA = -9999.0
MAX_BANDS = 1024  # computePixels limit per request
_initialised = False


def init() -> None:
    global _initialised
    if not _initialised:
        ee.Initialize(project=os.environ.get("EE_PROJECT") or DEFAULT_PROJECT)
        _initialised = True


TRANSIENT = ("too many", "rate limit", "quota", "429", "timed out", "deadline", "internal error",
             "unavailable", "503", "500", "connection", "reset by peer")


def retry(call, attempts: int = 5):
    """Run an Earth Engine call, retrying only errors that can pass (rate limits, timeouts,
    server/network hiccups). Errors in the request itself are raised immediately."""
    for attempt in range(attempts):
        try:
            return call()
        except Exception as err:  # noqa: BLE001 - network errors are not EEExceptions
            transient = not isinstance(err, ee.EEException) or any(m in str(err).lower() for m in TRANSIENT)
            if attempt == attempts - 1 or not transient:
                raise
            print(f"    retrying after: {str(err)[:200]}")
            time.sleep(3 * 2**attempt)


def pixels(image: ee.Image, grid: rio.Grid, col0: int = 0, row0: int = 0, width: int | None = None,
           height: int | None = None) -> np.ndarray:
    """One computePixels call on (part of) `grid` -> float32 (bands, height, width)."""
    init()
    width, height = width or grid.width, height or grid.height
    t = grid.transform
    request = {
        "expression": image,
        "fileFormat": "NPY",
        "grid": {
            "dimensions": {"width": width, "height": height},
            "affineTransform": {"scaleX": t.a, "shearX": t.b, "translateX": t.c + col0 * t.a,
                                "shearY": t.d, "scaleY": t.e, "translateY": t.f + row0 * t.e},
            "crsCode": grid.crs.to_string(),
        },
    }
    raw = retry(lambda: ee.data.computePixels(request))
    # Many-band structured arrays have headers larger than numpy's default limit.
    arr = np.load(io.BytesIO(raw), allow_pickle=False, max_header_size=1 << 22)
    data = rfn.structured_to_unstructured(arr) if arr.dtype.names else arr[..., None]
    return np.moveaxis(data.astype(np.float32), -1, 0)


def fetch(image: ee.Image, grid: rio.Grid, tile: int = 512, workers: int = 8) -> np.ndarray:
    """Download `image` on `grid` in tiles -> float32 (bands, H, W), NaN where masked."""
    init()
    image = ee.Image(image).toFloat().unmask(NODATA, False)
    jobs = [(r, c, min(tile, grid.height - r), min(tile, grid.width - c))
            for r in range(0, grid.height, tile) for c in range(0, grid.width, tile)]
    out = None
    with ThreadPoolExecutor(workers) as pool:
        for (r, c, h, w), block in pool.map(lambda j: (j, pixels(image, grid, j[1], j[0], j[3], j[2])), jobs):
            if out is None:
                out = np.empty((block.shape[0], grid.height, grid.width), np.float32)
            out[:, r:r + h, c:c + w] = block
    out[out == NODATA] = np.nan
    return out


def lonlat_bounds(grid: rio.Grid) -> tuple[float, float, float, float]:
    """(west, south, east, north) of the grid in degrees."""
    from rasterio.transform import array_bounds
    from rasterio.warp import transform_bounds

    return transform_bounds(grid.crs, "EPSG:4326", *array_bounds(grid.height, grid.width, grid.transform))


def region(grid: rio.Grid, margin_deg: float = 0.0) -> ee.Geometry:
    """The grid's footprint as a lon/lat rectangle (for filterBounds)."""
    w, s, e, n = lonlat_bounds(grid)
    return ee.Geometry.Rectangle([w - margin_deg, s - margin_deg, e + margin_deg, n + margin_deg], "EPSG:4326", False)
