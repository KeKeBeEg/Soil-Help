/**
 * Browser-side client for the field API (app/api/fields, app/api/files).
 * Everything goes through this site; the browser never talks to R2 or GitHub.
 *
 *   const r = await submitField(polygon, { label: 'North field', start: '2024-03-01', end: '2024-09-30' });
 *   const done = await waitForField(r.field_id, r.range, s => setStage(s.stage));
 *   const tif = fileUrl(done.files!.analysis, 'soil_loss_window_by_year.tif');
 */

export type FieldStateName = 'done' | 'queued' | 'running' | 'failed' | 'missing';

export interface FieldState {
  field_id: string;
  version: number;
  range: { start: string; end: string };
  state: FieldStateName;
  stage: string;
  message: string;
  run_url: string;
  updated_at: string;
  /** Folder URLs, present when state is "done". Use fileUrl() to point at a file inside. */
  files?: { analysis: string; view: string };
  deduplicated?: boolean;
}

export interface FieldIndexEntry {
  field_id: string;
  label: string;
  version: number;
  data_path: string;
  climate_years: [number, number];
  defaults_used: string[];
  bbox: [number, number, number, number];
  analysis: string;
  latest_view: string;
  updated_at: string;
}

export interface FieldIndex {
  schema_version: number;
  version: number;
  generated_at: string;
  fields: FieldIndexEntry[];
}

/** GeoJSON Polygon (one outer ring, [lon, lat]) or a Feature holding one. */
export type PolygonInput =
  | { type: 'Polygon'; coordinates: number[][][] }
  | { type: 'Feature'; geometry: { type: 'Polygon'; coordinates: number[][][] }; properties?: unknown };

export class FieldApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

async function call<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, { cache: 'no-store', ...init });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new FieldApiError(res.status, body.error ?? `HTTP ${res.status}`);
  return body as T;
}

/** Ask for a field. Same polygon → same field_id, so repeats return stored results. */
export function submitField(
  polygon: PolygonInput,
  opts: { label?: string; start?: string; end?: string; passcode?: string } = {},
): Promise<FieldState> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (opts.passcode) headers['x-demo-passcode'] = opts.passcode;
  return call<FieldState>('/api/fields', {
    method: 'POST',
    headers,
    body: JSON.stringify({ polygon, label: opts.label, t_start: opts.start, t_end: opts.end }),
  });
}

/** Where a field stands. Without a range: the dates it was first submitted with. */
export function getField(fieldId: string, range?: { start: string; end: string }): Promise<FieldState> {
  const q = range ? `?t_start=${range.start}&t_end=${range.end}` : '';
  return call<FieldState>(`/api/fields/${encodeURIComponent(fieldId)}${q}`);
}

/**
 * Poll until the field is done or failed. Resolves with the final state
 * (check .state). Pass an AbortSignal to stop polling, e.g. on unmount.
 */
export async function waitForField(
  fieldId: string,
  range: { start: string; end: string },
  onUpdate?: (s: FieldState) => void,
  signal?: AbortSignal,
  intervalMs = 5000,
): Promise<FieldState> {
  for (;;) {
    if (signal?.aborted) throw new DOMException('aborted', 'AbortError');
    const s = await getField(fieldId, range);
    onUpdate?.(s);
    if (s.state === 'done' || s.state === 'failed' || s.state === 'missing') return s;
    await new Promise((r) => setTimeout(r, intervalMs));
  }
}

/** URL of one file inside a results folder from FieldState.files. */
export function fileUrl(folder: string, name: string): string {
  return `${folder.replace(/\/$/, '')}/${name}`;
}

/** All published fields, newest first. */
export function getIndex(): Promise<FieldIndex> {
  return call<FieldIndex>('/api/files/index.json');
}
