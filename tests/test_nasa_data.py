"""NASA data layers without Earth Engine: cache files, band names, role tags, the HLS analysis
input, the wind maths, POWER parsing and the skip-on-failure behaviour. Network calls are
replaced by synthetic arrays."""
import numpy as np
import pytest
from affine import Affine
from rasterio.crs import CRS

from src.dataanal import raster_io as rio
from src.ingest import cache, nasa_data as nd

GRID = rio.Grid(CRS.from_epsg(32637), Affine(30.0, 0, 300000, 0, -30.0, 1220300), 10, 8)


@pytest.fixture
def fake_ee(monkeypatch):
    """Earth Engine replaced: image builders return a token, fetch returns one synthetic band
    per name the layer will label itself with. Returns the list of fetches made."""
    calls = []
    monkeypatch.setattr(nd.ee_io, "init", lambda: None)
    for name in ("_smap_image", "_fire_image", "_wind_image", "_snow_image"):
        monkeypatch.setattr(nd, name, lambda *a, _n=name, **k: _n)
    monkeypatch.setattr(nd, "_hls_image", lambda geom, pieces: ("_hls_image", len(pieces)))  # one band per piece
    monkeypatch.setattr(nd.ee_io, "region", lambda grid, margin=0.0: "region")

    def fetch(image, grid, **_):
        calls.append(image)
        bands = (image[1] if isinstance(image, tuple)
                 else {"_smap_image": 3, "_fire_image": 4, "_wind_image": 4, "_snow_image": 5}[image])
        return np.random.default_rng(0).random((bands, grid.height, grid.width)).astype(np.float32)

    monkeypatch.setattr(nd.ee_io, "fetch", fetch)
    return calls


# ----------------------------------------------------------------------------- Earth Engine layers


@pytest.mark.parametrize("pull, folder, role, bands", [
    (lambda c: nd.smap_soil_moisture(GRID, "2024-06-01", "2024-06-30", c), "smap", "soil_moisture", 3),
    (lambda c: nd.fire_burn_history(GRID, [2018, 2024], c), "fire", "fire_history", 4),
    (lambda c: nd.hls_ndvi_series(GRID, "2024-06-01", "2024-06-30", c), "hls_ndvi", "hls_ndvi", 2),
    (lambda c: nd.merra2_wind(GRID, "2024-06-01", "2024-06-30", c), "merra2_wind", "wind_climatic", 4),
    (lambda c: nd.merra2_snow(GRID, "2024-01-01", "2024-03-01", c), "merra2_snow", "snow_melt", 5),
])
def test_ee_layer_is_written_on_the_grid_with_role_and_bands(fake_ee, tmp_path, pull, folder, role, bands):
    path = pull(tmp_path)
    assert path.parent == tmp_path / folder and cache.grid_key(GRID) in path.name
    r = rio.read(path)
    assert r.data.shape == (bands, GRID.height, GRID.width) and rio.grid_of(r).same_as(GRID)
    assert r.tags["RUSLE_ROLE"] == role and r.tags["SOURCE"] and r.tags["UNITS"] and r.tags["FETCHED_UTC"]
    assert all(r.descriptions) and len(r.descriptions) == bands


def test_cached_layer_is_not_pulled_again(fake_ee, tmp_path):
    a = nd.merra2_wind(GRID, "2024-06-01", "2024-06-30", tmp_path)
    b = nd.merra2_wind(GRID, "2024-06-01", "2024-06-30", tmp_path)
    assert a == b and len(fake_ee) == 1
    nd.merra2_wind(GRID, "2024-07-01", "2024-07-30", tmp_path)  # other dates: a different file
    assert len(fake_ee) == 2


def test_failed_pull_leaves_nothing_behind(monkeypatch, tmp_path):
    monkeypatch.setattr(nd.ee_io, "init", lambda: None)
    monkeypatch.setattr(nd, "_smap_image", lambda *a: None)

    def down(*_, **__):
        raise RuntimeError("503 unavailable")

    monkeypatch.setattr(nd.ee_io, "fetch", down)
    with pytest.raises(RuntimeError):
        nd.smap_soil_moisture(GRID, "2024-06-01", "2024-06-30", tmp_path)
    assert not [p for p in tmp_path.rglob("*") if p.is_file()]


