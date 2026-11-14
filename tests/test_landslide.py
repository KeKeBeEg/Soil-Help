import numpy as np
import pytest

from src.dataanal import landslide as ls
from src.dataanal import soil_params as sp

DEG = np.radians


# ----------------------------------------------------------------------------- soil parameters

@pytest.mark.parametrize("sand, silt, clay, name", [
    (92, 5, 3, "sand"), (82, 12, 6, "loamy sand"), (65, 25, 10, "sandy loam"), (40, 40, 20, "loam"),
    (20, 65, 15, "silt loam"), (5, 88, 7, "silt"), (60, 12, 28, "sandy clay loam"), (35, 33, 32, "clay loam"),
    (10, 58, 32, "silty clay loam"), (50, 5, 45, "sandy clay"), (5, 47, 48, "silty clay"), (20, 20, 60, "clay"),
])
def test_usda_texture_class_reference_points(sand, silt, clay, name):
    assert sp.USDA_CLASSES[int(sp.usda_texture_class(sand, silt, clay))] == name


def test_texture_class_rescales_and_flags_missing():
    # g/kg instead of % classifies the same; missing data -> 0
    assert sp.usda_texture_class(400, 400, 200) == sp.usda_texture_class(40, 40, 20)
    assert sp.usda_texture_class(np.nan, 40, 20) == 0


def test_every_triangle_point_gets_a_class():
    s, c = np.meshgrid(np.arange(0, 101, 2.0), np.arange(0, 101, 2.0))
    ok = s + c <= 100
    cls = sp.usda_texture_class(s[ok], 100 - s[ok] - c[ok], c[ok])
    assert set(np.unique(cls)) == set(range(1, 13))


def test_strength_and_ksat_lookups():
    phi, cmin, cmax = sp.strength_params([1, 4, 12, 0])
    assert phi.tolist() == [33, 28, 22, 28]  # unknown -> loam
    assert np.all(cmin <= cmax)
    k = sp.ksat_from_texture([1, 4, 12])
    assert k[0] > k[1] > k[2] > 0


def test_landcover_schemes_map_to_groups():
    wc = sp.landcover_groups([[10, 40], [30, np.nan]], "worldcover")
    assert wc.tolist() == [[4, 1], [2, 0]]
    cdl = sp.landcover_groups([1, 70, 176, 142, 111, 999], "cdl")  # corn, Christmas trees, pasture, forest, water, junk
    assert cdl.tolist() == [1, 3, 2, 4, 7, 0]
    lo, hi = sp.root_cohesion(cdl)
    assert lo[3] == 5 and hi[3] == 10 and hi[0] == 0
    cap = sp.c_cap(cdl)
    assert cap[0] == 1.0 and cap[3] < cap[2] < 1.0  # cropland uncapped; forest below pasture
    with pytest.raises(ValueError):
        sp.landcover_groups([1], "nlcd")


# ----------------------------------------------------------------------------- physics

def test_dry_stable_and_saturated_wetness():
    assert ls.wetness(0.0, 100.0, DEG(30), 65.0) == 0.0
    assert ls.wetness(1.0, 1e5, DEG(30), 65.0) == 1.0
    assert ls.wetness(0.01, 100.0, 0.0, 65.0) == 1.0  # flat ground: saturated, not a NaN


def test_factor_of_safety_by_hand():
    # cohesionless, dry: FS = tan(phi) / tan(theta)
    assert ls.factor_of_safety(0.0, DEG(20), 0.0, DEG(30), 1.0) == pytest.approx(np.tan(DEG(30)) / np.tan(DEG(20)))
    # saturated: buoyancy reduces the frictional term by (gamma - gamma_w) / gamma
    sat = ls.factor_of_safety(1.0, DEG(20), 0.0, DEG(30), 1.0)
    assert sat == pytest.approx((18 - 9.81) / 18 * np.tan(DEG(30)) / np.tan(DEG(20)))


def test_fs_is_one_at_critical_rain():
    theta, a, c, phi, z, t = DEG(32.0), 250.0, 3.0, DEG(30.0), 1.2, 65.0
    rain = ls.critical_rain(theta, a, c, phi, z, t)
    assert 0 < rain < np.inf
    w = ls.wetness(ls.recharge(rain), a, theta, t)
    assert ls.factor_of_safety(w, theta, c, phi, z) == pytest.approx(1.0, abs=1e-9)
    # a little more rain fails, a little less holds
    assert ls.factor_of_safety(ls.wetness(ls.recharge(rain * 1.05), a, theta, t), theta, c, phi, z) < 1
    assert ls.factor_of_safety(ls.wetness(ls.recharge(rain * 0.95), a, theta, t), theta, c, phi, z) > 1


def test_unconditional_cases_and_classes():
    # gentle slope holds even saturated; cliff of loose soil fails dry; flat ground is stable
    rain = ls.critical_rain(DEG(np.array([10.0, 45.0, 1.0])), 100.0, 0.0, DEG(30.0), 1.0, 65.0)
    assert rain[0] == np.inf and rain[1] == 0.0 and rain[2] == np.inf
    assert ls.stability_class(rain).tolist() == [1, 3, 1]


def test_quantiles_increase_with_probability_and_bracket_the_mean_case():
    theta = DEG(np.array([28.0, 33.0, 38.0]))
    q = ls.rain_threshold_quantiles(theta, 300.0, 28.0, 4.0, 6.0, 0.0, 0.0, 1.0, 65.0, n=200, seed=1)
    assert q.shape == (9, 3)
    assert np.all(q[1:] >= q[:-1])  # more rain needed for higher failure probability (inf allowed)
    mean_case = ls.critical_rain(theta, 300.0, 5.0, DEG(28.0), 1.0, 65.0)
    assert np.all(q[0] <= mean_case) and np.all(mean_case <= q[-1])
    assert np.all(q[4] >= q[4][[2]])  # steeper -> needs less rain


def test_classify_and_probability_from_thresholds():
    q = np.array([[10.0], [20.0], [30.0], [40.0], [50.0], [60.0], [70.0], [80.0], [90.0]])
    thresholds = q[[0, 2, 5]]  # P = 0.1, 0.3, 0.6
    assert ls.classify(np.array([[5.0], [15.0], [35.0], [65.0]])[:, 0], thresholds[:, [0, 0, 0, 0]]).tolist() == [0, 1, 2, 3]
    assert ls.failure_probability(np.array([5.0, 35.0, 95.0]), q[:, [0, 0, 0]]).tolist() == pytest.approx([0.0, 0.3, 0.9])


def test_window_mean_handles_gaps():
    d = np.array([3.0, 6.0, 9.0, np.nan, 3.0, 3.0, 3.0])
    m = ls.window_mean(d, 3)
    assert np.isnan(m[:2]).all() and m[2] == pytest.approx(6.0)
    assert np.isnan(m[3:6]).all() and m[6] == pytest.approx(3.0)
