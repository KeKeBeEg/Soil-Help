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

GeoTIFFs in, GeoTIFFs out; no Earth Engine code here. It computes soil loss (RUSLE) and
shallow-landslide risk side by side. The backend (`python run.py request.json`, see
[docs/BACKEND.md](../../docs/BACKEND.md)) pulls the inputs and calls it; `src/dataanal_harness`
makes synthetic inputs for testing.

```powershell
# from the repo root, in the project's Python 3.11 environment (pip install -r requirements.txt)
python -m src.dataanal.pipeline --out outputs\my_area runs\my_field\inputs   # a folder of .tif files
python -m pytest                                                            # unit + end-to-end tests
```

From Python: `from src.dataanal.pipeline import run, Params; run([paths...], "outputs/x", Params(tolerance_t_ha_yr=11))`.

### Inputs

A file's role comes from its `RUSLE_ROLE` tag, or else its name: `<role>.tif` or `<role>_<anything>.tif`.
Inputs may use different grids and CRSs; all are resampled onto the DEM grid (bilinear, or nearest for classes and masks).
Optional inputs replace a default pixel by pixel; a NaN falls through to the next source.

| Role | Content | If missing |
|---|---|---|
| `dem` | Elevation (m), 1 band. Sets the analysis grid; a lat/lon DEM is reprojected to its UTM zone at 30 m. | required |
| `ndvi` | NDVI, 12 bands (Jan..Dec monthly composites) or 1 band (annual). | required |
| `precip` | Rain rate (mm/h), one band per time step; tags `TIME_START` (ISO 8601 UTC) and `TIME_STEP_MINUTES`. One file per year (a GeoTIFF holds at most 65,535 bands). Only calendar years with ≥ 95 % of their time steps count towards R. | required |
| `k_factor` | RUSLE K in SI units, e.g. from a soil survey | Williams from `soil_texture`, else `soil_type` class |
| `soil_texture` | 4 bands: sand, silt, clay, organic carbon (%), 0–30 cm | `soil_type` class-centroid texture, else loam |
| `soil_type` | ERA5-Land `soil_type` class (0 = non-land, 1–7) | (one of `k_factor`, `soil_texture`, `soil_type` is required) |
| `soil_tolerance` | Soil-loss tolerance T (t ha⁻¹ yr⁻¹) | `--tolerance` (11) |
| `soil_depth` | Depth to bedrock or a restrictive layer (m), clipped to 0.5–2 | 1 m |
| `ksat` | Saturated hydraulic conductivity (m/day) | Clapp & Hornberger value for the USDA texture class |
| `landcover` | Land-cover classes; tag `LANDCOVER_SCHEME` = `worldcover` (default) or `cdl` | no root cohesion |
| `aoi` | Study-area mask, 1 = inside. | whole DEM |
| `water` | Water mask, 1 = water (excluded). | none |

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
| `c_factor_monthly` | 12 bands, C per calendar month (used to compute soil loss for any date range). |
| `soil_tolerance` | Tolerance T used per cell (t ha⁻¹ yr⁻¹). |
| `erodibility_index` | R·K·LS / T. ≥ 8 is highly erodible under USDA conservation compliance. |
| `texture_class`, `landcover_group` | USDA texture class and land-cover group used by the landslide model (legends in tags). |
| `friction_angle`, `cohesion`, `soil_depth`, `transmissivity` | Landslide parameters per cell (middle of their ranges). |
| `wetness_index` | ln(a / tan θ). |
| `critical_rain` | Mean daily rain over the recharge window (3 days) that brings FS to 1, middle parameters (mm/day; 9999 = never fails). |
| `landslide_rain_quantiles` | 9 bands P10..P90: the rain at which P(FS < 1) reaches 0.1 … 0.9. |
| `stability_class` | 1 stable even when saturated, 2 rain-dependent, 3 unstable even when dry. |
| `landslide_class_wettest` | Alert level (0 low, 1 watch, 2 warning, 3 danger) in the wettest 3 days of the record. |
| `landslide_danger_days` | Mean days per year at danger level over the record. |

Plus `summary.json` (area statistics, tolerance and practice breakdowns, rainfall checks, K sources,
landslide statistics) and three figures (`rusle_maps.png`, `rainfall_erosivity.png`, `landslide_maps.png`).
`Params(crop_to_region=True)` writes every raster only over the bounding box of the study area.

### Landslide method

| Step | Formula / rule |
|---|---|
| Recharge | q = f_inf × mean daily rain over the last 3 days / 1000 (m/day), f_inf = 0.7 |
| Wetness | W = min(1, q·a / (T·sin θ)), a = specific catchment area (uncapped), T = 100 × Ksat × depth (the factor stands for macropore flow; loam, 1 m ≈ 60 m²/day, close to the README's 65) |
| Factor of safety | FS = [c′ + c_r + (γ − W·γw) z cos²θ tan φ′] / (γ z sin θ cos θ), γ = 18, γw = 9.81 kN/m³ |
| Critical rain | FS = 1 solved for the 3-day rain: W_crit = [c + γ z cos θ (cos θ tan φ − sin θ)] / (γw z cos²θ tan φ); ≤ 0 fails dry, ≥ 1 never fails; slopes < 2° never fail |
| Uncertainty | 100 parameter sets, each applied to the whole map: φ′ ± 3°, c′ and c_r anywhere in their table ranges, z × 0.7–1.3, T ÷/× 3 (log-uniform). Per cell, the rain at which P(FS < 1) reaches 0.1 … 0.9 is stored. |
| Alert levels | watch at P ≥ 0.1, warning at 0.3, danger at 0.6: any day's level is that day's 3-day rain compared with the stored thresholds, no simulation per day |
| Soil strength | USDA texture class → φ′, c′ (root README table; silt, sandy clay loam, silty clay loam, sandy clay and silty clay interpolated) |
| Root cohesion | land-cover group: cropland 0, grass/pasture 1–2, shrub/orchard 2–5, trees 5–10 kPa |

### Method

| Factor | Method |
|---|---|
| R | Storm EI30 (AH703): storms split by 6 h with < 1.27 mm, erosive if ≥ 12.7 mm, e = 0.29[1 − 0.72 exp(−0.05 i)], I30 = max half-hour rate. Renard & Freimund (1994) from annual rain as a cross-check. |
| K | Per cell, the first available of: a supplied `k_factor` (e.g. SSURGO `kwfact`); Williams (EPIC) from `soil_texture` sand/silt/clay/organic carbon; FAO texture class → class-centroid texture → AH703 Dg equation (Römkens et al. 1987). `summary.json` reports which share of the area each source covers. |
| LS | Depression-filled DEM → multiple-flow-direction accumulation (pysheds) → Moore & Burch (1986), upslope length capped at 300 m. |
| C | Monthly NDVI → C = exp(−2·NDVI/(1 − NDVI)) (van der Knijff 2000), averaged per pixel with that pixel's monthly R as weight. With a `landcover` input, permanent cover is capped (forest 0.003, grass/pasture and wetland 0.05, shrub/orchard 0.1; tops of the ranges in Panagos et al. 2015): NDVI sees only green leaves, so leafless forest and dormant pasture would otherwise read as nearly bare soil in winter. |
| P | `--p-factor`, default 1.0 (no conservation-practice data; an upper bound). |

A = R · K · LS · C · P in t ha⁻¹ yr⁻¹.