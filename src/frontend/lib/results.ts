/**
 * Everything the app shows from a finished analysis: the map layers (read
 * from the result GeoTIFFs and coloured here), the field's numbers
 * (field_summary.json) and the daily series behind the year slider.
 */
import { isNoData, parseGeoTiff, type GeoRaster } from './geotiff';
import { toLngLat } from './utm';
import type { LngLat } from './geo';
import type { Lang } from './i18n';

export type ResultLayerId = 'soilLoss' | 'soilLossDates' | 'landslide' | 'practice';
export const RESULT_LAYERS: ResultLayerId[] = ['soilLoss', 'soilLossDates', 'landslide', 'practice'];

/** Which dates the "in the dates" layers show: the analysed range, or one calendar year. */
export type Period = 'dates' | number;

interface LegendClass {
  color: string;
  label: Record<Lang, string>;
}

interface LayerDef {
  /** File in the view folder (the analysed dates). */
  viewFile: string;
  /** File in the analysis folder with one band per year, if the layer follows the year slider. */
  yearFile?: string;
  /** Classes by upper bound (continuous values) ... */
  edges?: number[];
  /** ... or by integer code (0, 1, 2, ...). */
  classes: LegendClass[];
  units: Record<Lang, string>;
}

const LOSS_CLASSES: LegendClass[] = [
  { color: '#1a9850', label: { tr: '< 2 · çok düşük', en: '< 2 · very low' } },
  { color: '#91cf60', label: { tr: '2–5 · düşük', en: '2–5 · low' } },
  { color: '#fee08b', label: { tr: '5–10 · orta', en: '5–10 · moderate' } },
  { color: '#fc8d59', label: { tr: '10–20 · yüksek', en: '10–20 · high' } },
  { color: '#d73027', label: { tr: '20–50 · çok yüksek', en: '20–50 · very high' } },
  { color: '#7f0000', label: { tr: '> 50 · şiddetli', en: '> 50 · severe' } },
];
const LOSS_EDGES = [2, 5, 10, 20, 50, Infinity]; // the backend's severity classes, t/ha

export const ALERT_CLASSES: LegendClass[] = [
  { color: '#1a9850', label: { tr: 'Düşük', en: 'Low' } },
  { color: '#fee08b', label: { tr: 'Dikkat', en: 'Watch' } },
  { color: '#fc8d59', label: { tr: 'Uyarı', en: 'Warning' } },
  { color: '#d73027', label: { tr: 'Tehlike', en: 'Danger' } },
];

export const PRACTICE_CLASSES: LegendClass[] = [
  { color: '#1a9850', label: { tr: 'Değişiklik gerekmiyor', en: 'No change needed' } },
  { color: '#fee08b', label: { tr: 'Eş yükselti eğrisine göre sürüm', en: 'Contour farming' } },
  { color: '#fdae61', label: { tr: 'Şerit ekim', en: 'Strip cropping' } },
  { color: '#f46d43', label: { tr: 'Teras veya taş set', en: 'Terraces or bunds' } },
  { color: '#a50026', label: { tr: 'Kalıcı bitki örtüsü', en: 'Permanent cover' } },
];

export const LAYER_DEFS: Record<ResultLayerId, LayerDef> = {
  soilLoss: { viewFile: 'soil_loss.tif', edges: LOSS_EDGES, classes: LOSS_CLASSES, units: { tr: 't/ha/yıl', en: 't/ha/yr' } },
  soilLossDates: {
    viewFile: 'soil_loss_window.tif',
    yearFile: 'soil_loss_window_by_year.tif',
    edges: LOSS_EDGES,
    classes: LOSS_CLASSES,
    units: { tr: 't/ha', en: 't/ha' },
  },
  landslide: {
    viewFile: 'landslide_peak_class.tif',
    yearFile: 'landslide_peak_class_by_year.tif',
    classes: ALERT_CLASSES,
    units: { tr: 'en kötü uyarı', en: 'worst alert' },
  },
  practice: { viewFile: 'recommended_practice.tif', classes: PRACTICE_CLASSES, units: { tr: 'öneri', en: 'advice' } },
};

// ----------------------------------------------------------------- rasters
const rasterCache = new Map<string, Promise<GeoRaster>>();

export function loadRaster(url: string): Promise<GeoRaster> {
  let hit = rasterCache.get(url);
  if (!hit) {
    hit = fetch(url).then(async (res) => {
      if (!res.ok) throw new Error(`${res.status} ${url}`);
      return parseGeoTiff(await res.arrayBuffer());
    });
    hit.catch(() => rasterCache.delete(url)); // let a later attempt retry
    rasterCache.set(url, hit);
  }
  return hit;
}

/** An image laid over the map: corners top-left, top-right, bottom-right, bottom-left. */
export interface Overlay {
  url: string;
  coordinates: [LngLat, LngLat, LngLat, LngLat];
}

