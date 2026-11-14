"""Entry point: request JSON -> outputs in working_save.

    python run.py request.json               # from the repository root
    python -m src.backend.run request.json
    python run.py request.json --refresh     # redo the analysis even if it is up to date

Steps: read the request; pick the data path (polygon inside CONUS -> US data, otherwise
global data); plan the grids; pull missing data into the caches and assemble the analysis
inputs; run the analysis over the fixed climate period; render the views for t_range.
The analysis is skipped when working_save already holds one for the same field, data path,
period and analysis version (analysis_version.json) that used no default for a missing
layer. Then only the views are rendered, from analysis/ alone: a request that only changes
t_range takes seconds and needs neither the plan nor Earth Engine.

working_save/
  request.json   copy of the request
  inputs/        analysis inputs (GeoTIFF), reproducible on their own
  analysis/      long-term outputs over the field + margin, summary.json, figures, plan.json,
                 rain_daily.npz, yearly range layers and daily_series.csv over the field
  view/          outputs for t_range over the field, field_summary.json, figures
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

from ..dataanal import pipeline
from ..ingest import collect
from . import plan as planner, region, request, views


LAST_FILE = "daily_series.csv"  # written last: an analysis without it was interrupted


def analysis_is_current(analysis_dir: Path, key: dict) -> bool:
    """True when analysis_dir holds a complete analysis for `key` (plan.request_key) that used
    no default for a layer that could not be pulled (that analysis is redone, retrying the pull)."""
    stamp_path = analysis_dir / "plan.json"
    if not (stamp_path.exists() and (analysis_dir / "summary.json").exists() and (analysis_dir / LAST_FILE).exists()
            and (analysis_dir / "soil_loss.tif").exists()):
        return False
    stamp = json.loads(stamp_path.read_text(encoding="utf-8"))
    if any(stamp.get(k) != v for k, v in key.items()):
        return False
    if stamp.get("defaults_used"):
        print(f"The saved analysis used defaults for: {', '.join(stamp['defaults_used'])}. Redoing it to retry the pull")
        return False
    return True


def run(request_path, refresh: bool = False) -> dict:
    t0 = time.time()
    req = request.load(request_path)
    path = region.data_path(req.polygon)
    print(f"Field '{req.label}': {len(req.polygon)} vertices -> {path.upper()} data path; "
          f"view {req.t_start} .. {req.t_end}")

    ws = req.working_save
    ws.mkdir(parents=True, exist_ok=True)
    if req.source and req.source != (ws / "request.json").resolve():
        shutil.copyfile(req.source, ws / "request.json")
    analysis_dir = ws / "analysis"
    if refresh or not analysis_is_current(analysis_dir, planner.request_key(req.polygon, path)):
        plan = planner.make(req, path)
        print(f"Grids ({plan.crs.to_string()}, {plan.res:g} m): DEM {plan.dem_grid.width} x {plan.dem_grid.height}, "
              f"field {plan.field_grid.width} x {plan.field_grid.height}, rain {plan.rain_grid.width} x "
              f"{plan.rain_grid.height} cells; climate {plan.climate_years[0]}-{plan.climate_years[1]}")
        inputs, defaults = collect.collect(plan, ws / "inputs", req.global_cache, req.us_cache)
        if analysis_dir.exists():
            shutil.rmtree(analysis_dir)
        pipeline.run(inputs, analysis_dir, pipeline.Params(label=req.label, crop_to_region=True))
        stamp = {**plan.fingerprint(), "defaults_used": defaults}
        (analysis_dir / "plan.json").write_text(json.dumps(stamp, indent=2), encoding="utf-8")
        views.precompute(analysis_dir)  # writes LAST_FILE
    else:
        print("Analysis is up to date for this field: rendering the selected dates only")
    summary = views.render(req, analysis_dir, ws / "view")
    print(f"Done in {time.time() - t0:.0f} s. Outputs in {ws}")
    for line in summary["advice"]:
        print(f"  - {line}")
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("request", type=Path, help="path of the request JSON")
    ap.add_argument("--refresh", action="store_true", help="redo the analysis even if it is up to date")
    args = ap.parse_args(argv)
    try:
        run(args.request, refresh=args.refresh)
    except Exception as err:
        msg = str(err)
        if "authorize access to your earth engine account" in msg.lower() or "earthengine authenticate" in msg.lower():
            print("\n[Error] Google Earth Engine is not authenticated on this machine.", file=sys.stderr)
            print("To pull satellite/GIS datasets, please run 'earthengine authenticate' in your shell", file=sys.stderr)
            print("and set your Google Cloud project (e.g. $env:EE_PROJECT = '<your-project>').", file=sys.stderr)
            return 1
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
