"""Static figures for a RUSLE run (matplotlib, light surface)."""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from matplotlib.colors import BoundaryNorm, LinearSegmentedColormap, ListedColormap, LogNorm, Normalize  # noqa: E402

from .factors import SEVERITY_CLASSES  # noqa: E402
from .landslide import CLASS_NAMES  # noqa: E402

SURFACE, INK, INK_2, MUTED, GRID, AXIS = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
SERIES_1 = "#2a78d6"
BLUE_RAMP = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
# One-hue ordinal ramp for the six severity classes (passes validate_palette.py --ordinal).
SEVERITY_RAMP = ["#ff8253", "#ea6733", "#cc5524", "#af4313", "#913306", "#732500"]
# Landslide alert levels are states, so they take the fixed status palette (good, warning,
# serious, critical) and are always shown with their names.
STATUS = ["#0ca30c", "#fab219", "#ec835a", "#d03b3b"]
NEUTRAL = "#f0efec"  # cells that never fail
# Ordinal practice intensity 0-4: blue steps 250..650 (ordinal ramps start no lighter than 250).
PRACTICE_RAMP = ["#86b6ef", "#5598e7", "#2a78d6", "#1c5cab", "#104281"]
UNIT_R = "MJ mm ha⁻¹ h⁻¹ yr⁻¹"
UNIT_K = "t ha h ha⁻¹ MJ⁻¹ mm⁻¹"
UNIT_A = "t ha⁻¹ yr⁻¹"


def _system_sans() -> str:
    installed = {f.name for f in font_manager.fontManager.ttflist}
    return next((f for f in ("Segoe UI", "Helvetica Neue", "Arial") if f in installed), "DejaVu Sans")


def _style():
    plt.rcParams.update({
        "font.family": _system_sans(), "font.size": 9,
        "text.color": INK, "axes.labelcolor": INK_2, "axes.edgecolor": AXIS,
        "xtick.color": MUTED, "ytick.color": MUTED, "xtick.major.size": 0, "ytick.major.size": 0,
        "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
        "axes.titlesize": 10, "axes.titleweight": "semibold", "axes.titlelocation": "left",
    })


def _chart_axes(ax, grid_axis="y"):
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(AXIS)
    ax.grid(axis=grid_axis, color=GRID, linewidth=0.6)
    ax.set_axisbelow(True)


def _crop(inside):
    rows, cols = np.flatnonzero(inside.any(axis=1)), np.flatnonzero(inside.any(axis=0))
    return slice(rows[0], rows[-1] + 1), slice(cols[0], cols[-1] + 1)


def _map(fig, ax, arr, title, label, cmap, norm, show_colorbar=True):
    cmap = cmap.with_extremes(bad=SURFACE)
    im = ax.imshow(np.ma.masked_invalid(arr), cmap=cmap, norm=norm)
    ax.set_title(title, pad=6)
    ax.set_axis_off()
    cb = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.02)
    cb.ax.set_visible(show_colorbar)  # hidden colorbars still reserve space, keeping maps aligned
    cb.outline.set_visible(False)
    cb.ax.tick_params(colors=MUTED, length=0, labelsize=8)
    cb.set_label(label, color=INK_2, fontsize=8)


def _scalebar(ax, shape, res):
    h, w = shape
    width_km = w * res / 1000
    km = max([k for k in (1, 2, 5, 10, 20, 50, 100) if k <= width_km / 4] or [1])
    x0, y0 = 0.04 * w, 0.95 * h
    ax.plot([x0, x0 + km * 1000 / res], [y0, y0], color=INK_2, linewidth=2, solid_capstyle="butt")
    ax.text(x0, y0 - 0.02 * h, f"{km:g} km", color=INK_2, fontsize=8, va="bottom")


def _robust(values, lo_pct, hi_pct, floor=None):
    v = values[np.isfinite(values)]
    lo, hi = (np.percentile(v, lo_pct) if lo_pct else 0.0), np.percentile(v, hi_pct)
    if floor is not None:
        hi = max(hi, floor)
    return Normalize(lo, hi if hi > lo else lo + 1e-6)


