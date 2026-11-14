"""RUSLE factor maths, A = R * K * LS * C * P, plus farmer-facing derivatives.

Pure numpy (pysheds for flow routing); no file or Earth Engine access.
Units: R  MJ mm ha-1 h-1 yr-1 | K  t ha h ha-1 MJ-1 mm-1 | LS, C, P dimensionless
       A  t ha-1 yr-1
"""
from __future__ import annotations

import numpy as np

# ============================================================================ R factor
# Storm-based EI30 (Renard et al. 1997, USDA Agriculture Handbook 703):
#   * storms are separated by a 6 h spell with < 1.27 mm of rain
#   * storms with < 12.7 mm total are not erosive
#   * unit energy e = 0.29 [1 - 0.72 exp(-0.05 i)]  MJ ha-1 mm-1  (Brown & Foster 1987)
#   * EI30 = (sum of e * depth) * I30, I30 = max 30-min intensity (mm/h)
# With half-hourly rain every time step is a 30-min intensity, so I30 = max rate.

STORM_GAP_H = 6.0
STORM_GAP_MM = 1.27
EROSIVE_MM = 12.7


def unit_energy(rate_mm_h):
    return 0.29 * (1.0 - 0.72 * np.exp(-0.05 * np.asarray(rate_mm_h, float)))


def storm_ei30(rate_mm_h, step_h: float = 0.5):
    """Erosive storms in one pixel's rain-rate series.

    Returns (start_index, depth_mm, energy_MJ_ha, i30_mm_h, ei30) arrays, one entry per
    erosive storm; start_index is the first wet time step of the storm."""
    rate = np.clip(np.nan_to_num(np.asarray(rate_mm_h, float)), 0.0, None)
    depth = rate * step_h
    n = depth.size
    gap = int(round(STORM_GAP_H / step_h))
    idx = np.arange(n)
    cs = np.concatenate([[0.0], np.cumsum(depth)])
    rain_next_6h = cs[np.minimum(idx + 1 + gap, n)] - cs[idx + 1]
    ends_storm = rain_next_6h < STORM_GAP_MM  # storm boundary right after this step
    seg = np.concatenate([[0], np.cumsum(ends_storm[:-1])])
    starts = np.concatenate([[0], np.flatnonzero(np.diff(seg)) + 1])

    total = np.add.reduceat(depth, starts)
    energy = np.add.reduceat(unit_energy(rate) * depth, starts)
    i30 = np.maximum.reduceat(rate, starts)
    first_wet = np.minimum.reduceat(np.where(depth > 0, idx, n), starts)
    keep = total >= EROSIVE_MM
    return first_wet[keep], total[keep], energy[keep], i30[keep], (energy * i30)[keep]


def storms_by_day(rate, times, days, step_h: float = 0.5):
    """Erosive storms of a (T, H, W) rain-rate cube, booked on the day each storm starts.

    Returns (ei, count), each (D, H, W) over `days`: the summed EI30 and the number of erosive
    storms that start on each day. Storms are found on the whole series, so the sum over any
    split of `days` into ranges equals the sum over the whole: no storm is cut at a range edge."""
    day_of = np.asarray(times).astype("datetime64[D]")
    ei_day = np.zeros((days.size,) + rate.shape[1:])
    n_day = np.zeros((days.size,) + rate.shape[1:])
    for r in range(rate.shape[1]):
        for c in range(rate.shape[2]):
            start, _, _, _, ei = storm_ei30(rate[:, r, c], step_h)
            d = (day_of[start] - days[0]).astype(int)
            ok = (d >= 0) & (d < days.size)
            np.add.at(ei_day[:, r, c], d[ok], ei[ok])
            np.add.at(n_day[:, r, c], d[ok], 1)
    return ei_day, n_day


def daily_totals(rate, times, step_h: float = 0.5):
    """Daily rain (mm) from a (T, H, W) rain-rate cube with sorted datetime64 `times`.

    Returns (days, totals): every calendar day (UTC) from the first to the last time step,
    and (D, H, W) totals; a day without any time step is NaN, missing values count as 0."""
    day = np.asarray(times).astype("datetime64[D]")
    days = np.arange(day[0], day[-1] + np.timedelta64(1, "D"))
    idx = (day - days[0]).astype(int)
    depth = np.clip(np.nan_to_num(np.asarray(rate, float)), 0.0, None) * step_h
    starts = np.concatenate([[0], np.flatnonzero(np.diff(idx)) + 1])
    totals = np.full((days.size,) + depth.shape[1:], np.nan)
    totals[idx[starts]] = np.add.reduceat(depth, starts, axis=0)
    return days, totals


