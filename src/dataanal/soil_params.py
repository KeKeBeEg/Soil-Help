"""Soil and land-cover parameters for the landslide model: lookups, no file access.

USDA texture class -> friction angle, effective cohesion, saturated conductivity;
land-cover class (any supported scheme) -> shared land-cover group -> root cohesion.
Values are starting points from the project README, to be calibrated; rows marked
"assumed" were not in the README table and are interpolated from their neighbours.
"""
from __future__ import annotations

import numpy as np

# ============================================================================ USDA texture

USDA_CLASSES = {1: "sand", 2: "loamy sand", 3: "sandy loam", 4: "loam", 5: "silt loam", 6: "silt",
                7: "sandy clay loam", 8: "clay loam", 9: "silty clay loam", 10: "sandy clay",
                11: "silty clay", 12: "clay"}


def usda_texture_class(sand, silt, clay):
    """USDA texture class code (1-12, see USDA_CLASSES) from percentages; 0 where undefined.

    Fractions are rescaled to sum to 100 first, so g/kg or slightly inconsistent inputs
    still classify."""
    s, si, c = (np.asarray(v, float) for v in (sand, silt, clay))
    total = s + si + c
    ok = np.isfinite(total) & (total > 0)
    with np.errstate(invalid="ignore", divide="ignore"):
        s, si, c = (np.where(ok, 100 * v / total, np.nan) for v in (s, si, c))
    cls = np.select(
        [
            si + 1.5 * c < 15,
            si + 2 * c < 30,
            (c >= 40) & (si >= 40),
            (c >= 40) & (s <= 45),
            (c >= 35) & (s > 45),
            (c >= 27) & (s <= 20),
            (c >= 27) & (s <= 45),
            (c >= 20) & (si < 28) & (s > 45),
            (si >= 80) & (c < 12),
            si >= 50,
            (c >= 7) & (si >= 28) & (s <= 52),
        ],
        [1, 2, 11, 12, 10, 9, 8, 7, 6, 5, 4],
        default=3,  # sandy loam: what is left of the triangle
    )
    return np.where(ok, cls, 0).astype(np.uint8)


# code: (friction angle deg, effective cohesion min kPa, max kPa)
STRENGTH = {
    1: (33.0, 0.0, 1.0),
    2: (32.0, 1.0, 1.0),
    3: (30.0, 2.0, 4.0),
    4: (28.0, 4.0, 6.0),
    5: (27.0, 3.0, 5.0),
    6: (28.0, 2.0, 4.0),  # assumed
    7: (27.0, 4.0, 8.0),  # assumed
    8: (25.0, 6.0, 10.0),
    9: (25.0, 5.0, 9.0),  # assumed
    10: (24.0, 6.0, 10.0),  # assumed
    11: (22.0, 7.0, 11.0),  # assumed
    12: (22.0, 8.0, 12.0),
}
DEFAULT_TEXTURE = 4  # loam, used where texture is unknown

# Saturated hydraulic conductivity by class, Clapp & Hornberger (1978), 1e-4 cm/s
# (silt is not in their table; silt loam's value is used).
_KSAT_1E4_CM_S = {1: 176.0, 2: 156.3, 3: 34.1, 4: 6.95, 5: 7.2, 6: 7.2, 7: 6.3, 8: 2.45, 9: 1.7,
                  10: 2.16, 11: 1.03, 12: 1.28}
KSAT_M_DAY = {k: v * 1e-6 * 86400 for k, v in _KSAT_1E4_CM_S.items()}


def _lut(table: dict, index: int, default_key: int, size: int = 13):
    lut = np.empty(size)
    for k in range(size):
        row = table.get(k, table[default_key])
        lut[k] = row[index] if isinstance(row, tuple) else row
    return lut


def strength_params(texture_class):
    """(phi_deg, c_min_kPa, c_max_kPa) arrays for texture codes; unknown codes -> loam."""
    t = np.clip(np.asarray(texture_class, int), 0, 12)
    return tuple(_lut(STRENGTH, i, DEFAULT_TEXTURE)[t] for i in range(3))


def ksat_from_texture(texture_class):
    """Matrix saturated conductivity (m/day) for texture codes; unknown codes -> loam."""
    t = np.clip(np.asarray(texture_class, int), 0, 12)
    return _lut(KSAT_M_DAY, 0, DEFAULT_TEXTURE)[t]


