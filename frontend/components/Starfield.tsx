'use client';

import { useCallback, useEffect, useRef } from 'react';

/**
 * The night sky behind the globe.
 *
 * Drawn once into a canvas at device pixel ratio, so the stars are real
 * single pixels rather than a blurry scaled image, and nothing animates —
 * a still backdrop costs one paint and no frame budget on a weak phone.
 */

/** Deterministic PRNG, so resizing does not reshuffle the sky. */
function mulberry32(seed: number) {
  let a = seed;
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Faint colour cast, the way real stars differ. */
const TINTS = [
  [255, 255, 255],
  [255, 255, 255],
  [255, 255, 255],
  [202, 221, 255], // hot blue-white
  [255, 236, 210], // cool amber
];

function draw(canvas: HTMLCanvasElement, lowDetail: boolean) {
  const rect = canvas.getBoundingClientRect();
  const w = Math.max(1, Math.round(rect.width));
  const h = Math.max(1, Math.round(rect.height));
  const dpr = Math.min(window.devicePixelRatio || 1, lowDetail ? 1 : 2);

  canvas.width = w * dpr;
  canvas.height = h * dpr;
  const ctx = canvas.getContext('2d');
  if (!ctx) return;
  ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, w, h);

  const rand = mulberry32(0x5eed);

  // A faint band of diffuse light, like the Milky Way crossing the frame.
  if (!lowDetail) {
    ctx.save();
    ctx.translate(w * 0.5, h * 0.5);
    ctx.rotate(-0.42);
    const band = ctx.createLinearGradient(0, -h * 0.34, 0, h * 0.34);
    band.addColorStop(0, 'rgba(120, 140, 190, 0)');
    band.addColorStop(0.46, 'rgba(132, 150, 198, 0.055)');
    band.addColorStop(0.54, 'rgba(146, 136, 190, 0.045)');
    band.addColorStop(1, 'rgba(120, 140, 190, 0)');
    ctx.fillStyle = band;
    ctx.fillRect(-w, -h * 0.34, w * 2, h * 0.68);
    ctx.restore();
  }

  const area = w * h;
  const count = Math.round(area / (lowDetail ? 3000 : 1000));

  for (let i = 0; i < count; i++) {
    const x = rand() * w;
    const y = rand() * h;

    // Most stars are faint pinpricks; a few are bright.
    const roll = rand();
    const radius = roll > 0.992 ? 1.15 + rand() * 0.75 : roll > 0.94 ? 0.75 : 0.45 + rand() * 0.22;
    const alpha = roll > 0.992 ? 0.9 + rand() * 0.1 : 0.28 + rand() * 0.55;
    const [r, g, b] = TINTS[Math.floor(rand() * TINTS.length)];

    ctx.beginPath();
    ctx.fillStyle = `rgba(${r}, ${g}, ${b}, ${alpha.toFixed(3)})`;
    ctx.arc(x, y, radius, 0, Math.PI * 2);
    ctx.fill();

    // The brightest few get a soft halo, which is what sells the depth.
    if (!lowDetail && roll > 0.992) {
      const glow = ctx.createRadialGradient(x, y, 0, x, y, radius * 6);
      glow.addColorStop(0, `rgba(${r}, ${g}, ${b}, 0.28)`);
      glow.addColorStop(1, `rgba(${r}, ${g}, ${b}, 0)`);
      ctx.fillStyle = glow;
      ctx.beginPath();
      ctx.arc(x, y, radius * 6, 0, Math.PI * 2);
      ctx.fill();
    }
  }
}

export default function Starfield({ lowDetail = false }: { lowDetail?: boolean }) {
  const ref = useRef<HTMLCanvasElement>(null);
  const frame = useRef(0);

  const redraw = useCallback(() => {
    const canvas = ref.current;
    if (!canvas) return;
    cancelAnimationFrame(frame.current);
    frame.current = requestAnimationFrame(() => draw(canvas, lowDetail));
  }, [lowDetail]);

  useEffect(() => {
    redraw();
    const canvas = ref.current;
    if (!canvas) return;
    const observer = new ResizeObserver(redraw);
    observer.observe(canvas);
    return () => {
      cancelAnimationFrame(frame.current);
      observer.disconnect();
    };
  }, [redraw]);

  return <canvas ref={ref} className="starfield" aria-hidden="true" />;
}
