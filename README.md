[README.md](https://github.com/user-attachments/files/34852672/README.md)
# Field-Level Landslide & Soil-Loss Risk for Farmers

Satellite-based tool that estimates, for an individual farm field:

1. **Landslide risk:** where in the field the slope can fail, and whether current rain makes failure likely (infinite-slope factor of safety driven by a topographic wetness index).
2. **Soil loss:** average annual sheet/rill erosion (RUSLE).

Resolution: **30 m** (FABDEM grid). Rain and climate inputs are coarser (~10 km) and set the timing. Terrain and soil data set the variation inside the field.

---

## 1. Input data

| # | Dataset | Variable(s) used | Resolution | Source / GEE ID | Used in | Notes |
|---|---|---|---|---|---|---|
| 1 | **FABDEM** (Copernicus GLO-30 with forests and buildings removed) | elevation → slope θ, specific catchment area *a*, LS | 30 m | `projects/sat-io/open-datasets/FABDEM` | W, FS, RUSLE-LS | CC BY-NC-SA 4.0, non-commercial only. Credit: "FABDEM is produced using Copernicus WorldDEM-30 © DLR e.V." |
| 2 | **GPM IMERG V07** | precipitation (mm/hr, 30-min) | ~10 km | `NASA/GPM_L3/IMERG_V07` | recharge *q* (W), RUSLE-R | Daily totals summed from 30-min data |
| 3 | **SoilGrids 2.0** | sand, silt, clay (g/kg), SOC (dg/kg) | 250 m | `projects/soilgrids-isric/*_mean` | c′, φ′ (lookup), RUSLE-K | Use 0–30 cm layers |
| 4 | **Global Depth to Bedrock** (Shangguan et al., 2017) | censored depth to bedrock, 0–200 cm | 250 m | files.isric.org / globalchange.bnu.edu.cn | soil depth *z* | Cap z to 0.5–2 m |
| 5 | **ESA WorldCover v200** | land cover class | 10 m | `ESA/WorldCover/v200` | root cohesion c_r, cropland mask | |
| 6 | **NASA HLS** (Landsat + Sentinel-2 harmonised) | red, NIR → NDVI | 30 m | `NASA/HLS/HLSL30/v002`, `NASA/HLS/HLSS30/v002` | RUSLE-C | Growing-season median |
| 7 | *(optional)* **NASA LHASA v2** nowcast / forecast | landslide probability | ~1 km | portal.nccs.nasa.gov/datashare/landslides/ | regional "watch" signal | Best-effort service, so cache the last file |
| 8 | **Landslide inventories** | landslide locations (+ dates) | points/polygons | NASA Global Landslide Catalog, MTA inventory (1:25k), AFAD | validation only | Not used to train the physics |
| 9 | **Farmer field polygon** | field boundary | vector | user input in app | field summary | |

### Constants and default parameters

| Symbol | Meaning | Default | Source |
|---|---|---|---|
| T | soil transmissivity (lateral drainage) | 65 m²/day | SAGA GIS Wetness tool default conductivity 2.7 m/hr × 1 m depth |
| f_inf | fraction of rain that infiltrates | 0.7 | team assumption |
| window | rain averaging window for recharge | 3 days | our choice (try 3–7) |
| γ | moist soil unit weight | 18 kN/m³ | typical value |
| γw | unit weight of water | 9.81 kN/m³ | physical constant |
| P (RUSLE) | support-practice factor | 1 | no conservation practice assumed |

### Soil strength lookup (by USDA texture class from SoilGrids): starting values, to be calibrated

| Texture | φ′ (°) | c′ (kPa) |
|---|---|---|
| Sand | 33 | 0–1 |
| Loamy sand | 32 | 1 |
| Sandy loam | 30 | 2–4 |
| Loam | 28 | 4–6 |
| Silt loam | 27 | 3–5 |
| Clay loam | 25 | 6–10 |
| Clay | 22 | 8–12 |

### Root cohesion lookup (by WorldCover class): rough values

| Land cover | c_r (kPa) |
|---|---|
| Cropland, bare | 0 |
| Grassland | 1–2 |
| Shrubland / orchard | 2–5 |
| Tree cover | 5–10 |

---

## 2. Formulas

### 2.1 Wetness: saturated fraction of soil depth (0–1)

```
W = min(1, q · a / (T · sin θ))
```

- `q`: recharge (m/day) = f_inf × mean daily IMERG rain over `window` days ÷ 1000
- `a`: specific catchment area (m) = upslope area ÷ contour width
- `T`: transmissivity (m²/day)
- `θ`: slope

Source: Montgomery & Dietrich (1994); capped form from SINMAP (Pack et al., 1998), where the recharge is written as R. **Our extension:** q varies daily with IMERG instead of being a constant.

### 2.2 Factor of safety (infinite slope)

```
FS = [c′ + c_r + (γ − W·γw) · z · cos²θ · tanφ′] / (γ · z · sinθ · cosθ)
```

FS < 1 means unstable. Source: infinite-slope model (Skempton & DeLory, 1957), in the form used by SINMAP and SHALSTAB.

### 2.3 Critical wetness (wetness at which FS = 1)

```
W_crit = [c′ + c_r + γ · z · cos²θ · (tanφ′ − tanθ)] / (γw · z · cos²θ · tanφ′)
```

W_crit ≤ 0 means unstable even when dry. W_crit ≥ 1 means stable even when saturated. Derived algebraically from 2.2.

### 2.4 RUSLE soil loss (t/ha/yr)

```
A = R · K · LS · C · P
```

| Factor | Formula | Source |
|---|---|---|
| R (erosivity) | P_ann ≤ 850 mm: `0.0483 · P_ann^1.61`; otherwise `587.8 − 1.219·P_ann + 0.004105·P_ann²` (MJ·mm/ha/h/yr) | Renard & Freimund (1994) |
| K (erodibility) | Williams EPIC equation from sand, silt, clay, organic C (×0.1317 for SI units) | Williams (1995) |
| LS | `(a / 22.13)^0.4 · (sin θ / 0.0896)^1.3`, with a capped at ~300 m | Moore & Burch (1986) |
| C (cover) | `exp(−2 · NDVI / (1 − NDVI))`, clamped 0–1 | van der Knijff et al. (2000) |
| P | 1 | assumption |

> ⚠️ RUSLE **R** (erosivity) is not the wetness recharge **q**. They have different units and meanings.

---

## 3. Functions

### Data loading

| Function | Inputs | Output | Description |
|---|---|---|---|
| `load_dem(aoi)` | area of interest | DEM (m) | Mosaic and clip FABDEM |
| `load_imerg(aoi, start, end)` | AOI, dates | daily rain (mm/day) | Sum 30-min IMERG to daily totals |
| `load_soil(aoi)` | AOI | sand, silt, clay (%), SOC (%) | SoilGrids 0–30 cm. Convert g/kg → % (÷10) and dg/kg → % (÷100) |
| `load_depth(aoi)` | AOI | z (m) | Depth to bedrock, cm → m, clipped to [0.5, 2] |
| `load_landcover(aoi)` | AOI | class raster | ESA WorldCover |
| `load_ndvi(aoi, season)` | AOI, months | NDVI | Cloud-masked HLS median |
| `load_lhasa(date)` *(optional)* | date | probability (~1 km) | Latest LHASA nowcast/forecast GeoTIFF |

### Terrain

| Function | Inputs | Output | Description |
|---|---|---|---|
| `compute_slope(dem)` | DEM | θ (radians) | Slope from DEM |
| `compute_sca(dem)` | DEM | a (m) | Specific catchment area via D-infinity flow accumulation (WhiteboxTools `DInfFlowAccumulation`, `out_type='sca'`) |

### Soil parameters

| Function | Inputs | Output | Description |
|---|---|---|---|
| `texture_class(sand, silt, clay)` | % fractions | USDA class | USDA texture triangle |
| `strength_params(texture)` | class | c′ (kPa), φ′ (°) | Lookup table (§1) |
| `root_cohesion(landcover)` | class | c_r (kPa) | Lookup table (§1) |

### Landslide model

| Function | Inputs | Output | Description |
|---|---|---|---|
| `recharge(rain_daily, date, window=3, f_inf=0.7)` | daily rain, date | q (m/day) | `f_inf × mean(rain[date−window+1 … date]) / 1000` |
| `wetness(q, a, theta, T=65)` | q, a, θ | W (0–1) | Formula 2.1 |
| `factor_of_safety(W, theta, c, c_r, phi, z, gamma=18, gamma_w=9.81)` | see formula | FS | Formula 2.2 |
| `critical_wetness(theta, c, c_r, phi, z, gamma=18, gamma_w=9.81)` | see formula | W_crit | Formula 2.3 (static "where" map) |
| `failure_probability(..., n=100)` | same as FS + parameter ranges | P(FS < 1) | Monte Carlo over φ′ ± 3°, c′ range, z ± 30%, T range |
| `classify_landslide(p_fail)` | P(FS < 1) | low / watch / warning / danger | Thresholds e.g. 0.1 / 0.3 / 0.6 (to be tuned) |

### Erosion model

| Function | Inputs | Output | Description |
|---|---|---|---|
| `r_factor(annual_rain_mm)` | P_ann | R | Renard & Freimund |
| `k_factor(sand, silt, clay, soc)` | % | K | Williams EPIC |
| `ls_factor(a, theta, a_max=300)` | a, θ | LS | Moore & Burch |
| `c_factor(ndvi)` | NDVI | C | van der Knijff |
| `rusle(R, K, LS, C, P=1)` | factors | A (t/ha/yr) | Formula 2.4 |

### Field output and validation

| Function | Inputs | Output | Description |
|---|---|---|---|
| `field_summary(field, layers)` | field polygon, rasters | dict | % of field in each risk class, 90th-percentile P(FS < 1), min W_crit, mean soil loss, cropland area |
| `farmer_advice(summary, alert)` | summary, alert level | text | Rule-based advice (e.g. contour ploughing, keep root cover, clear drainage, keep animals off slope) |
| `validate(predictions, inventory)` | maps, landslide events | metrics | Hit rate, false-alarm rate, AUC, distance (m) from each landslide to the nearest flagged pixel. Use spatial (by sub-basin) and temporal (by year) hold-outs |

---

## 4. Pipeline

```
FABDEM ─► slope θ, catchment a ───────────────┐
SoilGrids ─► texture ─► c′, φ′                 │
Depth-to-bedrock ─► z                          ├─► W_crit (static map)
WorldCover ─► c_r                              │
IMERG ─► recharge q(t) ─► W(t) ────────────────┴─► FS(t) ─► P(FS<1) ─► risk class
                                                                       │
HLS NDVI ─► C;  SoilGrids ─► K;  IMERG ─► R;  FABDEM ─► LS ─► RUSLE A  │
                                                                       ▼
                               Farmer field polygon ─► field_summary ─► farmer_advice
                                                    (+ optional LHASA regional watch)
```

---

## 5. Assumptions and limitations

- Steady-state wetness (2.1) is applied to multi-day mean rain. This is a simplification, not a transient infiltration model.
- c′, φ′ and c_r come from lookup tables. Results are relative unless calibrated against known landslides.
- Soil data (250 m) is coarser than terrain (30 m). The variation inside a field comes mainly from slope and catchment area.
- T = 65 m²/day is a default. Check it against 2–3 dated landslides and adjust if needed.
- RUSLE estimates sheet/rill erosion only, not landslides or gullies.

---

## 6. References

- Montgomery, D.R. & Dietrich, W.E. (1994). A physically based model for the topographic control on shallow landsliding. *Water Resources Research*, 30(5), 1153–1171.
- Pack, R.T., Tarboton, D.G. & Goodwin, C.N. (1998). *SINMAP User's Manual*. Utah State University.
- Skempton, A.W. & DeLory, F.A. (1957). Stability of natural slopes in London Clay. *Proc. 4th Int. Conf. Soil Mech. Found. Eng.*
- Renard, K.G. et al. (1997). *Predicting Soil Erosion by Water: RUSLE*. USDA Agriculture Handbook 703.
- Renard, K.G. & Freimund, J.R. (1994). Using monthly precipitation data to estimate the R-factor in the revised USLE. *Journal of Hydrology*, 157, 287–306.
- Williams, J.R. (1995). The EPIC model. In *Computer Models of Watershed Hydrology*, Water Resources Publications.
- Moore, I.D. & Burch, G.J. (1986). Physical basis of the length-slope factor in the Universal Soil Loss Equation. *Soil Sci. Soc. Am. J.*, 50, 1294–1298.
- van der Knijff, J.M., Jones, R.J.A. & Montanarella, L. (2000). *Soil Erosion Risk Assessment in Europe*. EUR 19044 EN.
- Hawker, L. et al. (2022). A 30 m global map of elevation with forests and buildings removed. *Environ. Res. Lett.*, 17, 024016.
- Shangguan, W. et al. (2017). Mapping the global depth to bedrock for land surface modeling. *J. Adv. Model. Earth Syst.*, 9(1).
- Stanley, T.A. et al. (2021). Data-driven landslide nowcasting at the global scale. *Front. Earth Sci.*, 9:640043.
