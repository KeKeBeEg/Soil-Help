"""Soil loss (RUSLE) and shallow-landslide risk from input GeoTIFFs, with farmer-facing outputs.

    python -m src.dataanal.pipeline --out outputs/my_area  data/my_area/inputs
    python -m src.dataanal.pipeline --out outputs/my_area  dem.tif soil_type.tif ndvi.tif precip_2010.tif ...

Inputs are described in raster_io; a directory argument means every .tif inside it. Outputs
are GeoTIFFs on the DEM grid (or, with Params.crop_to_region, on the part of it that holds the
study area), masked to the study area (see OUTPUTS), plus summary.json and PNG figures.

Optional inputs replace defaults, pixel by pixel (a NaN falls through to the next source):
  K              k_factor -> Williams (EPIC) from soil_texture -> soil_type class (AH703 Dg)
  texture        soil_texture -> soil_type class centroid -> loam
  tolerance      soil_tolerance -> Params.tolerance_t_ha_yr
  soil depth     soil_depth -> Params.default_soil_depth_m
  conductivity   ksat -> USDA texture class value (Clapp & Hornberger 1978)
  root cohesion  landcover group -> 0
  cover C        NDVI, capped for permanent cover (forest, grass, shrub) when landcover is given
  dated cover C  ndvi_series (each half-month of the actual years) -> the calendar-month C;
                 written as c_factor_halfmonth for the dated views, not used for long-term A

Rainfall erosivity uses only calendar years with at least Params.complete_year_pct of their
time steps, so rain files for a short event window can sit next to the climate record.
"""
from __future__ import annotations

import argparse
import json
import warnings
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
from affine import Affine
from rasterio.crs import CRS
from rasterio.warp import Resampling
from rasterio.warp import transform as warp_transform

from . import factors, landslide as ls, raster_io as rio, soil_params as sp

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

RUSLE_OUTPUTS = {  # file stem: (description, units)
    "r_factor": ("RUSLE R, rainfall erosivity (storm EI30)", "MJ mm ha-1 h-1 yr-1"),
    "k_factor": ("RUSLE K, soil erodibility", "t ha h ha-1 MJ-1 mm-1"),
    "ls_factor": ("RUSLE LS, slope length and steepness", "dimensionless"),
    "c_factor": ("RUSLE C, cover management (erosivity-weighted)", "dimensionless"),
    "c_factor_monthly": ("RUSLE C per calendar month, bands Jan..Dec", "dimensionless"),
    "p_factor": ("RUSLE P, support practice", "dimensionless"),
    "slope": ("terrain slope", "degrees"),
    "soil_loss": ("A, average annual soil loss = R*K*LS*C*P", "t ha-1 yr-1"),
    "soil_loss_class": ("soil-loss severity class, see CLASS_* tags (0 = no data)", "class"),
    "soil_loss_monthly": ("soil loss by calendar month, bands Jan..Dec, summing to soil_loss", "t ha-1 month-1"),
    "soil_tolerance": ("soil-loss tolerance T used for the farmer products", "t ha-1 yr-1"),
    "erodibility_index": ("erodibility index R*K*LS/T; >= 8 is highly erodible under USDA rules", "dimensionless"),
    "recommended_practice": ("least intensive practice that brings soil loss within tolerance, see CODE_* tags (255 = no data)", "class"),
    "soil_loss_with_practice": ("soil loss if the recommended practice is applied", "t ha-1 yr-1"),
    "max_cover_factor": ("largest C that keeps soil loss within tolerance; where c_factor is higher, cover must improve", "dimensionless"),
    "upslope_area": ("upslope area draining through each cell", "ha"),
    "stream_power_index": ("specific catchment area x tan(slope); high values mark gully risk and natural waterways", "m"),
}
LANDSLIDE_OUTPUTS = {
    "texture_class": ("USDA texture class, see CLASS_* tags (0 = unknown, treated as loam)", "class"),
    "landcover_group": ("land-cover group, see GROUP_* tags (0 = unknown)", "class"),
    "friction_angle": ("effective friction angle phi'", "degrees"),
    "cohesion": ("soil + root cohesion, middle of the range", "kPa"),
    "soil_depth": ("soil depth above bedrock or a restrictive layer", "m"),
    "transmissivity": ("lateral soil transmissivity", "m2 day-1"),
    "wetness_index": ("topographic wetness index ln(a / tan(slope))", "dimensionless"),
    "critical_rain": ("mean daily rain over the recharge window that brings FS to 1, middle parameters "
                      f"({ls.NEVER_MM_DAY:g} = never fails)", "mm day-1"),
    "landslide_rain_quantiles": ("rain at which P(FS < 1) reaches 0.1 .. 0.9, bands P10..P90 "
                                 f"({ls.NEVER_MM_DAY:g} = never)", "mm day-1"),
    "stability_class": ("1 stable even when saturated, 2 rain-dependent, 3 unstable even when dry (0 = no data)", "class"),
    "landslide_class_wettest": ("alert class on the wettest recharge window of the record: 0 low, 1 watch, "
                                "2 warning, 3 danger", "class"),
    "landslide_danger_days": ("mean days per year at danger level over the record", "days yr-1"),
}
OUTPUTS = {**RUSLE_OUTPUTS, **LANDSLIDE_OUTPUTS}
SERIES_OUTPUTS = {  # written only when their optional input is given
    "c_factor_halfmonth": ("RUSLE C per half-month of the actual years (from ndvi_series), bands named by their "
                           "first day; NaN = no clear view, the dated views then use c_factor_monthly", "dimensionless"),
}


