"""Shallow-landslide physics: steady-state wetness + infinite-slope factor of safety.

Pure numpy, no file access. Units: lengths m, rain mm/day, recharge m/day, stresses kPa,
unit weights kN/m3, angles radians unless a name ends in _deg.

    W  = min(1, q a / (T sin(theta)))                               Montgomery & Dietrich (1994)
    FS = [c + (gamma - W gamma_w) z cos^2(theta) tan(phi)] / (gamma z sin(theta) cos(theta))

FS falls as W rises and W rises with rain, so FS = 1 can be solved for the rain each cell
can take ("critical rain"). Repeating that for parameter sets drawn from their uncertainty
ranges gives, per cell, the rain at which the probability of failure reaches p:
P(FS < 1 | rain) = P(critical rain < rain). Alert classes for any day are then a comparison
of that day's rain with those thresholds, with no simulation per day.
"""
from __future__ import annotations

import numpy as np

GAMMA_SOIL = 18.0  # kN/m3, moist soil
GAMMA_WATER = 9.81  # kN/m3
NEVER_MM_DAY = 9999.0  # written instead of +inf: the cell stays stable even when saturated
CLASS_NAMES = {0: "low", 1: "watch", 2: "warning", 3: "danger"}
STABILITY_CLASSES = {1: "stable even when saturated", 2: "rain-dependent", 3: "unstable even when dry"}
QUANTILE_PROBS = tuple(round(0.1 * k, 1) for k in range(1, 10))  # 0.1 .. 0.9


def recharge(rain_mm_day, infiltration_fraction: float = 0.7):
    """Recharge q (m/day) from mean daily rain over the recharge window (mm/day)."""
    return infiltration_fraction * np.asarray(rain_mm_day, float) / 1000.0


def wetness(q, a, theta, transmissivity):
    """Saturated fraction of the soil column, 0-1."""
    with np.errstate(divide="ignore", invalid="ignore"):
        w = q * a / (transmissivity * np.sin(theta))
    return np.clip(np.nan_to_num(w, nan=1.0, posinf=1.0), 0.0, 1.0)


def factor_of_safety(w, theta, cohesion_kpa, phi, z, gamma=GAMMA_SOIL, gamma_w=GAMMA_WATER):
    """Infinite-slope factor of safety; cohesion is effective soil + root cohesion."""
    cos, sin = np.cos(theta), np.sin(theta)
    with np.errstate(divide="ignore", invalid="ignore"):
        return (cohesion_kpa + (gamma - w * gamma_w) * z * cos**2 * np.tan(phi)) / (gamma * z * sin * cos)


def critical_wetness(theta, cohesion_kpa, phi, z, gamma=GAMMA_SOIL, gamma_w=GAMMA_WATER):
    """W at which FS = 1 (<= 0: fails dry, >= 1: holds when saturated)."""
    cos, sin = np.cos(theta), np.sin(theta)
    with np.errstate(divide="ignore", invalid="ignore"):
        return (cohesion_kpa + gamma * z * cos * (cos * np.tan(phi) - sin)) / (gamma_w * z * cos**2 * np.tan(phi))


def critical_rain(theta, a, cohesion_kpa, phi, z, transmissivity, infiltration_fraction=0.7,
                  min_slope=np.radians(2.0), gamma=GAMMA_SOIL, gamma_w=GAMMA_WATER):
    """Mean daily rain over the recharge window (mm/day) that brings FS to 1.

    0 where the slope fails even when dry, +inf where it holds even when saturated or is
    flatter than `min_slope`."""
    theta = np.asarray(theta, float)
    w_crit = critical_wetness(theta, cohesion_kpa, phi, z, gamma, gamma_w)
    with np.errstate(divide="ignore", invalid="ignore"):
        rain = 1000.0 * w_crit * transmissivity * np.sin(theta) / (a * infiltration_fraction)
    rain = np.where(w_crit <= 0, 0.0, np.where(w_crit >= 1, np.inf, rain))
    return np.where(theta < min_slope, np.inf, rain)


def stability_class(rain_crit):
    """1 stable even when saturated, 2 rain-dependent, 3 unstable even when dry; 0 = no data."""
    r = np.asarray(rain_crit, float)
    cls = np.select([np.isinf(r), r <= 0, np.isfinite(r)], [1, 3, 2], default=0)
    return cls.astype(np.uint8)


