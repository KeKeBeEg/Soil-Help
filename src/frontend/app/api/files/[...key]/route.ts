import { isServableKey } from '@/lib/server/fields';
import { r2Fetch } from '@/lib/server/r2';

/**
 * Read-only pass-through for result files: GET/HEAD /api/files/<key>
 *   e.g. /api/files/index.json
 *        /api/files/fields/v3/<id>/analysis/soil_loss_window_by_year.tif
 *
 * The browser only ever talks to this site, never to r2.dev (blocked in
 * Turkey). Range requests are passed through, so GeoTIFF readers can fetch
 * parts of a file. Responses are streamed, so large files are fine.
 */
export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';
export const fetchCache = 'force-no-store';
export const maxDuration = 60;

const FORWARD_REQUEST = ['range', 'if-none-match', 'if-modified-since'];
const FORWARD_RESPONSE = ['content-type', 'content-length', 'content-range', 'accept-ranges', 'etag', 'last-modified'];
/** Files that change while a field is being processed must never be cached. */
const LIVE_FILE = /(^index\.json|\/entry\.json|\/status\/[^/]+\.json)$/;

async function handle(request: Request, params: Promise<{ key: string[] }>, method: 'GET' | 'HEAD') {
  const { key: parts } = await params;
  const key = parts.map(decodeURIComponent).join('/');
  if (!isServableKey(key)) return new Response('not found', { status: 404 });

  const headers: Record<string, string> = {};
  for (const h of FORWARD_REQUEST) {
    const v = request.headers.get(h);
    if (v) headers[h] = v;
  }

  let upstream: Response;
  try {
    upstream = await r2Fetch(key, method, headers);
  } catch (e) {
    console.error(e);
    return new Response('storage unavailable', { status: 502 });
  }
  if (upstream.status === 404) return new Response('not found', { status: 404 });
  if (![200, 206, 304, 416].includes(upstream.status)) {
    console.error(`R2 ${method} ${key}: ${upstream.status}`);
    return new Response('storage error', { status: 502 });
  }

  const out = new Headers();
  for (const h of FORWARD_RESPONSE) {
    const v = upstream.headers.get(h);
    if (v) out.set(h, v);
  }
  out.set('Cache-Control', LIVE_FILE.test(key) ? 'no-store' : 'public, max-age=300');
  return new Response(method === 'HEAD' ? null : upstream.body, { status: upstream.status, headers: out });
}

export function GET(request: Request, { params }: { params: Promise<{ key: string[] }> }) {
  return handle(request, params, 'GET');
}

export function HEAD(request: Request, { params }: { params: Promise<{ key: string[] }> }) {
  return handle(request, params, 'HEAD');
}