def r_renard_freimund(annual_precip_mm):
    """Empirical R from annual precipitation (Renard & Freimund 1994); baseline check."""
    p = np.asarray(annual_precip_mm, float)
    return np.where(p < 850.0, 0.0483 * p**1.61, 587.8 - 1.219 * p + 0.004105 * p**2)


def r_factor(rate, times, years, step_h: float = 0.5) -> dict:
    """R statistics per pixel from a (T, H, W) rain-rate cube and datetime64 `times`.

    Returns arrays: annual (H,W) mean EI30 R; yearly (Y,H,W); monthly (12,H,W) mean R per
    calendar month (sums to annual); storms (H,W) erosive storms per year; precip (H,W)
    mean annual rain; renard_freimund (H,W) the empirical baseline."""
    years = list(years)
    ny = len(years)
    _, h, w = rate.shape
    t_year = times.astype("datetime64[Y]").astype(int) + 1970
    t_month = times.astype("datetime64[M]").astype(int) % 12
    year_idx = np.searchsorted(years, t_year)
    in_period = (year_idx < ny) & (np.asarray(years)[np.minimum(year_idx, ny - 1)] == t_year)

    yearly = np.zeros((ny, h, w))
    monthly = np.zeros((12, h, w))
    storms = np.zeros((h, w))
    precip_yearly = np.zeros((ny, h, w))
    for r in range(h):
        for c in range(w):
            series = rate[:, r, c]
            start, _, _, _, ei = storm_ei30(series, step_h)
            ok = in_period[start]
            start, ei = start[ok], ei[ok]
            yearly[:, r, c] = np.bincount(year_idx[start], weights=ei, minlength=ny)[:ny]
            monthly[:, r, c] = np.bincount(t_month[start], weights=ei, minlength=12) / ny
            storms[r, c] = start.size / ny
            depth = np.clip(np.nan_to_num(series), 0, None) * step_h
            precip_yearly[:, r, c] = np.bincount(year_idx[in_period], weights=depth[in_period], minlength=ny)[:ny]
    return {
        "annual": yearly.mean(axis=0),
        "yearly": yearly,
        "monthly": monthly,
        "storms": storms,
        "precip": precip_yearly.mean(axis=0),
        "renard_freimund": r_renard_freimund(precip_yearly).mean(axis=0),
    }


# ============================================================================ K factor
# ERA5-Land `soil_type` holds FAO/UNESCO DSMW texture classes. Each mineral class is a
# region of the texture triangle; K is evaluated at the class centroid with the
# texture-only equation of AH703 (eq. 3-5, Romkens et al. 1987), and the K range over
# the whole class region is kept as the uncertainty. The Williams (EPIC) equation is
# reported alongside as a cross-check (it needs an assumed organic carbon content).

SOIL_CLASSES = {1: "coarse", 2: "medium", 3: "medium fine", 4: "fine", 5: "very fine",
                6: "organic", 7: "tropical organic"}
MEAN_DIAMETER_MM = np.array([1.025, 0.026, 0.001])  # sand, silt, clay (USDA size limits)
ORGANIC_K_FACTOR = 0.75  # EPIC organic-carbon reduction at its limit, applied to organic soils


def fao_texture_class(sand, clay):
    """FAO texture class (1-5) from sand and clay percentages."""
    sand, clay = np.asarray(sand), np.asarray(clay)
    return np.select(
        [clay >= 60, clay >= 35, sand < 15, (clay < 18) & (sand > 65)],
        [5, 4, 3, 1],
        default=2,
    )


def k_romkens(sand, silt, clay):
    """K from the geometric mean particle diameter Dg (AH703 eq. 3-5), SI units."""
    f = np.stack(np.broadcast_arrays(sand, silt, clay)).astype(float) / 100.0
    log10_dg = np.tensordot(np.log(MEAN_DIAMETER_MM), f, axes=1) / np.log(10.0)
    return 0.0034 + 0.0405 * np.exp(-0.5 * ((log10_dg + 1.659) / 0.7101) ** 2)


