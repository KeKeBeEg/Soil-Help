"""Entry point: request JSON -> outputs in working_save.

    python run.py request.json               # from the repository root
    python -m src.backend.run request.json
    python run.py request.json --refresh     # redo the analysis even if it is up to date

Steps: read the request; pick the data path (polygon inside CONUS -> US data, otherwise
global data); plan the grids; pull missing data into the caches and assemble the analysis
inputs; run the analysis over the fixed climate period; render the views for t_range.
The analysis is skipped when working_save already holds one for the same field and plan,
so a request that only changes t_range takes seconds.

working_save/
  request.json   copy of the request
  inputs/        analysis inputs (GeoTIFF), reproducible on their own
  analysis/      long-term outputs over the field + margin, summary.json, figures
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


def _analysis_is_current(analysis_dir: Path, fingerprint: dict) -> bool:
    stamp = analysis_dir / "plan.json"
    if not (stamp.exists() and (analysis_dir / "summary.json").exists()):
        return False
    return json.loads(stamp.read_text(encoding="utf-8")) == fingerprint


def run(request_path, refresh: bool = False) -> dict:
    t0 = time.time()
    req = request.load(request_path)
    path = region.data_path(req.polygon)
    print(f"Field '{req.label}': {len(req.polygon)} vertices -> {path.upper()} data path; "
          f"view {req.t_start} .. {req.t_end}")
    plan = planner.make(req, path)
    print(f"Grids ({plan.crs.to_string()}, {plan.res:g} m): DEM {plan.dem_grid.width} x {plan.dem_grid.height}, "
          f"field {plan.field_grid.width} x {plan.field_grid.height}, rain {plan.rain_grid.width} x "
          f"{plan.rain_grid.height} cells; climate {plan.climate_years[0]}-{plan.climate_years[1]}")

    ws = req.working_save
    ws.mkdir(parents=True, exist_ok=True)
    if req.source and req.source != (ws / "request.json").resolve():
        shutil.copyfile(req.source, ws / "request.json")
    analysis_dir = ws / "analysis"
    fingerprint = plan.fingerprint()
    if refresh or not _analysis_is_current(analysis_dir, fingerprint):
        inputs = collect.collect(plan, ws / "inputs", req.global_cache, req.us_cache)
        if analysis_dir.exists():
            shutil.rmtree(analysis_dir)
        pipeline.run(inputs, analysis_dir, pipeline.Params(label=req.label, crop_to_region=True))
        (analysis_dir / "plan.json").write_text(json.dumps(fingerprint, indent=2), encoding="utf-8")
    else:
        print("Analysis is up to date for this field: rendering the selected dates only")
    summary = views.render(req, plan, analysis_dir, ws / "view")
    print(f"Done in {time.time() - t0:.0f} s. Outputs in {ws}")
    for line in summary["advice"]:
        print(f"  - {line}")
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("request", type=Path, help="path of the request JSON")
    ap.add_argument("--refresh", action="store_true", help="redo the analysis even if it is up to date")
    args = ap.parse_args(argv)
    run(args.request, refresh=args.refresh)
    return 0


if __name__ == "__main__":
    sys.exit(main())
