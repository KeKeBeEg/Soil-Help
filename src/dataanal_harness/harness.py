"""Test and debug harness for src.dataanal (the GeoTIFF RUSLE pipeline).

    python -m src.dataanal_harness.harness                  # Earth Engine inputs (cached) -> pipeline
    python -m src.dataanal_harness.harness --synthetic      # synthetic inputs, no Earth Engine needed
    python -m src.dataanal_harness.harness --download-only  # only fetch the input GeoTIFFs
    python -m src.dataanal_harness.harness --refresh        # re-download inputs even if cached

It prints a report of every input GeoTIFF before the run and sanity checks after it.
Set EE_PROJECT to your Earth Engine Cloud project (after `earthengine authenticate`).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import rasterio

from ..dataanal import pipeline, raster_io as rio
from . import synthetic
from .config import HarnessConfig


def describe(paths) -> None:
    """One line per input GeoTIFF: role, grid, bands and value range."""
    print(f"{'file':<18} {'role':<10} {'crs':<11} {'size':>11} {'res':>8} {'bands':>6} "
          f"{'min':>9} {'max':>9} {'nodata':>7}")
    for p in rio.expand(paths):
        r = rio.read(p)
        try:
            role = rio.role_of(r)
        except ValueError:
            role = "UNKNOWN"
        finite = r.data[np.isfinite(r.data)]
        lo, hi = (f"{finite.min():.4g}", f"{finite.max():.4g}") if finite.size else ("-", "-")
        print(f"{p.name:<18} {role:<10} {str(r.crs):<11} {f'{r.data.shape[2]}x{r.data.shape[1]}':>11} "
              f"{abs(r.transform.a):>8.4g} {r.data.shape[0]:>6} {lo:>9} {hi:>9} "
              f"{100 * (1 - finite.size / r.data.size):>6.1f}%")


def _read(path):
    with rasterio.open(path) as src:
        return src.read(masked=True).astype(float).filled(np.nan)


def sanity_checks(summary: dict, out_dir: Path) -> bool:
    """PASS/WARN lines on the run's summary and on the written GeoTIFFs; True if all pass."""
    r, fm, sl = summary["rainfall"], summary["factor_mean"], summary["soil_loss_t_ha_yr"]
    ratio = r["R_ei30_mean"] / r["R_renard_freimund_mean"] if r["R_renard_freimund_mean"] else float("nan")

    a = _read(out_dir / "soil_loss.tif")[0]
    product = np.prod([_read(out_dir / f"{f}_factor.tif")[0] for f in ("r", "k", "ls", "c", "p")], axis=0)
    monthly = np.nansum(_read(out_dir / "soil_loss_monthly.tif"), axis=0)
    ok = np.isfinite(a)
    checks = [
        (summary["study_area"]["area_km2"] > 0, f"study area {summary['study_area']['area_km2']:,.0f} km2"),
        (min(r["coverage_pct_by_year"].values()) >= 95,
         f"rainfall time coverage per year >= 95%: {r['coverage_pct_by_year']}"),
        (r["missing_pct"] < 5, f"missing rainfall values {r['missing_pct']:.2f}%"),
        (0.25 <= ratio <= 4, f"R from storms {r['R_ei30_mean']:,.0f} vs Renard-Freimund "
                             f"{r['R_renard_freimund_mean']:,.0f} (ratio {ratio:.2f}, expected 0.25-4)"),
        (0.005 <= fm["K"] <= 0.07, f"mean K {fm['K']:.4f}"),
        (0 < fm["C"] < 1, f"mean C {fm['C']:.3f}"),
        (0.05 <= sl["mean"] <= 200, f"mean soil loss {sl['mean']:.1f} t/ha/yr (plausible 0.05-200)"),
        (np.allclose(a[ok], product[ok], rtol=1e-4), "soil_loss.tif = R*K*LS*C*P from the factor GeoTIFFs"),
        (np.allclose(a[ok], monthly[ok], rtol=1e-3, atol=1e-4), "soil_loss_monthly.tif bands add up to soil_loss.tif"),
    ]
    print("\nSanity checks")
    for passed, msg in checks:
        print(f"  [{'PASS' if passed else 'WARN'}] {msg}")
    return all(p for p, _ in checks)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--synthetic", action="store_true", help="use synthetic inputs instead of Earth Engine")
    ap.add_argument("--synthetic-years", type=int, default=2, help="years of synthetic rainfall")
    ap.add_argument("--download-only", action="store_true", help="fetch the inputs, do not run the pipeline")
    ap.add_argument("--refresh", action="store_true", help="re-download inputs even if cached")
    ap.add_argument("--no-figures", action="store_true")
    args = ap.parse_args(argv)

    if args.synthetic:
        cfg = HarnessConfig(aoi_name="synthetic", aoi_label="Synthetic test area")
        years = range(cfg.start_year, cfg.start_year + args.synthetic_years)
        paths = synthetic.make_inputs(cfg.inputs_dir, years=years)
    else:
        from . import gee_data  # imported here so --synthetic works without Earth Engine

        cfg = HarnessConfig()
        paths = gee_data.download_inputs(cfg, refresh=args.refresh)

    print(f"\nInputs in {cfg.inputs_dir}")
    describe(paths)
    if args.download_only:
        return 0
    print()
    summary = pipeline.run(paths, cfg.out_dir, pipeline.Params(label=cfg.aoi_label, figures=not args.no_figures))
    return 0 if sanity_checks(summary, cfg.out_dir) else 1


if __name__ == "__main__":
    sys.exit(main())
