"""Views for the selected dates (t_range), over the field only, rendered from saved files.

render() reads working_save/analysis/ and nothing else for dates inside the analysis period:
plan.json (field polygon, data path, rain grid, period), the long-term GeoTIFFs and
rain_daily.npz (daily rain and erosive storms on the IMERG cells). Dates outside the period
take their rain from the IMERG files in the global cache (pulled if missing). No plan, no
HydroBASINS lookup, so a new t_range renders in seconds without Earth Engine.

The range layers come from the rain between the selected dates:

  soil_loss_window            soil loss from the storms that start in the dates (t/ha), with the
                              cover of each storm's own half-month where the analysis has it
                              (c_factor_halfmonth, from HLS NDVI), else of its calendar month
  landslide_peak_class        worst alert level reached in the dates (0 low .. 3 danger)
  landslide_peak_probability  highest P(FS < 1) reached in the dates (lower bound, steps of 0.1)
  rain_window_max             wettest recharge-window mean rain in the dates (mm/day)

plus timeline.png, field_maps.png and field_summary.json (units of the region's profile).

precompute() runs once after the analysis and adds to analysis/, over the field:

  <range layer>_by_year.tif   one band per calendar year of the analysis period
  daily_series.csv            the field's numbers day by day over the period

Any run of whole years combines exactly from the yearly bands (tag COMBINE): soil_loss_window
adds up, because storms are found once on the continuous record and each counts on the day it
starts; the other three take the maximum, because every day's recharge window includes its
lead-in days from the previous year. render() over the same years gives the same maps.
"""
from __future__ import annotations

import csv
import json
import warnings
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
from rasterio.features import geometry_mask
from rasterio.warp import transform as warp_transform

from ..dataanal import factors, landslide as ls, plots, profiles, raster_io as rio
from ..dataanal.pipeline import MONTHS, RAIN_RECORD, load_rain_record
from ..ingest import shared

LONG_TERM = ["soil_loss", "soil_loss_class", "soil_loss_monthly", "soil_loss_with_practice", "recommended_practice",
             "max_cover_factor", "soil_tolerance", "erodibility_index", "slope", "critical_rain",
             "landslide_rain_quantiles", "stability_class", "landslide_danger_days", "landslide_class_wettest"]
RANGE_LAYERS = {  # name: (how ranges combine, description of "{span}", units, dtype, nodata)
    "soil_loss_window": ("sum", "soil loss from the storms that start in {span}", "t ha-1", "float32", np.nan),
    "landslide_peak_class": ("max", "worst landslide alert level in {span}", "class", "uint8", 255),
    "landslide_peak_probability": ("max", "highest P(FS < 1) in {span} (lower bound)", "probability", "float32", np.nan),
    "rain_window_max": ("max", "wettest {window}-day mean rain in {span}", "mm day-1", "float32", np.nan),
}
SERIES_COLUMNS = ["date", "rain_mm", "rain_window_mm_day", "soil_loss_t_ha", "erosive_storms",
                  "share_low_pct", "share_watch_pct", "share_warning_pct", "share_danger_pct"]
TAIL_DAYS = 3  # outside the record: rain read past the last date, so a storm starting on it is not cut short
HALF_MONTH_C = "c_factor_halfmonth"  # optional analysis output: cover of the actual half-months (HLS NDVI)


def cover_by_day(days, c_month, half=None) -> np.ndarray:
    """RUSLE C for each day (D, h, w): the cover of the day's own half-month where `half`
    = (first days, C stack) has a value, else the calendar month's (no clear view that
    half-month, or a date outside the series)."""
    c = c_month[days.astype("datetime64[M]").astype(int) % 12]
    if half is None:
        return c
    starts, c_half = half
    idx = np.searchsorted(starts, days, side="right") - 1
    ok = np.flatnonzero((idx >= 0) & (days - starts[np.maximum(idx, 0)] < np.timedelta64(16, "D")))
    picked = c_half[idx[ok]]
    c[ok] = np.where(np.isfinite(picked), picked, c[ok])
    return c


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


