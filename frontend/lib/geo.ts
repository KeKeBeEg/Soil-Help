/** Geodesic helpers. No dependencies: everything here is a few lines of maths. */

const R = 6378137; // WGS-84 equatorial radius, metres
const toRad = (d: number) => (d * Math.PI) / 180;

export type LngLat = [number, number];

/** Great-circle distance in metres (haversine). */
export function distance(a: LngLat, b: LngLat): number {
  const dLat = toRad(b[1] - a[1]);
  const dLng = toRad(b[0] - a[0]);
  const lat1 = toRad(a[1]);
  const lat2 = toRad(b[1]);
  const h =
    Math.sin(dLat / 2) ** 2 + Math.cos(lat1) * Math.cos(lat2) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.min(1, Math.sqrt(h)));
}

/** Total length of a path in metres. */
export function pathLength(points: LngLat[]): number {
  let total = 0;
  for (let i = 1; i < points.length; i++) total += distance(points[i - 1], points[i]);
  return total;
}

/**
 * Spherical excess area of a polygon, in square metres.
 * Accurate to well under a percent at field scale.
 */
export function polygonArea(ring: LngLat[]): number {
  if (ring.length < 3) return 0;
  let total = 0;
  for (let i = 0; i < ring.length; i++) {
    const [lng1, lat1] = ring[i];
    const [lng2, lat2] = ring[(i + 1) % ring.length];
    total += toRad(lng2 - lng1) * (2 + Math.sin(toRad(lat1)) + Math.sin(toRad(lat2)));
  }
  return Math.abs((total * R * R) / 2);
}

/** Point at a given fraction along a path, used to sample elevation profiles. */
export function interpolatePath(points: LngLat[], fraction: number): LngLat {
  if (points.length === 1) return points[0];
  const total = pathLength(points);
  if (total === 0) return points[0];
  let target = fraction * total;
  for (let i = 1; i < points.length; i++) {
    const seg = distance(points[i - 1], points[i]);
    if (target <= seg || i === points.length - 1) {
      const t = seg === 0 ? 0 : target / seg;
      return [
        points[i - 1][0] + (points[i][0] - points[i - 1][0]) * t,
        points[i - 1][1] + (points[i][1] - points[i - 1][1]) * t,
      ];
    }
    target -= seg;
  }
  return points[points.length - 1];
}

/** Recognises "41.05, 40.74" and similar, so users can paste coordinates. */
export function parseLatLng(input: string): LngLat | null {
  const m = input.trim().match(/^(-?\d+(?:\.\d+)?)\s*[,;\s]\s*(-?\d+(?:\.\d+)?)$/);
  if (!m) return null;
  const lat = parseFloat(m[1]);
  const lng = parseFloat(m[2]);
  if (Math.abs(lat) > 90 || Math.abs(lng) > 180) return null;
  return [lng, lat];
}

// ---------------------------------------------------------------- formatting

export type Units = 'metric' | 'imperial';

export function formatArea(m2: number, units: Units): string {
  if (units === 'imperial') {
    const acres = m2 / 4046.8564224;
    return acres < 1 ? `${(m2 * 10.7639).toFixed(0)} ft²` : `${acres.toFixed(2)} acres`;
  }
  const ha = m2 / 10000;
  return ha < 0.1 ? `${m2.toFixed(0)} m²` : `${ha.toFixed(2)} ha`;
}

export function formatDistance(m: number, units: Units): string {
  if (units === 'imperial') {
    const ft = m * 3.280839895;
    return ft < 1000 ? `${ft.toFixed(0)} ft` : `${(ft / 5280).toFixed(2)} mi`;
  }
  return m < 1000 ? `${m.toFixed(0)} m` : `${(m / 1000).toFixed(2)} km`;
}

export function formatElevation(m: number, units: Units): string {
  return units === 'imperial' ? `${(m * 3.280839895).toFixed(0)} ft` : `${m.toFixed(0)} m`;
}

export function formatLngLat([lng, lat]: LngLat): string {
  return `${lat.toFixed(5)}, ${lng.toFixed(5)}`;
}
