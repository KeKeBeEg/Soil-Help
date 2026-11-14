# dataanal_harness

Test and debug harness for `src/dataanal`. It makes synthetic input GeoTIFFs (or takes any folder
of real ones), prints a report of every input, runs the analysis pipeline and checks the result.
Real inputs are pulled by the backend (`python run.py request.json`, see [docs/BACKEND.md](../../docs/BACKEND.md)).

```powershell
# from the repo root, in the project's Python 3.11 environment (pip install -r requirements.txt)
python -m src.dataanal_harness.harness                                # synthetic inputs, minimum set
python -m src.dataanal_harness.harness --extras                       # + soil texture, depth, land cover
python -m src.dataanal_harness.harness --inputs runs\us_yancey_helene\inputs   # a backend run's inputs
```

| File | Role |
|---|---|
| `config.py` | Where synthetic inputs and outputs go (`data/synthetic/`, `outputs/synthetic/`). |
| `synthetic.py` | Synthetic inputs in the analysis input format, on mixed grids (30 m UTM + 0.1° lat/lon), with storm-like rain; `extras=True` adds `soil_texture`, `soil_depth` and a WorldCover-coded `landcover`. |
| `harness.py` | CLI: input report → pipeline → sanity checks (rainfall coverage, R vs Renard–Freimund, factor ranges, A = R·K·LS·C·P and monthly bands adding up to A in the written GeoTIFFs). Exit code 1 if a check warns. |
