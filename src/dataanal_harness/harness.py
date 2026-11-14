"""Test and debug harness for src.dataanal (the GeoTIFF analysis pipeline).

    python -m src.dataanal_harness.harness                          # synthetic inputs, no Earth Engine
    python -m src.dataanal_harness.harness --extras                 # + soil texture, depth, land cover
    python -m src.dataanal_harness.harness --inputs runs/x/inputs   # any folder of input GeoTIFFs

It prints a report of every input GeoTIFF before the run and sanity checks after it. Real
inputs are pulled by the backend (python run.py request.json), see src/ingest.
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
    ap.add_argument("--inputs", type=Path, help="run on this folder of input GeoTIFFs instead of synthetic data")
    ap.add_argument("--out", type=Path, help="output folder (default: outputs/<name>)")
    ap.add_argument("--extras", action="store_true", help="synthetic: add soil texture, depth and land cover")
    ap.add_argument("--synthetic", action="store_true", help="run on synthetic data (default when --inputs is omitted)")
    ap.add_argument("--synthetic-years", type=int, default=2, help="years of synthetic rainfall")
    ap.add_argument("--no-figures", action="store_true")
    args = ap.parse_args(argv)

    if args.inputs:
        cfg = HarnessConfig(aoi_name=args.inputs.resolve().parent.name, aoi_label=args.inputs.resolve().parent.name)
        paths = rio.expand([args.inputs])
    else:
        cfg = HarnessConfig()
        years = range(cfg.start_year, cfg.start_year + args.synthetic_years)
        paths = synthetic.make_inputs(cfg.inputs_dir, years=years, extras=args.extras)
    out_dir = args.out or cfg.out_dir

    print(f"\nInputs ({len(paths)} files)")
    describe(paths)
    print()
    summary = pipeline.run(paths, out_dir, pipeline.Params(label=cfg.aoi_label, figures=not args.no_figures))
    return 0 if sanity_checks(summary, out_dir) else 1


if __name__ == "__main__":
    sys.exit(main())