# ============================================================================ land cover

LANDCOVER_GROUPS = {0: "unknown", 1: "cropland", 2: "grass / pasture", 3: "shrub / orchard", 4: "trees",
                    5: "bare", 6: "built-up", 7: "water / snow", 8: "herbaceous wetland"}
# Root cohesion range per group, kPa (README table; wetland treated like grass).
ROOT_COHESION_KPA = {0: (0.0, 0.0), 1: (0.0, 0.0), 2: (1.0, 2.0), 3: (2.0, 5.0), 4: (5.0, 10.0),
                     5: (0.0, 0.0), 6: (0.0, 0.0), 7: (0.0, 0.0), 8: (1.0, 2.0)}

# Ceiling on RUSLE C for permanent cover. NDVI sees only green leaves, so leafless forest and
# dormant grass read as nearly bare (C 0.1-0.2) although litter and thatch still protect the
# soil. Caps at the top of published ranges (forest 0.0001-0.003, pasture/grassland
# 0.01-0.05, shrubland 0.01-0.1; Panagos et al. 2015). Cropland, bare and built-up: no cap.
C_CAP_BY_GROUP = {2: 0.05, 3: 0.1, 4: 0.003, 8: 0.05}

WORLDCOVER_TO_GROUP = {10: 4, 20: 3, 30: 2, 40: 1, 50: 6, 60: 5, 70: 7, 80: 7, 90: 8, 95: 4, 100: 2}


def _cdl_to_group() -> dict[int, int]:
    """USDA Cropland Data Layer codes -> groups. Every crop code not listed is cropland."""
    table = {code: 1 for code in list(range(1, 62)) + list(range(204, 255))}
    groups = {
        2: [36, 37, 58, 59, 60, 62, 176],  # hay, alfalfa, clover, sod, switchgrass, pasture, grassland
        3: [64, 66, 67, 68, 69, 70, 71, 72, 74, 75, 76, 77, 152, 204, 210, 211, 212, 215, 217, 218, 220,
            223, 242],  # shrubland, orchards, vines, Christmas trees, tree nuts, blueberries
        4: [63, 141, 142, 143, 190],  # forest, woody wetlands
        5: [65, 131],  # barren
        6: [82, 121, 122, 123, 124],  # developed
        7: [83, 92, 111, 112],  # water, aquaculture, ice/snow
        8: [87, 195],  # wetlands, herbaceous wetlands
        0: [0, 81, 88],  # background, clouds, undefined
    }
    for group, codes in groups.items():
        table.update({c: group for c in codes})
    return table


CDL_TO_GROUP = _cdl_to_group()
SCHEMES = {"worldcover": WORLDCOVER_TO_GROUP, "cdl": CDL_TO_GROUP, "groups": {k: k for k in LANDCOVER_GROUPS}}


def landcover_groups(classes, scheme: str = "worldcover"):
    """Map a land-cover raster in `scheme` to LANDCOVER_GROUPS codes (0 = unknown / no data)."""
    if scheme not in SCHEMES:
        raise ValueError(f"unknown land-cover scheme {scheme!r}; known: {', '.join(SCHEMES)}")
    lut = np.zeros(256, np.uint8)
    for code, group in SCHEMES[scheme].items():
        lut[code] = group
    c = np.asarray(classes, float)
    ok = np.isfinite(c) & (c >= 0) & (c <= 255)
    out = np.zeros(c.shape, np.uint8)
    out[ok] = lut[c[ok].astype(int)]
    return out


def c_cap(groups):
    """Largest C allowed for each land-cover group (1.0 = no cap)."""
    lut = np.ones(9)
    for g, cap in C_CAP_BY_GROUP.items():
        lut[g] = cap
    return lut[np.clip(np.asarray(groups, int), 0, 8)]


def root_cohesion(groups):
    """(min, max) root cohesion in kPa for land-cover group codes."""
    g = np.clip(np.asarray(groups, int), 0, 8)
    lo = np.array([ROOT_COHESION_KPA[k][0] for k in range(9)])
    hi = np.array([ROOT_COHESION_KPA[k][1] for k in range(9)])
    return lo[g], hi[g]
