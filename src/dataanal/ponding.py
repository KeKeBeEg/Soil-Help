"""Where rain collects on a DEM, and how much: hollows, what drains into each, and fill-and-spill.

Pure numpy/scipy (pysheds for depression filling and flow directions); no file or Earth Engine
access. Used by src/backend/water.py on the 1 m terrain.

  hollow          a connected area the depression-filled DEM raises above the ground: water
                  standing there cannot drain until it reaches the hollow's spill level. Hollows
                  shallower than min_depth_m or smaller than min_volume_m3 are lidar noise and
                  are treated as ground water flows over.
  capacity        water a hollow holds when brim-full: sum of (spill level - ground) x cell area.
  catchment       every cell whose runoff, following D8 steepest descent on the filled DEM
                  (flats resolved, so water crosses a full hollow towards its spill point), first
                  reaches that hollow. Includes the hollow itself and any area outside the grid
                  that drains in (extra_area_m2).
  fill-and-spill  hollows are visited upstream first: each keeps min(inflow, capacity) and
                  passes the rest to the next hollow downhill, or off the grid.
  water level     the stored volume spread from the lowest cell up, one level per hollow. A hollow
                  with sub-hollows inside it is treated as one pond (they fill as one), which
                  slightly misplaces water in partly filled compound hollows.

Runoff is rain x runoff_fraction everywhere (1 = soaked soil, the worst case); infiltration and
evaporation are not modelled, and flow takes no time: the result is the state after the storm
has run off, not its timing.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import ndimage

D8 = {64: (-1, 0), 128: (-1, 1), 1: (0, 1), 2: (1, 1), 4: (1, 0), 8: (1, -1), 16: (0, -1), 32: (-1, -1)}  # pysheds dirmap


def condition(dem, transform, crs):
    """Depression-filled DEM and D8 receivers (flat index; a cell is its own receiver where water
    leaves the grid). Flats, including filled hollows, drain towards their spill point."""
    from pyproj import CRS
    from pysheds.grid import Grid
    from pysheds.view import Raster, ViewFinder

    dem = np.asarray(dem, float)
    valid = np.isfinite(dem)
    nodata = -9999.0
    vf = ViewFinder(affine=transform, shape=dem.shape, nodata=nodata, crs=CRS.from_user_input(crs))
    grid = Grid(viewfinder=vf)
    z = Raster(np.where(valid, dem, nodata), viewfinder=vf)
    filled = grid.fill_depressions(grid.fill_pits(z))
    fdir = np.asarray(grid.flowdir(grid.resolve_flats(filled), routing="d8"))
    h, w = dem.shape
    rows, cols = np.indices(dem.shape)
    rr, cc = rows.copy(), cols.copy()
    for code, (dr, dc) in D8.items():
        sel = fdir == code
        rr[sel], cc[sel] = rows[sel] + dr, cols[sel] + dc
    inside = (rr >= 0) & (rr < h) & (cc >= 0) & (cc < w)
    rec = np.where(inside, rr * w + cc, rows * w + cols).ravel()
    rec[~valid.ravel()] = np.flatnonzero(~valid.ravel())  # no-data cells are outlets
    nxt_valid = valid.ravel()[rec]
    own = np.arange(rec.size)
    rec = np.where(nxt_valid, rec, own)  # flow into no-data leaves the grid
    return np.where(valid, np.asarray(filled, float), np.nan), rec


def accumulate(rec, weights):
    """Sum of `weights` over each cell and everything upstream of it along `rec`."""
    rec = np.asarray(rec)
    n = rec.size
    acc = np.asarray(weights, float).ravel().copy()
    own = np.arange(n)
    donors = np.bincount(rec[rec != own], minlength=n)
    order = []
    ready = np.flatnonzero(donors == 0)
    remaining = donors.copy()
    # Kahn's algorithm, vectorised one wavefront at a time.
    while ready.size:
        order.append(ready)
        nxt = rec[ready]
        moving = nxt != ready
        np.subtract.at(remaining, nxt[moving], 1)
        cand = np.unique(nxt[moving])
        ready = cand[remaining[cand] == 0]
    for front in order:
        moving = rec[front] != front
        np.add.at(acc, rec[front][moving], acc[front][moving])
    return acc


def first_hit(rec, label):
    """For every cell, the hollow its runoff reaches first (its own label inside a hollow), 0 if it
    leaves the grid without reaching one. Pointer jumping: O(log path length) passes."""
    label = np.asarray(label).ravel()
    own = np.arange(label.size)
    val = label.copy()
    done = (label > 0) | (rec == own)
    ptr = np.asarray(rec).copy()
    todo = np.flatnonzero(~done)
    for _ in range(64):  # 2**64 cells: only a loop in `rec` (unresolved flats) can exhaust this
        if not todo.size:
            break
        p = ptr[todo]
        hit = done[p]
        val[todo[hit]] = val[p[hit]]
        done[todo[hit]] = True
        rest = todo[~hit]
        ptr[rest] = ptr[ptr[rest]]
        todo = rest
    val[todo] = 0  # caught in a loop: treated as leaving the grid
    return val


@dataclass
class Hollows:
    shape: tuple
    cell_m2: float
    z: np.ndarray  # ground elevation (m)
    label: np.ndarray  # (H, W) int32, 1..n inside hollows, 0 elsewhere
    spill_level: np.ndarray  # (n+1,) m; index 0 unused
    capacity_m3: np.ndarray  # (n+1,)
    max_depth_m: np.ndarray  # (n+1,)
    area_m2: np.ndarray  # (n+1,)
    catchment_m2: np.ndarray  # (n+1,); [0] = area that leaves the grid without reaching a hollow
    downstream: np.ndarray  # (n+1,) int, hollow that receives the overflow, 0 = off the grid
    order: np.ndarray  # hollow labels, upstream first
    flow_area_m2: np.ndarray  # (H, W) area draining through each cell (as if every hollow were full)

    @property
    def n(self) -> int:
        return len(self.capacity_m3) - 1

    @property
    def brim_depth_m(self) -> np.ndarray:
        """(H, W) water depth with every hollow brim-full, NaN outside hollows."""
        lvl = self.spill_level[self.label]
        return np.where(self.label > 0, lvl - self.z, np.nan)


def find_hollows(dem, transform, crs, extra_area_m2=None, min_depth_m: float = 0.02,
                 min_volume_m3: float = 0.1) -> Hollows:
    """Hollows, their catchments and the overflow network of a DEM in a projected CRS (m)."""
    z = np.asarray(dem, float)
    cell = abs(transform.a * transform.e)
    filled, rec = condition(z, transform, crs)
    depth = np.where(np.isfinite(filled), filled - z, 0.0)
    regions, n0 = ndimage.label(depth > 1e-6, structure=np.ones((3, 3)))
    idx = np.arange(1, n0 + 1)
    vol = ndimage.sum_labels(depth, regions, idx) * cell
    deep = ndimage.maximum(depth, regions, idx) if n0 else np.array([])
    keep = (np.asarray(deep) >= min_depth_m) & (np.asarray(vol) >= min_volume_m3)
    relabel = np.zeros(n0 + 1, np.int32)
    relabel[1:][keep] = np.arange(1, keep.sum() + 1)
    label = relabel[regions]
    n = int(keep.sum())
    ids = np.arange(1, n + 1)

    def per(values, how):
        out = np.zeros(n + 1)
        if n:
            out[1:] = how(values, label, ids)
        return out

    spill = per(filled, ndimage.maximum)
    capacity = per(depth, ndimage.sum_labels) * cell
    max_depth = per(depth, ndimage.maximum)
    area = np.bincount(label.ravel(), minlength=n + 1) * cell
    area[0] = 0.0

    weights = np.full(z.size, cell)
    if extra_area_m2 is not None:
        weights += np.nan_to_num(np.asarray(extra_area_m2, float)).ravel()
    weights[~np.isfinite(z.ravel())] = 0.0
    hit = first_hit(rec, label)
    catchment = np.bincount(hit, weights=weights, minlength=n + 1)

    # Overflow: where a hollow's own cells drain out of it, and the hollow that water reaches next.
    lab = label.ravel()
    own = np.arange(lab.size)
    exits = np.flatnonzero((lab > 0) & (lab[rec] != lab))
    downstream = np.zeros(n + 1, np.int64)
    first_exit = np.full(n + 1, -1)
    first_exit[lab[exits][::-1]] = exits[::-1]  # one exit per hollow (the first in raster order)
    has = first_exit[1:] >= 0
    e = first_exit[1:][has]
    r = rec[e]
    downstream[1:][has] = np.where(r == e, 0, hit[r])
    # Hollows drain only downhill on the filled DEM, so this order puts every hollow before the
    # one it spills into (ties broken by the network below).
    order = _topological(downstream)

    flow = accumulate(rec, weights).reshape(z.shape)
    return Hollows(shape=z.shape, cell_m2=cell, z=z, label=label, spill_level=spill, capacity_m3=capacity,
                   max_depth_m=max_depth, area_m2=area, catchment_m2=catchment, downstream=downstream,
                   order=order, flow_area_m2=flow)


def _topological(downstream) -> np.ndarray:
    n = len(downstream) - 1
    indeg = np.bincount(downstream[1:], minlength=n + 1)
    indeg[0] = 0
    stack = [k for k in range(1, n + 1) if indeg[k] == 0]
    order = []
    while stack:
        k = stack.pop()
        order.append(k)
        d = downstream[k]
        if d:
            indeg[d] -= 1
            if indeg[d] == 0:
                stack.append(d)
    if len(order) != n:
        raise RuntimeError("hollow overflow network has a cycle")
    return np.array(order, np.int64)


def route(h: Hollows, runoff_m) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Fill-and-spill for one or more runoff depths (m over every cell).
    Returns inflow (n+1, S) and stored (n+1, S) per hollow in m3, and the volume that leaves the
    grid (S,) in m3."""
    runoff = np.atleast_1d(np.asarray(runoff_m, float))
    inflow = h.catchment_m2[:, None] * runoff[None, :]
    stored = np.zeros_like(inflow)
    out = inflow[0].copy()
    inflow[0] = 0.0
    for k in h.order:
        s = np.minimum(inflow[k], h.capacity_m3[k])
        stored[k] = s
        spill = inflow[k] - s
        d = h.downstream[k]
        if d:
            inflow[d] += spill
        else:
            out += spill
    return inflow, stored, out


