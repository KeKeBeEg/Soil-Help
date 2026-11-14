# Integration guide: Earth Engine → RUSLE pipeline → outputs

> **Superseded (2026-11-14).** Data ingestion is now part of the backend (`python run.py request.json`,
> [BACKEND.md](BACKEND.md)), which pulls everything itself; there is no separate exporter any more.
> The input-file rules below still hold for the analysis pipeline, which now also accepts
> `soil_texture`, `k_factor`, `soil_tolerance`, `soil_depth`, `ksat` and `landcover`
> (see [src/dataanal/README.md](../src/dataanal/README.md)).

This is the contract between the Earth Engine code (which produces input GeoTIFFs) and the
soil-loss pipeline in `src/dataanal` (which turns them into output GeoTIFFs). If the input files
follow section 2, the pipeline runs on them; nothing else is shared between the two sides.

## 1. The flow

```
 Earth Engine                     a folder of GeoTIFFs                pipeline                     results
 ───────────────                  ──────────────────────              ─────────────────            ───────────────────────
 FABDEM                 ──►  dem.tif                                                               r/k/ls/c/p_factor.tif
 ERA5-Land soil_type    ──►  soil_type.tif                                                         slope.tif
 Landsat 8/9            ──►  ndvi.tif, water.tif           ──►  src.dataanal.pipeline   ──►      soil_loss.tif (+ class, monthly)
 GPM IMERG half-hourly  ──►  precip_2010.tif … precip_2015.tif   (pure numpy/rasterio,             recommended_practice.tif …
 HydroBASINS polygon    ──►  aoi.tif                             no Earth Engine)                  summary.json, 2 PNG figures
```

- **Earth Engine side** writes one folder of input GeoTIFFs, e.g. `data/<area>/inputs/`.
- **Pipeline side** is called on that folder and writes an output folder, e.g. `outputs/<area>/`:

  ```powershell
  python -m src.dataanal.pipeline --out outputs\<area> data\<area>\inputs
  ```
  or from Python:
  ```python
  from src.dataanal.pipeline import run, Params
  summary = run(["data/<area>/inputs"], "outputs/<area>", Params(label="My area", tolerance_t_ha_yr=11))
  ```
  `run` takes a list of `.tif` paths and/or folders (every `.tif` directly inside a folder is used)
  and returns the same dictionary it writes to `summary.json`.

- A working reference producer already exists: `src/dataanal_harness/gee_data.py`
  (`download_inputs(cfg)`, Python Earth Engine API via `computePixels`). It writes files that meet
  this contract, but it has not yet been run against live Earth Engine. Use it, extend it, or
  replace it; the pipeline doesn't care where the files come from.
- `src/dataanal_harness/synthetic.py` writes fake inputs in exactly this format. Open its output
  (`python -m src.dataanal_harness.harness --synthetic`, then `data/synthetic/inputs/`) to see
  valid example files.

## 2. Input files

### 2.1 How the pipeline recognises a file

Each file has a **role**. The pipeline takes it from the GeoTIFF metadata tag `RUSLE_ROLE` if
present, otherwise from the file name: `<role>.tif` or `<role>_<anything>.tif` (case-insensitive).
So `dem.tif`, `dem_fabdem.tif` and `precip_2010_01.tif` all work.

- One file each for `dem`, `soil_type`, `ndvi`, `aoi`, `water`. Two files with the same role is an error.
- Any number of `precip` files; they are joined in time.
- Required: `dem`, `soil_type`, `ndvi`, `precip`. Optional: `aoi`, `water`.
- A file whose role can't be determined is an error. Keep other files out of the folder.

### 2.2 What each file must contain

