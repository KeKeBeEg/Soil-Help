/**
 * GitHub calls: start the field-run workflow, look up a run, and read the
 * backend's analysis_version.json from the branch the workflow runs on, so
 * the app and the workflow always agree on the version (= storage paths).
 */
import { githubConfig } from './env';

const API = 'https://api.github.com';

function headers(accept = 'application/vnd.github+json'): Record<string, string> {
  return {
    Authorization: `Bearer ${githubConfig().token}`,
    Accept: accept,
    'X-GitHub-Api-Version': '2022-11-28',
    'User-Agent': 'soil-help-api',
  };
}

export interface AnalysisVersion {
  version: number;
  climateYears: [number, number];
}

let versionCache: { value: AnalysisVersion; at: number } | null = null;
const VERSION_TTL_MS = 60_000;

export async function analysisVersion(): Promise<AnalysisVersion> {
  if (versionCache && Date.now() - versionCache.at < VERSION_TTL_MS) return versionCache.value;
  const g = githubConfig();
  const res = await fetch(`${API}/repos/${g.repo}/contents/analysis_version.json?ref=${encodeURIComponent(g.ref)}`, {
    headers: headers('application/vnd.github.raw+json'),
  });
  if (!res.ok) throw new Error(`GitHub: cannot read analysis_version.json (${res.status})`);
  const raw = (await res.json()) as { version: number; climate_years: [number, number] };
  const value: AnalysisVersion = { version: Number(raw.version), climateYears: raw.climate_years };
  if (!Number.isInteger(value.version) || value.climateYears?.length !== 2) {
    throw new Error('GitHub: analysis_version.json has an unexpected shape');
  }
  versionCache = { value, at: Date.now() };
  return value;
}

export interface DispatchResult {
  runId?: number;
  runUrl?: string;
}

/** Starts field-run. GitHub answers 200 with the run's ID, or 204 without it on older API behaviour. */
export async function dispatchFieldRun(inputs: Record<string, string>): Promise<DispatchResult> {
  const g = githubConfig();
  const res = await fetch(`${API}/repos/${g.repo}/actions/workflows/${g.workflow}/dispatches`, {
    method: 'POST',
    headers: { ...headers(), 'Content-Type': 'application/json' },
    body: JSON.stringify({ ref: g.ref, inputs }),
  });
  if (res.status === 204) return {};
  if (res.status === 200) {
    const body = (await res.json().catch(() => ({}))) as { workflow_run_id?: number; html_url?: string };
    return { runId: body.workflow_run_id, runUrl: body.html_url };
  }
  throw new Error(`GitHub dispatch failed (${res.status}): ${(await res.text()).slice(0, 300)}`);
}

export interface RunInfo {
  status: string; // queued | in_progress | completed | waiting | ...
  conclusion: string | null; // success | failure | cancelled | ...
  htmlUrl: string;
}

export async function getRun(runId: string | number): Promise<RunInfo | null> {
  const g = githubConfig();
  const res = await fetch(`${API}/repos/${g.repo}/actions/runs/${encodeURIComponent(String(runId))}`, {
    headers: headers(),
  });
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`GitHub run lookup failed (${res.status})`);
  const r = (await res.json()) as { status: string; conclusion: string | null; html_url: string };
  return { status: r.status, conclusion: r.conclusion, htmlUrl: r.html_url };
}
