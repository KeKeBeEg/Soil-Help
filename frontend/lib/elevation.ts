/**
 * Elevation sampling straight from Terrarium-encoded DEM tiles.
 *
 * Reading the tiles ourselves (rather than asking the map) means the
 * elevation profile works in flat 2D mode, in Lite mode, and before the 3D
 * terrain has finished loading.
 */
import { demTileUrl } from './config';
import type { LngLat } from './geo';

const TILE_SIZE = 256;
const cache = new Map<string, Promise<Uint8ClampedArray | null>>();

function loadTile(z: number, x: number, y: number): Promise<Uint8ClampedArray | null> {
  const key = `${z}/${x}/${y}`;
  const hit = cache.get(key);
  if (hit) return hit;

  const task = new Promise<Uint8ClampedArray | null>((resolve) => {
    const img = new Image();
    img.crossOrigin = 'anonymous';
    img.onload = () => {
      try {
        const canvas = document.createElement('canvas');
        canvas.width = TILE_SIZE;
        canvas.height = TILE_SIZE;
        const ctx = canvas.getContext('2d', { willReadFrequently: true });
        if (!ctx) return resolve(null);
        ctx.drawImage(img, 0, 0, TILE_SIZE, TILE_SIZE);
        resolve(ctx.getImageData(0, 0, TILE_SIZE, TILE_SIZE).data);
      } catch {
        // Tainted canvas: the tile host did not send CORS headers.
        resolve(null);
      }
    };
    img.onerror = () => resolve(null);
    img.src = demTileUrl(z, x, y);
  });

  cache.set(key, task);
  return task;
}

/** Web-Mercator position of a coordinate, in fractional tiles. */
function project([lng, lat]: LngLat, z: number) {
  const n = 2 ** z;
  const latRad = (lat * Math.PI) / 180;
  const x = ((lng + 180) / 360) * n;
  const y = ((1 - Math.log(Math.tan(latRad) + 1 / Math.cos(latRad)) / Math.PI) / 2) * n;
  return { x, y };
}

function decode(data: Uint8ClampedArray, px: number, py: number): number {
  const i = (py * TILE_SIZE + px) * 4;
  return data[i] * 256 + data[i + 1] + data[i + 2] / 256 - 32768;
}

/**
 * Elevation in metres for each point. Entries are `null` where the tile could
 * not be read. Tiles are fetched once each and reused.
 */
export async function sampleElevations(
  points: LngLat[],
  zoom = 13,
): Promise<(number | null)[]> {
  const z = Math.max(0, Math.min(14, Math.round(zoom)));

  // Group the points by the tile they fall in, so each tile loads once.
  const byTile = new Map<string, number[]>();
  const projected = points.map((p) => project(p, z));
  projected.forEach((p, i) => {
    const key = `${Math.floor(p.x)}/${Math.floor(p.y)}`;
    const list = byTile.get(key);
    if (list) list.push(i);
    else byTile.set(key, [i]);
  });

  const out = new Array<number | null>(points.length).fill(null);

  await Promise.all(
    [...byTile.entries()].map(async ([key, indices]) => {
      const [tx, ty] = key.split('/').map(Number);
      const data = await loadTile(z, tx, ty);
      if (!data) return;
      for (const i of indices) {
        const px = Math.min(TILE_SIZE - 1, Math.floor((projected[i].x - tx) * TILE_SIZE));
        const py = Math.min(TILE_SIZE - 1, Math.floor((projected[i].y - ty) * TILE_SIZE));
        out[i] = decode(data, px, py);
      }
    }),
  );

  return out;
}

/** Elevation for a single point, or null. */
export async function sampleElevation(point: LngLat, zoom = 13): Promise<number | null> {
  const [v] = await sampleElevations([point], zoom);
  return v;
}
