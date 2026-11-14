"""Backend tests without Earth Engine: request parsing, path choice, grids, and the views
step on a synthetic analysis (rain comes from a fake IMERG source)."""
import json

import numpy as np
import pytest
import rasterio
from rasterio.crs import CRS

from src.backend import plan as planner, region, request, views
from src.dataanal import pipeline, raster_io as rio
from src.dataanal_harness import synthetic


def _req(**over):
    data = {"polygon": {"1": [-98.0, 38.5], "2": [-97.99, 38.5], "3": [-97.99, 38.51]},
            "t_range": ["2024-06-01", "2024-06-30"], "us_cache": "c/us", "global_cache": "c/gl", "working_save": "w"}
    data.update(over)
    return data


# ----------------------------------------------------------------------------- request


def test_vertices_are_ordered_numerically_and_ring_is_opened(tmp_path):
    poly = {str(i): [10.0 + i * 0.001, 5.0 + (i % 2) * 0.001] for i in range(1, 12)}
    poly["12"] = poly["1"]  # explicitly closed ring
    shuffled = dict(reversed(list(poly.items())))
    req = request.parse(_req(polygon=shuffled), tmp_path)
    assert [round(x, 3) for x, _ in req.polygon] == [round(10.0 + i * 0.001, 3) for i in range(1, 12)]
    assert req.working_save == (tmp_path / "w").resolve()
    assert req.label == "w"


def test_tuple_strings_and_lists_are_accepted(tmp_path):
    req = request.parse(_req(polygon={"1": "(-98.0, 38.5)", "2": "(-97.99, 38.5)", "3": "(-97.99, 38.51)"}), tmp_path)
    assert req.polygon[0] == (-98.0, 38.5)
    req = request.parse(_req(polygon=[[-98.0, 38.5], [-97.99, 38.5], [-97.99, 38.51]]), tmp_path)
    assert len(req.polygon) == 3


@pytest.mark.parametrize("over, match", [
    ({"t_range": ["2024-06-30", "2024-06-01"]}, "before it starts"),
    ({"t_range": ["2024-06-01", "2999-01-01"]}, "must lie within"),
    ({"polygon": {"1": [0, 0], "2": [1, 1]}}, "at least 3"),
    ({"polygon": {"1": [200, 0], "2": [1, 1], "3": [1, 2]}}, "longitude, latitude"),
    ({"t_range": ["June", "July"]}, "YYYY-MM-DD"),
])
def test_bad_requests_say_why(tmp_path, over, match):
    with pytest.raises(ValueError, match=match):
        request.parse(_req(**over), tmp_path)


def test_load_resolves_paths_against_the_request_file(tmp_path):
    f = tmp_path / "sub" / "req.json"
    f.parent.mkdir()
    f.write_text(json.dumps(_req(working_save="../runs/x")), encoding="utf-8")
    req = request.load(f)
    assert req.working_save == (tmp_path / "runs" / "x").resolve()
    assert req.source == f.resolve()


# ----------------------------------------------------------------------------- region and grids


@pytest.mark.parametrize("lonlat, path", [
    ((-98.0, 38.5), "us"), ((-82.34, 35.97), "us"), ((-122.4, 37.77), "us"),  # Kansas, N Carolina, San Francisco
    ((-79.38, 43.65), "global"), ((-157.85, 21.30), "global"), ((-149.9, 61.2), "global"),  # Toronto, Hawaii, Alaska
    ((37.2, 11.05), "global"), ((-99.13, 19.43), "global"),  # Ethiopia, Mexico City
])
def test_data_path(lonlat, path):
    x, y = lonlat
    assert region.data_path([(x, y), (x + 0.001, y), (x + 0.001, y + 0.001)]) == path


def test_border_crossing_field_goes_global():
    # straddles the US-Canada border on the 49th parallel
    assert region.data_path([(-97.2, 48.99), (-97.19, 48.99), (-97.19, 49.01), (-97.2, 49.01)]) == "global"


def test_utm_zone():
    assert region.utm_crs(-82.3, 35.9).to_epsg() == 32617
    assert region.utm_crs(37.2, 11.0).to_epsg() == 32637
    assert region.utm_crs(150.0, -33.9).to_epsg() == 32756


