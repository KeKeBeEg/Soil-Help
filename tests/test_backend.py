"""Backend tests without Earth Engine: request parsing, path choice, grids, the views step
on a synthetic analysis (from saved files only; dates outside the record use a fake IMERG
source), and the fallback when ISRIC is unreachable."""
import csv
import json

import numpy as np
import pytest
import rasterio
from rasterio.errors import RasterioIOError
from rasterio.warp import transform as warp_transform

from src.backend import plan as planner, region, request, run as backend_run, views
from src.dataanal import pipeline, raster_io as rio
from src.dataanal_harness import synthetic
from src.ingest import collect, global_data


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


YEARS = (2010, 2011)


@pytest.fixture(scope="module")
def analysis(tmp_path_factory):
    """A synthetic analysis as run.py leaves it: pipeline outputs, plan.json, yearly files."""
    tmp = tmp_path_factory.mktemp("views")
    paths = synthetic.make_inputs(tmp / "inputs", years=list(YEARS), size_px=120, extras=True)
    out = tmp / "analysis"
    pipeline.run(paths, out, pipeline.Params(figures=False, crop_to_region=True))
    precip = rio.read(next(p for p in paths if p.name.startswith("precip")))
    cx, cy = synthetic.ORIGIN[0] + 1800, synthetic.ORIGIN[1] - 1500
    xs, ys = [cx - 150, cx + 150, cx + 150, cx - 150], [cy + 120, cy + 120, cy - 120, cy - 120]
    lon, lat = warp_transform(synthetic.UTM, "EPSG:4326", xs, ys)
    stamp = {**planner.request_key(list(zip(lon, lat)), "us"), "climate_years": list(YEARS),
             "rain_grid": rio.grid_of(precip).as_dict(), "defaults_used": []}
    (out / "plan.json").write_text(json.dumps(stamp), encoding="utf-8")
    views.precompute(out)
    return tmp


def _dates(tmp, start, end):
    return request.Request(polygon=[], t_start=np.datetime64(start), t_end=np.datetime64(end), us_cache=tmp,
                           global_cache=tmp, working_save=tmp, label="Synthetic field")


def _no_imerg(*_, **__):
    raise AssertionError("dates inside the analysis period must not read the rain cache")


def _maps(folder):
    return {name: rio.read(folder / f"{name}.tif").data[0] for name in views.RANGE_LAYERS}


def test_views_render_field_outputs_from_saved_files(analysis, monkeypatch):
    tmp = analysis
    monkeypatch.setattr(views.shared, "imerg_series", _no_imerg)
    s = views.render(_dates(tmp, "2010-07-01", "2010-08-15"), tmp / "analysis", tmp / "view")

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


def test_yearly_layers_combine_exactly_into_any_run_of_years(analysis, monkeypatch):
    tmp = analysis
    monkeypatch.setattr(views.shared, "imerg_series", _no_imerg)
    s = views.render(_dates(tmp, "2010-01-01", "2011-12-31"), tmp / "analysis", tmp / "both")
    whole = _maps(tmp / "both")
    for name, (how, *_) in views.RANGE_LAYERS.items():
        with rasterio.open(tmp / "analysis" / f"{name}_by_year.tif") as src:
            assert src.tags()["COMBINE"] == how and src.descriptions == ("2010", "2011")
        yearly = rio.read(tmp / "analysis" / f"{name}_by_year.tif").data
        combined = yearly.sum(axis=0) if how == "sum" else np.fmax(yearly[0], yearly[1])
        ok = np.isfinite(whole[name])
        assert ok.any() and np.allclose(combined[ok], whole[name][ok], rtol=1e-6, atol=0), name
    assert np.nanmax(whole["soil_loss_window"]) > 0  # storms in the record, so the sum check means something

    # the daily series gives the same field numbers as the view
    with open(tmp / "analysis" / "daily_series.csv", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["date"] == "2010-01-01" and rows[-1]["date"] == "2011-12-31"
    assert sum(float(r["rain_mm"]) for r in rows) == pytest.approx(s["window"]["rain_total_mm"], rel=1e-5)
    assert sum(float(r["erosive_storms"]) for r in rows) == s["window"]["erosive_storms"]


def test_splitting_the_dates_never_cuts_a_storm(analysis, monkeypatch):
    """A storm from 20:00 to 04:00 counts once, wholly on its first day, also outside the record."""
    tmp = analysis
    rain = {np.datetime64("2012-07-10T20:00") + np.timedelta64(30 * i, "m") for i in range(16)}

    def fake_imerg(grid, start, end, cache_dir):
        times = np.arange(np.datetime64(start, "D"), np.datetime64(end, "D"), np.timedelta64(30, "m")).astype("datetime64[m]")
        series = np.array([20.0 if t in rain else 0.0 for t in times], np.float32)
        return times, np.broadcast_to(series[:, None, None], (times.size, grid.height, grid.width)).copy()

    monkeypatch.setattr(views.shared, "imerg_series", fake_imerg)
    parts = []
    for i, (a, b) in enumerate([("2012-07-01", "2012-07-10"), ("2012-07-11", "2012-07-20"), ("2012-07-01", "2012-07-20")]):
        views.render(_dates(tmp, a, b), tmp / "analysis", tmp / f"split{i}")
        parts.append(_maps(tmp / f"split{i}"))
    first, second, whole = parts
    ok = np.isfinite(whole["soil_loss_window"])
    assert np.nanmax(whole["soil_loss_window"]) > 0 and np.nanmax(second["soil_loss_window"]) == 0
    assert np.allclose(first["soil_loss_window"][ok] + second["soil_loss_window"][ok], whole["soil_loss_window"][ok])
    for name in ("landslide_peak_class", "landslide_peak_probability", "rain_window_max"):
        assert np.array_equal(np.fmax(first[name], second[name])[ok], whole[name][ok]), name


# ----------------------------------------------------------------------------- missing optional data


def test_failed_isric_pull_is_not_cached(monkeypatch, tmp_path):
    def unreachable(*_, **__):
        raise RasterioIOError("HTTP response code: 504")

    monkeypatch.setattr(global_data.rasterio, "open", unreachable)
    grid = planner.snapped_grid(region.utm_crs(37.2, 11.0), (300000, 1220000, 300300, 1220300), 30.0)
    defaults = []
    assert collect._optional(lambda: global_data.soil_depth(grid, tmp_path), "Depth to bedrock", defaults) is None
    assert defaults == ["Depth to bedrock"]
    assert not list(tmp_path.rglob("*.tif"))  # nothing written: the next pull tries ISRIC again


def test_analysis_that_used_a_default_is_redone(tmp_path):
    key = planner.request_key([(37.2, 11.0), (37.21, 11.0), (37.21, 11.01)], "global")
    for name in ("summary.json", backend_run.LAST_FILE, "soil_loss.tif"):
        (tmp_path / name).write_text("{}", encoding="utf-8")
    stamp = tmp_path / "plan.json"
    stamp.write_text(json.dumps({**key, "defaults_used": []}), encoding="utf-8")
    assert backend_run.analysis_is_current(tmp_path, key)
    assert not backend_run.analysis_is_current(tmp_path, {**key, "version": key["version"] - 1})
    stamp.write_text(json.dumps({**key, "defaults_used": ["Depth to bedrock"]}), encoding="utf-8")
    assert not backend_run.analysis_is_current(tmp_path, key)