def factor_maps(path: Path, layers: dict, inside, res: float, summary: dict):
    rs, cs = _crop(inside)
    view = {k: np.where(inside, v, np.nan)[rs, cs] for k, v in layers.items()}
    blue = LinearSegmentedColormap.from_list("blue", BLUE_RAMP)
    edges = [lo for lo, _, _ in SEVERITY_CLASSES] + [1e9]
    sev_labels = [f"<{SEVERITY_CLASSES[0][1]:g}"] + [f"{lo:g}–{hi:g}" for lo, hi, _ in SEVERITY_CLASSES[1:-1]] \
        + [f">{SEVERITY_CLASSES[-1][0]:g}"]

    fig, axes = plt.subplots(2, 3, figsize=(13, 8.6))
    (ax_r, ax_k, ax_ls), (ax_c, ax_a, ax_bar) = axes
    _map(fig, ax_r, view["R"], "R · rainfall erosivity", UNIT_R, blue, _robust(view["R"], 1, 99))
    _map(fig, ax_k, view["K"], "K · soil erodibility", UNIT_K, blue, Normalize(0, 0.045))
    _map(fig, ax_ls, view["LS"], "LS · slope length & steepness", "dimensionless", blue, _robust(view["LS"], 0, 98))
    _map(fig, ax_c, view["C"], "C · cover management", "dimensionless", blue, _robust(view["C"], 0, 99, floor=0.1))
    # The class bar chart beside this map is its legend.
    _map(fig, ax_a, view["A"], "A · average annual soil loss (classes at right)", UNIT_A,
         ListedColormap(SEVERITY_RAMP), BoundaryNorm(edges, len(SEVERITY_RAMP)), show_colorbar=False)
    _scalebar(ax_r, view["R"].shape, res)

    shares = list(summary["severity_area_pct"].values())
    y = np.arange(len(shares))
    ax_bar.barh(y, shares, color=SEVERITY_RAMP, height=0.7)
    ax_bar.set_yticks(y, [f"{lab}  {name}" for lab, (_, _, name) in zip(sev_labels, SEVERITY_CLASSES)])
    ax_bar.tick_params(axis="y", colors=INK_2)
    ax_bar.invert_yaxis()
    for yi, v in zip(y, shares):
        ax_bar.text(v + max(shares) * 0.02, yi, f"{v:.1f}%", va="center", fontsize=8, color=INK_2)
    ax_bar.set_xlim(0, max(shares) * 1.18)
    ax_bar.set_xlabel("Share of study area (%)")
    ax_bar.set_title(f"Area by soil-loss class ({UNIT_A})", pad=6)
    _chart_axes(ax_bar, grid_axis="x")

    sa, sl = summary["study_area"], summary["soil_loss_t_ha_yr"]
    fig.suptitle(f"RUSLE average annual soil loss · {sa['label']}",
                 x=0.012, ha="left", fontsize=13, fontweight="semibold")
    fig.text(0.012, 0.925,
             f"{sa['area_km2']:,.0f} km² · rainfall {summary['period']['rainfall']} · "
             f"vegetation cover {summary['period']['cover']} · "
             f"mean {sl['mean']:.1f}, median {sl['median']:.1f} t ha⁻¹ yr⁻¹ · "
             f"total {summary['total_soil_loss_Mt_yr']:.2f} Mt yr⁻¹",
             color=INK_2, fontsize=9)
    fig.subplots_adjust(left=0.02, right=0.97, top=0.88, bottom=0.06, wspace=0.24, hspace=0.18)
    fig.savefig(path, dpi=160)
    plt.close(fig)


