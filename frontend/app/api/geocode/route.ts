import { NextResponse } from 'next/server';

/**
 * Place search, proxied to OpenStreetMap's Nominatim.
 *
 * It runs server-side for two reasons: Nominatim asks for an identifying
 * User-Agent, which a browser cannot set, and the response can be cached here
 * so repeated searches do not hit the service again.
 */
export const revalidate = 3600;

export async function GET(request: Request) {
  const q = new URL(request.url).searchParams.get('q')?.trim();
  if (!q) return NextResponse.json([], { status: 400 });

  const url = new URL('https://nominatim.openstreetmap.org/search');
  url.searchParams.set('q', q);
  url.searchParams.set('format', 'jsonv2');
  url.searchParams.set('limit', '5');
  url.searchParams.set('addressdetails', '0');

  try {
    const upstream = await fetch(url, {
      headers: {
        'User-Agent': 'Yamac-FieldViewer/0.1 (NASA Space Apps project)',
        'Accept-Language': 'tr,en',
      },
      next: { revalidate: 3600 },
    });

    if (!upstream.ok) {
      return NextResponse.json({ error: 'upstream' }, { status: 502 });
    }

    const raw = (await upstream.json()) as {
      lat: string;
      lon: string;
      display_name: string;
      type?: string;
    }[];

    const results = raw.map((r) => ({
      lat: parseFloat(r.lat),
      lon: parseFloat(r.lon),
      name: r.display_name,
      type: r.type ?? null,
    }));

    return NextResponse.json(results, {
      headers: { 'Cache-Control': 'public, max-age=3600, s-maxage=86400' },
    });
  } catch {
    return NextResponse.json({ error: 'unreachable' }, { status: 502 });
  }
}
