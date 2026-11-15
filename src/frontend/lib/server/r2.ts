/**
 * Minimal R2 access through its S3 API, signed with aws4fetch.
 * Uses the soilhelp-api token (Object Read & Write on the results bucket only).
 */
import { AwsClient } from 'aws4fetch';
import { r2Config } from './env';

let client: AwsClient | null = null;

function aws(): AwsClient {
  if (!client) {
    const c = r2Config();
    client = new AwsClient({
      accessKeyId: c.accessKeyId,
      secretAccessKey: c.secretAccessKey,
      service: 's3',
      region: 'auto',
    });
  }
  return client;
}

function objectUrl(key: string): string {
  const c = r2Config();
  const path = key.split('/').map(encodeURIComponent).join('/');
  return `https://${c.accountId}.r2.cloudflarestorage.com/${c.bucket}/${path}`;
}

/** Raw GET or HEAD of one object; the caller handles the status code. */
export function r2Fetch(key: string, method: 'GET' | 'HEAD' = 'GET', headers: Record<string, string> = {}) {
  return aws().fetch(objectUrl(key), { method, headers });
}

export async function r2Exists(key: string): Promise<boolean> {
  const res = await r2Fetch(key, 'HEAD');
  if (res.status === 404) return false;
  if (!res.ok) throw new Error(`R2 HEAD ${key}: ${res.status}`);
  return true;
}

export async function r2GetJson<T>(key: string): Promise<T | null> {
  const res = await r2Fetch(key);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`R2 GET ${key}: ${res.status}`);
  return (await res.json()) as T;
}

export async function r2PutJson(key: string, value: unknown, cacheControl = 'no-store'): Promise<void> {
  const res = await aws().fetch(objectUrl(key), {
    method: 'PUT',
    body: JSON.stringify(value),
    headers: { 'Content-Type': 'application/json', 'Cache-Control': cacheControl },
  });
  if (!res.ok) throw new Error(`R2 PUT ${key}: ${res.status} ${await res.text()}`);
}