def rainfall_charts(path: Path, summary: dict):
    r = summary["rainfall"]
    monthly = np.array(r["R_monthly_mean"])
    years, yearly = list(r["R_by_year"]), np.array(list(r["R_by_year"].values()))
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(11, 3.8), gridspec_kw={"width_ratios": [2, 1.2]})

    a1.bar(np.arange(12), monthly, color=SERIES_1, width=0.7)
    a1.set_xticks(np.arange(12), list("JFMAMJJASOND"))
    a1.set_ylabel("Mean R (MJ mm ha⁻¹ h⁻¹)")
    a1.set_title("Erosivity by calendar month")
    _chart_axes(a1)

    x = np.arange(len(years))
    a2.bar(x, yearly, color=SERIES_1, width=0.6)
    a2.axhline(yearly.mean(), color=INK_2, linewidth=1)
    a2.text(x[-1] + 0.45, yearly.mean(), "mean", color=INK_2, fontsize=8, va="bottom", ha="right")
    a2.set_xticks(x, years)
    a2.set_ylabel(f"R ({UNIT_R})")
    a2.set_title("Erosivity by year")
    _chart_axes(a2)

    fig.suptitle(f"Rainfall erosivity from {r['step_minutes']:g}-minute rainfall, storm EI30",
                 x=0.012, ha="left", fontsize=12, fontweight="semibold")
    fig.text(0.012, 0.86,
             f"Study-area mean R {r['R_ei30_mean']:,.0f} {UNIT_R} · "
             f"{r['erosive_storms_per_year']:.0f} erosive storms/yr · "
             f"{r['mean_annual_precip_mm']:,.0f} mm/yr rain · "
             f"Renard–Freimund check {r['R_renard_freimund_mean']:,.0f}",
             color=INK_2, fontsize=9)
    fig.subplots_adjust(left=0.07, right=0.98, top=0.76, bottom=0.12, wspace=0.28)
    fig.savefig(path, dpi=160)
    plt.close(fig)


def make_figures(out_dir: Path, layers: dict, inside, summary: dict):
    _style()
    res = summary["study_area"]["grid"]["res_m"]
    factor_maps(out_dir / "rusle_maps.png", layers, inside, res, summary)
    rainfall_charts(out_dir / "rainfall_erosivity.png", summary)


def class_bars(ax, shares, labels, colors, title, xlabel="Share of area (%)"):
    """Horizontal bars of class shares; doubles as the legend of a class map."""
    y = np.arange(len(shares))
    ax.barh(y, shares, color=colors, height=0.7)
    ax.set_yticks(y, labels)
    ax.tick_params(axis="y", colors=INK_2)
    ax.invert_yaxis()
    top = max(max(shares), 1e-9)
    for yi, v in zip(y, shares):
        ax.text(v + top * 0.02, yi, f"{v:.1f}%", va="center", fontsize=8, color=INK_2)
    ax.set_xlim(0, top * 1.2)
    ax.set_xlabel(xlabel)
    ax.set_title(title, pad=6)
    _chart_axes(ax, grid_axis="x")


def rain_threshold_map(fig, ax, rain, title, show_colorbar=True):
    """Rain that triggers failure: darker = less rain needed; cells that never fail in gray."""
    never = np.isinf(rain) | (rain >= 9000)
    ax.imshow(np.ma.masked_where(~never, np.ones(rain.shape)), cmap=ListedColormap([NEUTRAL]), vmin=0, vmax=1)
    cmap = LinearSegmentedColormap.from_list("risk", SEVERITY_RAMP).reversed().with_extremes(bad=(0, 0, 0, 0))
    im = ax.imshow(np.ma.masked_where(never | ~np.isfinite(rain), np.clip(rain, 5, 500)), cmap=cmap, norm=LogNorm(5, 500))
    ax.set_title(title, pad=6)
    ax.set_axis_off()
    cb = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.02)
    cb.ax.set_visible(show_colorbar)
    cb.outline.set_visible(False)
    cb.ax.tick_params(colors=MUTED, length=0, labelsize=8)
    cb.set_label("mm/day over 3 days (gray: never)", color=INK_2, fontsize=8)


