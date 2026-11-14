"""End-to-end tests of the GeoTIFF pipeline on synthetic inputs (no Earth Engine)."""
import numpy as np
import pytest
import rasterio
from affine import Affine
from rasterio.crs import CRS

from src.dataanal import landslide as ls, pipeline, raster_io as rio
from src.dataanal_harness import synthetic


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    """Minimum inputs: soil_type only, no texture, depth or land cover."""
    tmp = tmp_path_factory.mktemp("rusle")
    paths = synthetic.make_inputs(tmp / "inputs", years=[2010], size_px=160)
    summary = pipeline.run(paths, tmp / "out", pipeline.Params(figures=False))
    return paths, tmp / "out", summary


@pytest.fixture(scope="module")
def run_extras(tmp_path_factory):
    """Optional soil_texture, soil_depth and landcover added; outputs cropped to the study area."""
    tmp = tmp_path_factory.mktemp("rusle_extras")
    paths = synthetic.make_inputs(tmp / "inputs", years=[2010], size_px=160, extras=True)
    summary = pipeline.run(paths, tmp / "out", pipeline.Params(figures=True, crop_to_region=True))
    return paths, tmp / "out", summary


def _read(path):
    with rasterio.open(path) as src:
        return src.read(masked=True).astype(float).filled(np.nan), src.transform, src.crs, src.tags()


def test_every_output_is_written_on_the_dem_grid(run):
    paths, out, _ = run
    _, dem_transform, dem_crs, _ = _read(next(p for p in paths if p.name == "dem.tif"))
    for name in pipeline.OUTPUTS:
        _, transform, crs, tags = _read(out / f"{name}.tif")
        assert transform.almost_equals(dem_transform) and crs == dem_crs, name
        assert tags["UNITS"] == pipeline.OUTPUTS[name][1]


def test_soil_loss_is_product_of_factors_and_sum_of_months(run):
    _, out, _ = run
    a = _read(out / "soil_loss.tif")[0][0]
    factors = [_read(out / f"{f}_factor.tif")[0][0] for f in ("r", "k", "ls", "c", "p")]
    monthly = _read(out / "soil_loss_monthly.tif")[0]
    ok = np.isfinite(a)
    assert ok.sum() > 1000
    assert np.allclose(a[ok], np.prod(factors, axis=0)[ok], rtol=1e-4)
    assert np.allclose(a[ok], monthly.sum(axis=0)[ok], rtol=1e-3, atol=1e-4)


def test_farmer_outputs_are_consistent(run):
    _, out, summary = run
    a = _read(out / "soil_loss.tif")[0][0]
    with_practice = _read(out / "soil_loss_with_practice.tif")[0][0]
    practice = _read(out / "recommended_practice.tif")[0][0]
    ok = np.isfinite(a)
    assert set(np.unique(practice[ok])) <= {0, 1, 2, 3, 4}
    assert np.all(with_practice[ok] <= a[ok] + 1e-6)
    assert np.all(with_practice[ok & (practice >= 1) & (practice <= 3)] <= 11.0 + 1e-6)
    assert sum(summary["recommended_practice_area_pct"].values()) == pytest.approx(100)
    # water and outside-AOI pixels are excluded
    assert np.isnan(a).sum() > 0


def test_rainy_season_dominates_erosivity(run):
    _, _, summary = run
    share = summary["monthly_soil_loss_share_pct"]
    assert share["Jun"] + share["Jul"] + share["Aug"] + share["Sep"] > 80


def test_landslide_outputs_are_consistent(run):
    _, out, summary = run
    q = _read(out / "landslide_rain_quantiles.tif")[0]
    crit = _read(out / "critical_rain.tif")[0][0]
    stab = _read(out / "stability_class.tif")[0][0]
    ok = np.isfinite(crit)
    assert q.shape[0] == 9 and np.all(q[1:][:, ok] >= q[:-1][:, ok])
    assert np.all(stab[ok & (crit == ls.NEVER_MM_DAY)] == 1)
    assert np.all(stab[ok & (crit == 0)] == 3)
    lsum = summary["landslide"]
    assert sum(lsum["stability_class_area_pct"].values()) == pytest.approx(100)
    assert lsum["texture_class_area_pct"]  # texture comes from the soil_type class centroids
    assert summary["k_source_area_pct"]["soil_type class"] == pytest.approx(100)


