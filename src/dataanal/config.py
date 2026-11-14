"""Run configuration for the RUSLE soil-loss pipeline."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Config:
    # --- Study area -------------------------------------------------------
    # The largest HydroBASINS sub-basin that contains `aoi_point` (lon, lat) and is
    # no bigger than `aoi_max_area_km2`. Default: Gilgel Abay, the main river feeding
    # Lake Tana in the Ethiopian Highlands (a well-documented erosion hotspot).
    # Set `aoi_bbox` = (west, south, east, north) to use a plain rectangle instead.
    aoi_name: str = "gilgel_abay_ethiopia"  # folder name for data/ and outputs/
    aoi_label: str = "Gilgel Abay catchment, Ethiopia"  # shown on figures
    aoi_point: tuple[float, float] = (37.10, 11.15)
    aoi_max_area_km2: float = 3000.0
    aoi_bbox: tuple[float, float, float, float] | None = None

    # --- Period -----------------------------------------------------------
    start_year: int = 2010  # R factor (IMERG) covers start_year..end_year inclusive
    end_year: int = 2015
    # Landsat 8 science operations began April 2013 and Landsat 9 launched in 2021,
    # so within 2010-2015 the C factor can only be built from 2013-04..2015-12.
    c_start: str = "2013-04-01"
    c_end: str = "2016-01-01"

    # --- Factor parameters -------------------------------------------------
    scale_m: float = 30.0  # output grid resolution (Landsat / FABDEM native)
    organic_carbon_pct: float = 1.5  # only used by the Williams K cross-check
    ls_m: float = 0.4  # Moore & Burch (1986) exponents
    ls_n: float = 1.3
    ls_max_specific_area_m: float = 300.0  # cap on upslope length; beyond this flow is channelised
    ls_routing: str = "mfd"  # pysheds flow routing: "mfd", "dinf" or "d8"
    p_factor: float = 1.0  # no conservation-practice data: P = 1 (upper bound)

    # --- Execution ---------------------------------------------------------
    ee_project: str | None = os.environ.get("EE_PROJECT")
    workers: int = 8  # concurrent Earth Engine requests

    @property
    def data_dir(self) -> Path:
        return REPO_ROOT / "data" / self.aoi_name

    @property
    def out_dir(self) -> Path:
        return REPO_ROOT / "outputs" / self.aoi_name