| Role | Bands | Values and units | Suggested type / no-data | Earth Engine source |
|---|---|---|---|---|
| `dem` | 1 | elevation, metres | float32, no-data -9999 | `projects/sat-io/open-datasets/FABDEM` (mosaic) |
| `soil_type` | 1 | ERA5-Land class: 0 non-land, 1 coarse, 2 medium, 3 medium fine, 4 fine, 5 very fine, 6 organic, 7 tropical organic | uint8, no-data 255 | `ECMWF/ERA5_LAND/STATIC`, band `soil_type` |
| `ndvi` | **12** (band 1 = January … band 12 = December) or 1 (annual) | real NDVI, −1…1 (not scaled integers) | float32, no-data -9999 | Landsat 8/9 C2 L2 (see 2.4) |
| `precip` | one per time step | rain **rate in mm/h** | float32, no-data -9999 | `NASA/GPM_L3/IMERG_V07`, band `precipitation` |
| `aoi` | 1 | 1 = inside the study area, 0 = outside | uint8, no-data 255 | HydroBASINS polygon painted to 1/0 |
| `water` | 1 | 1 = water (excluded), 0 = land | uint8, no-data 255 | Landsat MNDWI > 0 |

Rules that apply to every file:

- **Band order matters, band names don't.** Earth Engine exports may drop band names, so the
  pipeline never reads them.
- **Set a no-data value.** The pipeline treats no-data pixels as missing. Without one, masked
  pixels get whatever value the export writes and are taken as real data (e.g. NDVI 0, elevation 0).
  In Earth Engine: `image.unmask(-9999, false)` and `formatOptions: {noData: -9999}` on the export
  (use 255 for uint8 files).
- **Data types are flexible.** Everything is read as float32; the types above just keep files small.

### 2.3 Grids and coordinate systems

- **The DEM defines the output grid.** Every other input is resampled onto it: bilinear for
  `ndvi` and `precip`-derived fields, nearest-neighbour for `soil_type`, `aoi`, `water`.
- **Recommended:** export `dem`, `ndvi`, `water`, `aoi` and `soil_type` on one identical grid. Use
  the local UTM zone at 30 m, with the same `crs`, the same `crsTransform` (origin snapped to a
  multiple of 30 m) and the same region. Then nothing is resampled except rainfall.
  Example `crsTransform`: `[30, 0, x0, 0, -30, y0]` with `crs: 'EPSG:32637'` (UTM 37N, Ethiopia).
- A DEM in lat/lon (EPSG:4326) also works; the pipeline reprojects it to its UTM zone at 30 m.
- Cover the study area plus a margin of at least ~600 m, so slopes and water flow near the
  boundary are computed from real terrain.
- **Rainfall stays on IMERG's native 0.1° grid** (`crs: 'EPSG:4326'`, `crsTransform: [0.1, 0, -180, 0, -0.1, 90]`),
  covering the study area plus **at least one 0.1° pixel of margin** on every side for interpolation.
  Don't resample IMERG to 30 m in Earth Engine: storm calculations run once per rainfall pixel,
  so 30 m rain would be ~100,000× slower and no more accurate.

### 2.4 Layer recipes (what the reference producer does)

- **dem:** `ee.ImageCollection(FABDEM).map(img => img.resample('bilinear')).mosaic()`, first band.
- **soil_type:** `ee.Image('ECMWF/ERA5_LAND/STATIC').select('soil_type')`, default (nearest) resampling.
- **ndvi:** Landsat 8 + 9 Collection 2 Level 2 (`LANDSAT/LC08/C02/T1_L2`, `LANDSAT/LC09/C02/T1_L2`),
  filtered to the study area and **2013-04-01 … 2016-01-01**. Landsat 8 starts in April 2013 and
  Landsat 9 in 2021, so this is all the Landsat data inside 2010–2015.
  - Cloud mask: keep pixels where `QA_PIXEL & 0b111111 == 0` (fill, dilated cloud, cirrus, cloud, shadow, snow).
  - Reflectance: `SR_B* × 0.0000275 − 0.2`; NDVI = `normalizedDifference(['SR_B5', 'SR_B4'])`.
  - Band *m* = median NDVI of all clear images from calendar month *m* (all years pooled). A month
    with no clear image is filled with the all-period median.
  - Optional tag `TIME_RANGE` (e.g. `2013-04 to 2015-12`); it is printed on the figures.