@dataclass
class Params:
    tolerance_t_ha_yr: float = 11.0  # default soil-loss tolerance T (soil_tolerance input overrides)
    p_factor: float = 1.0  # current support practice; 1 = none (no practice data)
    organic_carbon_pct: float = 1.5  # Williams K when soil_texture has no organic carbon
    ls_m: float = 0.4  # Moore & Burch (1986) exponents
    ls_n: float = 1.3
    ls_max_specific_area_m: float = 300.0  # cap on upslope length; beyond this flow is channelised
    ls_routing: str = "mfd"  # pysheds flow routing: "mfd", "dinf" or "d8"
    target_res_m: float = 30.0  # only used when the DEM is in a geographic CRS
    complete_year_pct: float = 95.0  # years with less rain coverage are left out of R and the record
    crop_to_region: bool = False  # write outputs only over the bounding box of the study area
    label: str = "Study area"  # shown on the figures
    figures: bool = True
    # --- landslide
    landslide: bool = True
    recharge_window_days: int = 3
    infiltration_fraction: float = 0.7
    default_soil_depth_m: float = 1.0
    min_soil_depth_m: float = 0.5
    max_soil_depth_m: float = 2.0
    macropore_factor: float = 100.0  # lateral T = factor x matrix Ksat x depth (loam, 1 m: ~60 m2/day)
    min_slope_deg: float = 2.0  # flatter cells are treated as stable
    mc_samples: int = 100
    mc_seed: int = 0
    alert_probabilities: tuple = (0.1, 0.3, 0.6)  # P(FS < 1) for watch, warning, danger


def _stats(a):
    a = a[np.isfinite(a)]
    if a.size == 0:
        return {k: None for k in ("mean", "median", "p90", "p99", "max")}
    return {"mean": float(a.mean()), "median": float(np.median(a)), "p90": float(np.percentile(a, 90)),
            "p99": float(np.percentile(a, 99)), "max": float(a.max())}


def _area_pct(codes, mask, names: dict) -> dict:
    vals = codes[mask]
    return {f"{k} {v}": float(100 * np.mean(vals == k)) for k, v in names.items() if vals.size and np.any(vals == k)}


# ============================================================================ stages


