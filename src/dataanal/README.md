# Data Alma

## ERA5-Land

### Volumetric Water Content (VWC)





# Data Isleme

## RUSLE Equation, Average Annual Soil Loss

A = R * K * L_s * C * P
A = Average annual soil loss, R = Rainfall erosivity factor, K = Soil erodibility factor, L_s = Slope length/steepness factor, C = Cover management factor, P = Support practice factor

### R, Rainfall erosivity factor

###

## Pipeline (`src/dataanal`)

GeoTIFFs in, GeoTIFFs out; no Earth Engine code here. The inputs come from
`src/dataanal_harness` (Earth Engine downloader + synthetic data + checks) or from anywhere else
that follows the input contract below. The full file contract for whoever produces the inputs
(tags, band order, grids, Earth Engine export limits) is in [docs/INTEGRATION.md](../../docs/INTEGRATION.md).

```powershell
# from the repo root, in the project's Python 3.11 environment (pip install -r requirements.txt)
python -m src.dataanal.pipeline --out outputs\my_area data\my_area\inputs   # a folder of .tif files
python -m src.dataanal.pipeline --out outputs\my_area dem.tif soil_type.tif ndvi.tif precip_2010.tif precip_2011.tif aoi.tif
python -m pytest                                                     # unit + end-to-end tests
```

From Python: `from src.dataanal.pipeline import run, Params; run([paths...], "outputs/x", Params(tolerance_t_ha_yr=11))`.

### Inputs

A file's role comes from its `RUSLE_ROLE` tag, or else its name: `<role>.tif` or `<role>_<anything>.tif`.
Inputs may use different grids and CRSs; all are resampled onto the DEM grid (bilinear, or nearest for classes and masks).

| Role | Content |
|---|---|
| `dem` | Elevation (m), 1 band. Sets the output grid; a lat/lon DEM is reprojected to its UTM zone at 30 m. |
| `soil_type` | ERA5-Land `soil_type` class (0 = non-land, 1–7), 1 band. |
| `ndvi` | NDVI, 12 bands (Jan..Dec monthly composites) or 1 band (annual). |
| `precip` | Rain rate (mm/h), one band per time step; tags `TIME_START` (ISO 8601 UTC) and `TIME_STEP_MINUTES`. One file per year is fine (a GeoTIFF holds at most 65,535 bands). |
| `aoi` | Optional. Study-area mask, 1 = inside. |
| `water` | Optional. Water mask, 1 = water (excluded). |

### Outputs (`--out` folder)

| File | Content |
|---|---|
| `r_factor`, `k_factor`, `ls_factor`, `c_factor`, `p_factor` | The RUSLE factors. |
| `slope` | Terrain slope (degrees). |
| `soil_loss` | **A, average annual soil loss** (t ha⁻¹ yr⁻¹). |
| `soil_loss_class` | Severity class 1–6 (<2, 2–5, 5–10, 10–20, 20–50, >50 t ha⁻¹ yr⁻¹); legend in the file's tags. |
| `soil_loss_monthly` | 12 bands, soil loss per calendar month (they add up to `soil_loss`): when the soil most needs cover. |
| `recommended_practice` | Least intensive practice bringing loss under the tolerance T (default 11 t ha⁻¹ yr⁻¹): 0 none needed, 1 contour farming, 2 strip cropping, 3 terraces / soil-stone bunds, 4 permanent vegetation (even terraces are not enough). |
| `soil_loss_with_practice` | Soil loss if that practice is applied (P values by slope class, Shin 1999). |
| `max_cover_factor` | Highest C that keeps loss under T. Where `c_factor` is higher, ground cover must improve. |
| `upslope_area` | Area draining through each cell (ha). |
| `stream_power_index` | Upslope area per width × tan(slope): high values mark gully risk and natural waterways. |

Plus `summary.json` (area statistics, tolerance and practice breakdowns, rainfall checks, K table and
K sensitivity) and two figures. The practice map is a screening guide that assumes cultivated land;
there is no land-use input, so check it against what is actually grown on each field.

### Method

| Factor | Method |
|---|---|
| R | Storm EI30 (AH703): storms split by 6 h with < 1.27 mm, erosive if ≥ 12.7 mm, e = 0.29[1 − 0.72 exp(−0.05 i)], I30 = max half-hour rate. Renard & Freimund (1994) from annual rain as a cross-check. |
| K | FAO texture class → class-centroid sand/silt/clay → AH703 Dg equation (Römkens et al. 1987). Williams (EPIC) and the K range within each class are reported as sensitivity. |
| LS | Depression-filled DEM → multiple-flow-direction accumulation (pysheds) → Moore & Burch (1986), upslope length capped at 300 m. |
| C | Monthly NDVI → C = exp(−2·NDVI/(1 − NDVI)) (van der Knijff 2000), averaged per pixel with that pixel's monthly R as weight. |
| P | `--p-factor`, default 1.0 (no conservation-practice data; an upper bound). |

A = R · K · LS · C · P in t ha⁻¹ yr⁻¹.