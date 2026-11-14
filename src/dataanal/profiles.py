"""Reporting profiles: units, practice names and policy screens per region.

Profiles only change how results are reported; the physics is the same everywhere. Rasters
stay in SI units (t/ha); summaries and advice use the profile's units.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import factors

T_HA_PER_TON_ACRE = 2.2417  # US short tons per acre -> metric tonnes per hectare
ACRES_PER_HA = 2.47105


@dataclass(frozen=True)
class Profile:
    name: str
    loss_unit: str
    loss_per_t_ha: float
    area_unit: str
    area_per_ha: float
    practices: dict  # recommended_practice code -> name
    cover_practice: str
    hel_screening: bool  # USDA highly-erodible-land screening

    def loss(self, t_ha):
        return None if t_ha is None else float(t_ha) * self.loss_per_t_ha

    def area(self, ha):
        return float(ha) * self.area_per_ha


GLOBAL = Profile("global", "t/ha", 1.0, "ha", 1.0, dict(factors.PRACTICES),
                 "keep the soil covered with a cover crop, mulch or crop residue", False)
US = Profile("us", "tons/acre", 1 / T_HA_PER_TON_ACRE, "acres", ACRES_PER_HA,
             {0: "no change needed", 1: "Contour Farming (NRCS 330)", 2: "Stripcropping (NRCS 585)",
              3: "Terrace (NRCS 600)", 4: "Conservation Cover (NRCS 327)"},
             "plant a Cover Crop (NRCS 340) or keep residue with Residue and Tillage Management (NRCS 329)", True)
PROFILES = {"global": GLOBAL, "us": US}

HEL_EI = 8.0  # erodibility index at or above which a soil is highly erodible


def hel_screen(ei, cell_ha: float) -> dict:
    """USDA rule of thumb: a field is highly erodible land (HEL) when a third or more of it,
    or 50 acres or more, has EI >= 8. Screening only: NRCS makes the official determination
    from mapped soil units."""
    ei = np.asarray(ei, float)
    ei = ei[np.isfinite(ei)]
    if ei.size == 0:
        return {"share_ei_ge_8_pct": None, "acres_ei_ge_8": None, "likely_hel": None}
    share = float(np.mean(ei >= HEL_EI))
    acres = float(np.sum(ei >= HEL_EI) * cell_ha * ACRES_PER_HA)
    return {"share_ei_ge_8_pct": 100 * share, "acres_ei_ge_8": acres, "likely_hel": bool(share >= 1 / 3 or acres >= 50)}
