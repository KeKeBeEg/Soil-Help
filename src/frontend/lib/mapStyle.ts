import type {
  DataDrivenPropertyValueSpecification,
  LayerSpecification,
  StyleSpecification,
} from 'maplibre-gl';
import {
  DEM_MAX_ZOOM,
  DEM_TILES,
  GLYPHS,
  LABELS_GEOJSON,
  LABEL_FONT,
  MAX_ZOOM,
  RASTER_SOURCES,
} from './config';

export const LAYER = {
  background: 'bg',
  satellite: 'satellite',
  topo: 'topo',
  hillshade: 'hillshade',
  roads: 'roads',
  labelCountryMajor: 'label-country-major',
  labelCountry: 'label-country',
  labelRegion: 'label-region',
  placeDot: 'place-dot',
  labelCapital: 'label-capital',
  labelCity: 'label-city',
  labelTown: 'label-town',
  itemFill: 'item-fill',
  itemLine: 'item-line',
  draftFill: 'draft-fill',
  draftLine: 'draft-line',
  draftRubber: 'draft-rubber',
} as const;

export const SOURCE = {
  terrain: 'terrain-dem',
  labels: 'labels',
  items: 'items',
  draft: 'draft',
} as const;

const EMPTY: GeoJSON.FeatureCollection = { type: 'FeatureCollection', features: [] };

/**
 * One style holding every layer we might show. Layers are switched with
 * `visibility` at runtime rather than by reloading a style, which avoids the
 * white flash you get when a basemap is swapped.
 */

/** Shared look for every place label: thin white text on a dark halo. */
const LABEL_PAINT = {
  'text-color': '#ffffff',
  'text-halo-color': 'rgba(0, 0, 0, 0.75)',
  'text-halo-width': 1.1,
  'text-halo-blur': 0.4,
} as const;

function textLayer(
  id: string,
  kinds: string[],
  minzoom: number,
  maxzoom: number,
  size: unknown,
  extra: Record<string, unknown> = {},
): LayerSpecification {
  return {
    id,
    type: 'symbol',
    source: SOURCE.labels,
    minzoom,
    maxzoom,
    filter: ['match', ['get', 'k'], kinds, true, false],
    layout: {
      visibility: 'none',
      'text-field': ['get', 'en'],
      'text-font': [...LABEL_FONT],
      'text-size': size as DataDrivenPropertyValueSpecification<number>,
      'text-padding': 4,
      'text-max-width': 8,
      ...extra,
    },
    paint: { ...LABEL_PAINT },
  } as LayerSpecification;
}

/**
 * Place names, drawn as real text rather than baked into raster tiles, so
 * they stay sharp at every zoom and pixel ratio.
 */
function labelLayers(): LayerSpecification[] {
  return [
    textLayer('label-country-major', ['country_major'], 1.2, 6.5,
      ['interpolate', ['linear'], ['zoom'], 1.5, 11, 4, 15, 6, 17],
      { 'text-letter-spacing': 0.14 }),

    textLayer('label-country', ['country'], 3, 6.5,
      ['interpolate', ['linear'], ['zoom'], 3, 10, 6, 13],
      { 'text-letter-spacing': 0.1 }),

    textLayer('label-region', ['region'], 5.5, 11,
      ['interpolate', ['linear'], ['zoom'], 6, 11, 10, 14],
      { 'text-letter-spacing': 0.06 }),

    {
      id: 'place-dot',
      type: 'circle',
      source: SOURCE.labels,
      minzoom: 3,
      filter: ['match', ['get', 'k'], ['capital', 'city', 'town'], true, false],
      layout: { visibility: 'none' },
      paint: {
        'circle-radius': ['interpolate', ['linear'], ['zoom'], 4, 1.6, 10, 2.8],
        'circle-color': '#ffffff',
        'circle-stroke-color': 'rgba(0, 0, 0, 0.6)',
        'circle-stroke-width': 1,
      },
    } as LayerSpecification,

    textLayer('label-capital', ['capital'], 3.2, 24,
      ['interpolate', ['linear'], ['zoom'], 4, 11, 9, 14],
      { 'text-offset': [0, 0.85], 'text-anchor': 'top' }),

    textLayer('label-city', ['city'], 5.5, 24,
      ['interpolate', ['linear'], ['zoom'], 6, 11, 11, 14],
      { 'text-offset': [0, 0.8], 'text-anchor': 'top' }),

    textLayer('label-town', ['town'], 8.5, 24,
      ['interpolate', ['linear'], ['zoom'], 9, 10.5, 13, 13],
      { 'text-offset': [0, 0.8], 'text-anchor': 'top' }),
  ];
}

