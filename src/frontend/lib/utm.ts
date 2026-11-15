/**
 * Coordinates of the result rasters. The backend writes each field in its UTM
 * zone (EPSG 326zz north, 327zz south); MapLibre wants longitude/latitude.
 * Standard inverse transverse Mercator on WGS 84 (Snyder 1987), accurate to
 * well under a metre within a zone, which is far finer than a 10 m cell.
 */
import type { LngLat } from './geo';

const A = 6378137;
const F = 1 / 298.257223563;
const K0 = 0.9996;
const E2 = F * (2 - F);
const EP2 = E2 / (1 - E2);

export function utmToLngLat(x: number, y: number, zone: number, south: boolean): LngLat {
  const e1 = (1 - Math.sqrt(1 - E2)) / (1 + Math.sqrt(1 - E2));
  const m = (south ? y - 10000000 : y) / K0;
  const mu = m / (A * (1 - E2 / 4 - (3 * E2 * E2) / 64 - (5 * E2 ** 3) / 256));
  const phi1 =
    mu +
    ((3 * e1) / 2 - (27 * e1 ** 3) / 32) * Math.sin(2 * mu) +
    ((21 * e1 * e1) / 16 - (55 * e1 ** 4) / 32) * Math.sin(4 * mu) +
    ((151 * e1 ** 3) / 96) * Math.sin(6 * mu) +
    ((1097 * e1 ** 4) / 512) * Math.sin(8 * mu);
  const sin = Math.sin(phi1), cos = Math.cos(phi1), tan = Math.tan(phi1);
  const n1 = A / Math.sqrt(1 - E2 * sin * sin);
  const t1 = tan * tan;
  const c1 = EP2 * cos * cos;
  const r1 = (A * (1 - E2)) / (1 - E2 * sin * sin) ** 1.5;
  const d = (x - 500000) / (n1 * K0);
  const lat =
    phi1 -
    ((n1 * tan) / r1) *
      ((d * d) / 2 -
        ((5 + 3 * t1 + 10 * c1 - 4 * c1 * c1 - 9 * EP2) * d ** 4) / 24 +
        ((61 + 90 * t1 + 298 * c1 + 45 * t1 * t1 - 252 * EP2 - 3 * c1 * c1) * d ** 6) / 720);
  const lon =
    (d - ((1 + 2 * t1 + c1) * d ** 3) / 6 + ((5 - 2 * c1 + 28 * t1 - 3 * c1 * c1 + 8 * EP2 + 24 * t1 * t1) * d ** 5) / 120) /
    cos;
  const lon0 = ((zone - 1) * 6 - 180 + 3) * (Math.PI / 180);
  return [((lon0 + lon) * 180) / Math.PI, (lat * 180) / Math.PI];
}

/** Any point of a raster in EPSG 4326 or a WGS 84 UTM zone, as [lng, lat]. */
export function toLngLat(epsg: number, x: number, y: number): LngLat {
  if (epsg === 4326) return [x, y];
  if (epsg >= 32601 && epsg <= 32660) return utmToLngLat(x, y, epsg - 32600, false);
  if (epsg >= 32701 && epsg <= 32760) return utmToLngLat(x, y, epsg - 32700, true);
  throw new Error(`unsupported coordinate system EPSG:${epsg}`);
}
