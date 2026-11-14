"""Harness configuration: which area and period to pull from Earth Engine."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class HarnessConfig:
    # --- Study area -------------------------------------------------------
    # The largest HydroBASINS sub-basin that contains `aoi_point` (lon, lat) and is
    # no bigger than `aoi_max_area_km2`. Default: Gilgel Abay, the main river feeding
    # Lake Tana in the Ethiopian Highlands (a well-documented erosion hotspot).
    # Set `aoi_bbox` = (west, south, east, north) to use a plain rectangle instead.
    aoi_name: str = "gilgel_abay_ethiopia"  # folder name under data/ and outputs/
    aoi_label: str = "Gilgel Abay catchment, Ethiopia"  # shown on figures
    aoi_point: tuple[float, float] = (37.10, 11.15)
    aoi_max_area_km2: float = 3000.0
    aoi_bbox: tuple[float, float, float, float] | None = None

    # --- Period -----------------------------------------------------------
    start_year: int = 2010  # IMERG rainfall covers start_year..end_year inclusive
    end_year: int = 2015
    # Landsat 8 science operations began April 2013 and Landsat 9 launched in 2021,
    # so within 2010-2015 vegetation cover can only come from 2013-04..2015-12.
    c_start: str = "2013-04-01"
    c_end: str = "2016-01-01"  # exclusive

    # --- Execution ---------------------------------------------------------
    scale_m: float = 30.0
    ee_project: str | None = os.environ.get("EE_PROJECT")
    workers: int = 8  # concurrent Earth Engine requests

    @property
    def data_dir(self) -> Path:
        return REPO_ROOT / "data" / self.aoi_name

    @property
    def inputs_dir(self) -> Path:
        return self.data_dir / "inputs"  # the pipeline's input GeoTIFFs

    @property
    def qa_dir(self) -> Path:
        return self.data_dir / "qa"  # diagnostics that are not pipeline inputs

    @property
    def out_dir(self) -> Path:
        return REPO_ROOT / "outputs" / self.aoi_name
