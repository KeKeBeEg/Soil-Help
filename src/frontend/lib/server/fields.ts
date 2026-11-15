/**
 * Pure field logic shared by the API routes: polygon checks, the canonical
 * form that makes the same field always get the same ID, dates, and the R2
 * keys. No network access here, so it can be unit-tested on its own.
 */
import { createHash } from 'node:crypto';

export type LonLat = [number, number];
/** Open ring: the first vertex is not repeated at the end. */
export type Ring = LonLat[];

export const MAX_VERTICES = 500;
/** Same rule as the field-run workflow. */
export const FIELD_ID_RE = /^[a-z0-9][a-z0-9_-]{0,63}$/;
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
export const EARLIEST_DATE = '2001-01-01';

type Result<T> = { ok: true; value: T } | { ok: false; error: string };

/** Accepts a GeoJSON Polygon geometry (or a Feature holding one) without holes. */
export function parsePolygon(input: unknown): Result<Ring> {
  const geom = (input as { type?: string; geometry?: unknown })?.type === 'Feature'
    ? (input as { geometry: unknown }).geometry
    : input;
  const g = geom as { type?: string; coordinates?: unknown };
  if (!g || g.type !== 'Polygon' || !Array.isArray(g.coordinates)) {
    return { ok: false, error: 'polygon must be a GeoJSON Polygon' };
  }
  if (g.coordinates.length !== 1) return { ok: false, error: 'polygons with holes are not supported' };
  const raw = g.coordinates[0];
  if (!Array.isArray(raw) || raw.length < 4) return { ok: false, error: 'the polygon needs at least 3 corners' };
  if (raw.length > MAX_VERTICES + 1) return { ok: false, error: `the polygon has more than ${MAX_VERTICES} corners` };
  const ring: Ring = [];
  for (const p of raw) {
    if (!Array.isArray(p) || p.length < 2) return { ok: false, error: 'every corner must be [longitude, latitude]' };
    const [lon, lat] = [Number(p[0]), Number(p[1])];
    if (!Number.isFinite(lon) || !Number.isFinite(lat) || Math.abs(lon) > 180 || Math.abs(lat) > 90) {
      return { ok: false, error: 'a corner is outside longitude -180..180 / latitude -90..90' };
    }
    ring.push([lon, lat]);
  }
  const canon = canonicalRing(ring);
  if (canon.length < 3) return { ok: false, error: 'the polygon needs at least 3 distinct corners' };
  if (Math.abs(signedArea(canon)) === 0) return { ok: false, error: 'the polygon has no area' };
  if (selfIntersects(canon)) return { ok: false, error: 'the polygon crosses itself' };
  return { ok: true, value: canon };
}

const round6 = (x: number) => {
  const r = Math.round(x * 1e6) / 1e6;
  return r === 0 ? 0 : r; // no -0
};

/**
 * Same field, same ring: rounded to 6 decimals (~0.1 m), closing and repeated
 * corners removed, counter-clockwise, starting at the westernmost (then
 * southernmost) corner. Two drawings of the same outline give the same ID.
 */
export function canonicalRing(ring: Ring): Ring {
  let pts: Ring = ring.map(([x, y]) => [round6(x), round6(y)]);
  pts = pts.filter((p, i) => i === 0 || p[0] !== pts[i - 1][0] || p[1] !== pts[i - 1][1]);
  while (pts.length > 1 && pts[0][0] === pts[pts.length - 1][0] && pts[0][1] === pts[pts.length - 1][1]) pts.pop();
  if (pts.length < 3) return pts;
  if (signedArea(pts) < 0) pts.reverse();
  let start = 0;
  pts.forEach((p, i) => {
    const s = pts[start];
    if (p[0] < s[0] || (p[0] === s[0] && p[1] < s[1])) start = i;
  });
  return [...pts.slice(start), ...pts.slice(0, start)];
}

/** Shoelace in degrees; positive = counter-clockwise. */
function signedArea(r: Ring): number {
  let a = 0;
  for (let i = 0; i < r.length; i++) {
    const [x1, y1] = r[i];
    const [x2, y2] = r[(i + 1) % r.length];
    a += x1 * y2 - x2 * y1;
  }
  return a / 2;
}

/** Area in hectares, local flat approximation (fine for field-sized shapes). */
export function areaHectares(r: Ring): number {
  const lat0 = (r.reduce((s, p) => s + p[1], 0) / r.length) * (Math.PI / 180);
  const mx = 111_320 * Math.cos(lat0);
  const my = 110_540;
  let a = 0;
  for (let i = 0; i < r.length; i++) {
    const [x1, y1] = r[i];
    const [x2, y2] = r[(i + 1) % r.length];
    a += x1 * mx * (y2 * my) - x2 * mx * (y1 * my);
  }
  return Math.abs(a) / 2 / 10_000;
}