export function buildStyle(): StyleSpecification {
  return {
    version: 8,
    projection: { type: 'globe' },
    glyphs: GLYPHS,
    /*
     * Space itself is transparent so the starfield canvas behind the map
     * shows through; only the thin atmosphere on the globe's limb and the
     * haze near the ground are painted.
     */
    sky: {
      'sky-color': 'rgba(10, 22, 38, 0)',
      'horizon-color': '#9dc3e0',
      'fog-color': '#d2e2ee',
      'fog-ground-blend': 0.5,
      'horizon-fog-blend': 0.6,
      'sky-horizon-blend': 0.55,
      'atmosphere-blend': ['interpolate', ['linear'], ['zoom'], 0, 0.9, 7, 0.35, 11, 0],
    },
    light: { anchor: 'map', position: [1.4, 210, 30], intensity: 0.3 },
    sources: {
      satellite: {
        type: 'raster',
        tiles: [RASTER_SOURCES.satellite],
        tileSize: 256,
        maxzoom: MAX_ZOOM,
        attribution: 'Esri, Maxar, Earthstar Geographics',
      },
      topo: { type: 'raster', tiles: [RASTER_SOURCES.topo], tileSize: 256, maxzoom: MAX_ZOOM },
      roads: { type: 'raster', tiles: [RASTER_SOURCES.roads], tileSize: 256, maxzoom: MAX_ZOOM },
      [SOURCE.terrain]: {
        type: 'raster-dem',
        tiles: [DEM_TILES],
        tileSize: 256,
        maxzoom: DEM_MAX_ZOOM,
        encoding: 'terrarium',
        attribution: 'Terrarium / AWS Open Data',
      },
      [SOURCE.labels]: { type: 'geojson', data: LABELS_GEOJSON },
      [SOURCE.items]: { type: 'geojson', data: EMPTY },
      [SOURCE.draft]: { type: 'geojson', data: EMPTY },
    },
    layers: [
      { id: LAYER.background, type: 'background', paint: { 'background-color': '#070b12' } },
      {
        id: LAYER.satellite,
        type: 'raster',
        source: 'satellite',
        paint: { 'raster-opacity': 1, 'raster-fade-duration': 120 },
      },
      {
        id: LAYER.topo,
        type: 'raster',
        source: 'topo',
        layout: { visibility: 'none' },
        paint: { 'raster-fade-duration': 120 },
      },
      {
        id: LAYER.hillshade,
        type: 'hillshade',
        source: SOURCE.terrain,
        // Relief shading shows nothing at world zoom, but would still pull
        // elevation tiles for the whole visible globe on every load.
        minzoom: 5,
        layout: { visibility: 'none' },
        paint: {
          'hillshade-exaggeration': 0.55,
          'hillshade-illumination-direction': 315,
          'hillshade-illumination-anchor': 'map',
          'hillshade-shadow-color': '#1b2430',
          'hillshade-highlight-color': '#ffffff',
          'hillshade-accent-color': '#2a3442',
        },
      },
      {
        id: LAYER.roads,
        type: 'raster',
        source: 'roads',
        layout: { visibility: 'none' },
        paint: { 'raster-opacity': 0.85, 'raster-fade-duration': 120 },
      },
      ...labelLayers(),

      // ------------------------------------------------- saved map content
      {
        id: LAYER.itemFill,
        type: 'fill',
        source: SOURCE.items,
        filter: ['==', ['geometry-type'], 'Polygon'],
        paint: { 'fill-color': '#8ab4f8', 'fill-opacity': 0.18 },
      },
      {
        id: LAYER.itemLine,
        type: 'line',
        source: SOURCE.items,
        filter: ['!=', ['geometry-type'], 'Point'],
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: {
          'line-color': '#ffffff',
          'line-width': 2.2,
          'line-opacity': 0.92,
        },
      },

      // ----------------------------------------------- shape being drawn
      {
        id: LAYER.draftFill,
        type: 'fill',
        source: SOURCE.draft,
        filter: ['==', ['geometry-type'], 'Polygon'],
        paint: { 'fill-color': '#8ab4f8', 'fill-opacity': 0.14 },
      },
      {
        id: LAYER.draftLine,
        type: 'line',
        source: SOURCE.draft,
        filter: ['!=', ['get', 'kind'], 'rubber'],
        layout: { 'line-cap': 'round', 'line-join': 'round' },
        paint: { 'line-color': '#ffffff', 'line-width': 2.4, 'line-opacity': 0.95 },
      },
      {
        id: LAYER.draftRubber,
        type: 'line',
        source: SOURCE.draft,
        filter: ['==', ['get', 'kind'], 'rubber'],
        layout: { 'line-cap': 'round' },
        paint: {
          'line-color': '#ffffff',
          'line-width': 1.6,
          'line-opacity': 0.55,
          'line-dasharray': [2, 2],
        },
      },
    ],
  };
}

/** Which raster layers each rendering mode shows, and which label set suits it. */
export const BASEMAP_LAYERS = {
  satellite: { satellite: true, topo: false, hillshade: false, bg: '#070b12', darkLabels: false },
  hybrid: { satellite: true, topo: false, hillshade: false, bg: '#070b12', darkLabels: false },
  relief: { satellite: false, topo: false, hillshade: true, bg: '#e9e7e1', darkLabels: true },
  topo: { satellite: false, topo: true, hillshade: false, bg: '#f2efe9', darkLabels: true },
} as const;