def k_williams(sand, silt, clay, organic_carbon_pct):
    """EPIC K (Sharpley & Williams 1990) converted to SI with 0.1317."""
    sand, silt, clay = (np.asarray(v, float) for v in (sand, silt, clay))
    oc = organic_carbon_pct
    sn1 = 1.0 - sand / 100.0
    f_csand = 0.2 + 0.3 * np.exp(-0.256 * sand * (1.0 - silt / 100.0))
    f_clsi = (silt / (clay + silt)) ** 0.3
    f_orgc = 1.0 - 0.25 * oc / (oc + np.exp(3.72 - 2.95 * oc))
    f_hisand = 1.0 - 0.7 * sn1 / (sn1 + np.exp(-5.51 + 22.9 * sn1))
    return 0.1317 * f_csand * f_clsi * f_orgc * f_hisand


def k_lookup(organic_carbon_pct: float = 1.5) -> dict[int, dict]:
    """K per ERA5-Land soil_type class: centroid texture, K, K range, Williams K."""
    sand, clay = np.meshgrid(np.arange(0.5, 100, 1.0), np.arange(0.5, 100, 1.0))
    valid = sand + clay <= 100
    sand, clay = sand[valid], clay[valid]
    silt = 100 - sand - clay
    cls = fao_texture_class(sand, clay)
    k_all = k_romkens(sand, silt, clay)
    table = {}
    for c in range(1, 6):
        m = cls == c
        s, si, cl = sand[m].mean(), silt[m].mean(), clay[m].mean()
        table[c] = {
            "name": SOIL_CLASSES[c],
            "sand": round(float(s), 1), "silt": round(float(si), 1), "clay": round(float(cl), 1),
            "k": float(k_romkens(s, si, cl)),
            "k_min": float(k_all[m].min()), "k_max": float(k_all[m].max()),
            "k_williams": float(k_williams(s, si, cl, organic_carbon_pct)),
        }
    for c in (6, 7):  # organic soils have no texture definition: medium texture, OC-reduced
        base = table[2]
        table[c] = {
            **base,
            "name": SOIL_CLASSES[c],
            "k": base["k"] * ORGANIC_K_FACTOR,
            "k_min": base["k_min"] * ORGANIC_K_FACTOR,
            "k_max": base["k_max"] * ORGANIC_K_FACTOR,
            "k_williams": float(k_williams(base["sand"], base["silt"], base["clay"], 8.0)),
        }
    return table


def k_from_soil_type(soil_type, table: dict[int, dict], key: str = "k"):
    """Map a soil_type raster to K (or another `key` of the table); class 0 / NaN -> NaN."""
    lut = np.full(8, np.nan)
    for c, row in table.items():
        lut[c] = row[key]
    st = np.asarray(soil_type, float)
    out = np.full(st.shape, np.nan)
    ok = np.isfinite(st)
    out[ok] = lut[np.clip(np.rint(st[ok]).astype(int), 0, 7)]
    return out


# ============================================================================ terrain
# LS after Moore & Burch (1986): LS = (As / 22.13)^m * (sin b / 0.0896)^n, with As the
# specific catchment area (upslope area per unit contour width, m) from flow accumulation
# on the depression-filled DEM, capped where overland flow would be channelised.


def slope_radians(dem, xres, yres=None):
    yres = xres if yres is None else yres
    dzdy, dzdx = np.gradient(np.asarray(dem, float), yres, xres)
    return np.arctan(np.hypot(dzdx, dzdy))


def flow_accumulation(dem, transform, crs, routing: str = "mfd"):
    """Upslope contributing area in cells (>= 1) on the depression-filled DEM."""
    from pyproj import CRS
    from pysheds.grid import Grid
    from pysheds.view import Raster, ViewFinder

    dem = np.asarray(dem, float)
    valid = np.isfinite(dem)
    nodata = -9999.0
    vf = ViewFinder(affine=transform, shape=dem.shape, nodata=nodata, crs=CRS.from_user_input(crs))
    grid = Grid(viewfinder=vf)
    z = Raster(np.where(valid, dem, nodata), viewfinder=vf)
    conditioned = grid.resolve_flats(grid.fill_depressions(grid.fill_pits(z)))
    fdir = grid.flowdir(conditioned, routing=routing)
    acc = np.asarray(grid.accumulation(fdir, routing=routing), float)
    return np.where(valid, acc, np.nan)


