import numpy as np
import pytest
from affine import Affine

from src.dataanal import factors as f


# ----------------------------------------------------------------------------- R

def test_single_storm_ei30_by_hand():
    # 2 h at 20 mm/h = 40 mm; e = 0.29 (1 - 0.72 e^-1) = 0.21319 MJ/ha/mm
    rate = np.zeros(200)
    rate[10:14] = 20.0
    start, depth, energy, i30, ei = f.storm_ei30(rate)
    e = 0.29 * (1 - 0.72 * np.exp(-1.0))
    assert start.tolist() == [10]
    assert depth[0] == pytest.approx(40.0)
    assert energy[0] == pytest.approx(e * 40.0)
    assert i30[0] == pytest.approx(20.0)
    assert ei[0] == pytest.approx(e * 40.0 * 20.0)


def test_small_storm_is_not_erosive():
    rate = np.zeros(200)
    rate[50:52] = 4.0  # 4 mm
    assert f.storm_ei30(rate)[0].size == 0


def test_bursts_3h_apart_are_one_storm():
    rate = np.zeros(200)
    rate[10:12] = 10.0  # 10 mm
    rate[18:20] = 10.0  # 3 h later, 10 mm
    start, depth, *_ = f.storm_ei30(rate)
    assert start.tolist() == [10]
    assert depth[0] == pytest.approx(20.0)


def test_drizzly_7h_gap_splits_storms():
    rate = np.zeros(200)
    rate[10:13] = 10.0  # 15 mm
    rate[13:27] = 0.1   # 7 h of drizzle, 0.7 mm in total (< 1.27 mm per 6 h)
    rate[27:30] = 10.0  # 15 mm
    start, depth, *_ = f.storm_ei30(rate)
    assert len(start) == 2
    assert start[0] == 10


def test_renard_freimund_is_continuous_at_850mm():
    lo, hi = f.r_renard_freimund([849.999, 850.0])
    assert lo == pytest.approx(hi, rel=5e-3)


def test_r_factor_annual_and_monthly_consistent():
    times = np.arange("2010-01-01", "2012-01-01", np.timedelta64(30, "m"), dtype="datetime64[m]")
    rate = np.zeros((times.size, 2, 2), np.float32)
    rate[100:104] = 20.0          # Jan 2010 storm in every pixel
    rate[20000:20004, 0, 0] = 30  # Feb 2011 storm in one pixel
    out = f.r_factor(rate, times, [2010, 2011])
    assert np.allclose(out["monthly"].sum(axis=0), out["annual"])
    assert out["annual"][0, 0] > out["annual"][1, 1] > 0
    assert out["storms"][1, 1] == pytest.approx(0.5)


# ----------------------------------------------------------------------------- K

def test_fao_classes_cover_triangle_corners():
    assert f.fao_texture_class(90, 5) == 1
    assert f.fao_texture_class(40, 20) == 2
    assert f.fao_texture_class(5, 20) == 3
    assert f.fao_texture_class(20, 45) == 4
    assert f.fao_texture_class(10, 70) == 5


def test_k_table_is_physically_ordered():
    t = f.k_lookup()
    k = {c: t[c]["k"] for c in t}
    assert all(0.003 < v < 0.045 for v in k.values())
    assert k[1] == min(k[c] for c in range(1, 6))  # coarse (sandy) soils least erodible
    assert k[3] == max(k[c] for c in range(1, 6))  # silty soils most erodible
    for c in range(1, 6):
        assert t[c]["k_min"] <= k[c] <= t[c]["k_max"]


def test_k_from_soil_type_handles_water_and_nan():
    t = f.k_lookup()
    k = f.k_from_soil_type(np.array([[0, 1], [3, np.nan]]), t)
    assert np.isnan(k[0, 0]) and np.isnan(k[1, 1])
    assert k[0, 1] == pytest.approx(t[1]["k"])
    assert k[1, 0] == pytest.approx(t[3]["k"])


# ----------------------------------------------------------------------------- LS

def test_ls_on_uniform_slope_grows_downhill_and_is_capped():
    res = 30.0
    rows = np.arange(80)[:, None] * np.ones((1, 40))
    dem = 1000.0 - 0.1 * rows * res  # 10 % slope facing south
    ls, slope = f.ls_factor(dem, res, "EPSG:32637", Affine(res, 0, 500000, 0, -res, 1250000),
                            max_specific_area=300.0)
    assert np.nanmedian(slope) == pytest.approx(np.arctan(0.1), rel=1e-3)
    col = ls[1:-1, 20]
    assert np.all(np.diff(col[:9]) > 0)  # increases downslope until the cap
    s_term = (np.sin(np.arctan(0.1)) / 0.0896) ** 1.3
    assert np.nanmax(ls) == pytest.approx((300 / 22.13) ** 0.4 * s_term, rel=1e-3)


# ----------------------------------------------------------------------------- C

def test_c_from_ndvi_bounds():
    c = f.c_from_ndvi([-0.2, 0.0, 0.4, 0.8])
    assert c[0] == 1.0 and c[1] == 1.0
    assert c[2] == pytest.approx(np.exp(-2 * 0.4 / 0.6))
    assert np.all(np.diff(c[1:]) < 0)


def test_c_effective_weights_by_erosivity():
    ndvi = np.stack([np.full((1, 1), 0.0)] * 6 + [np.full((1, 1), 0.8)] * 6)
    only_first_half = f.c_effective(ndvi, [1] * 6 + [0] * 6)
    assert only_first_half[0, 0] == pytest.approx(1.0)
