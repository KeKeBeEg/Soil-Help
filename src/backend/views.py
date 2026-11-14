"""Views for the selected dates (t_range), over the field only.

The analysis (fixed climate period) gives the long-term layers; they are clipped to the field.
The window layers come from the rain that fell between the selected dates:

  soil_loss_window            soil loss from the storms that started in the window (t/ha)
  landslide_peak_class        worst alert level reached in the window (0 low .. 3 danger)
  landslide_peak_probability  highest P(FS < 1) reached in the window (lower bound, steps of 0.1)
  rain_window_max             wettest recharge-window mean rain in the window (mm/day)

plus timeline.png, field_maps.png and field_summary.json (units of the region's profile).
Only rain is fetched here (from the cache when possible), so a new t_range renders in seconds.
"""
from __future__ import annotations

import json
import warnings
from pathlib import Path

import numpy as np
import rasterio
from rasterio.features import geometry_mask

from ..dataanal import factors, landslide as ls, plots, profiles, raster_io as rio
from ..dataanal.pipeline import MONTHS
from ..ingest import shared

LONG_TERM = ["soil_loss", "soil_loss_class", "soil_loss_monthly", "soil_loss_with_practice", "recommended_practice",
             "max_cover_factor", "soil_tolerance", "erodibility_index", "slope", "critical_rain",
             "landslide_rain_quantiles", "stability_class", "landslide_danger_days", "landslide_class_wettest"]


def _clip_copy(src_path: Path, dst_path: Path, rows, cols, field, sub: rio.Grid):
    with rasterio.open(src_path) as src:
        data, dtype, nodata = src.read()[:, rows, cols], src.dtypes[0], src.nodata
        tags, names = src.tags(), src.descriptions
    fill = np.nan if nodata is None or np.isnan(nodata) else nodata
    data = np.where(field, data, fill).astype(dtype)
    rio.write(dst_path, data, sub, description=tags.pop("DESCRIPTION", ""), units=tags.pop("UNITS", ""), dtype=dtype,
              nodata=nodata, band_names=names if all(names) else None, tags=tags)


def _fmt(v, nd=1):
    return "n/a" if v is None or not np.isfinite(v) else f"{v:,.{nd}f}"


def _rain(v):
    """Rain threshold for text: 9999 (or more) means the slope never fails."""
    if v is None or not np.isfinite(v):
        return "n/a"
    return "never (stable even when saturated)" if v >= 9000 else f"{v:,.0f} mm/day"


def polygon_area_m2(xy) -> float:
    x, y = np.asarray(xy, float).T
    return float(0.5 * abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1))))


