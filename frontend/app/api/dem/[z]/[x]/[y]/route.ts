import { NextResponse } from 'next/server';

/**
 * Elevation-tile proxy, used only when NEXT_PUBLIC_DEM_PROXY=1.
 *
 * The tile host normally serves CORS headers and the browser can read the
 * tiles directly. Where a network or corporate proxy strips them, routing the
 * tiles through this app restores both the 3D terrain and the elevation
 * profile without any other change.
 */
const UPSTREAM = 'https://elevation-tiles-prod.s3.amazonaws.com/terrarium';

export async function GET(
  _request: Request,
  { params }: { params: Promise<{ z: string; x: string; y: string }> },
) {
  const { z, x, y } = await params;

  // Reject anything that is not a plain tile coordinate.
  const zi = Number(z);
  const xi = Number(x);
  const yi = Number(y.replace(/\.png$/, ''));
  if (![zi, xi, yi].every(Number.isInteger) || zi < 0 || zi > 15 || xi < 0 || yi < 0) {
    return NextResponse.json({ error: 'bad tile' }, { status: 400 });
  }

  try {
    const upstream = await fetch(`${UPSTREAM}/${zi}/${xi}/${yi}.png`, {
      next: { revalidate: 86400 },
    });
    if (!upstream.ok) return new NextResponse(null, { status: upstream.status });

    return new NextResponse(upstream.body, {
      headers: {
        'Content-Type': 'image/png',
        'Cache-Control': 'public, max-age=86400, s-maxage=604800, immutable',
        'Access-Control-Allow-Origin': '*',
      },
    });
  } catch {
    return new NextResponse(null, { status: 502 });
  }
}
