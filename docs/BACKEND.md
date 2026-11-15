# Field soil-loss and landslide backend

One command takes a farm field and a date range and produces GeoTIFFs for a map plus graphs:
**soil loss** (RUSLE) and **shallow-landslide risk** (infinite slope + wetness), side by side.

```powershell
python run.py cases\us_yancey_helene.json            # US field (USGS/USDA data)
python run.py cases\ethiopia_gilgel_abay.json        # rest of the world (global data)
python run.py my_request.json --refresh              # redo the analysis even if it is up to date
```

The backend decides from the polygon which data to use. A field strictly inside the contiguous
US (lower 48 + DC) takes the **US path**: 10 m USGS 3DEP elevation, SSURGO soil survey,
USDA Cropland Data Layer. Any other field takes the **global path**: 30 m FABDEM, SoilGrids,
ISRIC depth to bedrock, ESA WorldCover. Rain (NASA GPM IMERG) and vegetation (Landsat 8/9)
are the same on both paths. The analysis code is identical; only the inputs and the reporting
units differ.

---

## 1. Setup (once per machine)

```powershell
# Python 3.11 environment outside the repo, e.g. c:\.code\Py\Hackathon-NASA2026
pip install -r requirements.txt
earthengine authenticate          # opens a browser
# optional: $env:EE_PROJECT = "<your Earth Engine Cloud project>"   (default: spaceapps-514713)
python -m pytest                  # 80+ tests, no Earth Engine needed, ~5 min
```

Runs that find everything in the caches need no network and no Earth Engine login.

---

## 2. The request

```json
{
  "label": "Pasture near Burnsville, NC",
  "polygon": {"1": [-82.3437, 35.9699], "2": [-82.3420, 35.9700], "3": [-82.3417, 35.9690],
              "4": [-82.3426, 35.9686], "5": [-82.3438, 35.9689]},
  "t_range": ["2024-09-20", "2024-10-05"],
  "us_cache": "../cache/us",
  "global_cache": "../cache/global",
  "working_save": "../runs/us_yancey_helene"
}
```

| Key | Meaning |
|---|---|
| `polygon` | Field vertices as (x, y) = (longitude, latitude), WGS84 degrees, ordered by vertex number ("10" after "9"). A list of vertices or `"(x, y)"` strings also work. The ring closes itself. |
| `t_range` | First and last day (inclusive) **to show**. It changes only the views (section 4). Allowed: 2001-01-01 to two days ago. |
| `us_cache` | Folder for US-only data (3DEP, SSURGO, CDL). |
| `global_cache` | Folder for worldwide data (IMERG, Landsat, HydroBASINS, FABDEM, SoilGrids, depth to bedrock, WorldCover). The US path also reads IMERG and Landsat from here, and fills gaps in SSURGO from SoilGrids and depth to bedrock. |
| `working_save` | Folder for everything this request produces. |
| `label` | Optional name for figures (default: the `working_save` folder name). |

Relative paths are resolved against the request file's folder. Ready-made requests are in `cases/`.

---

## 3. What happens

```
request.json ─► path: CONUS? ─► plan grids ─► pull missing data ─► analysis ─► views
                 US / global      (plan.py)     into caches         (fixed      (t_range,
                 (region.py)                    (src/ingest)        period)      the field)
```

1. **Path.** All vertices inside the bundled outline of the lower 48 states
   (`src/backend/data/conus.geojson`, from Census TIGER) means US; anything else (Alaska, Hawaii,
   Puerto Rico, a field across the border) is global.