- **water:** all-period median MNDWI = `normalizedDifference(['SR_B3', 'SR_B6'])` > 0.
- **aoi:** `ee.Image(0).paint(basinFeatureCollection, 1)` (HydroBASINS `WWF/HydroSHEDS/v1/Basins/hybas_<level>`).
- **precip:** see 2.5.

### 2.5 Rainfall files (the tricky one)

The pipeline computes rainfall erosivity storm by storm, so it needs the full half-hourly series,
2010–2015 ≈ 105,000 time steps. Three limits shape the files:

1. **A GeoTIFF holds at most 65,535 bands**, so rainfall must be split into several files.
2. **An Earth Engine image holds at most 5,000 bands** (`toBands()` fails above that), so a year
   (17,520 half-hours) can't be one Earth Engine image. **One file per month** (≤ 1,488 bands)
   works everywhere; one file per year also works if written from Python (as the reference producer does).
3. **The time of each band is not stored in the bands.** Each file needs two metadata tags:

| Tag | Example | Meaning |
|---|---|---|
| `TIME_START` | `2010-01-01T00:00:00Z` | UTC start time of band 1 |
| `TIME_STEP_MINUTES` | `30` | spacing between bands |

Band *i* (1-based) is read as time `TIME_START + (i − 1) × TIME_STEP_MINUTES`. So:

- **Bands must be contiguous and in time order.** Sort the collection by `system:time_start`. Check
  that each chunk has exactly the expected number of images (48 × days). If an image is missing,
  every later band in that file is assigned the wrong time, so insert a no-data band for the gap instead.
- All `precip` files must share **one grid** and **one time step**. Overlaps between files are
  allowed (duplicates are dropped).
- **Supply whole calendar years.** The average is taken over the calendar years present, so a
  half-covered year lowers R. The harness warns when a year is < 95 % covered.
- No-data values count as no rain and are reported as `missing_pct` in `summary.json`.

Earth Engine's export tasks can't write custom tags. After downloading, add them in Python
(`rasterio` can edit tags in place):

```python
import rasterio

with rasterio.open("precip_2010_01.tif", "r+") as f:
    f.update_tags(RUSLE_ROLE="precip", TIME_START="2010-01-01T00:00:00Z", TIME_STEP_MINUTES=30)
```

Example monthly export (Code Editor JavaScript):

```js
var start = ee.Date('2010-01-01'), end = start.advance(1, 'month');
var rain = ee.ImageCollection('NASA/GPM_L3/IMERG_V07')
    .filterDate(start, end).select('precipitation').sort('system:time_start');
print('images (expect 1488):', rain.size());
Export.image.toDrive({
  image: rain.toBands().toFloat().unmask(-9999, false),
  description: 'precip_2010_01', fileNamePrefix: 'precip_2010_01',
  crs: 'EPSG:4326', crsTransform: [0.1, 0, -180, 0, -0.1, 90],
  region: studyAreaBoundsPlusMargin, formatOptions: {noData: -9999}, maxPixels: 1e9
});
```

### 2.6 Export checklist

- [ ] File names start with the role (`dem`, `soil_type`, `ndvi`, `precip_…`, `aoi`, `water`), or carry a `RUSLE_ROLE` tag.
- [ ] If Earth Engine split an export into tiles (`…-0000000000-0000000000.tif`), merge them into one file first.
- [ ] No-data value set on every file; NDVI as real values; rain in mm/h.
- [ ] `ndvi` has 12 bands in January → December order.
- [ ] 30 m layers share one grid; the study area has a margin; rainfall stays at 0.1° with a 1-pixel margin.
- [ ] Each `precip` file has `TIME_START` + `TIME_STEP_MINUTES` and contiguous bands; whole years supplied.
- [ ] Test: `python -m src.dataanal.pipeline --out outputs\test <your folder>`. A bad file produces
      a clear error (unknown role, missing input, missing tag, grid mismatch).

## 3. Outputs

All rasters are on the DEM grid (same CRS, transform and size), GeoTIFF with deflate compression.
Pixels outside `aoi`, on `water`, or where any input is missing are no-data. Each file carries
`DESCRIPTION` and `UNITS` tags.