def render(req, plan, analysis_dir, view_dir) -> dict:
    prof = profiles.PROFILES[plan.path]
    analysis_dir, view_dir = Path(analysis_dir), Path(view_dir)
    view_dir.mkdir(parents=True, exist_ok=True)
    for old in [*view_dir.glob("*.tif"), *view_dir.glob("*.png")]:
        old.unlink()
    analysis = json.loads((analysis_dir / "summary.json").read_text(encoding="utf-8"))
    par = analysis["parameters"]
    window = int(par["recharge_window_days"])
    alert_idx = [ls.QUANTILE_PROBS.index(p) for p in par["alert_probabilities"]]
    years = f"{plan.climate_years[0]}–{plan.climate_years[1]}"

    # ------------------------------------------------------------------ field on the analysis grid
    grid = rio.grid_of(rio.read(analysis_dir / "soil_loss.tif"))
    ring = np.vstack([plan.field_xy, plan.field_xy[:1]])
    field_full = geometry_mask([{"type": "Polygon", "coordinates": [ring.tolist()]}], out_shape=(grid.height, grid.width),
                               transform=grid.transform, all_touched=True, invert=True)
    rows, cols = rio.bbox_window(field_full)
    sub, field = rio.subgrid(grid, rows, cols), field_full[rows, cols]

    def load(name):
        return rio.read(analysis_dir / f"{name}.tif").data[:, rows, cols]

    A = load("soil_loss")[0]
    K, LS, P = load("k_factor")[0], load("ls_factor")[0], load("p_factor")[0]
    c_month, quant = load("c_factor_monthly"), load("landslide_rain_quantiles")
    thresholds = quant[alert_idx]
    valid = field & np.isfinite(A)
    if not valid.any():
        raise ValueError("the field has no analysis results (all water, or outside the data)")
    cell_ha = sub.xres * sub.yres / 1e4

    # ------------------------------------------------------------------ rain in the window
    first = req.t_start - np.timedelta64(window - 1, "D")  # lead-in for the recharge window
    print(f"Views: rain {req.t_start} .. {req.t_end}")
    times, rate = shared.imerg_series(plan.rain_grid, first, req.t_end + 1, req.global_cache)
    days, daily = factors.daily_totals(rate, times, 0.5)
    rain_win = ls.window_mean(daily, window)
    show = days >= req.t_start
    pg = plan.rain_grid
    ei_day = np.zeros(daily.shape)
    storms = 0
    for r in range(pg.height):
        for c in range(pg.width):
            start, _, _, _, ei = factors.storm_ei30(rate[:, r, c], 0.5)
            d = (times[start].astype("datetime64[D]") - days[0]).astype(int)
            keep = show[d]
            np.add.at(ei_day[:, r, c], d[keep], ei[keep])
            storms = max(storms, int(keep.sum()))

    # ------------------------------------------------------------------ day by day over the field
    n_days = days.size
    month = days.astype("datetime64[M]").astype(int) % 12
    f_rain, f_win, f_loss = (np.full(n_days, np.nan) for _ in range(3))
    share = np.full((n_days, 4), np.nan)
    loss_window = np.zeros(field.shape)
    peak_cls = np.zeros(field.shape, np.uint8)
    peak_prob = np.full(field.shape, np.nan)
    peak_rain = np.full(field.shape, np.nan)

    def onto(stack):
        return rio.align(stack, pg.transform, pg.crs, sub)

    warnings.filterwarnings("ignore", "Mean of empty slice", RuntimeWarning)  # lead-in days before the window fills
    for b0 in range(0, n_days, 31):
        b = slice(b0, min(b0 + 31, n_days))
        r_ei, rd, rw = onto(ei_day[b]), onto(daily[b]), onto(rain_win[b])
        loss = r_ei * K * LS * c_month[month[b]] * P
        for j, d in enumerate(range(b.start, b.stop)):
            f_rain[d], f_win[d] = np.nanmean(rd[j][valid]), np.nanmean(rw[j][valid])
            if not show[d] or not np.isfinite(f_win[d]):
                continue
            loss_window += np.nan_to_num(loss[j])
            f_loss[d] = np.nanmean(loss[j][valid])
            cls = ls.classify(rw[j], thresholds)
            share[d] = [np.mean(cls[valid] == k) for k in range(4)]
            peak_cls = np.maximum(peak_cls, cls)
            peak_prob = np.fmax(peak_prob, ls.failure_probability(rw[j], quant))
            peak_rain = np.fmax(peak_rain, rw[j])

    # ------------------------------------------------------------------ GeoTIFFs
    for name in LONG_TERM:
        _clip_copy(analysis_dir / f"{name}.tif", view_dir / f"{name}.tif", rows, cols, field, sub)
    span = {"T_RANGE": f"{req.t_start}/{req.t_end}"}

    def write(name, arr, description, units, dtype="float32", nodata=np.nan, tags=None):
        data = np.where(valid, arr, np.nan if dtype == "float32" else nodata)
        rio.write(view_dir / f"{name}.tif", data, sub, description=description, units=units, dtype=dtype, nodata=nodata,
                  tags={**span, **(tags or {})})

    write("soil_loss_window", loss_window, "soil loss from the storms in the selected dates", "t ha-1")
    write("landslide_peak_class", peak_cls, "worst landslide alert level in the selected dates", "class", "uint8", 255,
          tags={f"CLASS_{k}": v for k, v in ls.CLASS_NAMES.items()})
    write("landslide_peak_probability", peak_prob, "highest P(FS < 1) in the selected dates (lower bound)", "probability")
    write("rain_window_max", peak_rain, f"wettest {window}-day mean rain in the selected dates", "mm day-1")

    # ------------------------------------------------------------------ field summary
    sd = np.flatnonzero(show)
    loss_lt, tol = A[valid], load("soil_tolerance")[0][valid]
    practice = load("recommended_practice")[0][valid]
    stab, crit = load("stability_class")[0][valid], load("critical_rain")[0][valid]
    q10 = quant[0][valid]
    rain_dep = crit[(stab == 2)]
    monthly = np.nanmean(load("soil_loss_monthly")[:, valid], axis=1)
    month_share = 100 * monthly / monthly.sum() if monthly.sum() > 0 else np.zeros(12)
    hot = share[sd, 2] + share[sd, 3]
    peak_day = int(sd[np.nanargmax(np.where(np.isfinite(hot), hot + 1e-6 * np.nan_to_num(f_win[sd]), -1))])
    worst = int(peak_cls[valid].max())
    lt_mean, win_mean = float(np.mean(loss_lt)), float(np.mean(loss_window[valid]))
    hel = profiles.hel_screen(load("erodibility_index")[0][valid], cell_ha)
    s = {
        "field": {"path": plan.path, "profile": prof.name, "label": req.label,
                  "area": prof.area(polygon_area_m2(plan.field_xy) / 1e4), "area_unit": prof.area_unit,
                  "resolution_m": sub.xres, "cells": int(valid.sum()),
                  "centroid_lonlat": [float(np.mean([p[0] for p in plan.field_lonlat])),
                                      float(np.mean([p[1] for p in plan.field_lonlat]))]},
        "t_range": [str(req.t_start), str(req.t_end)],
        "analysis_period": years,
        "data_sources": analysis.get("sources", {}),
        "units": {"soil_loss": f"{prof.loss_unit}/yr", "soil_loss_window": prof.loss_unit, "rain": "mm/day"},
        "long_term": {
            "soil_loss_mean": prof.loss(lt_mean), "soil_loss_p90": prof.loss(np.percentile(loss_lt, 90)),
            "tolerance": prof.loss(float(np.mean(tol))),
            "share_above_tolerance_pct": float(100 * np.mean(loss_lt > tol)),
            "practice_share_pct": {prof.practices[c]: float(100 * np.mean(practice == c))
                                   for c in prof.practices if np.any(practice == c)},
            "monthly_soil_loss_share_pct": dict(zip(MONTHS, month_share.round(2).tolist())),
        },
        "landslide": {
            "stability_share_pct": {ls.STABILITY_CLASSES[k]: float(100 * np.mean(stab == k)) for k in (1, 2, 3)},
            "critical_rain_median_mm_day": float(np.median(rain_dep)) if rain_dep.size else None,
            "rain_for_10pct_failure_weakest_mm_day": float(np.min(q10)) if q10.size else None,
            "danger_days_per_year": float(np.nanmean(load("landslide_danger_days")[0][valid])),
            "recharge_window_days": window,
        },
        "window": {
            "rain_total_mm": float(np.nansum(f_rain[sd])),
            "wettest_window_mm_day": float(np.nanmax(f_win[sd])) if np.isfinite(f_win[sd]).any() else None,
            "wettest_window_end": str(days[sd[np.nanargmax(np.nan_to_num(f_win[sd], nan=-1))]]),
            "erosive_storms": storms,
            "soil_loss": prof.loss(win_mean),
            "soil_loss_pct_of_average_year": float(100 * win_mean / lt_mean) if lt_mean > 0 else None,
            "worst_alert": ls.CLASS_NAMES[worst],
            "worst_alert_day": str(days[peak_day]),
            "field_share_at_warning_or_danger_on_that_day_pct": float(100 * np.nan_to_num(hot[sd == peak_day]).max()),
            "days_with_warning_or_danger": int(np.sum(np.nan_to_num(hot) > 0)),
        },
    }
    if prof.hel_screening:
        s["highly_erodible_land_screening"] = hel
    s["advice"] = advice(prof, s, hel)
    (view_dir / "field_summary.json").write_text(json.dumps(s, indent=2, ensure_ascii=False), encoding="utf-8")

    # ------------------------------------------------------------------ figures
    lt, w, lnd = s["long_term"], s["window"], s["landslide"]
    title = f"{req.label} · {req.t_start} to {req.t_end}"
    subtitle = (f"{_fmt(s['field']['area'], 1)} {prof.area_unit} · {plan.path.upper()} data at {sub.xres:g} m · "
                f"long-term values {years}")
    plots.field_timeline(
        view_dir / "timeline.png", days[sd], f_rain[sd], f_win[sd], np.nanmin(thresholds[:, valid], axis=1),
        share[sd], f_loss[sd] * prof.loss_per_t_ha, title,
        f"{_fmt(w['rain_total_mm'], 0)} mm of rain · soil loss {_fmt(w['soil_loss'], 2)} {prof.loss_unit} · "
        f"worst landslide alert: {w['worst_alert']} ({w['worst_alert_day']})", prof.loss_unit, window)
    to_px = ~sub.transform
    outline = np.array([to_px @ (x, y) for x, y in ring]) - 0.5
    card = [
        f"Area: {_fmt(s['field']['area'], 1)} {prof.area_unit}",
        f"Average soil loss: {_fmt(lt['soil_loss_mean'], 2)} {prof.loss_unit}/yr",
        f"  tolerance {_fmt(lt['tolerance'], 1)}; above it on {_fmt(lt['share_above_tolerance_pct'], 0)}% of the field",
        f"Selected dates: {_fmt(w['soil_loss'], 2)} {prof.loss_unit} lost, {_fmt(w['rain_total_mm'], 0)} mm rain",
        f"  wettest {window} days: {_fmt(w['wettest_window_mm_day'], 0)} mm/day (to {w['wettest_window_end']})",
        f"Landslide, weakest cells (10% failure): {_rain(lnd['rain_for_10pct_failure_weakest_mm_day'])}",
        f"  worst alert {w['worst_alert']} on {w['worst_alert_day']}",
        f"  {_fmt(lnd['danger_days_per_year'], 1)} danger days per year ({years})",
    ]
    if prof.hel_screening and hel["likely_hel"] is not None:
        card.append(f"Erodibility index >= 8 on {_fmt(hel['share_ei_ge_8_pct'], 0)}% "
                    f"({'likely' if hel['likely_hel'] else 'not'} highly erodible)")
    codes = sorted(prof.practices)
    short = {0: "none", 1: "contour", 2: "strips", 3: "terraces", 4: "cover"}
    plots.field_maps(
        view_dir / "field_maps.png",
        {"soil_loss": A, "soil_loss_window": np.where(valid, loss_window, np.nan), "practice": load("recommended_practice")[0],
         "critical_rain": load("critical_rain")[0], "peak_class": np.where(valid, peak_cls, np.nan)},
        field, outline, sub.xres, title, subtitle, card, prof.loss_unit, prof.loss_per_t_ha,
        [short[c] for c in codes])
    print(f"Views written to {view_dir}")
    return s


