/**
 * Works out where a field stands for one date range, from what is in R2
 * (published files + status/<range>.json) and, if a run looks stuck, from GitHub.
 */
import { getRun } from './github';
import { keys, rangeId } from './fields';
import { r2Exists, r2GetJson } from './r2';

/** status/<range>.json as written by the submit route and the field-run workflow. */
export interface StoredStatus {
  state: 'queued' | 'running' | 'done' | 'failed';
  stage: string;
  message: string;
  field_id: string;
  range?: string;
  run_id?: string;
  run_url?: string;
  updated_at: string;
}

/** request.json, written once per field by the submit route. */
export interface StoredRequest {
  schema_version: number;
  field_id: string;
  label: string;
  created_at: string;
  polygon: { type: 'Polygon'; coordinates: number[][][] };
  t_range?: [string, string];
  area_ha?: number;
}

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
  /** Present when state is "done": folder URLs to read files from (append a file name). */
  files?: { analysis: string; view: string };
}

/** A queued/running status older than this is double-checked with GitHub. */
const STALE_MS = 30 * 60_000;

export async function fieldState(version: number, id: string, start: string, end: string): Promise<FieldState> {
  const range = rangeId(start, end);
  const [analysisDone, viewDone, status] = await Promise.all([
    r2Exists(keys.analysisDone(version, id)),
    r2Exists(keys.viewDone(version, id, range)),
    r2GetJson<StoredStatus>(keys.status(version, id, range)),
  ]);
  const base: FieldState = {
    field_id: id,
    version,
    range: { start, end },
    state: 'missing',
    stage: '',
    message: '',
    run_url: status?.run_url ?? '',
    updated_at: status?.updated_at ?? '',
  };

  if (analysisDone && viewDone) {
    return {
      ...base,
      state: 'done',
      stage: 'finished',
      files: {
        analysis: `/api/files/${keys.analysis(version, id)}`,
        view: `/api/files/${keys.view(version, id, range)}`,
      },
    };
  }
  // No run for these dates (never asked, or the results have since expired): the caller may submit.
  if (!status || status.state === 'done') return base;

  const reported = { ...base, state: status.state, stage: status.stage, message: status.message } as FieldState;
  if (status.state === 'failed') return reported;

  // queued / running: trust it unless it is old, then ask GitHub what happened.
  const age = Date.now() - Date.parse(status.updated_at);
  if (!(age > STALE_MS)) return reported;
  if (!status.run_id) {
    return { ...reported, state: 'failed', stage: 'lost', message: 'The run never reported back; submit again.' };
  }
  const run = await getRun(status.run_id).catch(() => null);
  if (run && run.status === 'completed') {
    return {
      ...reported,
      state: 'failed',
      stage: 'ended',
      message: `The run ended (${run.conclusion ?? 'unknown'}) without these results; submit again.`,
      run_url: run.htmlUrl,
    };
  }
  return reported; // still queued or running on GitHub
}
