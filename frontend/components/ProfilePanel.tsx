'use client';

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { formatDistance, formatElevation, type Units } from '@/lib/geo';
import { translator, type Lang } from '@/lib/i18n';
import { Download, X } from './Icons';

export interface Sample {
  /** Distance from the start of the line, metres. */
  d: number;
  /** Elevation above sea level, metres. Null where no DEM tile was readable. */
  z: number | null;
  lng: number;
  lat: number;
}

interface Props {
  lang: Lang;
  units: Units;
  title: string;
  samples: Sample[];
  loading: boolean;
  failed: boolean;
  onAddPoints: () => void;
  onSaveCsv: () => void;
  onClose: () => void;
}

const PAD = { top: 14, right: 16, bottom: 22, left: 46 };

export default function ProfilePanel(props: Props) {
  const t = translator(props.lang);
  const observer = useRef<ResizeObserver | null>(null);
  const [width, setWidth] = useState(900);
  const [hover, setHover] = useState<number | null>(null);

  /**
   * Render at real pixel size so text and strokes are never stretched.
   * A callback ref rather than an effect, because the chart only mounts once
   * loading finishes -- an effect on mount would observe nothing.
   */
  const wrapRef = useCallback((el: HTMLDivElement | null) => {
    observer.current?.disconnect();
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => {
      const w = entry.contentRect.width;
      if (w > 0) setWidth(w);
    });
    ro.observe(el);
    observer.current = ro;
    if (el.clientWidth > 0) setWidth(el.clientWidth);
  }, []);

  useEffect(() => () => observer.current?.disconnect(), []);

  const valid = useMemo(
    () => props.samples.filter((s): s is Sample & { z: number } => s.z !== null),
    [props.samples],
  );

  const stats = useMemo(() => {
    if (!valid.length) return null;
    let min = Infinity;
    let max = -Infinity;
    let gain = 0;
    let maxSlope = 0;
    for (let i = 0; i < valid.length; i++) {
      const z = valid[i].z;
      if (z < min) min = z;
      if (z > max) max = z;
      if (i > 0) {
        const dz = z - valid[i - 1].z;
        const dd = valid[i].d - valid[i - 1].d;
        if (dz > 0) gain += dz;
        if (dd > 0) maxSlope = Math.max(maxSlope, Math.abs(dz / dd));
      }
    }
    return { min, max, gain, slopeDeg: (Math.atan(maxSlope) * 180) / Math.PI };
  }, [valid]);

  const height = 150;
  const plotW = Math.max(60, width - PAD.left - PAD.right);
  const plotH = height - PAD.top - PAD.bottom;

  const geom = useMemo(() => {
    if (!valid.length || !stats) return null;
    const maxD = valid[valid.length - 1].d || 1;
    // A little headroom top and bottom keeps the trace off the frame.
    const span = Math.max(stats.max - stats.min, 10);
    const lo = stats.min - span * 0.12;
    const hi = stats.max + span * 0.12;

    const x = (d: number) => PAD.left + (d / maxD) * plotW;
    const y = (z: number) => PAD.top + (1 - (z - lo) / (hi - lo)) * plotH;

    const line = valid.map((s) => `${x(s.d).toFixed(1)},${y(s.z).toFixed(1)}`).join(' L');
    const area =
      `M${x(valid[0].d).toFixed(1)},${(PAD.top + plotH).toFixed(1)} ` +
      `L${line} L${x(valid[valid.length - 1].d).toFixed(1)},${(PAD.top + plotH).toFixed(1)} Z`;

    // Four recessive gridlines, on round elevation values.
    const ticks: { z: number; y: number }[] = [];
    const step = niceStep((hi - lo) / 4);
    for (let z = Math.ceil(lo / step) * step; z <= hi; z += step) {
      ticks.push({ z, y: y(z) });
    }

    return { x, y, maxD, path: `M${line}`, area, ticks };
  }, [valid, stats, plotW, plotH]);

  const hovered = hover !== null && geom ? valid[hover] : null;

  function onMove(e: React.MouseEvent<SVGSVGElement>) {
    if (!geom || !valid.length) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const px = e.clientX - rect.left;
    const frac = Math.min(1, Math.max(0, (px - PAD.left) / plotW));
    const targetD = frac * geom.maxD;
    // Nearest sample by distance along the line.
    let best = 0;
    let bestGap = Infinity;
    valid.forEach((s, i) => {
      const gap = Math.abs(s.d - targetD);
      if (gap < bestGap) {
        bestGap = gap;
        best = i;
      }
    });
    setHover(best);
  }

  return (
    <section className="profile" aria-label={t('elevationProfile')}>
      <div className="profile-head">
        <span className="profile-title">{props.title}</span>

        {stats && (
          <div className="profile-stats">
            <Stat
              k={t('minMax')}
              v={`${formatElevation(stats.min, props.units)} – ${formatElevation(stats.max, props.units)}`}
            />
            <Stat k={t('gain')} v={formatElevation(stats.gain, props.units)} />
            <Stat k={t('maxSlope')} v={`${stats.slopeDeg.toFixed(0)}°`} />
          </div>
        )}

        <div className="profile-actions">
          <button className="btn" onClick={props.onAddPoints}>
            {t('addPoints')}
          </button>
          <button className="btn" onClick={props.onSaveCsv} disabled={!valid.length}>
            <Download size={13} style={{ marginRight: 5, verticalAlign: -2 }} />
            {t('saveCsv')}
          </button>
          <button className="icon-btn" onClick={props.onClose} aria-label={t('close')}>
            <X size={15} />
          </button>
        </div>
      </div>

      {props.loading && <p className="profile-msg">{t('readingElevation')}</p>}
      {!props.loading && props.failed && <p className="profile-msg">{t('elevationFailed')}</p>}

      {!props.loading && !props.failed && (
        <div className="profile-chart" ref={wrapRef}>
          <svg
            viewBox={`0 0 ${width} ${height}`}
            width={width}
            height={height}
            onMouseMove={onMove}
            onMouseLeave={() => setHover(null)}
            role="img"
            aria-label={t('elevationProfile')}
          >
            <defs>
              <linearGradient id="profileFill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#8ab4f8" stopOpacity="0.28" />
                <stop offset="100%" stopColor="#8ab4f8" stopOpacity="0.02" />
              </linearGradient>
            </defs>

            {geom?.ticks.map((tick) => (
              <g key={tick.z}>
                <line
                  x1={PAD.left}
                  x2={width - PAD.right}
                  y1={tick.y}
                  y2={tick.y}
                  stroke="#3c4043"
                  strokeWidth={1}
                />
                <text
                  x={PAD.left - 8}
                  y={tick.y + 3.5}
                  textAnchor="end"
                  fontSize="10"
                  fill="#80868b"
                >
                  {props.units === 'imperial'
                    ? Math.round(tick.z * 3.28084)
                    : Math.round(tick.z)}
                </text>
              </g>
            ))}

            {geom && (
              <>
                <path d={geom.area} fill="url(#profileFill)" />
                <path
                  d={geom.path}
                  fill="none"
                  stroke="#8ab4f8"
                  strokeWidth={2}
                  strokeLinejoin="round"
                  strokeLinecap="round"
                />
              </>
            )}

            {/* x-axis: start and end distance only, so labels never collide */}
            {geom && (
              <>
                <text x={PAD.left} y={height - 6} fontSize="10" fill="#80868b">
                  0
                </text>
                <text
                  x={width - PAD.right}
                  y={height - 6}
                  fontSize="10"
                  fill="#80868b"
                  textAnchor="end"
                >
                  {formatDistance(geom.maxD, props.units)}
                </text>
              </>
            )}

            {hovered && geom && (
              <g pointerEvents="none">
                <line
                  x1={geom.x(hovered.d)}
                  x2={geom.x(hovered.d)}
                  y1={PAD.top}
                  y2={PAD.top + plotH}
                  stroke="#9aa0a6"
                  strokeWidth={1}
                />
                <circle
                  cx={geom.x(hovered.d)}
                  cy={geom.y(hovered.z)}
                  r={4.5}
                  fill="#8ab4f8"
                  stroke="#202124"
                  strokeWidth={2}
                />
                <ProfileTooltip
                  x={geom.x(hovered.d)}
                  width={width}
                  label={`${formatElevation(hovered.z, props.units)} · ${formatDistance(hovered.d, props.units)}`}
                />
              </g>
            )}
          </svg>
        </div>
      )}
    </section>
  );
}

function Stat({ k, v }: { k: string; v: string }) {
  return (
    <div className="profile-stat">
      <span className="k">{k}</span>
      <span className="v">{v}</span>
    </div>
  );
}

function ProfileTooltip({ x, width, label }: { x: number; width: number; label: string }) {
  const w = label.length * 6.6 + 16;
  // Flip the box inward when it would run off either edge.
  const bx = Math.min(Math.max(x - w / 2, 4), width - w - 4);
  return (
    <g>
      <rect x={bx} y={2} width={w} height={20} rx={6} fill="#35363a" stroke="none" />
      <text x={bx + w / 2} y={16} textAnchor="middle" fontSize="11" fill="#e8eaed">
        {label}
      </text>
    </g>
  );
}

/** Rounds a raw axis step up to 1, 2, 5 × a power of ten. */
function niceStep(raw: number): number {
  const pow = 10 ** Math.floor(Math.log10(Math.max(raw, 1e-6)));
  const n = raw / pow;
  if (n <= 1) return pow;
  if (n <= 2) return 2 * pow;
  if (n <= 5) return 5 * pow;
  return 10 * pow;
}
