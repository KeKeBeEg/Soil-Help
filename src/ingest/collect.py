"""Assemble the analysis inputs for a plan into one folder (working_save/inputs).

Every layer is pulled into its cache first (only if missing), then copied or derived into
the inputs folder under its role name, so the folder alone reproduces the analysis:

    python -m src.dataanal.pipeline --out <somewhere> <working_save>/inputs
"""
from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np

from ..dataanal import raster_io as rio
from . import global_data, shared, us_data

LATEST_CDL_YEAR = 2025


def _copy(src: Path, dst: Path) -> Path:
    shutil.copyfile(src, dst)
    return dst


def _optional(pull, what: str, defaults: list):
    """Pull an optional layer; on failure say so, add `what` to `defaults` and return None (the
    analysis then uses its default for that quantity). Nothing is cached for a failed pull, and
    the backend redoes an analysis that used a default, so the next run retries the pull."""
    try:
        return pull()
    except Exception as err:  # noqa: BLE001 - any network or server failure
        print(f"  {what} unavailable ({str(err).splitlines()[0][:150]}); continuing with the default")
        defaults.append(what)
        return None


def collect(plan, inputs_dir, global_cache, us_cache) -> tuple[list[Path], list[str]]:
    """Fill `inputs_dir` with every analysis input for `plan` (a backend.plan.Plan).
    Returns the input paths and the optional layers that could not be pulled (defaults used)."""
    inputs_dir = Path(inputs_dir)
    inputs_dir.mkdir(parents=True, exist_ok=True)
    for old in [*inputs_dir.glob("*.tif"), *inputs_dir.glob("*.npz")]:  # stale roles would be picked up
        old.unlink()
    gc, uc = Path(global_cache), Path(us_cache)
    years = range(plan.climate_years[0], plan.climate_years[1] + 1)
    fg = plan.field_grid
    defaults: list[str] = []

    paths = shared.precip_year_files(plan.rain_grid, years, gc, inputs_dir)
    paths += shared.landsat_inputs(fg, years, gc, inputs_dir)
    paths.append(rio.write(inputs_dir / "aoi.tif", np.ones((fg.height, fg.width)), fg, dtype="uint8", nodata=255,
                           description="field plus margin", units="mask", tags={"RUSLE_ROLE": "aoi"}))

    if plan.path == "global":
        paths.append(_copy(global_data.dem(plan.dem_grid, gc), inputs_dir / "dem.tif"))
        paths.append(_copy(global_data.soil_texture(fg, gc), inputs_dir / "soil_texture.tif"))
        depth = _optional(lambda: global_data.soil_depth(fg, gc), "Depth to bedrock", defaults)
        if depth is not None:
            paths.append(_copy(depth, inputs_dir / "soil_depth.tif"))
        paths.append(_copy(global_data.landcover(fg, gc), inputs_dir / "landcover.tif"))
        return paths, defaults

    paths.append(_copy(us_data.dem(plan.dem_grid, uc), inputs_dir / "dem.tif"))
    survey = rio.read(us_data.ssurgo(fg, uc)).data
    band = dict(zip(us_data.SSURGO_BANDS, survey))
    # Holes in the survey (water, urban land, rock outcrop, unmapped) fall back to global data.
    soilgrids = rio.read(global_data.soil_texture(fg, gc)).data
    bedrock_path = _optional(lambda: global_data.soil_depth(fg, gc), "Depth to bedrock (gap filler)", defaults)
    bedrock = rio.read(bedrock_path).data[0] if bedrock_path else np.full((fg.height, fg.width), np.nan)
    has_texture = np.isfinite(band["sand"])
    texture = np.where(has_texture, survey[4:8], soilgrids)
    texture[3] = np.where(np.isfinite(texture[3]), texture[3], soilgrids[3])
    depth = np.where(np.isfinite(band["soil_depth"]), band["soil_depth"], bedrock)
    src = "USDA NRCS SSURGO"
    for role, data, units in [("k_factor", band["k_factor"], "t ha h ha-1 MJ-1 mm-1"),
                              ("soil_tolerance", band["soil_tolerance"], "t ha-1 yr-1"),
                              ("ksat", band["ksat"], "m day-1")]:
        paths.append(rio.write(inputs_dir / f"{role}.tif", data, fg, description=f"SSURGO {role}", units=units,
                               tags={"RUSLE_ROLE": role, "SOURCE": src}))
    paths.append(rio.write(inputs_dir / "soil_depth.tif", depth, fg, units="m",
                           description="SSURGO depth to restrictive layer, gaps from ISRIC depth to bedrock",
                           tags={"RUSLE_ROLE": "soil_depth", "SOURCE": f"{src} + ISRIC BDRICM"}))
    paths.append(rio.write(inputs_dir / "soil_texture.tif", texture, fg, units="%",
                           band_names=["sand", "silt", "clay", "organic_carbon"],
                           description="SSURGO 0-30 cm texture and organic carbon, gaps from SoilGrids",
                           tags={"RUSLE_ROLE": "soil_texture", "SOURCE": f"{src} + SoilGrids 2.0"}))
    year = min(plan.climate_years[1], LATEST_CDL_YEAR)
    paths.append(_copy(us_data.landcover(fg, uc, year), inputs_dir / "landcover.tif"))
    return paths, defaults