def _rainfall(precip, params: Params) -> dict:
    times, rate, pgrid, step_h = rio.precip_cube(precip)
    t_year = times.astype("datetime64[Y]").astype(int) + 1970
    years = sorted(set(t_year.tolist()))
    steps_per_year = {y: (np.datetime64(f"{y + 1}-01-01") - np.datetime64(f"{y}-01-01")).astype(int) * 24 / step_h
                      for y in years}
    coverage = {y: float(100 * np.sum(t_year == y) / steps_per_year[y]) for y in years}
    used = [y for y in years if coverage[y] >= params.complete_year_pct]
    if not used:
        raise ValueError(f"no calendar year of rain has >= {params.complete_year_pct:g}% of its time steps: "
                         f"{ {y: round(c, 1) for y, c in coverage.items()} }")
    missing_pct = float(100 * np.isnan(rate).mean())
    print(f"Rainfall: {times.size} steps of {step_h * 60:g} min, {years[0]}-{years[-1]} "
          f"({len(used)} complete years used for R), {pgrid.width} x {pgrid.height} px; {missing_pct:.2f}% missing")
    rf = factors.r_factor(rate, times, used, step_h)
    days, daily = factors.daily_totals(rate, times, step_h)
    ei_day, storms_day = factors.storms_by_day(rate, times, days, step_h)
    return {"grid": pgrid, "step_h": step_h, "years": years, "used": used, "coverage": coverage,
            "missing_pct": missing_pct, "rf": rf, "days": days, "daily": daily, "ei_day": ei_day,
            "storms_day": storms_day}


RAIN_RECORD = "rain_daily.npz"


def save_rain_record(path, rain: dict) -> Path:
    """Day-by-day rain on the rain grid's cells over the whole record: daily total (mm), EI30
    and number of the erosive storms starting that day. Views for any dates inside the record
    are computed from this file alone, with storms found once on the continuous record."""
    g = rain["grid"]
    with open(path, "wb") as f:
        np.savez_compressed(f, days=rain["days"].astype("datetime64[D]").astype("int64"),
                            rain=rain["daily"].astype(np.float32), ei=rain["ei_day"].astype(np.float32),
                            storms=rain["storms_day"].astype(np.uint16), transform=np.array(list(g.transform)[:6]),
                            crs=np.array(g.crs.to_wkt()))
    return Path(path)


def load_rain_record(path) -> dict:
    with np.load(path, allow_pickle=False) as z:
        rain = z["rain"]
        grid = rio.Grid(CRS.from_wkt(str(z["crs"])), Affine(*z["transform"][:6]), rain.shape[2], rain.shape[1])
        return {"days": z["days"].astype("datetime64[D]"), "daily": rain.astype(float), "ei_day": z["ei"].astype(float),
                "storms_day": z["storms"].astype(float), "grid": grid}


def _soil(layers: dict, grid: rio.Grid, params: Params) -> dict:
    shape = (grid.height, grid.width)

    def layer(role, resampling=Resampling.bilinear, bands=1):
        if role not in layers:
            return np.full((bands,) + shape, np.nan)
        data = rio.align_raster(layers[role], grid, resampling)
        if data.shape[0] != bands:
            raise ValueError(f"{layers[role].path.name}: {role} needs {bands} band(s), got {data.shape[0]}")
        return data.astype(float)

    soil_type = layer("soil_type", Resampling.nearest)[0] if "soil_type" in layers else None
    k_table = factors.k_lookup(params.organic_carbon_pct) if soil_type is not None else None
    sand, silt, clay, oc = layer("soil_texture", bands=4)

    # K: survey value -> Williams from measured texture -> class value
    K = layer("k_factor")[0]
    k_source = np.where(np.isfinite(K), 1, 0).astype(np.uint8)
    if "soil_texture" in layers:
        with np.errstate(divide="ignore", invalid="ignore"):
            kw = factors.k_williams(sand, silt, clay, np.where(np.isfinite(oc), np.clip(oc, 0, None),
                                                               params.organic_carbon_pct))
        fill = ~np.isfinite(K) & np.isfinite(kw)
        K[fill], k_source[fill] = kw[fill], 2
    if soil_type is not None:
        kc = factors.k_from_soil_type(soil_type, k_table)
        fill = ~np.isfinite(K) & np.isfinite(kc)
        K[fill], k_source[fill] = kc[fill], 3
        for key, arr in (("sand", sand), ("silt", silt), ("clay", clay)):  # class-centroid texture
            centroid = factors.k_from_soil_type(soil_type, k_table, key)
            arr[...] = np.where(np.isfinite(arr), arr, centroid)
    texture_class = sp.usda_texture_class(sand, silt, clay)

    tolerance = layer("soil_tolerance")[0]
    tolerance = np.where(np.isfinite(tolerance) & (tolerance > 0), tolerance, params.tolerance_t_ha_yr)
    depth = layer("soil_depth")[0]
    depth = np.clip(np.where(np.isfinite(depth), depth, params.default_soil_depth_m),
                    params.min_soil_depth_m, params.max_soil_depth_m)
    ksat = layer("ksat")[0]
    ksat_from_input = np.isfinite(ksat) & (ksat > 0)
    ksat = np.where(ksat_from_input, ksat, sp.ksat_from_texture(texture_class))

    scheme = None
    groups = np.zeros(shape, np.uint8)
    if "landcover" in layers:
        scheme = layers["landcover"].tags.get("LANDCOVER_SCHEME", "worldcover").lower()
        groups = sp.landcover_groups(layer("landcover", Resampling.nearest)[0], scheme)
    return {"K": K, "k_source": k_source, "k_table": k_table, "soil_type": soil_type, "texture_class": texture_class,
            "tolerance": tolerance, "depth": depth, "ksat": ksat, "ksat_from_input": ksat_from_input,
            "transmissivity": params.macropore_factor * ksat * depth, "groups": groups, "landcover_scheme": scheme}


