import { NextResponse } from 'next/server';
import { ConfigError } from '@/lib/server/env';
import { climateRange, FIELD_ID_RE, isValidDate, keys } from '@/lib/server/fields';
import { analysisVersion } from '@/lib/server/github';
import { r2GetJson } from '@/lib/server/r2';
import { fieldState, type StoredRequest } from '@/lib/server/status';

/**
 * Where a field stands: GET /api/fields/<id>?t_start=YYYY-MM-DD&t_end=YYYY-MM-DD
 * Without dates: the dates the field was first submitted with.
 * Answers { state: "done" | "queued" | "running" | "failed" | "missing", stage, message, files? }.
 * Poll every few seconds while queued or running.
 */
export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';
export const fetchCache = 'force-no-store';
export const maxDuration = 30;

const NO_STORE = { 'Cache-Control': 'no-store' };

export async function GET(request: Request, { params }: { params: Promise<{ id: string }> }) {
  try {
    const { id } = await params;
    if (!FIELD_ID_RE.test(id)) return NextResponse.json({ error: 'bad field id' }, { status: 400, headers: NO_STORE });

    const q = new URL(request.url).searchParams;
    const qs = q.get('t_start'), qe = q.get('t_end');
    const { version, climateYears } = await analysisVersion();

    let start: string, end: string;
    if (qs || qe) {
      if (!isValidDate(qs) || !isValidDate(qe)) {
        return NextResponse.json({ error: 'give t_start and t_end as YYYY-MM-DD' }, { status: 400, headers: NO_STORE });
      }
      [start, end] = [qs, qe];
    } else {
      const stored = await r2GetJson<StoredRequest>(keys.request(version, id));
      if (!stored) return NextResponse.json({ error: 'unknown field' }, { status: 404, headers: NO_STORE });
      [start, end] = stored.t_range ?? climateRange(climateYears);
    }

    return NextResponse.json(await fieldState(version, id, start, end), { headers: NO_STORE });
  } catch (e) {
    console.error(e);
    const message = e instanceof ConfigError ? `server not configured: ${e.message}` : 'internal error';
    return NextResponse.json({ error: message }, { status: 500, headers: NO_STORE });
  }
}
