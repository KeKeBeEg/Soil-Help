"""RUSLE average annual soil loss from input GeoTIFFs, with farmer-facing outputs.

    python -m src.dataanal.pipeline --out outputs/my_area  data/my_area/inputs
    python -m src.dataanal.pipeline --out outputs/my_area  dem.tif soil_type.tif ndvi.tif precip_2010.tif ...

Inputs are described in raster_io (dem, soil_type, ndvi, precip, optional aoi / water);
a directory argument means every .tif inside it. All outputs are GeoTIFFs on the DEM grid,
masked to the study area (see OUTPUTS), plus summary.json and two PNG figures.
"""
from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
from rasterio.warp import Resampling

from . import factors, raster_io as rio

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

OUTPUTS = {  # file stem: (description, units)
    "r_factor": ("RUSLE R, rainfall erosivity (storm EI30)", "MJ mm ha-1 h-1 yr-1"),
    "k_factor": ("RUSLE K, soil erodibility", "t ha h ha-1 MJ-1 mm-1"),
    "ls_factor": ("RUSLE LS, slope length and steepness", "dimensionless"),
    "c_factor": ("RUSLE C, cover management (erosivity-weighted)", "dimensionless"),
    "p_factor": ("RUSLE P, support practice", "dimensionless"),
    "slope": ("terrain slope", "degrees"),
    "soil_loss": ("A, average annual soil loss = R*K*LS*C*P", "t ha-1 yr-1"),
    "soil_loss_class": ("soil-loss severity class, see CLASS_* tags (0 = no data)", "class"),
    "soil_loss_monthly": ("soil loss by calendar month, bands Jan..Dec, summing to soil_loss", "t ha-1 month-1"),
    "recommended_practice": ("least intensive practice that brings soil loss within tolerance, see CODE_* tags (255 = no data)", "class"),
    "soil_loss_with_practice": ("soil loss if the recommended practice is applied", "t ha-1 yr-1"),
    "max_cover_factor": ("largest C that keeps soil loss within tolerance; where c_factor is higher, cover must improve", "dimensionless"),
    "upslope_area": ("upslope area draining through each cell", "ha"),
    "stream_power_index": ("specific catchment area x tan(slope); high values mark gully risk and natural waterways", "m"),
}


@dataclass
class Params:
    tolerance_t_ha_yr: float = 11.0  # soil-loss tolerance T used by the farmer products
    p_factor: float = 1.0  # current support practice; 1 = none (no practice data)
    organic_carbon_pct: float = 1.5  # Williams K cross-check only
    ls_m: float = 0.4  # Moore & Burch (1986) exponents
    ls_n: float = 1.3
    ls_max_specific_area_m: float = 300.0  # cap on upslope length; beyond this flow is channelised
    ls_routing: str = "mfd"  # pysheds flow routing: "mfd", "dinf" or "d8"
    target_res_m: float = 30.0  # only used when the DEM is in a geographic CRS
    label: str = "Study area"  # shown on the figures
    figures: bool = True


def _stats(a):
    a = a[np.isfinite(a)]
    return {"mean": float(a.mean()), "median": float(np.median(a)), "p90": float(np.percentile(a, 90)),
            "p99": float(np.percentile(a, 99)), "max": float(a.max())}