def landslide_maps(path: Path, layers: dict, inside, summary: dict):
    _style()
    rs, cs = _crop(inside)
    view = {k: np.where(inside, v, np.nan)[rs, cs] for k, v in layers.items()}
    lsum = summary["landslide"]
    years = summary["period"]["rainfall"]
    fig, axes = plt.subplots(2, 2, figsize=(11.5, 9.2))
    (ax_rain, ax_cls), (ax_days, ax_bar) = axes

    rain_threshold_map(fig, ax_rain, view["critical_rain"], "Rain that makes the slope fail (3-day mean)")
    _map(fig, ax_cls, view["class_wettest"], f"Alert level in the wettest 3 days of {years} (levels at right)", "",
         ListedColormap(STATUS), BoundaryNorm([-0.5, 0.5, 1.5, 2.5, 3.5], 4), show_colorbar=False)
    days = view["danger_days"]
    _map(fig, ax_days, days, "Days per year at danger level", "days/yr",
         LinearSegmentedColormap.from_list("blue", BLUE_RAMP), _robust(days, 0, 99, floor=1.0))
    _scalebar(ax_rain, view["critical_rain"].shape, summary["study_area"]["grid"]["res_m"])

    cls = view["class_wettest"][np.isfinite(view["class_wettest"])]
    shares = [float(100 * np.mean(cls == k)) for k in CLASS_NAMES]
    class_bars(ax_bar, shares, [name for name in CLASS_NAMES.values()], STATUS,
               "Area by alert level, wettest 3 days")

    sa, ww = summary["study_area"], lsum["wettest_window"]
    stab = ", ".join(f"{v:.0f}% {k.split(' ', 1)[1]}" for k, v in lsum["stability_class_area_pct"].items())
    fig.suptitle(f"Shallow-landslide susceptibility · {sa['label']}", x=0.012, ha="left", fontsize=13,
                 fontweight="semibold")
    fig.text(0.012, 0.925, f"Wettest 3 days of {years} ended {ww['end_date']} ({ww['mean_rain_mm_day']:.0f} mm/day) · "
                           f"{stab}", color=INK_2, fontsize=9)
    fig.subplots_adjust(left=0.02, right=0.97, top=0.88, bottom=0.06, wspace=0.22, hspace=0.18)
    fig.savefig(path, dpi=160)
    plt.close(fig)


# ============================================================================ field views


def _class_map(fig, ax, arr, title, colors, labels):
    """Discrete map with one colour per class code 0..n-1 and a labelled colour bar."""
    n = len(colors)
    cmap = ListedColormap(colors).with_extremes(bad=SURFACE)
    im = ax.imshow(np.ma.masked_invalid(arr), cmap=cmap, norm=BoundaryNorm(np.arange(n + 1) - 0.5, n))
    ax.set_title(title, pad=6)
    ax.set_axis_off()
    cb = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.02, ticks=np.arange(n))
    cb.ax.set_yticklabels(labels)
    cb.outline.set_visible(False)
    cb.ax.tick_params(colors=INK_2, length=0, labelsize=8)


def field_maps(path: Path, layers: dict, field, outline_px, res: float, title: str, subtitle: str, card: list[str],
               loss_unit: str, loss_factor: float, practice_labels: list[str]):
    """Six panels over the field: long-term soil loss, soil loss in the window, practice,
    landslide rain threshold, peak landslide alert in the window, and a numbers card."""
    _style()
    view = {k: np.where(field, v, np.nan) for k, v in layers.items()}
    fig, axes = plt.subplots(2, 3, figsize=(14, 8.8))
    (a_loss, a_win, a_prac), (a_rain, a_peak, a_card) = axes

    edges = [lo for lo, _, _ in SEVERITY_CLASSES[1:]]
    cls = np.where(np.isfinite(view["soil_loss"]), np.digitize(view["soil_loss"], edges), np.nan)
    sev_labels = ([f"<{SEVERITY_CLASSES[0][1] * loss_factor:.2g}"]
                  + [f"{lo * loss_factor:.2g}–{hi * loss_factor:.2g}" for lo, hi, _ in SEVERITY_CLASSES[1:-1]]
                  + [f">{SEVERITY_CLASSES[-1][0] * loss_factor:.2g}"])
    _class_map(fig, a_loss, cls, f"Average yearly soil loss ({loss_unit}/yr)", SEVERITY_RAMP, sev_labels)
    win = view["soil_loss_window"] * loss_factor
    _map(fig, a_win, win, "Soil loss in the selected dates", loss_unit,
         LinearSegmentedColormap.from_list("loss", SEVERITY_RAMP), _robust(win, 0, 98, floor=1e-3))
    _class_map(fig, a_prac, view["practice"], "Practice that keeps loss within tolerance", PRACTICE_RAMP,
               practice_labels)
    rain_threshold_map(fig, a_rain, view["critical_rain"], "Rain that makes the slope fail")
    _class_map(fig, a_peak, view["peak_class"], "Worst landslide alert in the selected dates", STATUS,
               list(CLASS_NAMES.values()))
    for ax in (a_loss, a_win, a_prac, a_rain, a_peak):
        ax.plot(outline_px[:, 0], outline_px[:, 1], color=INK, linewidth=1.2)
    _scalebar_m(a_loss, field.shape, res)

    a_card.set_axis_off()
    a_card.set_title("This field", pad=6)
    for i, line in enumerate(card):
        a_card.text(0.0, 0.95 - i * 0.075, line, transform=a_card.transAxes, fontsize=9,
                    color=INK if not line.startswith("  ") else INK_2, va="top")
    fig.suptitle(title, x=0.012, ha="left", fontsize=13, fontweight="semibold")
    fig.text(0.012, 0.925, subtitle, color=INK_2, fontsize=9)
    fig.subplots_adjust(left=0.02, right=0.94, top=0.88, bottom=0.04, wspace=0.42, hspace=0.2)
    fig.savefig(path, dpi=160)
    plt.close(fig)


