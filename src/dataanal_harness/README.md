# dataanal_harness

Test and debug harness for `src/dataanal`. It produces the pipeline's input GeoTIFFs (from Google
Earth Engine, or synthetic), prints a report of every input, runs the pipeline and checks the result.

```powershell
# from the repo root, in the project's Python 3.11 environment (pip install -r requirements.txt)
python -m src.dataanal_harness.harness --synthetic       # offline: synthetic inputs, no Earth Engine
python -m src.dataanal_harness.harness                   # Earth Engine inputs (cached) -> pipeline
python -m src.dataanal_harness.harness --download-only   # only fetch the input GeoTIFFs
python -m src.dataanal_harness.harness --refresh         # re-download even if cached
```

Earth Engine setup (once): `earthengine authenticate`, then `$env:EE_PROJECT = "spaceapps-514713"`
(the team project; your account needs access to it) or another Cloud project registered for Earth Engine.

| File | Role |
|---|---|
| `config.py` | Study area (HydroBASINS sub-basin around a point, or a bbox), years, Landsat window. Default: Gilgel Abay, Ethiopia, 2010–2015. |
| `gee_data.py` | Earth Engine pulls → `data/<aoi>/inputs/` (`dem`, `soil_type`, `aoi`, `ndvi`, `water`, `precip_<year>`), diagnostics → `data/<aoi>/qa/`. |
| `synthetic.py` | Synthetic inputs in the same format, on mixed grids (30 m UTM + 0.1° lat/lon), with storm-like rain. |
| `harness.py` | CLI: input report → pipeline → sanity checks (rainfall coverage, R vs Renard–Freimund, factor ranges, and that the written GeoTIFFs are consistent: A = R·K·LS·C·P and the monthly bands add up to A). Exit code 1 if a check warns. |

| Earth Engine dataset | ID |
|---|---|
| IMERG V07 half-hourly | `NASA/GPM_L3/IMERG_V07` (`precipitation`, mm/h) |
| ERA5-Land static | `ECMWF/ERA5_LAND/STATIC` (`soil_type`) |
| Landsat 8/9 C2 L2 | `LANDSAT/LC08/C02/T1_L2`, `LANDSAT/LC09/C02/T1_L2` |
| FABDEM | `projects/sat-io/open-datasets/FABDEM` (CC BY-NC-SA 4.0) |
| HydroBASINS | `WWF/HydroSHEDS/v1/Basins/hybas_<level>` |

Landsat 8 began science operations in April 2013 and Landsat 9 launched in 2021, so for 2010–2015
the NDVI composites cover 2013-04 to 2015-12 and are applied to the whole rainfall period.