function orient(a: LonLat, b: LonLat, c: LonLat): number {
  const v = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0]);
  return v > 0 ? 1 : v < 0 ? -1 : 0;
}
function onSegment(a: LonLat, b: LonLat, p: LonLat): boolean {
  return Math.min(a[0], b[0]) <= p[0] && p[0] <= Math.max(a[0], b[0])
    && Math.min(a[1], b[1]) <= p[1] && p[1] <= Math.max(a[1], b[1]);
}
function segmentsTouch(a: LonLat, b: LonLat, c: LonLat, d: LonLat): boolean {
  const o1 = orient(a, b, c), o2 = orient(a, b, d), o3 = orient(c, d, a), o4 = orient(c, d, b);
  if (o1 !== o2 && o3 !== o4) return true;
  return (o1 === 0 && onSegment(a, b, c)) || (o2 === 0 && onSegment(a, b, d))
    || (o3 === 0 && onSegment(c, d, a)) || (o4 === 0 && onSegment(c, d, b));
}

/** True if any two non-neighbouring edges touch or cross. */
export function selfIntersects(r: Ring): boolean {
  const n = r.length;
  for (let i = 0; i < n; i++) {
    for (let j = i + 1; j < n; j++) {
      if (j === i + 1 || (i === 0 && j === n - 1)) continue; // neighbours share a corner
      if (segmentsTouch(r[i], r[(i + 1) % n], r[j], r[(j + 1) % n])) return true;
    }
  }
  return false;
}

export function closeRing(r: Ring): LonLat[] {
  return [...r, r[0]];
}

export function bbox(r: Ring): [number, number, number, number] {
  const xs = r.map((p) => p[0]), ys = r.map((p) => p[1]);
  return [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
}

/** "f" + 16 hex characters of SHA-256 of the canonical ring. Version-independent. */
export function fieldIdFor(r: Ring): string {
  return 'f' + createHash('sha256').update(JSON.stringify(r)).digest('hex').slice(0, 16);
}

/** Workflow slot for a field: stable, so the same field always queues in the same slot. */
export function slotFor(fieldId: string, slots: number): number {
  const h = createHash('sha256').update(fieldId).digest();
  return h.readUInt32BE(0) % slots;
}

export function isValidDate(s: unknown): s is string {
  if (typeof s !== 'string' || !DATE_RE.test(s)) return false;
  const d = new Date(`${s}T00:00:00Z`);
  return !Number.isNaN(d.getTime()) && d.toISOString().slice(0, 10) === s;
}

/** Last day the backend accepts: two days ago (IMERG fills in recent rain late). */
export function latestDate(now = new Date()): string {
  return new Date(now.getTime() - 2 * 86_400_000).toISOString().slice(0, 10);
}

export function checkRange(start: string, end: string, now = new Date()): string | null {
  if (!isValidDate(start) || !isValidDate(end)) return 'dates must be valid YYYY-MM-DD';
  if (start > end) return 't_start is after t_end';
  if (start < EARLIEST_DATE) return `dates start at ${EARLIEST_DATE}`;
  const last = latestDate(now);
  if (end > last) return `the latest date available is ${last}`;
  return null;
}

export function climateRange(years: [number, number]): [string, string] {
  return [`${years[0]}-01-01`, `${years[1]}-12-31`];
}

export function rangeId(start: string, end: string): string {
  return `${start}_${end}`;
}

export const keys = {
  field: (v: number, id: string) => `fields/v${v}/${id}`,
  request: (v: number, id: string) => `fields/v${v}/${id}/request.json`,
  /** One status file per date range, so two ranges of one field never overwrite each other. */
  status: (v: number, id: string, range: string) => `fields/v${v}/${id}/status/${range}.json`,
  analysisDone: (v: number, id: string) => `fields/v${v}/${id}/analysis/daily_series.csv`,
  analysis: (v: number, id: string) => `fields/v${v}/${id}/analysis/`,
  view: (v: number, id: string, range: string) => `views/v${v}/${id}/${range}/`,
  viewDone: (v: number, id: string, range: string) => `views/v${v}/${id}/${range}/field_summary.json`,
};

/** Keys the file proxy may serve. */
export const SERVABLE_KEY_RE = /^(index\.json|(fields|views)\/v\d{1,3}\/[a-z0-9][a-z0-9_-]{0,63}\/[A-Za-z0-9._-]+(\/[A-Za-z0-9._-]+)*)$/;
export function isServableKey(key: string): boolean {
  return SERVABLE_KEY_RE.test(key) && !key.split('/').some((s) => s === '.' || s === '..');
}
