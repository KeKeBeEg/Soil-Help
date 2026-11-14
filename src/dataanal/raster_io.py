"""GeoTIFF input/output for the analysis pipeline. The input contract lives here.

Each input GeoTIFF has a role, taken from its RUSLE_ROLE tag or else from its file name
(`<role>.tif` or `<role>_<anything>.tif`):

  dem             elevation (m), 1 band. Defines the output grid; a geographic DEM is
                  reprojected to its UTM zone.
  ndvi            NDVI, 12 bands (Jan..Dec monthly composites) or 1 band (annual).
  precip          rain rate (mm/h), one band per time step, with tags TIME_START (ISO 8601,
                  UTC) and TIME_STEP_MINUTES. Several files (e.g. one per year) are joined in time.
                  GeoTIFF or the same content as .npz (see write_npz): GDAL handles tens of
                  thousands of bands slowly (~15 s per year of half-hours each way).
  soil inputs     at least one of soil_type, soil_texture, k_factor:
    soil_type     ERA5-Land soil_type class (0 = non-land, 1-7), 1 band.
    soil_texture  4 bands: sand, silt, clay, organic carbon, all in % (0-30 cm).
    k_factor      RUSLE K in SI units (t ha h ha-1 MJ-1 mm-1), e.g. from a soil survey.
  soil_tolerance  optional soil-loss tolerance T (t/ha/yr).
  soil_depth      optional depth to bedrock or a restrictive layer (m).
  ksat            optional saturated hydraulic conductivity (m/day).
  landcover       optional land-cover classes; tag LANDCOVER_SCHEME names the scheme
                  (worldcover by default, or cdl; see soil_params.SCHEMES).
  aoi             optional study-area mask, 1 = inside.
  water           optional water mask, 1 = water (excluded from the results).

Optional inputs replace a default: per pixel, a NaN in an optional input falls back to the
next source (see pipeline). Inputs may be on different grids and CRSs; everything is
resampled onto the DEM grid.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
from affine import Affine
from rasterio.crs import CRS
from rasterio.transform import array_bounds
from rasterio.warp import Resampling, calculate_default_transform, reproject

ROLE_TAG = "RUSLE_ROLE"
ROLES = ("dem", "soil_type", "soil_texture", "k_factor", "soil_tolerance", "soil_depth", "ksat", "landcover",
         "ndvi", "precip", "aoi", "water")
REQUIRED = ("dem", "ndvi", "precip")
SOIL_ROLES = ("soil_type", "soil_texture", "k_factor")  # at least one is required


@dataclass
class Raster:
    path: Path
    data: np.ndarray  # (bands, H, W) float32, NaN = no data
    transform: Affine
    crs: CRS
    tags: dict
    descriptions: tuple


@dataclass(frozen=True)
class Grid:
    crs: CRS
    transform: Affine
    width: int
    height: int

    @property
    def xres(self) -> float:
        return abs(self.transform.a)

    @property
    def yres(self) -> float:
        return abs(self.transform.e)

    def same_as(self, other: "Grid") -> bool:
        return (self.crs == other.crs and (self.width, self.height) == (other.width, other.height)
                and self.transform.almost_equals(other.transform))

    def as_dict(self) -> dict:
        return {"crs": self.crs.to_string(), "transform": list(self.transform)[:6],
                "width": self.width, "height": self.height, "res_m": self.xres}


def grid_of(r: Raster) -> Grid:
    return Grid(r.crs, r.transform, r.data.shape[2], r.data.shape[1])


def grid_from_dict(d: dict) -> Grid:
    """Inverse of Grid.as_dict."""
    return Grid(CRS.from_string(d["crs"]), Affine(*d["transform"][:6]), int(d["width"]), int(d["height"]))


# --------------------------------------------------------------------------- reading


def read(path) -> Raster:
    if Path(path).suffix.lower() == ".npz":
        return read_npz(path)
    with rasterio.open(path) as src:
        data = src.read(masked=True).astype(np.float32).filled(np.nan)
        return Raster(Path(path), data, src.transform, src.crs, src.tags(), src.descriptions)


def write_npz(path, data, grid: Grid, tags: dict) -> Path:
    """A raster stack as .npz: data (bands, H, W) float32 with NaN as no-data, the affine
    transform, the CRS as WKT and the tags as JSON. Used for long time series."""
    data = np.asarray(data, np.float32)
    if data.ndim == 2:
        data = data[None]
    with open(path, "wb") as f:
        np.savez_compressed(f, data=data, transform=np.array(list(grid.transform)[:6]),
                            crs=np.array(grid.crs.to_wkt()), tags=np.array(json.dumps({k: str(v) for k, v in tags.items()})))
    return Path(path)


def read_npz(path) -> Raster:
    with np.load(path, allow_pickle=False) as z:
        return Raster(Path(path), z["data"].astype(np.float32), Affine(*z["transform"][:6]),
                      CRS.from_wkt(str(z["crs"])), json.loads(str(z["tags"])), ())


def role_of(r: Raster) -> str:
    tagged = r.tags.get(ROLE_TAG, "").lower()
    if tagged in ROLES:
        return tagged
    stem = r.path.stem.lower()
    for role in sorted(ROLES, key=len, reverse=True):
        if stem == role or stem.startswith((role + "_", role + "-")):
            return role
    raise ValueError(f"{r.path.name}: cannot tell which input this is. Name it <role>.tif or "
                     f"<role>_<suffix>.tif, or set the {ROLE_TAG} tag; roles: {', '.join(ROLES)}")


def expand(paths) -> list[Path]:
    """Paths as given, with any directory replaced by the .tif and .npz files inside it."""
    out = []
    for p in map(Path, paths):
        out += sorted([*p.glob("*.tif"), *p.glob("*.npz")]) if p.is_dir() else [p]
    return out


def load_inputs(paths) -> dict:
    """Read and sort input GeoTIFFs -> {role: Raster}, with 'precip' a list of Rasters."""
    layers: dict = {"precip": []}
    for path in expand(paths):
        r = read(path)
        role = role_of(r)
        if role == "precip":
            layers["precip"].append(r)
        elif role in layers:
            raise ValueError(f"two '{role}' inputs: {layers[role].path} and {path}")
        else:
            layers[role] = r
    missing = [role for role in REQUIRED if not layers.get(role)]
    if not any(layers.get(role) for role in SOIL_ROLES):
        missing.append(" or ".join(SOIL_ROLES))
    if missing:
        raise ValueError(f"missing required input(s): {', '.join(missing)} (got {sorted(k for k, v in layers.items() if v)})")
    return layers


def precip_cube(rasters: list[Raster]):
    """Join precipitation rasters in time -> (times datetime64[m], rate (T,H,W), grid, step_hours)."""
    grid, steps, times, data = grid_of(rasters[0]), set(), [], []
    for r in rasters:
        if not grid_of(r).same_as(grid):
            raise ValueError(f"{r.path.name}: precipitation files must share one grid")
        try:
            start = np.datetime64(r.tags["TIME_START"].rstrip("Z").replace("+00:00", ""), "m")
            step = int(r.tags["TIME_STEP_MINUTES"])
        except KeyError as err:
            raise ValueError(f"{r.path.name}: precipitation GeoTIFF needs tag {err}") from None
        steps.add(step)
        times.append(start + np.arange(r.data.shape[0]) * np.timedelta64(step, "m"))
        data.append(r.data)
    if len(steps) != 1:
        raise ValueError(f"precipitation files use different time steps: {sorted(steps)} minutes")
    t = np.concatenate(times)
    t, keep = np.unique(t, return_index=True)  # sorted, duplicates dropped
    return t, np.concatenate(data)[keep], grid, steps.pop() / 60.0


# --------------------------------------------------------------------------- grids


def reference_grid(dem: Raster, target_res_m: float = 30.0) -> Grid:
    """The DEM's grid, or for a geographic DEM its footprint in the local UTM zone."""
    if dem.crs is None:
        raise ValueError(f"{dem.path.name}: DEM has no CRS")
    g = grid_of(dem)
    if dem.crs.is_projected:
        return g
    w, s, e, n = array_bounds(g.height, g.width, g.transform)
    lon, lat = (w + e) / 2, (s + n) / 2
    dst = CRS.from_epsg((32600 if lat >= 0 else 32700) + int((lon + 180) // 6) + 1)
    transform, width, height = calculate_default_transform(
        dem.crs, dst, g.width, g.height, w, s, e, n, resolution=target_res_m)
    return Grid(dst, transform, width, height)


def align(data, src_transform, src_crs, grid: Grid, resampling=Resampling.bilinear) -> np.ndarray:
    """Resample (H, W) or (B, H, W) data onto `grid` (no-op when already on it)."""
    data = np.asarray(data, np.float32)
    flat = data.ndim == 2
    if flat:
        data = data[None]
    src = Grid(src_crs, src_transform, data.shape[2], data.shape[1])
    if src.same_as(grid):
        out = data
    else:
        out = np.full((data.shape[0], grid.height, grid.width), np.nan, np.float32)
        reproject(source=data, destination=out, src_transform=src_transform, src_crs=src_crs,
                  dst_transform=grid.transform, dst_crs=grid.crs, resampling=resampling,
                  src_nodata=np.nan, dst_nodata=np.nan)
    return out[0] if flat else out


def align_raster(r: Raster, grid: Grid, resampling=Resampling.bilinear) -> np.ndarray:
    return align(r.data, r.transform, r.crs, grid, resampling)


def bbox_window(mask) -> tuple[slice, slice]:
    """Row and column slices of the smallest box holding every True cell of `mask`."""
    rows, cols = np.flatnonzero(mask.any(axis=1)), np.flatnonzero(mask.any(axis=0))
    if rows.size == 0:
        raise ValueError("empty mask: no cell to keep")
    return slice(rows[0], rows[-1] + 1), slice(cols[0], cols[-1] + 1)


def subgrid(grid: Grid, rows: slice, cols: slice) -> Grid:
    """The part of `grid` covered by a row/column window."""
    transform = grid.transform @ Affine.translation(cols.start, rows.start)
    return Grid(grid.crs, transform, cols.stop - cols.start, rows.stop - rows.start)


# --------------------------------------------------------------------------- writing


def profile(grid: Grid, count: int = 1, dtype: str = "float32", nodata=np.nan) -> dict:
    p = dict(driver="GTiff", width=grid.width, height=grid.height, count=count, dtype=dtype,
             crs=grid.crs, transform=grid.transform, nodata=nodata, compress="deflate", interleave="band")
    if grid.width >= 256 and grid.height >= 256:
        p.update(tiled=True, blockxsize=256, blockysize=256)
    return p


def write(path, data, grid: Grid, *, description: str, units: str, dtype: str = "float32",
          nodata=np.nan, band_names=None, tags: dict | None = None) -> Path:
    data = np.asarray(data)
    if data.ndim == 2:
        data = data[None]
    with rasterio.open(path, "w", **profile(grid, data.shape[0], dtype, nodata)) as dst:
        dst.write(data.astype(dtype))
        dst.update_tags(DESCRIPTION=description, UNITS=units, **(tags or {}))
        for i, name in enumerate(band_names or [], 1):
            dst.set_band_description(i, name)
    return Path(path)
