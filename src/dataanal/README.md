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

All data is read through Google Earth Engine; downloads are cached in `data/<aoi_name>/`,
results are written to `outputs/<aoi_name>/` (GeoTIFFs of R, K, LS, C, A + `summary.json` + figures).

```powershell
c:\.code\Py\Hackathon-NASA2026\Scripts\earthengine.exe authenticate   # once, opens a browser
$env:EE_PROJECT = "<cloud-project-id>"                                # project registered for Earth Engine
c:\.code\Py\Hackathon-NASA2026\Scripts\python.exe -m src.dataanal.pipeline   # from the repo root
c:\.code\Py\Hackathon-NASA2026\Scripts\python.exe -m pytest                 # factor unit tests
```

Study area, years and parameters live in `config.py` (default: Gilgel Abay catchment, Ethiopia, 2010–2015).

| Factor | Data (Earth Engine ID) | Method |
|---|---|---|
| R | IMERG V07 half-hourly, `NASA/GPM_L3/IMERG_V07` | Storm EI30 (AH703): storms split by 6 h < 1.27 mm, erosive if ≥ 12.7 mm, e = 0.29[1 − 0.72 exp(−0.05 i)], I30 = max half-hour rate. Renard & Freimund (1994) from annual rain as a cross-check. |
| K | ERA5-Land `soil_type`, `ECMWF/ERA5_LAND/STATIC` | FAO texture class → class-centroid sand/silt/clay → AH703 Dg equation (Römkens et al. 1987). Williams (EPIC) and the min/max K within each class are reported as sensitivity. |
| LS | FABDEM 30 m, `projects/sat-io/open-datasets/FABDEM` | Depression-filled DEM → multiple-flow-direction accumulation (pysheds) → Moore & Burch (1986), upslope length capped at 300 m. |
| C | Landsat 8/9 C2 L2, `LANDSAT/LC08/C02/T1_L2` (+LC09) | Cloud-masked monthly median NDVI → C = exp(−2·NDVI/(1 − NDVI)) (van der Knijff 2000), averaged with each month's share of R as weight. Landsat 8 starts Apr 2013, so C uses 2013-04..2015-12. Water (MNDWI > 0) is excluded. |
| P | – | 1.0 (no conservation-practice data; an upper bound). |

A = R · K · LS · C · P in t ha⁻¹ yr⁻¹, computed on a 30 m UTM grid inside the HydroBASINS sub-basin.