def rain_threshold_quantiles(theta, a, phi_deg, c_min, c_max, root_min, root_max, z, transmissivity,
                             infiltration_fraction=0.7, n=100, seed=0, probs=QUANTILE_PROBS,
                             phi_spread_deg=3.0, z_spread=0.3, t_factor=3.0, min_slope=np.radians(2.0),
                             gamma=GAMMA_SOIL, gamma_w=GAMMA_WATER, chunk=200_000):
    """Rain (mm/day) at which P(FS < 1) reaches each of `probs`, per cell -> (len(probs), N).

    Each of the `n` realisations draws one set of offsets and applies it to every cell:
    friction angle +/- phi_spread_deg, a quantile within each cell's cohesion ranges, soil
    depth x (1 +/- z_spread), transmissivity x/÷ t_factor (log-uniform). The uncertainty is in
    the lookup tables, so it is shared across cells rather than independent per cell.
    Inputs are 1-D arrays of equal length (or scalars)."""
    rng = np.random.default_rng(seed)
    d_phi = rng.uniform(-phi_spread_deg, phi_spread_deg, n)
    u_c, u_r = rng.uniform(0, 1, n), rng.uniform(0, 1, n)
    f_z = rng.uniform(1 - z_spread, 1 + z_spread, n)
    f_t = np.exp(rng.uniform(-np.log(t_factor), np.log(t_factor), n))

    arrays = np.broadcast_arrays(*(np.asarray(v, float).ravel() for v in
                                   (theta, a, phi_deg, c_min, c_max, root_min, root_max, z, transmissivity)))
    size = arrays[0].size
    out = np.empty((len(probs), size), np.float32)
    for lo in range(0, size, chunk):
        th, aa, ph, cmin, cmax, rmin, rmax, zz, tt = (v[lo:lo + chunk] for v in arrays)
        samples = np.empty((n, th.size))
        for i in range(n):
            cohesion = cmin + u_c[i] * (cmax - cmin) + rmin + u_r[i] * (rmax - rmin)
            samples[i] = critical_rain(th, aa, cohesion, np.radians(ph + d_phi[i]), zz * f_z[i], tt * f_t[i],
                                       infiltration_fraction, min_slope, gamma, gamma_w)
        # "inverted_cdf": the smallest sampled rain whose failure share reaches p; no
        # interpolation, so +inf samples stay +inf instead of turning into NaN.
        out[:, lo:lo + chunk] = np.quantile(samples, probs, axis=0, method="inverted_cdf")
    return out


def classify(rain, thresholds):
    """Alert class 0 low / 1 watch / 2 warning / 3 danger.

    `thresholds` holds the rain at which P(fail) reaches the watch, warning and danger
    probabilities (leading axis of length 3); a cell is at a level once rain reaches it."""
    r = np.asarray(rain, float)
    cls = sum((r >= t) for t in thresholds).astype(np.uint8)
    return np.where(np.isfinite(r), cls, 0).astype(np.uint8)


def failure_probability(rain, quantiles, probs=QUANTILE_PROBS):
    """Lower bound of P(FS < 1) for a rain amount, from the stored quantiles (steps of 0.1)."""
    r = np.asarray(rain, float)
    reached = np.zeros(np.broadcast(r, quantiles[0]).shape)
    for p, q in zip(probs, quantiles):
        reached = np.where(r >= q, p, reached)
    return np.where(np.isfinite(r), reached, np.nan)


def window_mean(daily, window: int = 3, axis: int = 0):
    """Trailing mean over `window` days (NaN until `window` days are available or if any is NaN)."""
    d = np.moveaxis(np.asarray(daily, float), axis, 0)
    out = np.full(d.shape, np.nan)
    if d.shape[0] >= window:
        zero = np.zeros((1,) + d.shape[1:])
        cs = np.cumsum(np.concatenate([zero, np.nan_to_num(d)]), axis=0)
        gaps = np.cumsum(np.concatenate([zero, np.isnan(d)]), axis=0)
        mean = (cs[window:] - cs[:-window]) / window
        out[window - 1:] = np.where(gaps[window:] - gaps[:-window] > 0, np.nan, mean)
    return np.moveaxis(out, 0, axis)
