"""Where rain collects on the field, and how much (US path, on the 1 m terrain).

On working_save/terrain/dem.tif (1 m lidar, field + 500 m), see src/dataanal/ponding.py:
hollows that hold water, what drains into each (with runoff entering from outside the 1 m area,
traced on the ~10 m dem_context.tif), and fill-and-spill for a set of storms. Runoff is
rain x RUNOFF_FRACTION: 1 means soaked soil, every drop runs off (as during Hurricane Helene);
infiltration is not modelled yet, so this is the worst case for a given rain total.

working_save/water/
  water_depth.tif   standing water (m) after each storm in STORMS_MM, one band per storm
  hollow_depth.tif  water depth (m) with every hollow brim-full; NaN outside hollows
  fill_rain.tif     rain (mm) that fills the hollow a cell belongs to (overflow from hollows
                    upstream included): the map for any storm, every hollow under X mm is full
  flow_area.tif     area (m2) draining through each cell, inflow from outside included: the paths
                    runoff takes
  hollows.json      every hollow touching the field: location, size, capacity, fill rain, and
                    the water it holds after each storm
  water.json        the field's totals per storm, parameters, and the key that makes it current
                    (written last)
  water_maps.png    hollows by fill rain with flow paths, and standing water after a storm

Redone when the terrain changes, the parameters below change, or with --refresh.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from rasterio.warp import transform as warp_transform

from ..dataanal import ponding
from ..dataanal import raster_io as rio
from ..ingest import terrain

VERSION = 1  # bump when the method or the files change
STORMS_MM = (25, 50, 100, 200)  # rain totals for water_depth.tif
RUNOFF_FRACTION = 1.0  # share of rain that runs off (1 = soaked soil, worst case)
MIN_DEPTH_M = 0.02  # shallower hollows are lidar noise
MIN_VOLUME_M3 = 0.1  # smaller hollows are lidar noise
WET_M = 0.01  # standing water counted in the field totals
FIGURE_STORM_MM = 100  # storm shown in water_maps.png
META_FILE = "water.json"
FILES = ("water_depth.tif", "hollow_depth.tif", "fill_rain.tif", "flow_area.tif", "hollows.json")


def outside_inflow(context: rio.Raster, fine_dem: np.ndarray, fine: rio.Grid) -> np.ndarray | None:
    """(H, W) on `fine`: area (m2) outside the fine grid whose runoff enters it at each cell, traced
    with D8 on the context DEM and placed on the lowest fine cell of the context cell it enters.
    None if the fine grid does not sit exactly on the context lattice."""
    cgrid = rio.grid_of(context)
    k = cgrid.xres / fine.xres
    col0, row0 = ~cgrid.transform @ (fine.transform.c, fine.transform.f)
    if abs(k - round(k)) > 1e-6 or abs(col0 - round(col0)) > 1e-6 or abs(row0 - round(row0)) > 1e-6:
        return None
    k, r0, c0 = round(k), round(row0), round(col0)
    fh, fw = fine.height // k, fine.width // k
    if fh * k != fine.height or fw * k != fine.width or r0 < 0 or c0 < 0 \
            or r0 + fh > cgrid.height or c0 + fw > cgrid.width:
        return None
    z = context.data[0]
    _, rec = ponding.condition(z, cgrid.transform, cgrid.crs)
    inside = np.zeros(z.shape, bool)
    inside[r0:r0 + fh, c0:c0 + fw] = True
    acc = ponding.accumulate(rec, np.where(~inside & np.isfinite(z), cgrid.xres * cgrid.yres, 0.0))
    outside = np.flatnonzero(~inside.ravel())
    to = rec[outside]
    enters = inside.ravel()[to]
    ext = np.zeros(z.size)
    np.add.at(ext, to[enters], acc[outside[enters]])
    block = ext.reshape(z.shape)[r0:r0 + fh, c0:c0 + fw]
    cells = fine_dem.reshape(fh, k, fw, k).transpose(0, 2, 1, 3).reshape(fh, fw, k * k)
    lowest = np.argmin(np.where(np.isfinite(cells), cells, np.inf), axis=2)
    out = np.zeros((fh, fw, k * k))
    np.put_along_axis(out, lowest[..., None], block[..., None], axis=2)
    return out.reshape(fh, fw, k, k).transpose(0, 2, 1, 3).reshape(fine.height, fine.width)


def _is_current(out_dir: Path, key: dict) -> bool:
    if not ((out_dir / META_FILE).exists() and all((out_dir / f).exists() for f in FILES)):
        return False
    meta = json.loads((out_dir / META_FILE).read_text(encoding="utf-8"))
    return all(meta.get(k) == v for k, v in key.items())


def ensure(analysis_dir, terrain_dir, out_dir, refresh: bool = False, figures: bool = True) -> dict | None:
    """Write working_save/water from the fine terrain unless it is current. Returns water.json's
    content, or None when there is no fine terrain."""
    terrain_dir, out_dir = Path(terrain_dir), Path(out_dir)
    tmeta_path = terrain_dir / terrain.META_FILE
    if not (tmeta_path.exists() and (terrain_dir / terrain.DEM_FILE).exists()):
        return None
    tmeta = json.loads(tmeta_path.read_text(encoding="utf-8"))
    key = {"version": VERSION, "terrain_created_utc": tmeta.get("created_utc"), "storms_mm": list(STORMS_MM),
           "runoff_fraction": RUNOFF_FRACTION, "min_depth_m": MIN_DEPTH_M, "min_volume_m3": MIN_VOLUME_M3}
    if not refresh and _is_current(out_dir, key):
        return json.loads((out_dir / META_FILE).read_text(encoding="utf-8"))

    stamp = json.loads((Path(analysis_dir) / "plan.json").read_text(encoding="utf-8"))
    dem_r = rio.read(terrain_dir / terrain.DEM_FILE)
    dem, grid = dem_r.data[0].astype(float), rio.grid_of(dem_r)
    cell = grid.xres * grid.yres
    inflow = None
    if (terrain_dir / terrain.CONTEXT_FILE).exists():
        inflow = outside_inflow(rio.read(terrain_dir / terrain.CONTEXT_FILE), dem, grid)
    print(f"Water: hollows and fill-and-spill on {grid.width} x {grid.height} cells at {grid.xres:g} m"
          + ("" if inflow is not None else " (no inflow from outside the 1 m area)"))

    h = ponding.find_hollows(dem, grid.transform, grid.crs, extra_area_m2=inflow,
                             min_depth_m=MIN_DEPTH_M, min_volume_m3=MIN_VOLUME_M3)
    rain = np.array(STORMS_MM, float)
    _, stored, off_grid = ponding.route(h, rain / 1000 * RUNOFF_FRACTION)
    depth = np.stack([ponding.water_depth(h, stored[:, s]) for s in range(len(rain))]).astype(np.float32)
    fill = ponding.fill_rain_mm(h, RUNOFF_FRACTION)
    field = terrain._field_mask(stamp["polygon"], grid)
    lab = h.label

    in_field = np.bincount(lab[field], minlength=h.n + 1)
    touching = np.flatnonzero(in_field[1:] > 0) + 1
    storms = []
    for s, mm in enumerate(STORMS_MM):
        d = depth[s]
        wet = field & (d >= WET_M)
        held = stored[touching, s]
        storms.append({
            "rain_mm": mm,
            "field": {"water_m3": round(float(d[field].sum() * cell), 2),
                      "wet_area_m2": round(float(wet.sum() * cell), 1),
                      "wet_share_pct": round(100 * float(wet.sum()) / max(int(field.sum()), 1), 2),
                      "max_depth_m": round(float(d[field].max()) if field.any() else 0.0, 3),
                      "hollows_holding_water": int((held > 0).sum()),
                      "hollows_full": int((held >= h.capacity_m3[touching] * (1 - 1e-9)).sum())},
            "grid": {"water_m3": round(float(stored[:, s].sum()), 1), "off_grid_m3": round(float(off_grid[s]), 1),
                     "rain_m3": round(float(mm / 1000 * RUNOFF_FRACTION * h.catchment_m2.sum()), 1)},
        })
        print(f"  {mm:g} mm of rain: {storms[-1]['field']['water_m3']:,.1f} m3 stands in the field "
              f"({storms[-1]['field']['wet_share_pct']:.1f}% of it wet, deepest {storms[-1]['field']['max_depth_m']:.2f} m)")

    # Hollows touching the field, largest first.
    from scipy import ndimage
    centres = ndimage.center_of_mass(np.ones(lab.shape), lab, touching) if touching.size else []
    xs = [grid.transform.c + (c + 0.5) * grid.transform.a for _, c in centres]
    ys = [grid.transform.f + (r + 0.5) * grid.transform.e for r, _ in centres]
    lons, lats = warp_transform(grid.crs, "EPSG:4326", xs, ys) if touching.size else ([], [])
    rows = []
    for i, k in enumerate(touching):
        rows.append({"id": int(k), "lon": round(lons[i], 6), "lat": round(lats[i], 6),
                     "in_field_pct": round(100 * in_field[k] * cell / h.area_m2[k], 1),
                     "area_m2": round(float(h.area_m2[k]), 1), "capacity_m3": round(float(h.capacity_m3[k]), 2),
                     "max_depth_m": round(float(h.max_depth_m[k]), 3),
                     "catchment_m2": round(float(h.catchment_m2[k]), 1),
                     "fill_rain_mm": None if not np.isfinite(fill[k]) else round(float(fill[k]), 1),
                     "stored_m3": {f"{mm:g}": round(float(stored[k, s]), 2) for s, mm in enumerate(STORMS_MM)}})
    rows.sort(key=lambda r: -r["capacity_m3"])

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / META_FILE).unlink(missing_ok=True)  # a half-written result is never taken as current
    rio.write(out_dir / "water_depth.tif", depth, grid, units="m", band_names=[f"{mm:g}mm" for mm in STORMS_MM],
              description=f"standing water after each storm, runoff fraction {RUNOFF_FRACTION:g}",
              tags={"STORMS_MM": ",".join(f"{mm:g}" for mm in STORMS_MM), "RUNOFF_FRACTION": RUNOFF_FRACTION})
    rio.write(out_dir / "hollow_depth.tif", h.brim_depth_m.astype(np.float32), grid, units="m",
              description="water depth with every hollow brim-full")
    rio.write(out_dir / "fill_rain.tif", np.where(lab > 0, fill[lab], np.nan).astype(np.float32), grid, units="mm",
              description="rain that fills the hollow each cell belongs to (NaN: none, or more than 2000 mm)")
    rio.write(out_dir / "flow_area.tif", h.flow_area_m2.astype(np.float32), grid, units="m2",
              description="area draining through each cell, inflow from outside the grid included")
    (out_dir / "hollows.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    if figures:
        _figure(out_dir / "water_maps.png", dem, grid, h, fill, depth, field, stamp, tmeta)

    meta = {**key, "res_m": grid.xres, "grid": grid.as_dict(), "outside_inflow": inflow is not None,
            "outside_inflow_m2": round(float(inflow.sum()), 0) if inflow is not None else 0.0,
            "hollows": {"count": h.n, "touching_field": int(touching.size),
                        "capacity_m3": round(float(h.capacity_m3.sum()), 1),
                        "capacity_touching_field_m3": round(float(h.capacity_m3[touching].sum()), 1)},
            "wet_threshold_m": WET_M, "storms": storms,
            "created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}
    (out_dir / META_FILE).write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


# ============================================================================ figure

FILL_BINS = (0, 25, 50, 100, 200, np.inf)
FILL_COLOURS = ("#08306b", "#2171b5", "#6baed6", "#c6dbef", "#f7fbff")


def _figure(path, dem, grid, h, fill, depth, field, stamp, tmeta):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.colors import BoundaryNorm, LinearSegmentedColormap, ListedColormap, LogNorm
    from matplotlib.patches import Patch

    s = STORMS_MM.index(FIGURE_STORM_MM) if FIGURE_STORM_MM in STORMS_MM else len(STORMS_MM) - 1
    ls = matplotlib.colors.LightSource(azdeg=315, altdeg=45)
    shade = ls.hillshade(np.nan_to_num(dem, nan=np.nanmean(dem)), vert_exag=2, dx=grid.xres, dy=grid.yres)
    rows, cols = np.nonzero(field)
    pad = 60 / grid.xres
    zoom = (max(cols.min() - pad, 0), min(cols.max() + pad, grid.width), min(rows.max() + pad, grid.height),
            max(rows.min() - pad, 0))
    lons, lats = np.array(stamp["polygon"], float).T
    xs, ys = warp_transform("EPSG:4326", grid.crs, lons.tolist(), lats.tolist())
    px = [(x - grid.transform.c) / grid.transform.a for x in xs] + [(xs[0] - grid.transform.c) / grid.transform.a]
    py = [(y - grid.transform.f) / grid.transform.e for y in ys] + [(ys[0] - grid.transform.f) / grid.transform.e]
    fill_map = np.where(h.label > 0, fill[h.label], np.nan)
    fill_map = np.where(np.isnan(fill_map) & (h.label > 0), 1e9, fill_map)
    cmap_fill, norm_fill = ListedColormap(FILL_COLOURS), BoundaryNorm(FILL_BINS[:-1] + (1e10,), len(FILL_COLOURS))
    flow = np.where(h.flow_area_m2 >= 2000, h.flow_area_m2, np.nan)
    water = np.where(depth[s] >= WET_M, depth[s], np.nan)

    fig, axes = plt.subplots(2, 2, figsize=(13, 12), constrained_layout=True)
    for row, window in enumerate((None, zoom)):
        a, b = axes[row]
        for ax in (a, b):
            ax.imshow(shade, cmap="gray", vmin=0, vmax=1, interpolation="nearest")
            ax.plot(px, py, color="white", lw=1.5)
            if window:
                ax.set_xlim(window[0], window[1])
                ax.set_ylim(window[2], window[3])
            ax.set_xticks([])
            ax.set_yticks([])
        a.imshow(flow, cmap=LinearSegmentedColormap.from_list("flow", ["#6fd3ff", "#0050c8"]),
                 norm=LogNorm(2000, max(np.nanmax(h.flow_area_m2), 2001)), interpolation="nearest")
        a.imshow(fill_map, cmap=cmap_fill, norm=norm_fill, interpolation="nearest")
        im = b.imshow(water, cmap="Blues", vmin=0, vmax=max(0.3, float(np.nanmax(water)) if np.isfinite(water).any()
                      else 0.3), interpolation="nearest")
        if row == 0:
            a.set_title("Hollows by the rain that fills them, and where runoff flows")
            b.set_title(f"Standing water after {STORMS_MM[s]:g} mm of rain (all of it running off)")
            labels = ["< 25 mm", "25-50 mm", "50-100 mm", "100-200 mm", "> 200 mm"]
            a.legend(handles=[Patch(color=c, label=l) for c, l in zip(FILL_COLOURS, labels)], loc="lower left",
                     fontsize=8, title="fills with", title_fontsize=8, framealpha=0.85)
            fig.colorbar(im, ax=b, shrink=0.7, label="water depth (m)")
        else:
            a.set_title("The field (white) and 60 m around it")
            b.set_title("The field: standing water")
    lidar = tmeta.get("lidar_share_field_pct")
    fig.suptitle(f"Where rain collects - 1 m terrain ({lidar:.0f}% lidar on the field)" if lidar is not None
                 else "Where rain collects - 1 m terrain", fontsize=13)
    fig.savefig(path, dpi=110)
    plt.close(fig)
