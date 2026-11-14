"""Average annual soil loss with RUSLE, A = R * K * LS * C * P, for one study area.

Run from the repository root (after `earthengine authenticate`):
    $env:EE_PROJECT = "<your-cloud-project-id>"        # PowerShell
    python -m src.dataanal.pipeline

Downloads are cached under data/<aoi_name>/; results go to outputs/<aoi_name>/.
"""
from __future__ import annotations

import json
from datetime import datetime

import numpy as np
import rasterio
from pyproj import Transformer
from scipy.interpolate import RegularGridInterpolator

from . import factors, gee_data as gee
from .config import Config


def _cached(path, grid: gee.Grid, make):
    """Array cached for this exact grid, else build it with `make()` and cache it."""
    if path.exists():
        z = np.load(path)
        if json.loads(str(z["grid"])) == grid.as_dict():
            return z["data"]
    data = make()
    np.savez_compressed(path, data=data, grid=json.dumps(grid.as_dict()))
    return data


def to_grid(field, src: gee.Grid, dst: gee.Grid):
    """Bilinear-resample (H, W) or (B, H, W) arrays from lat/lon grid `src` onto `dst`."""
    xs, ys = dst.centers()
    x, y = np.meshgrid(xs, ys)
    lon, lat = Transformer.from_crs(dst.crs, src.crs, always_xy=True).transform(x, y)
    sx, sy = src.centers()
    pts = np.column_stack([lat.ravel(), lon.ravel()])

    def one(a):
        interp = RegularGridInterpolator((sy[::-1], sx), a[::-1], bounds_error=False, fill_value=None)
        return interp(pts).reshape(dst.height, dst.width)

    return one(field) if field.ndim == 2 else np.stack([one(a) for a in field])


def write_tif(path, array, grid: gee.Grid):
    with rasterio.open(
        path, "w", driver="GTiff", width=grid.width, height=grid.height, count=1,
        dtype="float32", crs=grid.crs, transform=grid.transform, nodata=np.nan,
        compress="deflate", tiled=True,
    ) as dst:
        dst.write(np.asarray(array, np.float32), 1)


def _stats(a):
    a = a[np.isfinite(a)]
    return {
        "mean": float(a.mean()), "median": float(np.median(a)),
        "p90": float(np.percentile(a, 90)), "p99": float(np.percentile(a, 99)), "max": float(a.max()),
    }


