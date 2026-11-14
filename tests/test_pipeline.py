"""End-to-end tests of the GeoTIFF pipeline on synthetic inputs (no Earth Engine)."""
import numpy as np
import pytest
import rasterio
from affine import Affine
from rasterio.crs import CRS

from src.dataanal import pipeline, raster_io as rio
from src.dataanal_harness import synthetic


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("rusle")
    paths = synthetic.make_inputs(tmp / "inputs", years=[2010], size_px=160)
    summary = pipeline.run(paths, tmp / "out", pipeline.Params(figures=False))
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


def test_role_detection_and_missing_inputs(tmp_path):
    grid = rio.Grid(CRS.from_epsg(32637), Affine(30, 0, 0, 0, -30, 0), 4, 4)
    tagged = rio.write(tmp_path / "elevation_model.tif", np.ones((4, 4)), grid, description="x", units="m",
                       tags={rio.ROLE_TAG: "dem"})
    named = rio.write(tmp_path / "soil_type_era5.tif", np.ones((4, 4)), grid, description="x", units="class")
    unknown = rio.write(tmp_path / "mystery.tif", np.ones((4, 4)), grid, description="x", units="?")
    assert rio.role_of(rio.read(tagged)) == "dem"
    assert rio.role_of(rio.read(named)) == "soil_type"
    with pytest.raises(ValueError, match="cannot tell"):
        rio.role_of(rio.read(unknown))
    with pytest.raises(ValueError, match="ndvi, precip"):
        rio.load_inputs([tagged, named])