def ls_factor(specific_area, slope, m=0.4, n=1.3, max_specific_area=300.0):
    a = np.minimum(specific_area, max_specific_area)
    return (a / 22.13) ** m * (np.sin(slope) / 0.0896) ** n


def stream_power_index(specific_area, slope):
    """SPI = As * tan(slope): where concentrated runoff can cut rills and gullies."""
    return specific_area * np.tan(slope)


# ============================================================================ C factor
# C from NDVI (van der Knijff et al. 2000): C = exp(-a NDVI / (b - NDVI)), a=2, b=1.
# The pipeline averages monthly C with each month's R as weight, so the cover that
# counts is the cover present when the storms fall.


def c_from_ndvi(ndvi, alpha: float = 2.0, beta: float = 1.0):
    v = np.clip(np.asarray(ndvi, float), -1.0, 0.999)
    return np.clip(np.exp(-alpha * v / (beta - v)), 0.0, 1.0)


# ============================================================================ A and farmer products

SEVERITY_CLASSES = [(0, 2, "very low"), (2, 5, "low"), (5, 10, "moderate"),
                    (10, 20, "high"), (20, 50, "very high"), (50, np.inf, "severe")]


def soil_loss(r, k, ls, c, p):
    return r * k * ls * c * p


def severity_class(a):
    """1..6 following SEVERITY_CLASSES; 0 where A is undefined."""
    edges = [hi for _, hi, _ in SEVERITY_CLASSES[:-1]]
    a = np.asarray(a, float)
    return np.where(np.isfinite(a), np.digitize(a, edges) + 1, 0).astype(np.uint8)


# Support-practice P by slope class (Shin 1999, after Wischmeier & Smith 1978).
PRACTICES = {0: "no change needed", 1: "contour farming", 2: "strip cropping",
             3: "terraces or soil/stone bunds", 4: "permanent vegetation cover"}
P_SLOPE_BREAKS_PCT = [7.0, 11.3, 17.6, 26.8]
P_BY_PRACTICE = {1: [0.55, 0.60, 0.80, 0.90, 1.00],
                 2: [0.27, 0.30, 0.40, 0.45, 0.50],
                 3: [0.10, 0.12, 0.16, 0.18, 0.20]}


def practice_p(practice: int, slope_pct):
    return np.asarray(P_BY_PRACTICE[practice])[np.digitize(slope_pct, P_SLOPE_BREAKS_PCT)]


def recommend_practice(a_without_practice, slope_pct, tolerance):
    """Least intensive practice whose P brings soil loss within `tolerance`.

    Returns (code, p): code 0 = already within tolerance, 1-3 = PRACTICES, 4 = even
    terraces are not enough, so cover itself must change (p then holds the terrace P)."""
    a = np.asarray(a_without_practice, float)
    code = np.full(a.shape, 4, np.uint8)
    p = practice_p(3, slope_pct).astype(float)
    done = (a <= tolerance) | ~np.isfinite(a)
    code[done], p[done] = 0, 1.0
    for c in (1, 2, 3):
        pc = practice_p(c, slope_pct)
        ok = ~done & (a * pc <= tolerance)
        code[ok], p[ok] = c, pc[ok]
        done |= ok
    return code, p


def erodibility_index(r, k, ls, tolerance):
    """EI = R*K*LS / T, the potential (bare, no-practice) soil loss as a multiple of the
    tolerance. Under USDA conservation compliance, EI >= 8 marks highly erodible land."""
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.asarray(r * k * ls, float) / tolerance


def max_cover_factor(r, k, ls, p, tolerance):
    """Largest C that keeps soil loss within tolerance (1 = any cover is fine)."""
    denom = np.asarray(r * k * ls * p, float)
    with np.errstate(divide="ignore", invalid="ignore"):
        c = tolerance / denom
    return np.where(np.isfinite(denom), np.clip(np.nan_to_num(c, posinf=1.0), 0.0, 1.0), np.nan)
