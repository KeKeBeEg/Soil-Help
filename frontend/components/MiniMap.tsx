'use client';

import { RASTER_SOURCES } from '@/lib/config';
import { useTelemetry } from '@/lib/telemetry';

const WIDTH = 148;
const HEIGHT = 96;

/**
 * The world inset, as in Google Earth.
 *
 * It is one zoom-0 tile — a single request, no second map instance and no GPU
 * cost — with a marker placed by the Web Mercator formula.
 */
export default function MiniMap() {
  const { center } = useTelemetry();
  const [lng, lat] = center;

  const x = ((lng + 180) / 360) * WIDTH;
  const clampedLat = Math.max(-85.05, Math.min(85.05, lat));
  const latRad = (clampedLat * Math.PI) / 180;
  const mercatorY =
    (1 - Math.log(Math.tan(latRad) + 1 / Math.cos(latRad)) / Math.PI) / 2;
  // The tile is square; the inset is a letterboxed crop of its middle.
  const y = mercatorY * WIDTH - (WIDTH - HEIGHT) / 2;

  const url = RASTER_SOURCES.satellite
    .replace('{z}', '0')
    .replace('{y}', '0')
    .replace('{x}', '0');

  return (
    <div className="minimap" style={{ width: WIDTH, height: HEIGHT }} aria-hidden="true">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={url}
        alt=""
        width={WIDTH}
        height={WIDTH}
        style={{ marginTop: -(WIDTH - HEIGHT) / 2 }}
        draggable={false}
      />
      <span
        className="minimap-dot"
        style={{ left: x, top: Math.max(4, Math.min(HEIGHT - 4, y)) }}
      />
    </div>
  );
}