def _exceedance_days(thresholds, mask, grid: rio.Grid, pgrid: rio.Grid, series):
    """Days in `series` (D, h, w on the rain grid) with rain >= each masked cell's threshold,
    using the rain cell nearest to the cell centre."""
    out = np.full(mask.shape, np.nan)
    rr, cc = np.nonzero(mask)
    if rr.size == 0:
        return out
    xs, ys = grid.transform @ (cc + 0.5, rr + 0.5)
    lon, lat = warp_transform(grid.crs, pgrid.crs, xs, ys)
    col, row = ~pgrid.transform @ (np.asarray(lon), np.asarray(lat))
    col = np.clip(np.floor(col).astype(int), 0, pgrid.width - 1)
    row = np.clip(np.floor(row).astype(int), 0, pgrid.height - 1)
    counts = np.zeros(rr.size)
    thr = thresholds[rr, cc]
    for r, c in set(zip(row.tolist(), col.tolist())):
        sel = (row == r) & (col == c)
        s = np.sort(series[:, r, c][np.isfinite(series[:, r, c])])
        counts[sel] = s.size - np.searchsorted(s, thr[sel], side="left")
    out[rr, cc] = counts
    return out


def _landslide(params: Params, grid, rain: dict, onto, slope, a, soil: dict, inside) -> dict:
    phi_deg, c_min, c_max = sp.strength_params(soil["texture_class"])
    r_min, r_max = sp.root_cohesion(soil["groups"])
    z, t = soil["depth"], soil["transmissivity"]
    min_slope = np.radians(params.min_slope_deg)
    cohesion_mid = (c_min + c_max) / 2 + (r_min + r_max) / 2
    with np.errstate(divide="ignore", invalid="ignore"):
        crit = ls.critical_rain(slope, a, cohesion_mid, np.radians(phi_deg), z, t, params.infiltration_fraction,
                                min_slope)
    calc = inside & np.isfinite(slope) & np.isfinite(a)
    quant = np.full((len(ls.QUANTILE_PROBS),) + slope.shape, np.nan, np.float32)
    print(f"Landslide: {params.mc_samples} parameter sets x {int(calc.sum()):,} cells")
    with np.errstate(divide="ignore", invalid="ignore"):
        quant[:, calc] = ls.rain_threshold_quantiles(
            slope[calc], a[calc], phi_deg[calc], c_min[calc], c_max[calc], r_min[calc], r_max[calc], z[calc], t[calc],
            params.infiltration_fraction, params.mc_samples, params.mc_seed, min_slope=min_slope)
    thresholds = quant[[ls.QUANTILE_PROBS.index(p) for p in params.alert_probabilities]]

    days, daily = rain["days"], rain["daily"]
    window = ls.window_mean(daily, params.recharge_window_days)
    in_used = np.isin(days.astype("datetime64[Y]").astype(int) + 1970, rain["used"])
    valid = in_used & np.isfinite(window).all(axis=(1, 2))
    record = window[valid]
    wettest = int(np.flatnonzero(valid)[np.argmax(record.mean(axis=(1, 2)))])
    rain_wettest = onto(window[wettest])
    with np.errstate(divide="ignore", invalid="ignore"):
        twi = np.log(a / np.tan(np.maximum(slope, np.radians(0.1))))
    return {
        "phi_deg": phi_deg, "cohesion": cohesion_mid, "critical_rain": crit, "stability": ls.stability_class(crit),
        "quantiles": quant, "class_wettest": ls.classify(rain_wettest, thresholds), "twi": twi,
        "danger_days": _exceedance_days(thresholds[-1], calc, grid, rain["grid"], record) / len(rain["used"]),
        "wettest_day": str(days[wettest]), "wettest_rain": float(np.nanmean(rain_wettest[inside])),
    }