def run(inputs, out_dir, params: Params | None = None) -> dict:
    params = params or Params()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    T = params.tolerance_t_ha_yr
    P = params.p_factor

    # ------------------------------------------------------------------ inputs on the DEM grid
    layers = rio.load_inputs(inputs)
    grid = rio.reference_grid(layers["dem"], params.target_res_m)
    print(f"Output grid: {grid.width} x {grid.height} px at {grid.xres:g} m ({grid.crs.to_string()})")
    dem = rio.align_raster(layers["dem"], grid, Resampling.bilinear)[0]
    soil = rio.align_raster(layers["soil_type"], grid, Resampling.nearest)[0]
    ndvi = rio.align_raster(layers["ndvi"], grid, Resampling.bilinear)
    if ndvi.shape[0] not in (1, 12):
        raise ValueError(f"ndvi must have 12 monthly bands or 1 annual band, got {ndvi.shape[0]}")
    region = np.isfinite(dem)
    if "aoi" in layers:
        region &= rio.align_raster(layers["aoi"], grid, Resampling.nearest)[0] == 1
    if "water" in layers:
        region &= ~(rio.align_raster(layers["water"], grid, Resampling.nearest)[0] == 1)

    # ------------------------------------------------------------------ R
    times, rate, pgrid, step_h = rio.precip_cube(layers["precip"])
    t_year = times.astype("datetime64[Y]").astype(int) + 1970
    years = sorted(set(t_year.tolist()))
    steps_per_year = {y: (np.datetime64(f"{y + 1}-01-01") - np.datetime64(f"{y}-01-01")).astype(int) * 24 / step_h
                      for y in years}
    coverage = {str(y): float(100 * np.sum(t_year == y) / steps_per_year[y]) for y in years}
    missing_pct = float(100 * np.isnan(rate).mean())
    print(f"Rainfall: {times.size} steps of {step_h * 60:g} min, {years[0]}-{years[-1]}, "
          f"{pgrid.width} x {pgrid.height} px; {missing_pct:.2f}% missing values")
    rf = factors.r_factor(rate, times, years, step_h)

    def onto(field):
        return rio.align(field, pgrid.transform, pgrid.crs, grid, Resampling.bilinear)

    def region_mean(field):
        return float(np.nanmean(onto(field)[region]))

    R = onto(rf["annual"])
    R_month = onto(rf["monthly"])  # sums to R

    # ------------------------------------------------------------------ K, terrain
    k_table = factors.k_lookup(params.organic_carbon_pct)
    K = factors.k_from_soil_type(soil, k_table)
    slope = factors.slope_radians(dem, grid.xres, grid.yres)
    acc = factors.flow_accumulation(dem, grid.transform, grid.crs, params.ls_routing)
    specific_area = acc * grid.xres
    LS = factors.ls_factor(specific_area, slope, params.ls_m, params.ls_n, params.ls_max_specific_area_m)

    # ------------------------------------------------------------------ C and monthly soil loss
    # C = sum(R_m C_m) / R, so the monthly soil losses add up exactly to A.
    rkc_sum = np.zeros(R.shape)
    month_loss = np.zeros(12)
    with rasterio.open(out_dir / "soil_loss_monthly.tif", "w", **rio.profile(grid, 12)) as dst:
        for m in range(12):
            c_m = factors.c_from_ndvi(ndvi[m if ndvi.shape[0] == 12 else 0])
            rkc_sum += R_month[m] * c_m
            a_m = np.where(region, R_month[m] * K * LS * c_m * P, np.nan)
            dst.write(a_m.astype(np.float32), m + 1)
            dst.set_band_description(m + 1, MONTHS[m])
            month_loss[m] = np.nanmean(a_m)
        dst.update_tags(DESCRIPTION=OUTPUTS["soil_loss_monthly"][0], UNITS=OUTPUTS["soil_loss_monthly"][1])
    with np.errstate(invalid="ignore", divide="ignore"):
        C = np.where(R > 0, rkc_sum / R, factors.c_from_ndvi(np.nanmean(ndvi, axis=0)))
    A = factors.soil_loss(R, K, LS, C, P)
    inside = region & np.isfinite(A)

    # ------------------------------------------------------------------ farmer products
    slope_pct = 100 * np.tan(slope)
    practice, p_practice = factors.recommend_practice(A / P, slope_pct, T)
    A_practice = A / P * p_practice
    max_c = factors.max_cover_factor(R, K, LS, P, T)
    loss_class = factors.severity_class(A)

    # ------------------------------------------------------------------ write GeoTIFFs
    def out(name, arr, dtype="float32", nodata=np.nan, tags=None):
        fill = nodata if dtype != "float32" else np.nan
        rio.write(out_dir / f"{name}.tif", np.where(inside, arr, fill), grid, description=OUTPUTS[name][0],
                  units=OUTPUTS[name][1], dtype=dtype, nodata=nodata, tags=tags)

    out("r_factor", R)
    out("k_factor", K)
    out("ls_factor", LS)
    out("c_factor", C)
    out("p_factor", np.full(A.shape, P))
    out("slope", np.degrees(slope))
    out("soil_loss", A)
    out("soil_loss_class", loss_class, "uint8", 0,
        tags={f"CLASS_{i}": f"{lo:g}-{hi:g} t/ha/yr {name}" for i, (lo, hi, name) in enumerate(factors.SEVERITY_CLASSES, 1)})
    out("recommended_practice", practice, "uint8", 255,
        tags={**{f"CODE_{c}": name for c, name in factors.PRACTICES.items()}, "TOLERANCE_T_HA_YR": T})
    out("soil_loss_with_practice", A_practice)
    out("max_cover_factor", max_c)
    out("upslope_area", acc * grid.xres * grid.yres / 1e4)
    out("stream_power_index", factors.stream_power_index(specific_area, slope))

    # ------------------------------------------------------------------ summary
    px_ha = grid.xres * grid.yres / 1e4
    a_in = A[inside]
    present = {int(c): int(n) for c, n in zip(*np.unique(soil[inside & np.isfinite(soil)], return_counts=True))}
    yearly_r = [region_mean(f) for f in rf["yearly"]]
    monthly_r = [region_mean(f) for f in rf["monthly"]]
    summary = {
        "study_area": {
            "label": params.label, "area_km2": float(inside.sum() * px_ha / 100), "grid": grid.as_dict(),
            "mean_slope_deg": float(np.degrees(np.nanmean(slope[inside]))),
        },
        "period": {"rainfall": f"{years[0]}–{years[-1]}", "cover": layers["ndvi"].tags.get("TIME_RANGE", "n/a")},
        "inputs": {role: [str(r.path) for r in (v if isinstance(v, list) else [v])] for role, v in layers.items()},
        "soil_loss_t_ha_yr": _stats(a_in),
        "total_soil_loss_Mt_yr": float(a_in.sum() * px_ha / 1e6),
        "severity_area_pct": {
            f"{lo:g}-{hi:g} {name}": float(100 * np.mean((a_in >= lo) & (a_in < hi)))
            for lo, hi, name in factors.SEVERITY_CLASSES
        },
        "tolerance": {
            "t_ha_yr": T,
            "area_above_pct": float(100 * np.mean(a_in > T)),
            "mean_with_practice_t_ha_yr": float(np.nanmean(A_practice[inside])),
            "total_with_practice_Mt_yr": float(np.nansum(A_practice[inside]) * px_ha / 1e6),
            "area_cover_must_improve_pct": float(100 * np.mean(C[inside] > max_c[inside])),
        },
        "recommended_practice_area_pct": {
            name: float(100 * np.mean(practice[inside] == c)) for c, name in factors.PRACTICES.items()
        },
        "monthly_soil_loss_share_pct": dict(zip(MONTHS, (100 * month_loss / month_loss.sum()).tolist())),
        "factor_mean": {name: float(np.nanmean(arr[inside])) for name, arr in
                        {"R": R, "K": K, "LS": LS, "C": C, "P": np.full(A.shape, P)}.items()},
        "rainfall": {
            "step_minutes": step_h * 60,
            "coverage_pct_by_year": coverage,
            "missing_pct": missing_pct,
            "mean_annual_precip_mm": region_mean(rf["precip"]),
            "erosive_storms_per_year": region_mean(rf["storms"]),
            "R_ei30_mean": float(np.nanmean(R[inside])),
            "R_renard_freimund_mean": region_mean(rf["renard_freimund"]),
            "R_by_year": {str(y): v for y, v in zip(years, yearly_r)},
            "R_monthly_mean": monthly_r,
        },
        "k_sensitivity_mean_A_t_ha_yr": {
            name: float(np.nanmean((A * factors.k_from_soil_type(soil, k_table, key) / K)[inside]))
            for name, key in [("williams", "k_williams"), ("class_min", "k_min"), ("class_max", "k_max")]
        },
        "soil_type_pixels": {f"{c} {factors.SOIL_CLASSES.get(c, 'non-land')}": n for c, n in present.items()},
        "k_table": k_table,
        "outputs": sorted(p.name for p in out_dir.glob("*.tif")),
    }
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    if params.figures:
        from . import plots

        plots.make_figures(out_dir, {"R": R, "K": K, "LS": LS, "C": C, "A": A}, inside, summary)

    sl = summary["soil_loss_t_ha_yr"]
    print(f"Soil loss: mean {sl['mean']:.1f}, median {sl['median']:.1f} t/ha/yr; "
          f"total {summary['total_soil_loss_Mt_yr']:.3f} Mt/yr; "
          f"{summary['tolerance']['area_above_pct']:.0f}% of the area above {T:g} t/ha/yr")
    print(f"Outputs written to {out_dir}")
    return summary


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+", type=Path, help="input GeoTIFFs or directories of them")
    ap.add_argument("--out", type=Path, required=True, help="output directory")
    ap.add_argument("--tolerance", type=float, default=Params.tolerance_t_ha_yr, help="soil-loss tolerance, t/ha/yr")
    ap.add_argument("--p-factor", type=float, default=Params.p_factor, help="current support-practice factor")
    ap.add_argument("--label", default=Params.label, help="study-area name for the figures")
    ap.add_argument("--no-figures", action="store_true")
    args = ap.parse_args(argv)
    run(args.inputs, args.out, Params(tolerance_t_ha_yr=args.tolerance, p_factor=args.p_factor,
                                      label=args.label, figures=not args.no_figures))


if __name__ == "__main__":
    main()
