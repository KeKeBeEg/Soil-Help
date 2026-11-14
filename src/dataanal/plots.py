"""Static figures for a RUSLE run (matplotlib, light surface)."""
from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib import font_manager  # noqa: E402
from matplotlib.colors import BoundaryNorm, LinearSegmentedColormap, ListedColormap, Normalize  # noqa: E402

from .factors import SEVERITY_CLASSES  # noqa: E402

SURFACE, INK, INK_2, MUTED, GRID, AXIS = "#fcfcfb", "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7"
SERIES_1 = "#2a78d6"
BLUE_RAMP = ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
# One-hue ordinal ramp for the six severity classes (passes validate_palette.py --ordinal).
SEVERITY_RAMP = ["#ff8253", "#ea6733", "#cc5524", "#af4313", "#913306", "#732500"]
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
    cmap = cmap.copy()
    cmap.set_bad(SURFACE)
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
             f"{sa['area_km2']:,.0f} km² · rainfall {summary['period']['rainfall']} (IMERG V07), cover "
             f"{summary['period']['cover']} (Landsat 8/9), soil ERA5-Land, terrain FABDEM · "
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

    fig.suptitle("Rainfall erosivity from IMERG V07 half-hourly storms (EI30)",
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
    res = summary["study_area"]["grid"]["res"]
    factor_maps(out_dir / "rusle_maps.png", layers, inside, res, summary)
    rainfall_charts(out_dir / "rainfall_erosivity.png", summary)