# ============================================================================ run


def run(inputs, out_dir, params: Params | None = None) -> dict:
    params = params or Params()
    bad = [p for p in params.alert_probabilities if p not in ls.QUANTILE_PROBS]
    if bad or len(params.alert_probabilities) != 3:
        raise ValueError(f"alert_probabilities must be three of {ls.QUANTILE_PROBS}, got {params.alert_probabilities}")
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    P = params.p_factor

    # ------------------------------------------------------------------ inputs on the DEM grid
    layers = rio.load_inputs(inputs)
    grid = rio.reference_grid(layers["dem"], params.target_res_m)
    print(f"Analysis grid: {grid.width} x {grid.height} px at {grid.xres:g} m ({grid.crs.to_string()})")
    dem = rio.align_raster(layers["dem"], grid, Resampling.bilinear)[0]
    ndvi = rio.align_raster(layers["ndvi"], grid, Resampling.bilinear)
    if ndvi.shape[0] not in (1, 12):
        raise ValueError(f"ndvi must have 12 monthly bands or 1 annual band, got {ndvi.shape[0]}")
    region = np.isfinite(dem)
    if "aoi" in layers:
        region &= rio.align_raster(layers["aoi"], grid, Resampling.nearest)[0] == 1
    if "water" in layers:
        region &= ~(rio.align_raster(layers["water"], grid, Resampling.nearest)[0] == 1)
    rows, cols = rio.bbox_window(region) if params.crop_to_region else (slice(0, grid.height), slice(0, grid.width))
    out_grid = rio.subgrid(grid, rows, cols)

    # ------------------------------------------------------------------ R
    rain = _rainfall(layers["precip"], params)
    pgrid, rf = rain["grid"], rain["rf"]

    def onto(field):
        return rio.align(field, pgrid.transform, pgrid.crs, grid, Resampling.bilinear)

    def region_mean(field):
        return float(np.nanmean(onto(field)[region]))

    R = onto(rf["annual"])
    R_month = onto(rf["monthly"])  # sums to R

    # ------------------------------------------------------------------ soil, terrain
    soil = _soil(layers, grid, params)
    K, T = soil["K"], soil["tolerance"]
    slope = factors.slope_radians(dem, grid.xres, grid.yres)
    acc = factors.flow_accumulation(dem, grid.transform, grid.crs, params.ls_routing)
    specific_area = acc * grid.xres
    LS = factors.ls_factor(specific_area, slope, params.ls_m, params.ls_n, params.ls_max_specific_area_m)

    # ------------------------------------------------------------------ C and soil loss
    # C = sum(R_m C_m) / R, so the monthly soil losses add up exactly to A.
    c_month = np.stack([factors.c_from_ndvi(ndvi[m if ndvi.shape[0] == 12 else 0]) for m in range(12)]).astype(np.float32)
    if soil["landcover_scheme"] is not None:  # permanent cover protects the soil even when it is not green
        c_month = np.minimum(c_month, sp.c_cap(soil["groups"])[None]).astype(np.float32)
    a_month = np.stack([R_month[m] * K * LS * c_month[m] * P for m in range(12)]).astype(np.float32)
    rkc_sum = np.sum(R_month * c_month, axis=0)
    with np.errstate(invalid="ignore", divide="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # cells without NDVI (outside the field grid)
        C = np.where(R > 0, rkc_sum / R, factors.c_from_ndvi(np.nanmean(ndvi, axis=0)))
    A = factors.soil_loss(R, K, LS, C, P)
    inside = region & np.isfinite(A)
    if not inside.any():
        raise ValueError("no cell has every input: check that the soil, NDVI and DEM layers overlap the study area")
    month_loss = np.array([np.nanmean(a_month[m][inside]) for m in range(12)])

    # ------------------------------------------------------------------ farmer products
    slope_pct = 100 * np.tan(slope)
    practice, p_practice = factors.recommend_practice(A / P, slope_pct, T)
    A_practice = A / P * p_practice
    max_c = factors.max_cover_factor(R, K, LS, P, T)
    loss_class = factors.severity_class(A)
    ei = factors.erodibility_index(R, K, LS, T)

    slide = _landslide(params, grid, rain, onto, slope, specific_area, soil, inside) if params.landslide else None

    # ------------------------------------------------------------------ write GeoTIFFs
    def out(name, arr, dtype="float32", nodata=np.nan, tags=None, band_names=None):
        fill = nodata if dtype != "float32" else np.nan
        data = np.where(inside, arr, fill)[..., rows, cols]
        rio.write(out_dir / f"{name}.tif", data, out_grid, description=OUTPUTS[name][0], units=OUTPUTS[name][1],
                  dtype=dtype, nodata=nodata, tags=tags, band_names=band_names)

    out("r_factor", R)
    out("k_factor", K)
    out("ls_factor", LS)
    out("c_factor", C)
    out("c_factor_monthly", c_month, band_names=MONTHS)
    if "ndvi_series" in layers:  # cover of the actual half-months, for the dated views (output grid only: many bands)
        series = layers["ndvi_series"]
        c_half = factors.c_from_ndvi(rio.align_raster(series, out_grid, Resampling.bilinear))
        if soil["landcover_scheme"] is not None:
            c_half = np.minimum(c_half, sp.c_cap(soil["groups"][rows, cols])[None])
        desc, units = SERIES_OUTPUTS["c_factor_halfmonth"]
        rio.write(out_dir / "c_factor_halfmonth.tif", np.where(inside[rows, cols], c_half, np.nan), out_grid,
                  description=desc, units=units, band_names=[d.removeprefix("ndvi_") for d in series.descriptions],
                  tags={"SOURCE": series.tags.get("SOURCE", ""), "TIME_RANGE": series.tags.get("TIME_RANGE", "")})
    out("p_factor", np.full(A.shape, P))
    out("slope", np.degrees(slope))
    out("soil_loss", A)
    out("soil_loss_monthly", a_month, band_names=MONTHS)
    out("soil_loss_class", loss_class, "uint8", 0,
        tags={f"CLASS_{i}": f"{lo:g}-{hi:g} t/ha/yr {name}" for i, (lo, hi, name) in enumerate(factors.SEVERITY_CLASSES, 1)})
    out("soil_tolerance", T)
    out("erodibility_index", ei)
    out("recommended_practice", practice, "uint8", 255,
        tags={**{f"CODE_{c}": name for c, name in factors.PRACTICES.items()}, "TOLERANCE_T_HA_YR": float(np.mean(T[inside]))})
    out("soil_loss_with_practice", A_practice)
    out("max_cover_factor", max_c)
    out("upslope_area", acc * grid.xres * grid.yres / 1e4)
    out("stream_power_index", factors.stream_power_index(specific_area, slope))
    if slide is not None:
        never = ls.NEVER_MM_DAY
        out("texture_class", soil["texture_class"], "uint8", 255, tags={f"CLASS_{k}": v for k, v in sp.USDA_CLASSES.items()})
        out("landcover_group", soil["groups"], "uint8", 255,
            tags={**{f"GROUP_{k}": v for k, v in sp.LANDCOVER_GROUPS.items()}, "SCHEME": soil["landcover_scheme"] or "none"})
        out("friction_angle", slide["phi_deg"])
        out("cohesion", slide["cohesion"])
        out("soil_depth", soil["depth"])
        out("transmissivity", soil["transmissivity"])
        out("wetness_index", slide["twi"])
        out("critical_rain", np.where(np.isinf(slide["critical_rain"]), never, slide["critical_rain"]))
        out("landslide_rain_quantiles", np.where(np.isinf(slide["quantiles"]), never, slide["quantiles"]),
            band_names=[f"P{round(100 * p)}" for p in ls.QUANTILE_PROBS],
            tags={"PROBABILITIES": ",".join(f"{p:g}" for p in ls.QUANTILE_PROBS),
                  "ALERT_PROBABILITIES": ",".join(f"{p:g}" for p in params.alert_probabilities),
                  "RECHARGE_WINDOW_DAYS": params.recharge_window_days, "NEVER_VALUE": never})
        out("stability_class", slide["stability"], "uint8", 0, tags={f"CLASS_{k}": v for k, v in ls.STABILITY_CLASSES.items()})
        out("landslide_class_wettest", slide["class_wettest"], "uint8", 255,
            tags={**{f"CLASS_{k}": v for k, v in ls.CLASS_NAMES.items()}, "WINDOW_END": slide["wettest_day"]})
        out("landslide_danger_days", slide["danger_days"])
    save_rain_record(out_dir / RAIN_RECORD, rain)

    # ------------------------------------------------------------------ summary
    px_ha = grid.xres * grid.yres / 1e4
    a_in = A[inside]
    yearly_r = [region_mean(f) for f in rf["yearly"]]
    monthly_r = [region_mean(f) for f in rf["monthly"]]
    summary = {
        "study_area": {
            "label": params.label, "area_km2": float(inside.sum() * px_ha / 100), "grid": out_grid.as_dict(),
            "mean_slope_deg": float(np.degrees(np.nanmean(slope[inside]))),
        },
        "period": {"rainfall": f"{rain['used'][0]}–{rain['used'][-1]}", "rainfall_years": rain["used"],
                   "cover": layers["ndvi"].tags.get("TIME_RANGE", "n/a"),
                   "cover_by_date": layers["ndvi_series"].tags.get("TIME_RANGE", "n/a") if "ndvi_series" in layers else None},
        "inputs": {role: [str(r.path) for r in (v if isinstance(v, list) else [v])] for role, v in layers.items()},
        "sources": {role: sorted({(r.tags.get("SOURCE") or r.path.name) for r in (v if isinstance(v, list) else [v])})
                    for role, v in layers.items()},
        "soil_loss_t_ha_yr": _stats(a_in),
        "total_soil_loss_Mt_yr": float(a_in.sum() * px_ha / 1e6),
        "severity_area_pct": {
            f"{lo:g}-{hi:g} {name}": float(100 * np.mean((a_in >= lo) & (a_in < hi)))
            for lo, hi, name in factors.SEVERITY_CLASSES
        },
        "tolerance": {
            "t_ha_yr": float(np.mean(T[inside])),
            "area_above_pct": float(100 * np.mean(a_in > T[inside])),
            "mean_with_practice_t_ha_yr": float(np.nanmean(A_practice[inside])),
            "total_with_practice_Mt_yr": float(np.nansum(A_practice[inside]) * px_ha / 1e6),
        },
        "erodibility_index": {**_stats(ei[inside]), "area_ge_8_pct": float(100 * np.mean(ei[inside] >= 8))},
        "recommended_practice_area_pct": {
            name: float(100 * np.mean(practice[inside] == c)) for c, name in factors.PRACTICES.items()
        },
        "monthly_soil_loss_share_pct": dict(zip(MONTHS, (100 * month_loss / month_loss.sum()).tolist())),
        "factor_mean": {name: float(np.nanmean(arr[inside])) for name, arr in
                        {"R": R, "K": K, "LS": LS, "C": C, "P": np.full(A.shape, P)}.items()},
        "k_source_area_pct": {name: float(100 * np.mean(soil["k_source"][inside] == code)) for code, name in
                              {1: "k_factor input", 2: "Williams from soil_texture", 3: "soil_type class"}.items()},
        "rainfall": {
            "step_minutes": rain["step_h"] * 60,
            "coverage_pct_by_year": {str(y): c for y, c in rain["coverage"].items()},
            "years_used": rain["used"],
            "missing_pct": rain["missing_pct"],
            "mean_annual_precip_mm": region_mean(rf["precip"]),
            "erosive_storms_per_year": region_mean(rf["storms"]),
            "R_ei30_mean": float(np.nanmean(R[inside])),
            "R_renard_freimund_mean": region_mean(rf["renard_freimund"]),
            "R_by_year": {str(y): v for y, v in zip(rain["used"], yearly_r)},
            "R_monthly_mean": monthly_r,
        },
    }
    if soil["soil_type"] is not None:
        st, k_table = soil["soil_type"], soil["k_table"]
        present = {int(c): int(n) for c, n in zip(*np.unique(st[inside & np.isfinite(st)], return_counts=True))}
        with np.errstate(invalid="ignore", divide="ignore"):
            summary["k_sensitivity_mean_A_t_ha_yr"] = {
                name: float(np.nanmean((A * factors.k_from_soil_type(st, k_table, key) / K)[inside & (soil["k_source"] == 3)]))
                for name, key in [("williams", "k_williams"), ("class_min", "k_min"), ("class_max", "k_max")]
            } if np.any(soil["k_source"][inside] == 3) else {}
        summary["soil_type_pixels"] = {f"{c} {factors.SOIL_CLASSES.get(c, 'non-land')}": n for c, n in present.items()}
        summary["k_table"] = k_table
    if slide is not None:
        crit_in = slide["critical_rain"][inside]
        summary["landslide"] = {
            "parameters": {k: getattr(params, k) for k in (
                "recharge_window_days", "infiltration_fraction", "macropore_factor", "min_slope_deg", "mc_samples",
                "alert_probabilities", "default_soil_depth_m")},
            "landcover_scheme": soil["landcover_scheme"],
            "landcover_group_area_pct": _area_pct(soil["groups"], inside, sp.LANDCOVER_GROUPS),
            "texture_class_area_pct": _area_pct(soil["texture_class"], inside, sp.USDA_CLASSES),
            "ksat_from_input_pct": float(100 * np.mean(soil["ksat_from_input"][inside])),
            "soil_depth_m_mean": float(np.mean(soil["depth"][inside])),
            "transmissivity_m2_day_mean": float(np.mean(soil["transmissivity"][inside])),
            "stability_class_area_pct": _area_pct(slide["stability"], inside, ls.STABILITY_CLASSES),
            "critical_rain_mm_day_rain_dependent": _stats(crit_in[np.isfinite(crit_in) & (crit_in > 0)]),
            "wettest_window": {"end_date": slide["wettest_day"], "mean_rain_mm_day": slide["wettest_rain"]},
            "alert_class_area_pct_wettest_window": _area_pct(slide["class_wettest"], inside, ls.CLASS_NAMES),
            "danger_days_per_year_mean": float(np.nanmean(slide["danger_days"][inside])),
        }
    summary["parameters"] = asdict(params)
    summary["outputs"] = sorted(p.name for p in out_dir.glob("*.tif"))
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    if params.figures:
        from . import plots

        maps = {"R": R, "K": K, "LS": LS, "C": C, "A": A}
        plots.make_figures(out_dir, {k: v[rows, cols] for k, v in maps.items()}, inside[rows, cols], summary)
        if slide is not None:
            plots.landslide_maps(out_dir / "landslide_maps.png", {
                "stability": slide["stability"][rows, cols], "critical_rain": slide["critical_rain"][rows, cols],
                "class_wettest": slide["class_wettest"][rows, cols], "danger_days": slide["danger_days"][rows, cols],
            }, inside[rows, cols], summary)

    sl = summary["soil_loss_t_ha_yr"]
    print(f"Soil loss: mean {sl['mean']:.1f}, median {sl['median']:.1f} t/ha/yr; "
          f"total {summary['total_soil_loss_Mt_yr']:.3f} Mt/yr; "
          f"{summary['tolerance']['area_above_pct']:.0f}% of the area above tolerance")
    if slide is not None:
        lsum = summary["landslide"]
        print(f"Landslide: stability {lsum['stability_class_area_pct']}; "
              f"{lsum['danger_days_per_year_mean']:.2f} danger days/yr on average")
    print(f"Outputs written to {out_dir}")
    return summary


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+", type=Path, help="input GeoTIFFs or directories of them")
    ap.add_argument("--out", type=Path, required=True, help="output directory")
    ap.add_argument("--tolerance", type=float, default=Params.tolerance_t_ha_yr, help="soil-loss tolerance, t/ha/yr")
    ap.add_argument("--p-factor", type=float, default=Params.p_factor, help="current support-practice factor")
    ap.add_argument("--label", default=Params.label, help="study-area name for the figures")
    ap.add_argument("--no-landslide", action="store_true")
    ap.add_argument("--no-figures", action="store_true")
    args = ap.parse_args(argv)
    run(args.inputs, args.out, Params(tolerance_t_ha_yr=args.tolerance, p_factor=args.p_factor, label=args.label,
                                      landslide=not args.no_landslide, figures=not args.no_figures))


if __name__ == "__main__":
    main()
