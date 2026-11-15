/**
 * Server-only settings, read from environment variables at request time
 * (Vercel → Project → Settings → Environment Variables). Never import this
 * from a client component: it holds keys.
 */

function required(name: string): string {
  const value = process.env[name]?.trim();
  if (!value) throw new ConfigError(`${name} is not set`);
  return value;
}

function optional(name: string, fallback: string): string {
  return process.env[name]?.trim() || fallback;
}

export class ConfigError extends Error {}

export function r2Config() {
  return {
    accountId: required('R2_ACCOUNT_ID'),
    accessKeyId: required('R2_ACCESS_KEY_ID'),
    secretAccessKey: required('R2_SECRET_ACCESS_KEY'),
    bucket: optional('R2_BUCKET_RESULTS', 'soilhelp-results'),
  };
}

export function githubConfig() {
  return {
    token: required('GITHUB_TOKEN'),
    repo: optional('GITHUB_REPO', 'KeKeBeEg/Soil-Help'),
    workflow: optional('GITHUB_WORKFLOW', 'field-run.yml'),
    ref: optional('GITHUB_REF', 'main'),
  };
}

export function limits() {
  return {
    /** Parallel workflow slots (1–10). Fields are spread over them by ID. */
    slots: Math.min(10, Math.max(1, Number(optional('FIELD_SLOTS', '3')) || 3)),
    /** Largest field accepted, in hectares. */
    maxAreaHa: Number(optional('MAX_FIELD_AREA_HA', '500')) || 500,
    /** If set, submissions must send this in the x-demo-passcode header. */
    passcode: process.env.DEMO_PASSCODE?.trim() || '',
  };
}