| File | Type / no-data | Units | Content |
|---|---|---|---|
| `r_factor.tif` | float32 / NaN | MJ mm ha⁻¹ h⁻¹ yr⁻¹ | R, rainfall erosivity (mean annual storm EI30) |
| `k_factor.tif` | float32 / NaN | t ha h ha⁻¹ MJ⁻¹ mm⁻¹ | K, soil erodibility |
| `ls_factor.tif` | float32 / NaN | – | LS, slope length and steepness |
| `c_factor.tif` | float32 / NaN | – | C, cover management (each month weighted by its rainfall erosivity) |
| `p_factor.tif` | float32 / NaN | – | P, support practice (constant; default 1) |
| `slope.tif` | float32 / NaN | degrees | terrain slope |
| `soil_loss.tif` | float32 / NaN | t ha⁻¹ yr⁻¹ | **A = R·K·LS·C·P, average annual soil loss** |
| `soil_loss_class.tif` | uint8 / 0 | class | 1 <2, 2 2–5, 3 5–10, 4 10–20, 5 20–50, 6 >50 t ha⁻¹ yr⁻¹ (legend in `CLASS_*` tags) |
| `soil_loss_monthly.tif` | float32 / NaN, **12 bands** Jan…Dec | t ha⁻¹ month⁻¹ | soil loss by calendar month; the bands add up to `soil_loss` |
| `recommended_practice.tif` | uint8 / 255 | code | 0 no change needed, 1 contour farming, 2 strip cropping, 3 terraces or soil/stone bunds, 4 permanent vegetation (legend in `CODE_*` tags, tolerance in `TOLERANCE_T_HA_YR`) |
| `soil_loss_with_practice.tif` | float32 / NaN | t ha⁻¹ yr⁻¹ | soil loss if the recommended practice is applied |
| `max_cover_factor.tif` | float32 / NaN | – | highest C that keeps loss under the tolerance; where `c_factor` is higher, cover must improve |
| `upslope_area.tif` | float32 / NaN | ha | area draining through each cell |
| `stream_power_index.tif` | float32 / NaN | m | gully risk / natural waterways (high = concentrated runoff) |

Also written:

- `summary.json`, with these keys:
  - `study_area`: area, grid, mean slope.
  - `period`: rainfall years and the vegetation date range.
  - `inputs`: files used per role.
  - `soil_loss_t_ha_yr`: mean, median, p90, p99, max.
  - `total_soil_loss_Mt_yr`.
  - `severity_area_pct`.
  - `tolerance`: area above T, and soil loss with the recommended practices.
  - `recommended_practice_area_pct` and `monthly_soil_loss_share_pct`.
  - `factor_mean`.
  - `rainfall`: coverage, missing values, storms per year, R by year and month, empirical cross-check.
  - `k_sensitivity_mean_A_t_ha_yr` and `k_table`.
  - `outputs`: the list of files written.
- `rusle_maps.png` (factor maps, soil-loss classes) and `rainfall_erosivity.png` (R by month and year).

The tolerance (default 11 t ha⁻¹ yr⁻¹) and P can be changed: `--tolerance`, `--p-factor`, or
`Params(tolerance_t_ha_yr=…, p_factor=…)`. The practice map is a screening guide that assumes
cultivated land; there is no land-use input.

## 4. Testing the integration

| Command | What it checks |
|---|---|
| `python -m pytest` | formula tests + the full pipeline on synthetic files |
| `python -m src.dataanal_harness.harness --synthetic` | full run on synthetic inputs, with an input report and sanity checks |
| `python -m src.dataanal.pipeline --out outputs\test <folder>` | the pipeline on any folder of real exports |
| `python -m src.dataanal_harness.harness` | reference Earth Engine download + run (needs `earthengine authenticate` and `EE_PROJECT`) |

Use a Python 3.11 environment with `pip install -r requirements.txt`, and run from the repository root.
Detailed methods (formulas and references) are in `src/dataanal/README.md`.
