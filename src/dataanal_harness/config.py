"""Harness configuration: where synthetic inputs and outputs go."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass
class HarnessConfig:
    aoi_name: str = "synthetic"  # folder name under data/ and outputs/
    aoi_label: str = "Synthetic test area"  # shown on figures
    start_year: int = 2010  # first year of synthetic rainfall

    @property
    def data_dir(self) -> Path:
        return REPO_ROOT / "data" / self.aoi_name

    @property
    def inputs_dir(self) -> Path:
        return self.data_dir / "inputs"

    @property
    def out_dir(self) -> Path:
        return REPO_ROOT / "outputs" / self.aoi_name
