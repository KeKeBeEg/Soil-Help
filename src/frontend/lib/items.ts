import type { LngLat } from './geo';

/** Anything the user has placed on the map, listed in the Map content panel. */
export type ItemKind = 'pin' | 'field' | 'path';

/** A field's analysis run (see lib/fieldsApi.ts), kept with the field. */
export interface FieldAnalysis {
  fieldId: string;
  start: string;
  end: string;
  state: 'queued' | 'running' | 'done' | 'failed' | 'missing';
  stage: string;
  message: string;
  /** Result folders, once done. */
  files?: { analysis: string; view: string };
  runUrl?: string;
  /** When this request was sent (ms), for the elapsed-time display. */
  startedAt: number;
}

export interface MapItem {
  id: string;
  kind: ItemKind;
  name: string;
  points: LngLat[];
  visible: boolean;
  createdAt: number;
  analysis?: FieldAnalysis;
}

const STORAGE_KEY = 'soilhelp.items.v1';

/** Drawn items survive a reload. Storage can be missing or full; that only loses the convenience. */
export function loadItems(): MapItem[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    const items = raw ? (JSON.parse(raw) as MapItem[]) : [];
    return Array.isArray(items) ? items.filter((i) => i && Array.isArray(i.points)) : [];
  } catch {
    return [];
  }
}

export function saveItems(items: MapItem[]): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(items));
  } catch {
    /* private mode or quota: nothing to do */
  }
}

export function newId(): string {
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`;
}

/** Minimum points before a shape of each kind can be closed. */
export const MIN_POINTS: Record<ItemKind, number> = { pin: 1, field: 3, path: 2 };

export function defaultName(kind: ItemKind, index: number, lang: 'tr' | 'en'): string {
  const names = {
    pin: { tr: 'İşaret', en: 'Pin' },
    field: { tr: 'Tarla', en: 'Field' },
    path: { tr: 'Profil', en: 'Profile' },
  };
  return `${names[kind][lang]} ${index}`;
}

/** GeoJSON for export and for handing a field to the backend. */
export function itemToFeature(item: MapItem): GeoJSON.Feature {
  const properties = { name: item.name, kind: item.kind };
  if (item.kind === 'pin') {
    return {
      type: 'Feature',
      properties,
      geometry: { type: 'Point', coordinates: item.points[0] },
    };
  }
  if (item.kind === 'field') {
    return {
      type: 'Feature',
      properties,
      geometry: { type: 'Polygon', coordinates: [[...item.points, item.points[0]]] },
    };
  }
  return {
    type: 'Feature',
    properties,
    geometry: { type: 'LineString', coordinates: item.points },
  };
}

export function itemsToCollection(items: MapItem[]): GeoJSON.FeatureCollection {
  return { type: 'FeatureCollection', features: items.map(itemToFeature) };
}