def _scalebar_m(ax, shape, res):
    h, w = shape
    width_m = w * res
    m = max([k for k in (10, 20, 50, 100, 200, 500, 1000, 2000) if k <= width_m / 3] or [10])
    x0, y0 = 0.04 * w, 0.95 * h
    ax.plot([x0, x0 + m / res], [y0, y0], color=INK_2, linewidth=2, solid_capstyle="butt")
    ax.text(x0, y0 - 0.02 * h, f"{m:g} m" if m < 1000 else f"{m / 1000:g} km", color=INK_2, fontsize=8, va="bottom")


def field_timeline(path: Path, days, rain_daily, rain_window, thresholds, share, loss_daily, title: str,
                   subtitle: str, loss_unit: str, window_days: int):
    """Daily rain and the recharge-window mean against the field's alert thresholds, the share
    of the field at each alert level, and daily soil loss."""
    import matplotlib.dates as mdates

    _style()
    x = days.astype("datetime64[D]").astype("datetime64[ms]").astype(object)
    fig, (a1, a2, a3) = plt.subplots(3, 1, sharex=True, figsize=(11, 8.4),
                                     gridspec_kw={"height_ratios": [2.0, 1.2, 1.1]})
    width = 0.8 if len(x) <= 120 else 1.0
    a1.bar(x, rain_daily, color=SERIES_1, width=width, label="daily rain")
    a1.plot(x, rain_window, color="#eb6834", linewidth=2, label=f"{window_days}-day mean")
    top = np.nanmax(np.concatenate([np.nan_to_num(rain_daily), np.nan_to_num(rain_window), [1.0]]))
    for t, name, color in zip(thresholds, list(CLASS_NAMES.values())[1:], STATUS[1:]):
        if np.isfinite(t) and t < 3 * top:
            a1.axhline(t, color=color, linewidth=1.2, linestyle=(0, (4, 3)))
            a1.text(x[0], t, f"{name} {t:.0f} mm/day", color=INK_2, fontsize=8, va="bottom", ha="left")
    a1.set_ylabel("mm/day")
    a1.set_title(f"Rain on the field ({window_days}-day mean) against the alert thresholds of its weakest spot")
    a1.legend(loc="upper left", bbox_to_anchor=(1.01, 1.0), frameon=False, fontsize=8)
    _chart_axes(a1)

    a2.stackplot(x, (100 * np.asarray(share)).T, colors=STATUS, labels=list(CLASS_NAMES.values()))
    a2.set_ylim(0, 100)
    a2.set_ylabel("% of field")
    a2.set_title("Field area at each landslide alert level")
    a2.legend(loc="upper left", bbox_to_anchor=(1.01, 1.0), frameon=False, fontsize=8)
    _chart_axes(a2)

    a3.bar(x, loss_daily, color=SEVERITY_RAMP[1], width=width)
    a3.set_ylabel(loss_unit)
    a3.set_title("Soil loss from each day's storms (field mean)")
    _chart_axes(a3)
    locator = mdates.AutoDateLocator()
    a3.xaxis.set_major_locator(locator)
    a3.xaxis.set_major_formatter(mdates.ConciseDateFormatter(locator))

    fig.suptitle(title, x=0.012, ha="left", fontsize=13, fontweight="semibold")
    fig.text(0.012, 0.935, subtitle, color=INK_2, fontsize=9)
    fig.subplots_adjust(left=0.08, right=0.84, top=0.87, bottom=0.06, hspace=0.38)
    fig.savefig(path, dpi=160)
    plt.close(fig)
