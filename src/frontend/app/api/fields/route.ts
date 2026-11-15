import { timingSafeEqual } from 'node:crypto';
import { NextResponse } from 'next/server';
import { ConfigError, limits } from '@/lib/server/env';
import {
  areaHectares, checkRange, climateRange, closeRing, fieldIdFor, keys, parsePolygon, rangeId, slotFor,
} from '@/lib/server/fields';
import { analysisVersion, dispatchFieldRun } from '@/lib/server/github';
import { r2GetJson, r2PutJson } from '@/lib/server/r2';
import { fieldState, type StoredRequest, type StoredStatus } from '@/lib/server/status';

/**
 * Submit a field: POST /api/fields
 *   body    { polygon: GeoJSON Polygon, label?: string, t_start?: "YYYY-MM-DD", t_end?: "YYYY-MM-DD" }
 *   header  x-demo-passcode (only if DEMO_PASSCODE is set)
 *
 * The field ID comes from the polygon itself, so drawing the same field again
 * reuses its results. Answers:
 *   200 { state: "done", files, ... }                    results already published
 *   202 { state: "queued" | "running", ... }             a run is (now) under way; poll /api/fields/<id>
 *   4xx { error }                                        bad input
 */
export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';
export const fetchCache = 'force-no-store';
export const maxDuration = 30;

const NO_STORE = { 'Cache-Control': 'no-store' };

function fail(status: number, error: string) {
  return NextResponse.json({ error }, { status, headers: NO_STORE });
}

function passcodeOk(given: string | null, expected: string): boolean {
  if (!given) return false;
  const a = Buffer.from(given), b = Buffer.from(expected);
  return a.length === b.length && timingSafeEqual(a, b);
}

export async function POST(request: Request) {
  try {
    const lim = limits();
    if (lim.passcode && !passcodeOk(request.headers.get('x-demo-passcode'), lim.passcode)) {
      return fail(401, 'wrong or missing passcode');
    }

    let body: { polygon?: unknown; label?: unknown; t_start?: unknown; t_end?: unknown };
    try {
      body = await request.json();
    } catch {
      return fail(400, 'the body must be JSON');
    }

    const parsed = parsePolygon(body.polygon);
    if (!parsed.ok) return fail(422, parsed.error);
    const ring = parsed.value;
    const area = areaHectares(ring);
    if (area > lim.maxAreaHa) {
      return fail(413, `the field is ${area.toFixed(1)} ha; the limit is ${lim.maxAreaHa} ha`);
    }

    const hasStart = typeof body.t_start === 'string' && body.t_start !== '';
    const hasEnd = typeof body.t_end === 'string' && body.t_end !== '';
    if (hasStart !== hasEnd) return fail(422, 'give both t_start and t_end, or neither');

    const { version, climateYears } = await analysisVersion();
    const id = fieldIdFor(ring);
    const stored = await r2GetJson<StoredRequest>(keys.request(version, id));
    const [start, end] = hasStart
      ? [body.t_start as string, body.t_end as string]
      : stored?.t_range ?? climateRange(climateYears);
    const rangeError = checkRange(start, end);
    if (rangeError) return fail(422, rangeError);

    // request.json is written once and never edited (the workflow reads the polygon from it).
    if (!stored) {
      const label = typeof body.label === 'string' && body.label.trim()
        ? body.label.trim().slice(0, 120)
        : `Field ${id}`;
      const req: StoredRequest = {
        schema_version: 1,
        field_id: id,
        label,
        created_at: new Date().toISOString(),
        polygon: { type: 'Polygon', coordinates: [closeRing(ring)] },
        t_range: [start, end],
        area_ha: Math.round(area * 100) / 100,
      };
      await r2PutJson(keys.request(version, id), req);
    }

    const state = await fieldState(version, id, start, end);
    if (state.state === 'done') return NextResponse.json(state, { status: 200, headers: NO_STORE });
    if (state.state === 'queued' || state.state === 'running') {
      return NextResponse.json({ ...state, deduplicated: true }, { status: 202, headers: NO_STORE });
    }

    // Start a run. The status is written first so the page has something to poll at once.
    const statusKey = keys.status(version, id, rangeId(start, end));
    const queued: StoredStatus = {
      state: 'queued',
      stage: 'waiting for a runner',
      message: '',
      field_id: id,
      range: rangeId(start, end),
      run_id: '',
      run_url: '',
      updated_at: new Date().toISOString(),
    };
    await r2PutJson(statusKey, queued);

    let run;
    try {
      run = await dispatchFieldRun({
        field_id: id,
        slot: String(slotFor(id, lim.slots)),
        t_start: start,
        t_end: end,
      });
    } catch (e) {
      console.error(e);
      await r2PutJson(statusKey, {
        ...queued, state: 'failed', stage: 'starting', message: 'Could not start the analysis. Try again later.',
        updated_at: new Date().toISOString(),
      });
      if (e instanceof ConfigError) return fail(500, `server not configured: ${e.message}`);
      return fail(502, 'could not start the analysis');
    }

    // Record the run ID, unless the workflow has already taken over the status file.
    if (run.runId) {
      const current = await r2GetJson<StoredStatus>(statusKey);
      if (current?.state === 'queued' && !current.run_id) {
        await r2PutJson(statusKey, { ...current, run_id: String(run.runId), run_url: run.runUrl ?? '' });
      }
    }

    return NextResponse.json(
      { ...state, state: 'queued', stage: 'waiting for a runner', run_url: run.runUrl ?? '', updated_at: queued.updated_at },
      { status: 202, headers: NO_STORE },
    );
  } catch (e) {
    console.error(e);
    if (e instanceof ConfigError) return fail(500, `server not configured: ${e.message}`);
    return fail(500, 'internal error');
  }
}
