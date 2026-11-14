"""US-path datasets (CONUS), cached in the US cache.

  USGS 3DEP 1/3 arc-second (~10 m)   USGS/3DEP/10m_collection (Earth Engine)
  SSURGO soil survey                 gNATSGO map-unit raster, 10 m (Earth Engine community catalog,
                                     projects/sat-io/open-datasets/gNATSGO) + map-unit properties
                                     from USDA Soil Data Access (SQL over HTTPS)
  USDA Cropland Data Layer, 30 m     USDA/NASS/CDL (Earth Engine)

Per map unit the dominant component (largest comppct_r with horizon data) gives:
  K          kwfact of the shallowest horizon that has one, x 0.1317 -> SI
  tolerance  tfact (US tons/acre/yr) x 2.2417 -> t/ha/yr
  depth      shallowest restrictive layer (corestrictions.resdept_r); none -> 2 m
  ksat       thickness-weighted ksat_r (um/s) above that depth -> m/day
  texture    0-30 cm thickness-weighted sand, silt, clay; organic carbon = om_r / 1.724
"""
from __future__ import annotations

import csv
from pathlib import Path

import ee
import numpy as np
import requests

from ..dataanal import raster_io as rio
from . import cache, ee_io

DEM_3DEP = "USGS/3DEP/10m_collection"
GNATSGO = "projects/sat-io/open-datasets/gNATSGO/raster/mukey"
CDL = "USDA/NASS/CDL"
SDA_URL = "https://sdmdataaccess.sc.egov.usda.gov/Tabular/post.rest"

K_US_TO_SI = 0.1317
TONS_ACRE_TO_T_HA = 2.2417
UM_S_TO_M_DAY = 1e-6 * 86400
SSURGO_BANDS = ["k_factor", "soil_tolerance", "soil_depth", "ksat", "sand", "silt", "clay", "organic_carbon"]


def _ee_raster(dataset: str, grid: rio.Grid, cache_dir: Path, make_image, **write) -> Path:
    def build():
        ee_io.init()
        return ee_io.fetch(make_image(), grid)

    return cache.cached_raster(Path(cache_dir) / dataset / f"{cache.grid_key(grid)}.tif", grid, build, **write)


def dem(grid: rio.Grid, cache_dir: Path) -> Path:
    print("Elevation: USGS 3DEP 1/3 arc-second (~10 m)")
    return _ee_raster("3dep", grid, cache_dir,
                      lambda: ee.ImageCollection(DEM_3DEP).map(lambda im: im.resample("bilinear")).mosaic().select("elevation"),
                      description="USGS 3DEP 1/3 arc-second elevation", units="m",
                      tags={"RUSLE_ROLE": "dem", "SOURCE": DEM_3DEP})


def landcover(grid: rio.Grid, cache_dir: Path, year: int) -> Path:
    print(f"Land cover: USDA Cropland Data Layer {year}")
    return _ee_raster(f"cdl_{year}", grid, cache_dir, lambda: ee.Image(f"{CDL}/{year}").select("cropland"),
                      description=f"USDA NASS Cropland Data Layer {year}", units="class", dtype="uint8", nodata=255,
                      tags={"RUSLE_ROLE": "landcover", "LANDCOVER_SCHEME": "cdl", "SOURCE": f"{CDL}/{year}"})


# ============================================================================ SSURGO


def _sda(query: str) -> list[dict]:
    """Rows of a Soil Data Access query as dicts (strings; None for NULL)."""
    r = requests.post(SDA_URL, data={"query": query, "format": "JSON+COLUMNNAME"}, timeout=120)
    r.raise_for_status()
    table = (r.json() or {}).get("Table", [])
    if not table:
        return []
    header, *rows = table
    return [dict(zip(header, row)) for row in rows]


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return np.nan


def _weighted(horizons, key, top, bottom):
    """Thickness-weighted mean of `key` over the depth range [top, bottom) cm."""
    total = weight = 0.0
    for h in horizons:
        a, b, v = max(h["top"], top), min(h["bottom"], bottom), h[key]
        if b > a and np.isfinite(v):
            total, weight = total + v * (b - a), weight + (b - a)
    return total / weight if weight else np.nan


