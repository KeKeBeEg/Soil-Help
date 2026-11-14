import type { LngLat } from './geo';

/** Anything the user has placed on the map, listed in the Map content panel. */
export type ItemKind = 'pin' | 'field' | 'path';

export interface MapItem {
  id: string;
  kind: ItemKind;
  name: string;
  points: LngLat[];
  visible: boolean;
  createdAt: number;
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