def advice(prof: profiles.Profile, s: dict, hel: dict) -> list[str]:
    lt, w, lnd = s["long_term"], s["window"], s["landslide"]
    out = []
    unit = prof.loss_unit
    needed = {k: v for k, v in lt["practice_share_pct"].items() if k != prof.practices[0]}
    if lt["share_above_tolerance_pct"] >= 10 and needed:
        best = max(needed, key=needed.get)
        out.append(f"{lt['share_above_tolerance_pct']:.0f}% of the field loses soil faster than the tolerance of "
                   f"{lt['tolerance']:.1f} {unit}/yr. The practice needed on most of that area: {best}.")
    else:
        out.append(f"Soil loss is within the tolerance ({lt['tolerance']:.1f} {unit}/yr) on most of the field: "
                   "keep the current cover and practices.")
    top = sorted(lt["monthly_soil_loss_share_pct"].items(), key=lambda kv: -kv[1])[:3]
    if top and top[0][1] > 0:
        out.append(f"Most erosion happens in {', '.join(m for m, _ in top)} "
                   f"({sum(v for _, v in top):.0f}% of the yearly loss): {prof.cover_practice} in those months.")
    if prof.hel_screening and hel.get("likely_hel"):
        out.append("Likely highly erodible land under USDA conservation compliance (a screening estimate, not an NRCS "
                   "determination): a conservation plan keeps the farm eligible for USDA programs.")
    unstable = lnd["stability_share_pct"].get(ls.STABILITY_CLASSES[3], 0.0)
    if unstable >= 1:
        out.append(f"{unstable:.0f}% of the field is steep enough to slide even without rain: keep it under permanent, "
                   "deep-rooted vegetation and keep machinery and animals off it.")
    if lnd["rain_for_10pct_failure_weakest_mm_day"] is not None and lnd["rain_for_10pct_failure_weakest_mm_day"] < 1000:
        out.append(f"Landslide watch: the weakest parts of the field can fail when rain averages about "
                   f"{lnd['rain_for_10pct_failure_weakest_mm_day']:.0f} mm/day over {lnd['recharge_window_days']} days.")
    if w["worst_alert"] in ("warning", "danger"):
        out.append(f"In the selected dates the field reached '{w['worst_alert']}' on {w['worst_alert_day']}: look for "
                   "cracks, bulges and new springs on the slope, clear drainage ditches, and keep water from pooling "
                   "above steep ground.")
    pct = w["soil_loss_pct_of_average_year"]
    if pct is not None and pct >= 50:
        out.append(f"The selected dates alone removed about {pct:.0f}% of an average year's soil loss.")
    return out