# ============================================================================ the field, from analysis/


@dataclass
class Field:
    dir: Path
    stamp: dict  # plan.json
    analysis: dict  # summary.json
    sub: rio.Grid  # the field's bounding box on the analysis grid
    rows: slice
    cols: slice
    field: np.ndarray  # inside the polygon, on `sub`
    valid: np.ndarray  # field cells with analysis results
    xy: np.ndarray  # polygon vertices in the analysis CRS
    rain_grid: rio.Grid
    rain_cell: tuple  # (row, col) of the IMERG cell under the field's centre
    window: int  # recharge window, days
    thresholds: np.ndarray  # rain for watch, warning, danger over `sub`
    cover_half: tuple | None = None  # (first days, C stack over `sub`) of the half-month cover, if any

    @property
    def path(self) -> str:
        return self.stamp["path"]

    @property
    def years(self) -> tuple:
        return tuple(self.stamp["climate_years"])

    @property
    def lonlat(self) -> list:
        return [tuple(p) for p in self.stamp["polygon"]]

    def load(self, name) -> np.ndarray:
        return rio.read(self.dir / f"{name}.tif").data[:, self.rows, self.cols]


def load_field(analysis_dir) -> Field:
    analysis_dir = Path(analysis_dir)
    stamp = json.loads((analysis_dir / "plan.json").read_text(encoding="utf-8"))
    analysis = json.loads((analysis_dir / "summary.json").read_text(encoding="utf-8"))
    par = analysis["parameters"]
    grid = rio.grid_of(rio.read(analysis_dir / "soil_loss.tif"))
    lons, lats = np.array(stamp["polygon"], float).T
    xs, ys = warp_transform("EPSG:4326", grid.crs, lons.tolist(), lats.tolist())
    xy = np.column_stack([xs, ys])
    ring = np.vstack([xy, xy[:1]])
    full = geometry_mask([{"type": "Polygon", "coordinates": [ring.tolist()]}], out_shape=(grid.height, grid.width),
                         transform=grid.transform, all_touched=True, invert=True)
    rows, cols = rio.bbox_window(full)
    rain_grid = rio.grid_from_dict(stamp["rain_grid"])
    (cx,), (cy,) = warp_transform("EPSG:4326", rain_grid.crs, [float(lons.mean())], [float(lats.mean())])
    col, row = ~rain_grid.transform @ (cx, cy)
    rain_cell = (int(np.clip(np.floor(row), 0, rain_grid.height - 1)), int(np.clip(np.floor(col), 0, rain_grid.width - 1)))
    fld = Field(dir=analysis_dir, stamp=stamp, analysis=analysis, sub=rio.subgrid(grid, rows, cols), rows=rows,
                cols=cols, field=full[rows, cols], valid=None, xy=xy, rain_grid=rain_grid, rain_cell=rain_cell,
                window=int(par["recharge_window_days"]), thresholds=None)
    fld.valid = fld.field & np.isfinite(fld.load("soil_loss")[0])
    if not fld.valid.any():
        raise ValueError("the field has no analysis results (all water, or outside the data)")
    alert_idx = [ls.QUANTILE_PROBS.index(p) for p in par["alert_probabilities"]]
    fld.thresholds = fld.load("landslide_rain_quantiles")[alert_idx]
    if (analysis_dir / f"{HALF_MONTH_C}.tif").exists():
        r = rio.read(analysis_dir / f"{HALF_MONTH_C}.tif")
        fld.cover_half = (np.array(r.descriptions, "datetime64[D]"), r.data[:, rows, cols])
    return fld


# ============================================================================ rain by day


def _record(fld: Field) -> dict:
    """The saved rain record (analysis period) with the recharge-window mean over all of it."""
    rec = load_rain_record(fld.dir / RAIN_RECORD)
    rec["window"] = ls.window_mean(rec["daily"], fld.window)
    return rec