def run(cfg: Config | None = None) -> dict:
    cfg = cfg or Config()
    gee.init(cfg.ee_project)
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    cfg.out_dir.mkdir(parents=True, exist_ok=True)

    geom, aoi = gee.study_area(cfg.aoi_point, cfg.aoi_max_area_km2, cfg.aoi_bbox)
    bounds = gee.lonlat_bounds(geom)
    grid = gee.utm_grid(bounds, cfg.scale_m)
    rgrid = gee.latlon_grid(bounds)
    print(f"Study area: {aoi}, bounds {np.round(bounds, 3).tolist()}")
    print(f"30 m grid: {grid.width} x {grid.height} px in {grid.crs}; IMERG grid: {rgrid.width} x {rgrid.height}")

    print("[1/4] FABDEM elevation + ERA5-Land soil type + basin mask")
    dem, soil, basin = _cached(
        cfg.data_dir / "static.npz", grid,
        lambda: gee.fetch(gee.static_image(geom), grid, workers=cfg.workers),
    )
    print("[2/4] Landsat 8/9 NDVI composites")
    ndvi = _cached(
        cfg.data_dir / f"ndvi_{cfg.c_start}_{cfg.c_end}.npz", grid,
        lambda: gee.fetch(gee.ndvi_image(geom, cfg.c_start, cfg.c_end), grid, tile=256, workers=cfg.workers),
    )
    print("[3/4] IMERG V07 half-hourly precipitation")
    times, rate = gee.fetch_imerg(
        rgrid, datetime(cfg.start_year, 1, 1), datetime(cfg.end_year + 1, 1, 1),
        cfg.data_dir / "imerg", workers=cfg.workers,
    )

    print("[4/4] RUSLE factors")
    years = list(range(cfg.start_year, cfg.end_year + 1))
    rf = factors.r_factor(rate, times, years)
    R = to_grid(rf["annual"], rgrid, grid)
    R_rf = to_grid(rf["renard_freimund"], rgrid, grid)

    k_table = factors.k_lookup(cfg.organic_carbon_pct)
    K = factors.k_from_soil_type(soil, k_table)

    LS, slope = factors.ls_factor(
        dem, grid.res, grid.crs, grid.transform, m=cfg.ls_m, n=cfg.ls_n,
        max_specific_area=cfg.ls_max_specific_area_m, routing=cfg.ls_routing,
    )

    water = ndvi[13] == 1
    region = (basin == 1) & ~water

    def region_mean(field):
        """Mean over the study area of a lat/lon field (or stack), via the 30 m grid."""
        if field.ndim == 2:
            return float(np.nanmean(to_grid(field, rgrid, grid)[region]))
        return np.array([region_mean(f) for f in field])

    monthly_r = region_mean(rf["monthly"])  # study-area erosivity seasonality
    yearly_r = region_mean(rf["yearly"])
    C = factors.c_effective(ndvi[:12], monthly_r)
    P = np.full(R.shape, cfg.p_factor)

    A = factors.soil_loss(R, K, LS, C, P)
    inside = (basin == 1) & ~water & np.isfinite(A)
    layers = {"R": R, "K": K, "LS": LS, "C": C, "A": A}
    for name, arr in layers.items():
        write_tif(cfg.out_dir / f"{name}.tif", np.where(inside, arr, np.nan), grid)

    # ---------------------------------------------------------------- summary
    px_ha = grid.res**2 / 1e4
    a_in = A[inside]
    k_alt = {
        name: factors.k_from_soil_type(soil, {c: {**row, "k": row[key]} for c, row in k_table.items()})
        for name, key in [("williams", "k_williams"), ("class_min", "k_min"), ("class_max", "k_max")]
    }
    present = {int(c): int(n) for c, n in zip(*np.unique(soil[inside & np.isfinite(soil)], return_counts=True))}
    summary = {
        "study_area": {
            **aoi, "name": cfg.aoi_name, "label": cfg.aoi_label, "bounds_lonlat": bounds,
            "area_km2": float(inside.sum() * px_ha / 100), "grid": grid.as_dict(),
            "mean_slope_deg": float(np.degrees(np.nanmean(slope[inside]))),
        },
        "period": {
            "rainfall": f"{cfg.start_year}–{cfg.end_year}",
            "cover": f"{cfg.c_start[:7]} to {np.datetime64(cfg.c_end, 'M') - 1}",  # c_end is exclusive
        },
        "soil_loss_t_ha_yr": _stats(a_in),
        "total_soil_loss_Mt_yr": float(a_in.sum() * px_ha / 1e6),
        "severity_area_pct": {
            f"{lo:g}-{hi:g} {label}": float(100 * np.mean((a_in >= lo) & (a_in < hi)))
            for lo, hi, label in factors.SEVERITY_CLASSES
        },
        "factor_mean": {name: float(np.nanmean(arr[inside])) for name, arr in
                        {"R": R, "K": K, "LS": LS, "C": C, "P": P}.items()},
        "rainfall": {
            "mean_annual_precip_mm": region_mean(rf["precip"]),
            "erosive_storms_per_year": region_mean(rf["storms"]),
            "R_ei30_mean": float(np.nanmean(R[inside])),
            "R_renard_freimund_mean": float(np.nanmean(R_rf[inside])),
            "R_by_year": {str(y): float(v) for y, v in zip(years, yearly_r)},
            "R_monthly_mean": [float(v) for v in monthly_r],
            "R_monthly_share_pct": [float(v) for v in 100 * monthly_r / monthly_r.sum()],
        },
        "k_sensitivity_mean_A_t_ha_yr": {
            name: float(np.nanmean((A * kk / K)[inside])) for name, kk in k_alt.items()
        },
        "soil_type_pixels": {f"{c} {factors.SOIL_CLASSES.get(c, 'non-land')}": n for c, n in present.items()},
        "k_table": k_table,
        "landsat_clear_obs": {"median": float(np.nanmedian(ndvi[14][inside])),
                              "min": float(np.nanmin(ndvi[14][inside]))},
    }
    (cfg.out_dir / "summary.json").write_text(json.dumps(summary, indent=2))

    from . import plots
    plots.make_figures(cfg.out_dir, layers, inside, summary)

    print(json.dumps({k: summary[k] for k in ("soil_loss_t_ha_yr", "total_soil_loss_Mt_yr", "factor_mean")}, indent=2))
    print(f"Outputs written to {cfg.out_dir}")
    return summary


if __name__ == "__main__":
    run()