def water_depth(h: Hollows, stored_m3) -> np.ndarray:
    """(H, W) standing water depth (m) for the stored volume of each hollow, 0 outside water."""
    stored_m3 = np.asarray(stored_m3, float)
    depth = np.zeros(h.shape)
    lab = h.label.ravel()
    cells = np.flatnonzero(lab > 0)
    if not cells.size:
        return depth
    zc = h.z.ravel()[cells]
    srt = np.lexsort((zc, lab[cells]))
    cells, zc, lc = cells[srt], zc[srt], lab[cells][srt]
    bounds = np.flatnonzero(np.diff(lc)) + 1
    flat = depth.ravel()
    for seg_cells, seg_z in zip(np.split(cells, bounds), np.split(zc, bounds)):
        k = lab[seg_cells[0]]
        v = stored_m3[k]
        if v <= 0:
            continue
        level = h.spill_level[k]
        if v < h.capacity_m3[k]:
            cum = np.cumsum(seg_z)
            m = np.arange(1, seg_z.size + 1)
            vol_at = h.cell_m2 * (m * seg_z - cum)  # volume with the level at each cell's ground
            j = int(np.searchsorted(vol_at, v, side="right"))  # cells under water
            level = min((v / h.cell_m2 + cum[j - 1]) / j, level)
        flat[seg_cells] = np.maximum(level - seg_z, 0.0)
    return depth


def fill_rain_mm(h: Hollows, runoff_fraction: float = 1.0, max_mm: float = 2000.0, steps: int = 240) -> np.ndarray:
    """(n+1,) rain (mm) that brings each hollow brim-full, counting overflow from hollows upstream;
    NaN where even max_mm does not."""
    rain = np.geomspace(0.1, max_mm, steps)
    inflow, _, _ = route(h, rain / 1000 * runoff_fraction)
    cap = h.capacity_m3[:, None]
    full = inflow >= cap
    out = np.full(h.n + 1, np.nan)
    for k in range(1, h.n + 1):
        j = int(np.argmax(full[k])) if full[k].any() else -1
        if j < 0:
            continue
        if j == 0:
            out[k] = rain[0] * h.capacity_m3[k] / max(inflow[k, 0], 1e-12)
        else:
            a, b = inflow[k, j - 1], inflow[k, j]
            out[k] = rain[j - 1] + (h.capacity_m3[k] - a) / max(b - a, 1e-12) * (rain[j] - rain[j - 1])
    return out