def rain_days(fld: Field, first, last, rain_cache) -> dict:
    """Daily rain, recharge-window mean rain, storm EI30 and storm count (D, h, w) on the rain
    grid for the days first - lead-in .. last: from the saved record when it holds first..last,
    otherwise from the IMERG cache (pulled if missing)."""
    first, last = np.datetime64(first, "D"), np.datetime64(last, "D")
    lead = first - (fld.window - 1)
    rec = _record(fld)
    days = rec["days"]
    if days[0] <= first and last <= days[-1]:
        sel = (days >= lead) & (days <= last)
        return {k: v[sel] for k, v in rec.items() if k != "grid"}
    print(f"Views: rain {first} .. {last} from the IMERG cache (outside the analysis period)")
    end = max(last + 1, min(last + 1 + TAIL_DAYS, np.datetime64("today", "D") - 1))
    times, rate = shared.imerg_series(fld.rain_grid, lead, end, rain_cache)
    days, daily = factors.daily_totals(rate, times, 0.5)
    ei, n = factors.storms_by_day(rate, times, days, 0.5)
    keep = days <= last
    return {"days": days[keep], "daily": daily[keep], "ei_day": ei[keep], "storms_day": n[keep],
            "window": ls.window_mean(daily, fld.window)[keep]}


def _accumulate(fld: Field, rd: dict, group, n_groups: int):
    """Day by day over the field. Returns the field's daily series (every day of `rd`) and the
    range layers per group: day d adds to group[d] (-1: to none, e.g. lead-in days)."""
    days = rd["days"]
    n = days.size
    K, LS, P = fld.load("k_factor")[0], fld.load("ls_factor")[0], fld.load("p_factor")[0]
    c_month, quant = fld.load("c_factor_monthly"), fld.load("landslide_rain_quantiles")
    valid, shape = fld.valid, fld.field.shape
    series = {"rain": np.full(n, np.nan), "window": np.full(n, np.nan), "loss": np.full(n, np.nan),
              "share": np.full((n, 4), np.nan), "storms": rd["storms_day"][:, fld.rain_cell[0], fld.rain_cell[1]]}
    maps = {"soil_loss_window": np.zeros((n_groups,) + shape),
            "landslide_peak_class": np.zeros((n_groups,) + shape, np.uint8),
            "landslide_peak_probability": np.full((n_groups,) + shape, np.nan),
            "rain_window_max": np.full((n_groups,) + shape, np.nan)}
    pg = fld.rain_grid

    def onto(stack):
        return rio.align(stack, pg.transform, pg.crs, fld.sub)

    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", "Mean of empty slice", RuntimeWarning)  # days without a full window
        for b0 in range(0, n, 31):
            b = slice(b0, min(b0 + 31, n))
            r_ei, r_day, r_win = onto(rd["ei_day"][b]), onto(rd["daily"][b]), onto(rd["window"][b])
            loss = r_ei * K * LS * cover_by_day(days[b], c_month, fld.cover_half) * P
            for j, d in enumerate(range(b.start, b.stop)):
                g = group[d]
                series["rain"][d], series["window"][d] = np.nanmean(r_day[j][valid]), np.nanmean(r_win[j][valid])
                series["loss"][d] = np.nanmean(loss[j][valid])
                if g >= 0:
                    maps["soil_loss_window"][g] += np.nan_to_num(loss[j])
                if not np.isfinite(series["window"][d]):
                    continue
                cls = ls.classify(r_win[j], fld.thresholds)
                series["share"][d] = [np.mean(cls[valid] == k) for k in range(4)]
                if g >= 0:
                    maps["landslide_peak_class"][g] = np.maximum(maps["landslide_peak_class"][g], cls)
                    maps["landslide_peak_probability"][g] = np.fmax(maps["landslide_peak_probability"][g],
                                                                    ls.failure_probability(r_win[j], quant))
                    maps["rain_window_max"][g] = np.fmax(maps["rain_window_max"][g], r_win[j])
    return series, maps


