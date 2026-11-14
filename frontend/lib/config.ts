/**
 * Central configuration for the map: tile endpoints, basemaps, camera limits
 * and attribution. Everything that might need swapping later lives here.
 */

/** Opening camera position: the Eastern Black Sea, around Rize / Çayeli. */
export const HOME_VIEW = {
  center: [40.74, 41.05] as [number, number],
  zoom: 13.2,
  pitch: 62,
  bearing: -18,
};

/** Where the globe sits before the opening fly-in. */
export const GLOBE_VIEW = {
  center: [35.0, 39.0] as [number, number],
  zoom: 2.6,
  pitch: 0,
  bearing: 0,
};

/**
 * Camera limits.
 *
 * MIN_ZOOM stops the view at a whole Earth. Past it the globe would shrink
 * into empty space, and in flat mode the world would detach from the edges of
 * the screen and leave black bands.
 *
 * Pitch is released only once the camera is close enough to the ground for a
 * tilted view to mean anything. Tilting a world-scale map just points the
 * camera at the void above the horizon.
 */
export const MIN_ZOOM = 2.4;
export const MAX_ZOOM = 19;
export const MAX_PITCH = 80;
/** Below this zoom the camera is held flat. */
export const PITCH_FREE_ZOOM = 6;

/**
 * Digital elevation model, Terrarium-encoded RGB PNGs.
 * Swap this for the backend's FABDEM tiles once they are served in the same
 * format and the 3D surface will match the model exactly.
 */
const DEM_DIRECT = 'https://elevation-tiles-prod.s3.amazonaws.com/terrarium/{z}/{x}/{y}.png';
const DEM_PROXIED = '/api/dem/{z}/{x}/{y}';

export const USE_DEM_PROXY = process.env.NEXT_PUBLIC_DEM_PROXY === '1';
export const DEM_TILES = USE_DEM_PROXY ? DEM_PROXIED : DEM_DIRECT;
export const DEM_MAX_ZOOM = 14;

/** Template used by the elevation-profile sampler (needs a concrete URL). */
export function demTileUrl(z: number, x: number, y: number) {
  return DEM_TILES.replace('{z}', String(z)).replace('{x}', String(x)).replace('{y}', String(y));
}

const ESRI = 'https://server.arcgisonline.com/ArcGIS/rest/services';

export const RASTER_SOURCES = {
  satellite: `${ESRI}/World_Imagery/MapServer/tile/{z}/{y}/{x}`,
  topo: `${ESRI}/World_Topo_Map/MapServer/tile/{z}/{y}/{x}`,
  roads: `${ESRI}/Reference/World_Transportation/MapServer/tile/{z}/{y}/{x}`,
};

/**
 * Place names are drawn by the map itself from a small Natural Earth extract
 * served out of /public, with self-hosted glyphs.
 *
 * Every keyless raster label service has now gone behind an API key, and a
 * raster label tile is soft on a high-DPI screen anyway. Vector text is sharp
 * at any zoom and any pixel ratio, needs no key, costs one 110 KB file, and
 * cannot break because somebody else's terms changed.
 */
export const LABELS_GEOJSON = '/data/labels.geojson';
export const GLYPHS = '/glyphs/{fontstack}/{range}.pbf';
/**
 * The folder under /public/glyphs, and what MapLibre puts in {fontstack}.
 * Deliberately free of spaces: a URL-encoded space in a static asset path is
 * a reliable way to get a 404 out of a CDN. The glyphs themselves are Open
 * Sans Semibold; MapLibre does not check the name inside the file.
 */
export const LABEL_FONT = ['OpenSans-Semibold'];

export type BasemapId = 'satellite' | 'hybrid' | 'relief' | 'topo';

export const ATTRIBUTION = [
  'Imagery © Esri, Maxar, Earthstar Geographics',
  'Place names: Natural Earth (public domain)',
  'Elevation: Terrarium / AWS Open Data',
  'FABDEM is produced using Copernicus WorldDEM-30 © DLR e.V.',
];

/** Short line for the status bar; the full list lives in the About dialog. */
export const ATTRIBUTION_SHORT = 'Esri · Maxar · Natural Earth · AWS Terrarium · FABDEM / DLR e.V.';

/** Risk layers the backend will provide. Listed but inactive for now. */
export const PENDING_LAYERS = ['risk', 'wetness', 'soilLoss', 'regional'] as const;
export type PendingLayerId = (typeof PENDING_LAYERS)[number];
