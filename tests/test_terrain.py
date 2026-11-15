"""Fine terrain (US path) without Earth Engine: the 1 m grid nests on the 10 m field grid,
lidar cells keep their elevation, gaps take the ~10 m DEM, a current terrain is reused, and a
failed 1 m pull falls back everywhere and is retried on the next run."""
import json

import numpy as np
import pytest
from rasterio.crs import CRS
from rasterio.warp import transform as warp_transform

from src.backend import plan as planner
from src.dataanal import raster_io as rio
from src.ingest import terrain

CRS_UTM = CRS.from_epsg(32617)
DEM_GRID = planner.snapped_grid(CRS_UTM, (378000, 3981000, 379500, 3982500), 10.0)
FIELD_GRID = planner.snapped_grid(CRS_UTM, (378300, 3981300, 378900, 3981900), 10.0)


def _plane(grid):
    """z = 500 + 0.02 x + 0.01 y (m) at the cell centres: bilinear resampling keeps it exact."""
    t = grid.transform
    x = t.c + (np.arange(grid.width) + 0.5) * t.a - 378000
    y = t.f + (np.arange(grid.height) + 0.5) * t.e - 3981000
    return (500 + 0.02 * x[None, :] + 0.01 * y[:, None]).astype(np.float32)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    """A US analysis stamp plus fake 3DEP pulls; lidar covers the western half of the fine grid."""
    xs, ys = [378500, 378700, 378700, 378500], [3981700, 3981700, 3981500, 3981500]
    lon, lat = warp_transform(CRS_UTM, "EPSG:4326", xs, ys)
    analysis = tmp_path / "analysis"
    analysis.mkdir()
    (analysis / "plan.json").write_text(json.dumps({
        "path": "us", "polygon": [list(p) for p in zip(lon, lat)],
        "dem_grid": DEM_GRID.as_dict(), "field_grid": FIELD_GRID.as_dict()}), encoding="utf-8")
    calls = {"lidar": 0, "fail": False}

    def fake_dem(grid, cache_dir):
        return rio.write(tmp_path / "dem10.tif", _plane(grid), grid, description="fake 3DEP", units="m")

    def fake_lidar(grid, cache_dir):
        calls["lidar"] += 1
        if calls["fail"]:
            raise ConnectionError("Earth Engine unreachable")
        z = _plane(grid) + 0.5
        z[:, grid.width // 2:] = np.nan
        return rio.write(tmp_path / "dem1.tif", z, grid, description="fake 3DEP 1 m", units="m")

    monkeypatch.setattr(terrain.us_data, "dem", fake_dem)
    monkeypatch.setattr(terrain.us_data, "dem_lidar", fake_lidar)
    return analysis, tmp_path / "terrain", calls


def test_fine_grid_nests_on_the_field_grid(monkeypatch):
    g = terrain.fine_grid(FIELD_GRID)
    assert g.xres == 1 and (g.width, g.height) == (FIELD_GRID.width * 10, FIELD_GRID.height * 10)
    assert (g.transform.c, g.transform.f) == (FIELD_GRID.transform.c, FIELD_GRID.transform.f)
    monkeypatch.setattr(terrain, "MAX_CELLS", FIELD_GRID.width * FIELD_GRID.height * 25)
    assert terrain.fine_grid(FIELD_GRID).xres == 2  # too big at 1 m
    monkeypatch.setattr(terrain, "MAX_CELLS", FIELD_GRID.width * FIELD_GRID.height)
    assert terrain.fine_grid(FIELD_GRID) is None  # too big even at 5 m


def test_merge_prefers_lidar():
    lidar = np.array([[1.0, np.nan, np.nan]])
    fallback = np.array([[9.0, 2.0, np.nan]])
    z, src = terrain.merge(lidar, fallback)
    assert np.array_equal(z[0, :2], [1.0, 2.0]) and np.isnan(z[0, 2])
    assert src.tolist() == [[terrain.SOURCE_LIDAR, terrain.SOURCE_FALLBACK, terrain.SOURCE_NONE]]


def test_lidar_gaps_take_the_10m_dem_and_a_current_terrain_is_reused(setup):
    analysis, out, calls = setup
    meta = terrain.ensure(analysis, out, "cache")
    z, src = rio.read(out / terrain.DEM_FILE), rio.read(out / terrain.SOURCE_FILE).data[0]
    g = rio.grid_of(z)
    assert g.xres == 1 and g.same_as(terrain.fine_grid(FIELD_GRID))
    half = g.width // 2
    assert (src[:, :half] == terrain.SOURCE_LIDAR).all() and (src[:, half:] == terrain.SOURCE_FALLBACK).all()
    expected = _plane(g)
    assert np.allclose(z.data[0][:, :half], expected[:, :half] + 0.5, atol=1e-3)  # lidar kept as pulled
    assert np.allclose(z.data[0][:, half:], expected[:, half:], atol=1e-2)  # 10 m resampled to 1 m
    assert meta["lidar_share_field_pct"] == 50 and meta["lidar_pull_failed"] is None
    assert terrain.ensure(analysis, out, "cache") == meta and calls["lidar"] == 1  # reused, no pull
    terrain.ensure(analysis, out, "cache", refresh=True)
    assert calls["lidar"] == 2


def test_failed_lidar_pull_falls_back_everywhere_and_is_retried(setup):
    analysis, out, calls = setup
    calls["fail"] = True
    meta = terrain.ensure(analysis, out, "cache")
    assert meta["lidar_pull_failed"] and meta["lidar_share_grid_pct"] == 0
    assert (rio.read(out / terrain.SOURCE_FILE).data[0] == terrain.SOURCE_FALLBACK).all()
    calls["fail"] = False
    meta = terrain.ensure(analysis, out, "cache")  # retried, now with lidar
    assert calls["lidar"] == 2 and meta["lidar_pull_failed"] is None and meta["lidar_share_field_pct"] == 50


def test_no_earth_engine_login_skips_the_terrain_without_failing(setup, monkeypatch):
    analysis, out, calls = setup

    def no_login(*_, **__):
        raise RuntimeError("Please authorize access to your Earth Engine account by running")

    monkeypatch.setattr(terrain.us_data, "dem", no_login)
    monkeypatch.setattr(terrain.us_data, "dem_lidar", no_login)
    assert terrain.ensure(analysis, out, "cache") is None
    assert not out.exists()  # nothing written: the next run tries again


def test_the_runs_own_input_dem_is_used_before_the_cache(setup, monkeypatch):
    analysis, out, calls = setup
    inputs = analysis.parent / "inputs"
    inputs.mkdir()
    rio.write(inputs / "dem.tif", _plane(DEM_GRID) + 7, DEM_GRID, description="saved input", units="m")

    def not_needed(*_, **__):
        raise AssertionError("the saved input DEM should have been used")

    monkeypatch.setattr(terrain.us_data, "dem", not_needed)
    terrain.ensure(analysis, out, "cache")
    z, src = rio.read(out / terrain.DEM_FILE).data[0], rio.read(out / terrain.SOURCE_FILE).data[0]
    fb = src == terrain.SOURCE_FALLBACK
    assert np.allclose(z[fb], (_plane(rio.grid_of(rio.read(out / terrain.DEM_FILE))) + 7)[fb], atol=1e-2)


def test_global_path_has_no_fine_terrain(setup):
    analysis, out, calls = setup
    stamp = json.loads((analysis / "plan.json").read_text(encoding="utf-8"))
    (analysis / "plan.json").write_text(json.dumps({**stamp, "path": "global"}), encoding="utf-8")
    assert terrain.ensure(analysis, out, "cache") is None and not out.exists() and calls["lidar"] == 0