def map_unit_properties(mukeys) -> dict[int, list[float]]:
    """SSURGO_BANDS values per map unit key (see module docstring)."""
    props = {}
    keys = sorted({int(k) for k in mukeys})
    for i in range(0, len(keys), 400):
        batch = ",".join(str(k) for k in keys[i:i + 400])
        rows = _sda(f"""
            SELECT c.mukey, c.cokey, c.comppct_r, c.tfact, ch.hzdept_r, ch.hzdepb_r, ch.kwfact, ch.ksat_r,
                   ch.sandtotal_r, ch.silttotal_r, ch.claytotal_r, ch.om_r
            FROM component c LEFT JOIN chorizon ch ON ch.cokey = c.cokey
            WHERE c.mukey IN ({batch})""")
        restrictions = {r["cokey"]: _num(r["depth"]) for r in _sda(f"""
            SELECT c.cokey, MIN(r.resdept_r) AS depth
            FROM component c JOIN corestrictions r ON r.cokey = c.cokey
            WHERE c.mukey IN ({batch}) GROUP BY c.cokey""")}
        comps: dict[str, dict] = {}
        for r in rows:
            comp = comps.setdefault(r["cokey"], {"mukey": int(r["mukey"]), "pct": _num(r["comppct_r"]),
                                                  "tfact": _num(r["tfact"]), "horizons": []})
            top, bottom = _num(r["hzdept_r"]), _num(r["hzdepb_r"])
            if np.isfinite(top) and np.isfinite(bottom) and bottom > top:
                comp["horizons"].append({"top": top, "bottom": bottom, "kwfact": _num(r["kwfact"]),
                                         "ksat": _num(r["ksat_r"]), "sand": _num(r["sandtotal_r"]),
                                         "silt": _num(r["silttotal_r"]), "clay": _num(r["claytotal_r"]),
                                         "om": _num(r["om_r"])})
        dominant: dict[int, tuple] = {}
        for cokey, comp in comps.items():
            hz = sorted(comp["horizons"], key=lambda h: h["top"])
            if not any(np.isfinite(h["sand"]) for h in hz):
                continue  # rock outcrop, water, etc.
            if comp["mukey"] not in dominant or comp["pct"] > dominant[comp["mukey"]][0]:
                dominant[comp["mukey"]] = (comp["pct"], cokey, comp, hz)
        for mukey, (_, cokey, comp, hz) in dominant.items():
            kw = next((h["kwfact"] for h in hz if np.isfinite(h["kwfact"])), np.nan)
            depth_cm = restrictions.get(cokey, np.nan)
            depth_cm = min(depth_cm, 200.0) if np.isfinite(depth_cm) else 200.0
            om = _weighted(hz, "om", 0, 30)
            props[mukey] = [
                kw * K_US_TO_SI,
                comp["tfact"] * TONS_ACRE_TO_T_HA,
                depth_cm / 100.0,
                _weighted(hz, "ksat", 0, max(depth_cm, 1.0)) * UM_S_TO_M_DAY,
                _weighted(hz, "sand", 0, 30), _weighted(hz, "silt", 0, 30), _weighted(hz, "clay", 0, 30),
                om / 1.724 if np.isfinite(om) else np.nan,
            ]
    return props


def ssurgo(grid: rio.Grid, cache_dir: Path) -> Path:
    """8-band raster of SSURGO_BANDS on `grid` (NaN where the survey has no usable data)."""
    print("Soil: SSURGO (gNATSGO map units + USDA Soil Data Access properties)")
    key = cache.grid_key(grid)
    folder = Path(cache_dir) / "ssurgo"
    mukey_path = _ee_raster("ssurgo", grid, cache_dir, lambda: ee.ImageCollection(GNATSGO).mosaic().select(0),
                            description="gNATSGO map unit key", units="mukey", tags={"SOURCE": GNATSGO})
    table_path = folder / f"{key}_properties.csv"

    def build():
        mukey = rio.read(mukey_path).data[0]
        ok = np.isfinite(mukey) & (mukey > 0)
        props = map_unit_properties(np.unique(mukey[ok]))
        with open(table_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["mukey"] + SSURGO_BANDS)
            for k, v in sorted(props.items()):
                w.writerow([k] + [f"{x:.6g}" for x in v])
        out = np.full((len(SSURGO_BANDS),) + mukey.shape, np.nan, np.float32)
        for k, values in props.items():
            sel = mukey == k
            for b, v in enumerate(values):
                out[b][sel] = v
        found = np.isfinite(out[0]) | np.isfinite(out[4])
        print(f"  {len(props)} map units; survey data on {100 * found[ok].mean() if ok.any() else 0:.0f}% of the field area")
        return out

    return cache.cached_raster(folder / f"{key}_properties.tif", grid, build,
                               description="SSURGO properties per map unit (dominant component)",
                               units="see band names", band_names=SSURGO_BANDS, tags={"SOURCE": "USDA NRCS SSURGO"})