def test_rain_grid_is_on_the_imerg_lattice_with_a_margin():
    g = planner.rain_grid((-82.3438, 35.9686, -82.3417, 35.9700))
    assert (g.width, g.height) == (3, 3)
    assert g.transform.c == pytest.approx(-82.5) and g.transform.f == pytest.approx(36.1)


def test_plan_grids_nest_and_snap(monkeypatch, tmp_path):
    monkeypatch.setattr(planner.shared, "basins", lambda poly, cache: {
        "ids": [1], "bounds_lonlat": [-82.40, 35.93, -82.30, 36.02]})
    req = request.parse(_req(polygon={"1": [-82.3437, 35.9699], "2": [-82.3420, 35.9700], "3": [-82.3417, 35.9690],
                                      "4": [-82.3426, 35.9686]}), tmp_path)
    p = planner.make(req, "us")
    assert p.res == 10 and p.crs.to_epsg() == 32617
    dg, fg = p.dem_grid, p.field_grid
    off = ~dg.transform @ (fg.transform.c, fg.transform.f)
    assert np.allclose(off, np.round(off))  # same lattice
    assert off[0] >= 0 and off[1] >= 0 and off[0] + fg.width <= dg.width and off[1] + fg.height <= dg.height
    assert dg.width * 10 <= 2 * 5000 + 300  # basin clipped to 5 km around the field
    assert p.fingerprint() == planner.make(req, "us").fingerprint()


# ----------------------------------------------------------------------------- views on a synthetic analysis


@pytest.fixture(scope="module")
def analysis(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("views")
    paths = synthetic.make_inputs(tmp / "inputs", years=[2010], size_px=120, extras=True)
    pipeline.run(paths, tmp / "analysis", pipeline.Params(figures=False, crop_to_region=True))
    precip = rio.read(next(p for p in paths if p.name.startswith("precip")))
    return tmp, rio.grid_of(precip)


def test_views_render_field_outputs(analysis, monkeypatch):
    tmp, rain_grid = analysis
    rng = np.random.default_rng(1)

    def fake_imerg(grid, start, end, cache_dir):
        times = np.arange(np.datetime64(start, "D"), np.datetime64(end, "D"), np.timedelta64(30, "m")).astype("datetime64[m]")
        series = synthetic._storm_series(times, rng) * 3  # a wet spell
        return times, np.broadcast_to(series[:, None, None], (times.size, grid.height, grid.width)).astype(np.float32)

    monkeypatch.setattr(views.shared, "imerg_series", fake_imerg)
    cx, cy = synthetic.ORIGIN[0] + 1800, synthetic.ORIGIN[1] - 1500
    xy = np.array([[cx - 150, cy + 120], [cx + 150, cy + 120], [cx + 150, cy - 120], [cx - 150, cy - 120]])
    plan = planner.Plan(path="us", crs=CRS.from_epsg(32637), res=30.0, field_lonlat=[(37.0, 11.3)] * 4, field_xy=xy,
                        dem_grid=None, field_grid=None, rain_grid=rain_grid, climate_years=(2010, 2010), basins={})
    req = request.Request(polygon=[(37.0, 11.3)] * 4, t_start=np.datetime64("2010-07-01"),
                          t_end=np.datetime64("2010-08-15"), us_cache=tmp, global_cache=tmp, working_save=tmp,
                          label="Synthetic field")
    s = views.render(req, plan, tmp / "analysis", tmp / "view")

    view = tmp / "view"
    for name in ["soil_loss", "soil_loss_window", "landslide_peak_class", "critical_rain", "erodibility_index"]:
        with rasterio.open(view / f"{name}.tif") as src:
            data = src.read(1, masked=True)
            assert src.width <= 13 and data.count() > 0, name  # the field's box, not the whole area
    assert (view / "timeline.png").exists() and (view / "field_maps.png").exists()
    assert s["field"]["area_unit"] == "acres" and "highly_erodible_land_screening" in s
    assert s["window"]["soil_loss"] >= 0 and s["window"]["rain_total_mm"] > 0
    assert s["window"]["worst_alert"] in ("low", "watch", "warning", "danger")
    assert sum(s["landslide"]["stability_share_pct"].values()) == pytest.approx(100)
    assert s["advice"] and json.loads((view / "field_summary.json").read_text())["t_range"] == ["2010-07-01", "2010-08-15"]