function hexToRgb(hex: string): [number, number, number] {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

/** Colours one band of a raster by the layer's classes and places it on the map. */
export function rasterOverlay(r: GeoRaster, band: number, layer: ResultLayerId): Overlay {
  const def = LAYER_DEFS[layer];
  const colors = def.classes.map((c) => hexToRgb(c.color));
  const canvas = document.createElement('canvas');
  canvas.width = r.width;
  canvas.height = r.height;
  const ctx = canvas.getContext('2d')!;
  const img = ctx.createImageData(r.width, r.height);
  const data = r.bands[band];
  for (let i = 0; i < data.length; i++) {
    const v = data[i];
    if (isNoData(v, r.nodata)) continue; // stays transparent
    let k: number;
    if (def.edges) {
      k = def.edges.findIndex((edge) => v < edge);
      if (k < 0) k = def.edges.length - 1;
    } else {
      k = Math.min(colors.length - 1, Math.max(0, Math.round(v)));
    }
    const [red, green, blue] = colors[k];
    img.data.set([red, green, blue, 235], i * 4);
  }
  ctx.putImageData(img, 0, 0);

  const x0 = r.originX, y0 = r.originY;
  const x1 = x0 + r.width * r.pixelX, y1 = y0 - r.height * r.pixelY;
  return {
    url: canvas.toDataURL('image/png'),
    coordinates: [toLngLat(r.epsg, x0, y0), toLngLat(r.epsg, x1, y0), toLngLat(r.epsg, x1, y1), toLngLat(r.epsg, x0, y1)],
  };
}

/** The URL and band to read for a layer and period. */
export function layerSource(
  layer: ResultLayerId,
  period: Period,
  files: { analysis: string; view: string },
): { url: string; band: number | string } {
  const def = LAYER_DEFS[layer];
  if (period !== 'dates' && def.yearFile) return { url: files.analysis + def.yearFile, band: String(period) };
  return { url: files.view + def.viewFile, band: 0 };
}

// ----------------------------------------------------------------- numbers
/** The parts of field_summary.json the app reads. */
export interface FieldSummary {
  field: { path: 'us' | 'global'; profile: string; area: number; area_unit: string; centroid_lonlat: LngLat };
  t_range: [string, string];
  analysis_period: string;
  defaults_used: string[];
  units: { soil_loss: string; soil_loss_window: string; rain: string };
  long_term: {
    soil_loss_mean: number;
    soil_loss_p90: number;
    tolerance: number;
    share_above_tolerance_pct: number;
    practice_share_pct: Record<string, number>;
    monthly_soil_loss_share_pct: Record<string, number>;
  };
  landslide: {
    stability_share_pct: Record<string, number>;
    danger_days_per_year: number;
    rain_for_10pct_failure_weakest_mm_day: number | null;
  };
  window: {
    rain_total_mm: number;
    wettest_window_mm_day: number | null;
    wettest_window_end: string;
    erosive_storms: number;
    soil_loss: number;
    soil_loss_pct_of_average_year: number | null;
    worst_alert: 'low' | 'watch' | 'warning' | 'danger';
    worst_alert_day: string;
  };
  advice: string[];
}

export async function loadSummary(viewFolder: string): Promise<FieldSummary> {
  const res = await fetch(viewFolder + 'field_summary.json');
  if (!res.ok) throw new Error(`${res.status} field_summary.json`);
  return res.json();
}

export const ALERT_LEVELS = ['low', 'watch', 'warning', 'danger'] as const;

/** Practice needed on most of the area that is over tolerance: its code (1-4), or 0. */
export function mainPractice(s: FieldSummary): number {
  const shares = Object.values(s.long_term.practice_share_pct); // codes 0..4 in order
  let best = 0;
  let bestShare = 0;
  for (let code = 1; code < shares.length; code++) {
    if (shares[code] > bestShare) {
      best = code;
      bestShare = shares[code];
    }
  }
  return best;
}

/** Totals for one calendar year, from the daily series (soil loss in t/ha). */
export interface YearStats {
  rainMm: number;
  storms: number;
  soilLossTHa: number;
  worstAlert: number; // 0 low .. 3 danger
}

export async function loadYearStats(analysisFolder: string): Promise<Map<number, YearStats>> {
  const res = await fetch(analysisFolder + 'daily_series.csv');
  if (!res.ok) throw new Error(`${res.status} daily_series.csv`);
  const [head, ...rows] = (await res.text()).trim().split(/\r?\n/);
  const col = Object.fromEntries(head.split(',').map((name, i) => [name, i]));
  const shareCols = ['share_watch_pct', 'share_warning_pct', 'share_danger_pct'].map((n) => col[n]);
  const years = new Map<number, YearStats>();
  for (const row of rows) {
    const f = row.split(',');
    const year = Number(f[col.date].slice(0, 4));
    const y = years.get(year) ?? { rainMm: 0, storms: 0, soilLossTHa: 0, worstAlert: 0 };
    y.rainMm += Number(f[col.rain_mm]) || 0;
    y.storms += Number(f[col.erosive_storms]) || 0;
    y.soilLossTHa += Number(f[col.soil_loss_t_ha]) || 0;
    shareCols.forEach((c, level) => {
      if (Number(f[c]) > 0) y.worstAlert = Math.max(y.worstAlert, level + 1);
    });
    years.set(year, y);
  }
  return years;
}

/** t/ha → the unit the summary reports in (tons/acre for US fields). */
export function lossInReportUnits(tHa: number, s: FieldSummary): number {
  return s.units.soil_loss_window.startsWith('tons/acre') ? tHa * 0.446089561 : tHa;
}

/** "2016–2025" → [2016, 2025]. */
export function periodYears(s: FieldSummary): [number, number] {
  const m = s.analysis_period.match(/\d{4}/g) ?? ['2016', '2025'];
  return [Number(m[0]), Number(m[m.length - 1])];
}
