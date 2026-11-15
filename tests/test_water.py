"""Where rain collects (src/dataanal/ponding.py, src/backend/water.py), without Earth Engine:
known bowls, every drop accounted for, overflow from one hollow into the next, inflow from
outside the 1 m grid, and the backend step's files."""
import json

import numpy as np
import pytest
from affine import Affine
from rasterio.crs import CRS
from rasterio.warp import transform as warp_transform

from src.backend import water
from src.dataanal import ponding
from src.dataanal import raster_io as rio
from src.ingest import terrain

CRS_UTM = CRS.from_epsg(32617)
T1 = Affine(1, 0, 378200, 0, -1, 3981800)  # 1 m grid


def bowls(shape=(60, 120), slope=0.01, centres=((30, 30, 1.0), (85, 30, 0.6)), radius=12):
    """A surface falling east with cone-shaped bowls (x, y, depth m)."""
    y, x = np.mgrid[0:shape[0], 0:shape[1]] + 0.5
    z = 100 - slope * x
    for cx, cy, d in centres:
        z -= d * np.clip(1 - np.hypot(x - cx, y - cy) / radius, 0, None)
    return z


@pytest.fixture(scope="module")
def two():
    return ponding.find_hollows(bowls(), T1, CRS_UTM)


def test_bowls_are_found_and_the_upper_one_spills_into_the_lower(two):
    h = two
    assert h.n == 2
    west = h.label[30, 30]
    east = h.label[30, 85]
    assert {west, east} == {1, 2}
    assert h.downstream[west] == east and h.downstream[east] == 0  # then off the grid
    assert list(h.order).index(west) < list(h.order).index(east)
    assert np.isclose(h.capacity_m3[west], np.nansum(h.brim_depth_m[h.label == west]))


def test_every_drop_is_accounted_for_and_depths_hold_the_stored_volume(two):
    h = two
    runoff = np.array([0.002, 0.02, 0.1])
    inflow, stored, off = ponding.route(h, runoff)
    assert np.allclose(stored.sum(0) + off, h.catchment_m2.sum() * runoff)
    assert np.allclose(h.catchment_m2.sum(), 60 * 120)
    assert (stored[1:] <= h.capacity_m3[1:, None] + 1e-9).all()
    for s in range(len(runoff)):
        assert np.isclose(ponding.water_depth(h, stored[:, s]).sum(), stored[:, s].sum())
    d = ponding.water_depth(h, h.capacity_m3)  # brim-full everywhere = the brim depth
    assert np.allclose(d[h.label > 0], h.brim_depth_m[h.label > 0])


def test_water_fills_from_the_bottom(two):
    h = two
    k = h.label[30, 30]
    stored = np.zeros(h.n + 1)
    stored[k] = 0.1 * h.capacity_m3[k]
    d = ponding.water_depth(h, stored)
    lowest = np.unravel_index(np.argmin(np.where(h.label == k, h.z, np.inf)), h.z.shape)
    assert d[lowest] == d.max() > 0
    assert (d[h.label != k] == 0).all()


def test_fill_rain_counts_overflow_from_upstream(two):
    h = two
    fill = ponding.fill_rain_mm(h)
    west, east = h.label[30, 30], h.label[30, 85]
    own = h.capacity_m3 / h.catchment_m2 * 1000
    assert fill[west] == pytest.approx(own[west], rel=1e-3)  # nothing upstream
    assert fill[east] <= own[east] + 1e-6  # helped by the west bowl's overflow once it is full
    inflow, stored, _ = ponding.route(h, np.array([fill[east] * 1.001, fill[east] * 0.98]) / 1000)
    assert stored[east, 0] == pytest.approx(h.capacity_m3[east]) and stored[east, 1] < h.capacity_m3[east]


def test_noise_hollows_are_ignored():
    z = bowls(centres=((30, 30, 1.0),))
    z[50, 100] -= 0.01  # a 1 cm pit
    h = ponding.find_hollows(z, T1, CRS_UTM)
    assert h.n == 1 and h.label[50, 100] == 0


def test_outside_inflow_enters_where_the_upslope_land_drains_in():
    cg = rio.Grid(CRS_UTM, Affine(10, 0, 378000, 0, -10, 3982000), 30, 30)
    cx = np.mgrid[0:30, 0:30][1] * 10.0
    context = rio.Raster(None, (500 - 0.05 * cx)[None].astype(np.float32), cg.transform, cg.crs, {}, ())
    fine = rio.Grid(CRS_UTM, Affine(1, 0, 378100, 0, -1, 3981900), 100, 100)  # context rows/cols 10..19
    fdem = 500 - 0.05 * (np.mgrid[0:100, 0:100][1] + 100.5)
    ext = water.outside_inflow(context, fdem, fine)
    assert ext.sum() == pytest.approx(10 * 10 * 100)  # the 10 x 10 context cells west of it, 100 m2 each
    assert (ext[:, 10:] == 0).all() and (ext[:, :10].sum(axis=1) > 0).sum() == 10  # one entry per row block
    off = rio.Grid(CRS_UTM, Affine(1, 0, 378100.5, 0, -1, 3981900), 100, 100)
    assert water.outside_inflow(context, fdem, off) is None  # not on the context lattice


def test_backend_step_writes_the_maps_and_is_reused(tmp_path):
    (tmp_path / "analysis").mkdir()
    tdir = tmp_path / "terrain"
    tdir.mkdir()
    z = bowls()
    grid = rio.Grid(CRS_UTM, T1, z.shape[1], z.shape[0])
    rio.write(tdir / terrain.DEM_FILE, z.astype(np.float32), grid, description="dem", units="m")
    (tdir / terrain.META_FILE).write_text(json.dumps({"created_utc": "t1", "lidar_share_field_pct": 100.0}))
    xs, ys = [378210, 378250, 378250, 378210], [3981790, 3981790, 3981750, 3981750]  # around the west bowl
    lon, lat = warp_transform(CRS_UTM, "EPSG:4326", xs, ys)
    (tmp_path / "analysis" / "plan.json").write_text(json.dumps({"path": "us", "polygon": [list(p) for p in zip(lon, lat)]}))
    out = tmp_path / "water"
    meta = water.ensure(tmp_path / "analysis", tdir, out, figures=False)
    for f in water.FILES + (water.META_FILE,):
        assert (out / f).exists(), f
    assert meta["outside_inflow"] is False and meta["hollows"]["touching_field"] == 1
    depth = rio.read(out / "water_depth.tif")
    assert depth.data.shape[0] == len(water.STORMS_MM)
    big = meta["storms"][-1]["field"]
    assert big["hollows_full"] == 1 and big["water_m3"] == pytest.approx(meta["hollows"]["capacity_touching_field_m3"], rel=1e-3)
    rows = json.loads((out / "hollows.json").read_text())
    assert rows[0]["in_field_pct"] == 100 and rows[0]["fill_rain_mm"] > 0
    assert water.ensure(tmp_path / "analysis", tdir, out, figures=False) == meta  # current: reused
    (tdir / terrain.META_FILE).write_text(json.dumps({"created_utc": "t2"}))  # new terrain: redone
    assert water.ensure(tmp_path / "analysis", tdir, out, figures=False)["terrain_created_utc"] == "t2"


def test_no_terrain_no_water(tmp_path):
    assert water.ensure(tmp_path, tmp_path / "terrain", tmp_path / "water") is None