2. **Grids** (`src/backend/plan.py`), in the field's UTM zone, snapped to whole multiples of the
   resolution so the same field always hits the same cache files:

   | Grid | Covers | Used for |
   |---|---|---|
   | DEM grid | the HydroBASINS level-12 sub-basin(s) under the field, clipped to 5 km around it, never less than 1 km around it | elevation, slope, flow accumulation: slope length and wetness need all the land that drains into the field |
   | field grid | the field + 500 m | soil, land cover, NDVI; all analysis outputs |
   | rain grid | the 0.1° IMERG cells over the field + 1 cell on every side | rainfall |

   Resolution: 10 m on the US path (3DEP's native resolution), 30 m on the global path.
3. **Data** (`src/ingest`). Every layer is looked up in its cache and pulled only if missing:

   | Layer | US path | Global path | Cache |
   |---|---|---|---|
   | Elevation | USGS 3DEP 1/3″ (`USGS/3DEP/10m_collection`) | FABDEM 30 m (`projects/sat-io/open-datasets/FABDEM`) | us / global |
   | Soil erodibility K | SSURGO `kwfact` → SI | Williams equation from SoilGrids texture + organic carbon | us / global |
   | Soil-loss tolerance T | SSURGO `tfact` (1–5 t/ac/yr) | 11 t/ha/yr | us / – |
   | Soil texture | SSURGO 0–30 cm (gaps: SoilGrids) | SoilGrids 2.0, 0–30 cm | us + global / global |
   | Soil depth | SSURGO restrictive layer (gaps: ISRIC) | ISRIC depth to bedrock (BDRICM, Shangguan et al. 2017) | us + global / global |
   | Conductivity Ksat | SSURGO `ksat_r` | from USDA texture class (Clapp & Hornberger 1978) | us / – |
   | Land cover | USDA Cropland Data Layer 2025 | ESA WorldCover 2021 | us / global |
   | Rain | NASA GPM IMERG V07 half-hourly | same | global |
   | Vegetation (NDVI), water | Landsat 8/9 C2 L2 monthly medians | same | global |
   | Vegetation by date (NDVI) | NASA HLS L30 + S30 (Landsat + Sentinel-2) half-month medians, optional | same | global |

   SSURGO comes from the gNATSGO 10 m map-unit raster (Earth Engine community catalog) joined
   with map-unit properties from USDA Soil Data Access (SQL over HTTPS). The depth-to-bedrock
   file is read over HTTP from ISRIC (only the window around the field). Everything else comes
   from Earth Engine through `computePixels` (direct downloads, no Drive exports).
4. **Analysis** (`src/dataanal/pipeline.py`) over the **fixed period 2016–2025**: rainfall
   erosivity, cover and the landslide rain record all use these ten years, whatever `t_range`
   says. The period and the analysis version live in `analysis_version.json` (repository
   root); bump `version` whenever the period, a model, the grids or the outputs change.
   Results go to `working_save/analysis/`, stamped with the plan (`plan.json`). A later
   request for the same field, data path, period and version skips the plan, data and
   analysis steps, unless the saved analysis used a default for a layer that could not be
   pulled (e.g. ISRIC timed out): that analysis is redone so the pull is retried.
5. **Views** (`src/backend/views.py`) for `t_range`, over the field only: see section 4. They
   are rendered from `analysis/` alone; only dates outside 2016–2025 read rain from the cache.

---

## 4. Outputs

```
working_save/
  request.json      copy of the request
  inputs/           the analysis inputs (GeoTIFF; rain as precip_<year>.npz, since GDAL needs ~15 s per
                    17,520-band GeoTIFF); python -m src.dataanal.pipeline --out X <this folder> reproduces the analysis
  analysis/         long-term results over the field + 500 m, summary.json, rusle_maps.png, rainfall_erosivity.png, landslide_maps.png,
                    plan.json, rain_daily.npz, <range layer>_by_year.tif and daily_series.csv (see below)
  view/             results for the selected dates over the field only, field_summary.json, field_maps.png, timeline.png
```

**Time slider files (`analysis/`, 2016–2025).**

| File | Content |
|---|---|
| `rain_daily.npz` | Day by day on the IMERG cells: rain (mm), EI30 and number of the erosive storms starting that day. Storms are found once on the continuous record, so no range edge cuts a storm |
| `soil_loss_window_by_year.tif` | One band per year (band names 2016 … 2025), tag `COMBINE=sum` |
| `landslide_peak_class_by_year.tif`, `landslide_peak_probability_by_year.tif`, `rain_window_max_by_year.tif` | One band per year, tag `COMBINE=max` |
| `daily_series.csv` | The field's daily numbers: rain, 3-day mean rain, soil loss (t/ha), erosive storms, share of the field at each alert level |

Any run of whole years is the band-wise sum (or max) of its yearly bands, and equals what
`view/` shows for those dates (checked to float32 rounding on both demo cases and in
`tests/test_backend.py`). A storm counts on the day it starts, with all its rain.

**`view/` (what the app shows).** All rasters cover the field's bounding box, no-data outside
the field polygon, in the analysis CRS (UTM), SI units:

| File | Content |
|---|---|
| `soil_loss.tif` | Average yearly soil loss 2016–2025 (t ha⁻¹ yr⁻¹) |
| `soil_loss_class.tif` | 1 very low (<2) … 6 severe (>50 t ha⁻¹ yr⁻¹) |
| `soil_loss_monthly.tif` | 12 bands, soil loss per calendar month |
| `recommended_practice.tif`, `soil_loss_with_practice.tif` | Least effort that keeps soil loss within tolerance (0 none, 1 contour farming, 2 strip cropping, 3 terraces/bunds, 4 permanent cover), and the loss with it |
| `max_cover_factor.tif`, `soil_tolerance.tif`, `erodibility_index.tif`, `slope.tif` | Cover needed, tolerance, R·K·LS/T, slope (°) |
| `soil_loss_window.tif` | **Soil loss from the storms in `t_range`** (t ha⁻¹) |
| `critical_rain.tif` | 3-day mean rain that makes the slope fail (mm/day; 9999 = never) |
| `landslide_rain_quantiles.tif` | 9 bands: rain at which the failure probability reaches 10 … 90 % |
| `stability_class.tif` | 1 stable even when saturated, 2 rain-dependent, 3 unstable even when dry |
| `landslide_peak_class.tif` | **Worst landslide alert in `t_range`**: 0 low, 1 watch, 2 warning, 3 danger |
| `landslide_peak_probability.tif`, `rain_window_max.tif` | Highest failure probability and wettest 3-day rain in `t_range` |
| `landslide_danger_days.tif`, `landslide_class_wettest.tif` | Danger days per year, and the alert level in the wettest 3 days of 2016–2025 |

`field_summary.json` holds the field's numbers in the region's units (US: tons/acre and acres;
elsewhere: t/ha and ha): long-term soil loss vs tolerance, practice shares, the months with the most
erosion, landslide stability shares and rain thresholds, the selected dates' rain, soil loss and
worst alert, a highly-erodible-land screening on the US path, and plain-language advice.
Practices on the US path carry their NRCS conservation practice codes (Contour Farming 330,
Stripcropping 585, Terrace 600, Conservation Cover 327, Cover Crop 340).

**Graphs.** `view/field_maps.png` (six panels: yearly soil loss, soil loss in the dates, practice,
rain that triggers failure, worst alert in the dates, the field's numbers) and `view/timeline.png`
(daily rain against the field's landslide thresholds, share of the field at each alert level,
daily soil loss).

---

## 5. Caches

Both cache folders are ours; their layout:

```
global_cache/
  imerg/<grid>/<YYYYMMDD>.npz           half-months of IMERG (fixed boundaries: 1st and 16th)
  landsat_ndvi/<grid>_<years>.tif       12 monthly NDVI medians, annual median, water, clear-obs count
  hls_ndvi/<grid>_<year span>.tif       HLS NDVI, one band per half-month, one file per calendar year
  hydrobasins/<field hash>.json         sub-basins under a field
  fabdem/ soilgrids/ bedrock_depth/ worldcover/   <grid>.tif
us_cache/
  3dep/ cdl_<year>/                     <grid>.tif
  ssurgo/<grid>.tif                     gNATSGO map-unit keys
  ssurgo/<grid>_properties.tif / .csv   SSURGO properties per cell / per map unit
```

`<grid>` names the CRS, resolution, origin and size. Files are written under a temporary name and
renamed when complete, so an interrupted run never leaves a broken cache file. Rain from the last
two days is never cached (IMERG still fills it in). Delete a file or folder to pull it again.

---

## 6. Models in one page

**Soil loss**, A = R · K · LS · C · P (t ha⁻¹ yr⁻¹): R storm-by-storm EI30 from half-hourly IMERG
(complete years only); K as in section 3; LS Moore & Burch from multiple-flow-direction catchment
area (capped at 300 m); C = exp(−2·NDVI/(1 − NDVI)) per month, weighted by that month's R, capped
for permanent cover by land-cover group (forest 0.003, grass/pasture 0.05, shrub/orchard 0.1), since
NDVI reads leafless forest and dormant pasture as bare soil; P = 1.
Soil loss for `t_range` uses the storms that started in it and the same K, LS, P. Each storm's C is
that of its own half-month from HLS NDVI (`analysis/c_factor_halfmonth.tif`, same formula and caps)
where that half-month had a clear view, else the calendar month's C.

**Landslide**, infinite slope with steady-state wetness (Montgomery & Dietrich 1994; SINMAP form):
W = min(1, q·a / (T·sin θ)) with q = 0.7 × 3-day mean rain, T = 100 × Ksat × depth;
FS = [c′ + c_r + (γ − W·γw) z cos²θ tan φ′] / (γ z sin θ cos θ). Solving FS = 1 gives, per cell,
the rain that triggers failure. Repeating that for 100 parameter sets (φ′ ± 3°, cohesions across
their table ranges, depth ± 30 %, T ÷/× 3) gives the rain at which the failure probability
reaches 10 … 90 %. Alert levels: watch ≥ 10 %, warning ≥ 30 %, danger ≥ 60 %. Any day's level is
that day's 3-day rain compared with these thresholds. Details and the lookup tables:
[src/dataanal/README.md](../src/dataanal/README.md), `src/dataanal/soil_params.py`.

---

## 7. Demo cases and run times

| | `cases/us_yancey_helene.json` | `cases/ethiopia_gilgel_abay.json` |
|---|---|---|
| Field | 2.3 ha (5.6 acres) of pasture on 15–30° slopes near Burnsville, NC | 1.9 ha of cropland on 8–20° slopes, Gilgel Abay catchment |
| Path, resolution | US, 10 m | global, 30 m |
| Dates shown | 2024-09-20 … 10-05 (Hurricane Helene) | 2024-07-01 … 08-31 (main rainy season) |
| Long-term soil loss | 7.2 tons/acre/yr; above the SSURGO tolerance on 67 % of the field; erodibility index ≥ 8 everywhere (likely highly erodible) | 4.5 t/ha/yr; within tolerance on 97 % of the field |
| When erosion happens | Dec–Feb (dormant pasture) | Apr–Jun (bare fields at planting) |
| In the selected dates | 204 mm of rain, 0.64 tons/acre lost in one storm | 525 mm of rain, 0.29 t/ha lost |
| Landslide | 16 % of the field rain-dependent; weakest spot reaches watch at 77 mm/day (3-day mean). IMERG's wettest 3 days during Helene: 64 mm/day, so the alert stayed *low* | stable even when saturated (clay soil, gentle slopes) |

| Run | Time |
|---|---|
| New field, nothing cached | ~3–5 min (10 years of half-hourly IMERG ≈ 240 requests, Landsat composites, terrain, soil); ISRIC's depth-to-bedrock server can add minutes or time out (then the run continues with the 1 m default, caches nothing for it, notes it in `field_summary.json` → `defaults_used`, and the next run retries) |
| Same field, data cached, analysis redone (`--refresh`) | 20–25 s |
| Same field, only `t_range` changed (inside 2016–2025) | ~2 s, no Earth Engine, no cache |

The Helene result is the model's main limitation in one picture: IMERG averages rain over ~10 km
cells and misses the mountain peaks that triggered thousands of landslides, and the thresholds are
uncalibrated. Calibrating against the USGS Helene inventory is the obvious next step.

---

## 8. Limits

- Edge cases are out of scope (hackathon): fields crossing the US border take the global path,
  huge polygons are not guarded against, and SSURGO gaps are filled from global data.
- The landslide model is uncalibrated: soil strength and root cohesion come from lookup tables, and
  T includes a fixed macropore factor. Treat results as relative risk until checked against
  mapped landslides (e.g. the USGS Hurricane Helene inventory for the US case).
- IMERG averages rain over ~10 km cells and smooths storm peaks, so R and the landslide rain are
  likely underestimated, and a 2 ha field sees the same rain everywhere.
- RUSLE covers sheet and rill erosion only; P = 1 (existing practices unknown).
- WorldCover is a 2021 map and CDL is 2025; land cover is treated as fixed over the period.
- FABDEM is CC BY-NC-SA 4.0 (non-commercial): "FABDEM is produced using Copernicus WorldDEM-30 © DLR e.V."