def test_hls_series_has_one_band_per_half_month(fake_ee, tmp_path):
    r = rio.read(nd.hls_ndvi_series(GRID, "2024-06-01", "2024-07-31", tmp_path))
    assert r.descriptions == ("ndvi_2024-06-01", "ndvi_2024-06-16", "ndvi_2024-07-01", "ndvi_2024-07-16")


def test_hls_input_joins_cached_years_into_one_series(fake_ee, tmp_path):
    out = tmp_path / "inputs"
    out.mkdir()
    r = rio.read(nd.hls_ndvi_input(GRID, [2023, 2024], tmp_path, out))
    assert r.path == out / "ndvi_series.tif" and r.data.shape == (48, GRID.height, GRID.width)
    assert r.descriptions[0] == "ndvi_2023-01-01" and r.descriptions[24] == "ndvi_2024-01-01"
    assert r.descriptions[-1] == "ndvi_2024-12-16" and r.tags["RUSLE_ROLE"] == "ndvi_series"
    assert rio.role_of(r) == "ndvi_series" and len(fake_ee) == 2  # one pull per year
    nd.hls_ndvi_input(GRID, [2023, 2024, 2025], tmp_path, out)
    assert len(fake_ee) == 3  # a longer period pulls only the new year


def test_hls_range_over_the_band_limit_is_refused(tmp_path):
    with pytest.raises(ValueError, match="exceeds"):
        nd.hls_ndvi_series(GRID, "1980-01-01", "2024-01-01", tmp_path)


def test_end_must_follow_start(tmp_path):
    with pytest.raises(ValueError, match="after start"):
        nd.merra2_snow(GRID, "2024-06-30", "2024-06-01", tmp_path)


# ----------------------------------------------------------------------------- wind


def test_wind_speed_is_the_vector_length():
    assert nd.wind_speed(3.0, 4.0) == 5.0


def test_wind_stats_count_only_hours_over_the_threshold():
    s = nd.wind_stats([2.0, 4.0, 8.0, 10.0], threshold=6.0)
    assert s["mean"] == 6.0 and s["max"] == 10.0 and s["frac_above"] == 0.5
    assert s["erosivity"] == pytest.approx((8**3 + 10**3) / 4)
    assert nd.wind_stats([1.0, 2.0])["erosivity"] == 0.0


def test_power_wind_is_parsed_cleaned_and_cached(monkeypatch, tmp_path):
    import requests

    calls = []

    class Reply:
        def raise_for_status(self): ...

        def json(self):
            return {"properties": {"parameter": {"WS10M": {"20240601": 3.2, "20240602": -999},
                                                 "WS10M_MAX": {"20240601": 7.5, "20240602": 6.0}}}}

    def get(url, params, timeout):
        calls.append(params)
        return Reply()

    monkeypatch.setattr(requests, "get", get)
    out = nd.power_wind_daily(37.2, 11.05, "2024-06-01", "2024-06-03", tmp_path)
    assert out["dates"] == ["20240601", "20240602"] and out["WS10M"] == [3.2, None]
    assert calls[0]["end"] == "20240602" and calls[0]["start"] == "20240601"  # end day is exclusive
    assert nd.power_wind_daily(37.2, 11.05, "2024-06-01", "2024-06-03", tmp_path) == out and len(calls) == 1


# ----------------------------------------------------------------------------- everything at once


def test_pull_all_skips_what_fails_and_keeps_the_rest(fake_ee, monkeypatch, tmp_path):
    def down(*_, **__):
        raise RuntimeError("503 unavailable")

    monkeypatch.setattr(nd, "_snow_image", down)
    paths, skipped = nd.pull_all(GRID, "2024-06-01", "2024-07-01", tmp_path)
    assert skipped == ["snow"]
    assert sorted(paths) == ["fire", "hls_ndvi", "smap", "wind"]
    assert all(p.exists() for p in paths.values())