def _write_range_layer(fld: Field, path: Path, name: str, data, span: str, band_names=None, tags=None):
    how, description, units, dtype, nodata = RANGE_LAYERS[name]
    data = np.where(fld.valid, data, np.nan if dtype == "float32" else nodata)
    extra = {f"CLASS_{k}": v for k, v in ls.CLASS_NAMES.items()} if name == "landslide_peak_class" else {}
    rio.write(path, data, fld.sub, description=description.format(span=span, window=fld.window), units=units,
              dtype=dtype, nodata=nodata, band_names=band_names, tags={"COMBINE": how, **extra, **(tags or {})})


# ============================================================================ once per analysis


def precompute(analysis_dir) -> None:
    """<range layer>_by_year.tif and daily_series.csv over the analysis period (module doc)."""
    fld = load_field(analysis_dir)
    rd = {k: v for k, v in _record(fld).items() if k != "grid"}
    y0, y1 = fld.years
    year = rd["days"].astype("datetime64[Y]").astype(int) + 1970
    group = np.where((year >= y0) & (year <= y1), year - y0, -1)
    print(f"Yearly range layers and daily series {y0}-{y1}")
    series, maps = _accumulate(fld, rd, group, y1 - y0 + 1)
    names = [str(y) for y in range(y0, y1 + 1)]
    for name in RANGE_LAYERS:
        _write_range_layer(fld, fld.dir / f"{name}_by_year.tif", name, maps[name], "each calendar year (one band per year)",
                           band_names=names, tags={"YEARS": f"{y0}-{y1}"})
    with open(fld.dir / "daily_series.csv", "w", newline="", encoding="utf-8") as f:
        out = csv.writer(f)
        out.writerow(SERIES_COLUMNS)
        for d in np.flatnonzero(group >= 0):
            vals = [series["rain"][d], series["window"][d], series["loss"][d], series["storms"][d],
                    *(100 * series["share"][d])]
            out.writerow([str(rd["days"][d])] + ["" if not np.isfinite(v) else f"{v:.6g}" for v in vals])


# ============================================================================ per request