def test_optional_inputs_take_over_and_outputs_are_cropped(run, run_extras):
    paths, out, summary = run_extras
    assert summary["k_source_area_pct"]["Williams from soil_texture"] == pytest.approx(100)
    assert any(k.endswith("trees") for k in summary["landslide"]["landcover_group_area_pct"])
    assert summary["landslide"]["soil_depth_m_mean"] != pytest.approx(1.0)
    # cropped outputs: smaller than the DEM, on the same lattice
    _, dem_transform, _, _ = _read(next(p for p in paths if p.name == "dem.tif"))
    a, transform, _, _ = _read(out / "soil_loss.tif")
    dem = _read(next(p for p in paths if p.name == "dem.tif"))[0]
    assert a.shape[1:] < dem.shape[1:] or a.shape[1:] != dem.shape[1:]
    offset = ~dem_transform @ (transform.c, transform.f)
    assert np.allclose(offset, np.round(offset)) and transform.a == dem_transform.a
    assert (out / "landslide_maps.png").exists() and (out / "rusle_maps.png").exists()
    # same rain and terrain, so R and LS agree with the minimum-input run where both exist
    assert summary["factor_mean"]["R"] == pytest.approx(run[2]["factor_mean"]["R"], rel=0.05)


def test_partial_years_are_left_out_of_r(tmp_path):
    paths = synthetic.make_inputs(tmp_path / "inputs", years=[2010, 2011], size_px=60)
    p2011 = next(p for p in paths if p.name == "precip_2011.tif")
    with rasterio.open(p2011) as src:  # keep only January 2011
        data, profile, tags = src.read()[: 31 * 48], src.profile, src.tags()
    profile.update(count=data.shape[0])
    with rasterio.open(p2011, "w", **profile) as dst:
        dst.write(data)
        dst.update_tags(**tags)
    summary = pipeline.run(paths, tmp_path / "out", pipeline.Params(figures=False, landslide=False))
    assert summary["rainfall"]["years_used"] == [2010]
    assert set(summary["rainfall"]["coverage_pct_by_year"]) == {"2010", "2011"}


def test_precip_npz_gives_the_same_result_as_geotiff(tmp_path):
    paths = synthetic.make_inputs(tmp_path / "inputs", years=[2010], size_px=50)
    tif = next(p for p in paths if p.name == "precip_2010.tif")
    r = rio.read(tif)
    npz = rio.write_npz(tmp_path / "precip_2010.npz", r.data, rio.grid_of(r), r.tags)
    back = rio.read(npz)
    assert rio.role_of(back) == "precip" and back.tags["TIME_STEP_MINUTES"] == "30"
    assert np.array_equal(back.data, r.data) and rio.grid_of(back).same_as(rio.grid_of(r))
    params = pipeline.Params(figures=False, landslide=False)
    a = pipeline.run(paths, tmp_path / "out_tif", params)
    b = pipeline.run([p for p in paths if p != tif] + [npz], tmp_path / "out_npz", params)
    assert a["factor_mean"]["R"] == pytest.approx(b["factor_mean"]["R"])


def test_role_detection_and_missing_inputs(tmp_path):
    grid = rio.Grid(CRS.from_epsg(32637), Affine(30, 0, 0, 0, -30, 0), 4, 4)
    tagged = rio.write(tmp_path / "elevation_model.tif", np.ones((4, 4)), grid, description="x", units="m",
                       tags={rio.ROLE_TAG: "dem"})
    named = rio.write(tmp_path / "soil_type_era5.tif", np.ones((4, 4)), grid, description="x", units="class")
    texture = rio.write(tmp_path / "soil_texture.tif", np.ones((4, 4, 4)), grid, description="x", units="%")
    unknown = rio.write(tmp_path / "mystery.tif", np.ones((4, 4)), grid, description="x", units="?")
    assert rio.role_of(rio.read(tagged)) == "dem"
    assert rio.role_of(rio.read(named)) == "soil_type"
    assert rio.role_of(rio.read(texture)) == "soil_texture"
    with pytest.raises(ValueError, match="cannot tell"):
        rio.role_of(rio.read(unknown))
    with pytest.raises(ValueError, match="ndvi, precip"):
        rio.load_inputs([tagged, named])
    with pytest.raises(ValueError, match="soil_type or soil_texture or k_factor"):
        rio.load_inputs([tagged])