def render(req, analysis_dir, view_dir) -> dict:
    """Views for req.t_start .. req.t_end (req supplies the dates, the label and the rain cache)."""
    fld = load_field(analysis_dir)
    prof = profiles.PROFILES[fld.path]
    view_dir = Path(view_dir)
    view_dir.mkdir(parents=True, exist_ok=True)
    for old in [*view_dir.glob("*.tif"), *view_dir.glob("*.png")]:
        old.unlink()
    window, sub, valid = fld.window, fld.sub, fld.valid
    years = f"{fld.years[0]}–{fld.years[1]}"
    cell_ha = sub.xres * sub.yres / 1e4

    # ------------------------------------------------------------------ day by day over the field
    print(f"Views: {req.t_start} .. {req.t_end}")
    rd = rain_days(fld, req.t_start, req.t_end, req.global_cache)
    days = rd["days"]
    show = (days >= req.t_start) & (days <= req.t_end)
    series, maps = _accumulate(fld, rd, np.where(show, 0, -1), 1)
    f_rain, f_win, f_loss, share = series["rain"], series["window"], series["loss"], series["share"]
    loss_window, peak_cls = maps["soil_loss_window"][0], maps["landslide_peak_class"][0]

    # ------------------------------------------------------------------ GeoTIFFs
    for name in LONG_TERM:
        _clip_copy(fld.dir / f"{name}.tif", view_dir / f"{name}.tif", fld.rows, fld.cols, fld.field, sub)
    for name in RANGE_LAYERS:
        _write_range_layer(fld, view_dir / f"{name}.tif", name, maps[name][0], "the selected dates",
                           tags={"T_RANGE": f"{req.t_start}/{req.t_end}"})

    # ------------------------------------------------------------------ field summary
    sd = np.flatnonzero(show)
    A = fld.load("soil_loss")[0]
    quant = fld.load("landslide_rain_quantiles")
    loss_lt, tol = A[valid], fld.load("soil_tolerance")[0][valid]
    practice = fld.load("recommended_practice")[0][valid]
    stab, crit = fld.load("stability_class")[0][valid], fld.load("critical_rain")[0][valid]
    q10 = quant[0][valid]
    rain_dep = crit[(stab == 2)]
    monthly = np.nanmean(fld.load("soil_loss_monthly")[:, valid], axis=1)
    month_share = 100 * monthly / monthly.sum() if monthly.sum() > 0 else np.zeros(12)
    hot = share[sd, 2] + share[sd, 3]
    peak_day = int(sd[np.nanargmax(np.where(np.isfinite(hot), hot + 1e-6 * np.nan_to_num(f_win[sd]), -1))])
    worst = int(peak_cls[valid].max())
    lt_mean, win_mean = float(np.mean(loss_lt)), float(np.mean(loss_window[valid]))
    hel = profiles.hel_screen(fld.load("erodibility_index")[0][valid], cell_ha)
    s = {
        "field": {"path": fld.path, "profile": prof.name, "label": req.label,
                  "area": prof.area(polygon_area_m2(fld.xy) / 1e4), "area_unit": prof.area_unit,
                  "resolution_m": sub.xres, "cells": int(valid.sum()),
                  "centroid_lonlat": [float(np.mean([p[0] for p in fld.lonlat])),
                                      float(np.mean([p[1] for p in fld.lonlat]))]},
        "t_range": [str(req.t_start), str(req.t_end)],
        "analysis_period": years,
        "data_sources": fld.analysis.get("sources", {}),
        "defaults_used": fld.stamp.get("defaults_used", []),
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
            "danger_days_per_year": float(np.nanmean(fld.load("landslide_danger_days")[0][valid])),
            "recharge_window_days": window,
        },
        "window": {
            "rain_total_mm": float(np.nansum(f_rain[sd])),
            "wettest_window_mm_day": float(np.nanmax(f_win[sd])) if np.isfinite(f_win[sd]).any() else None,
            "wettest_window_end": str(days[sd[np.nanargmax(np.nan_to_num(f_win[sd], nan=-1))]]),
            "erosive_storms": int(np.sum(series["storms"][sd])),
            "cover_source": ("HLS NDVI of each half-month (Landsat + Sentinel-2) where clear, else the monthly median"
                             if fld.cover_half is not None else f"monthly median NDVI {years}"),
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
    subtitle = (f"{_fmt(s['field']['area'], 1)} {prof.area_unit} · {fld.path.upper()} data at {sub.xres:g} m · "
                f"long-term values {years}")
    plots.field_timeline(
        view_dir / "timeline.png", days[sd], f_rain[sd], f_win[sd], np.nanmin(fld.thresholds[:, valid], axis=1),
        share[sd], f_loss[sd] * prof.loss_per_t_ha, title,
        f"{_fmt(w['rain_total_mm'], 0)} mm of rain · soil loss {_fmt(w['soil_loss'], 2)} {prof.loss_unit} · "
        f"worst landslide alert: {w['worst_alert']} ({w['worst_alert_day']})", prof.loss_unit, window)
    to_px = ~sub.transform
    outline = np.array([to_px @ (x, y) for x, y in np.vstack([fld.xy, fld.xy[:1]])]) - 0.5
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
        {"soil_loss": A, "soil_loss_window": np.where(valid, loss_window, np.nan),
         "practice": fld.load("recommended_practice")[0], "critical_rain": fld.load("critical_rain")[0],
         "peak_class": np.where(valid, peak_cls, np.nan)},
        fld.field, outline, sub.xres, title, subtitle, card, prof.loss_unit, prof.loss_per_t_ha,
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
    if s.get("defaults_used"):
        out.append(f"Not available when this analysis ran, so a default was used: {', '.join(s['defaults_used'])}. "
                   "The next run retries the download.")
    return out
