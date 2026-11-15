'use client';

import { useCallback, useEffect, useRef, useState, type RefObject } from 'react';
import maplibregl, { type Map as MLMap, type LngLatLike } from 'maplibre-gl';

import {
  HOME_VIEW,
  INTRO_SPIN_MS,
  INTRO_VIEW,
  MAX_PITCH,
  MAX_ZOOM,
  MIN_ZOOM,
  MIN_ZOOM_FLAT,
  PITCH_FREE_ZOOM,
  TERRAIN_MIN_ZOOM,
  type BasemapId,
} from '@/lib/config';
import { BASEMAP_LAYERS, LAYER, SOURCE, buildStyle } from '@/lib/mapStyle';
import { sampleElevation } from '@/lib/elevation';
import { setTelemetry } from '@/lib/telemetry';
import { itemsToCollection, type ItemKind, type MapItem } from '@/lib/items';
import type { LngLat } from '@/lib/geo';

export interface MapController {
  /** Turns the globe from the intro position round to HOME_VIEW. */
  playIntro(): void;
  zoomIn(): void;
  zoomOut(): void;
  resetNorth(): void;
  setPitch(pitch: number): void;
  flyTo(center: LngLat, zoom?: number): void;
  fitPoints(points: LngLat[]): void;
  locate(onError: (message: string) => void): void;
}

export interface MapCanvasProps {
  /**
   * The visible map area between the panels. The map itself fills the whole
   * window; camera padding centres the globe inside this element.
   */
  frameRef: RefObject<HTMLElement | null>;
  /** Which name to show on the map: 'tr' or 'en'. */
  lang: 'tr' | 'en';
  basemap: BasemapId;
  showLabels: boolean;
  showRoads: boolean;
  blendHillshade: boolean;
  exaggeration: number;
  sunAzimuth: number;
  shadowStrength: number;
  lite: boolean;
  items: MapItem[];
  draftKind: ItemKind | null;
  draftPoints: LngLat[];
  onDraftPointsChange: (points: LngLat[]) => void;
  onFinishDraft: () => void;
  onController: (controller: MapController) => void;
  /** Style parsed. */
  onLoaded: () => void;
  /** Tiles drawn — the globe is worth looking at now. */
  onReady: () => void;
  onDemError: () => void;
}

/** Every label layer, and the subset that draws text. */
const LABEL_TEXT_LAYERS = [
  LAYER.labelCountryMajor,
  LAYER.labelCountry,
  LAYER.labelRegion,
  LAYER.labelCapital,
  LAYER.labelCity,
  LAYER.labelTown,
];
const LABEL_LAYERS = [...LABEL_TEXT_LAYERS, LAYER.placeDot];

/** Telemetry is pushed at most this often while the camera moves. */
const TELEMETRY_INTERVAL_MS = 90;

/** How long the globe takes to glide when a panel opens or closes. */
const PADDING_EASE_MS = 320;

type Insets = { top: number; right: number; bottom: number; left: number };
const NO_INSETS: Insets = { top: 0, right: 0, bottom: 0, left: 0 };

/** How far the visible frame sits inside the map container, on each side. */
function frameInsets(container: HTMLElement | null, frame: HTMLElement | null): Insets {
  if (!container || !frame) return NO_INSETS;
  const c = container.getBoundingClientRect();
  const f = frame.getBoundingClientRect();
  const r = (v: number) => Math.max(0, Math.round(v));
  return {
    top: r(f.top - c.top),
    right: r(c.right - f.right),
    bottom: r(c.bottom - f.bottom),
    left: r(f.left - c.left),
  };
}

function sameInsets(a: Partial<Insets>, b: Insets): boolean {
  return a.top === b.top && a.right === b.right && a.bottom === b.bottom && a.left === b.left;
}

/**
 * Starts from rest and comes to rest. An ease-out alone sets off at full
 * speed in a single frame, which reads as a jolt the moment the veil lifts.
 */
function easeInOutCubic(t: number): number {
  return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
}

/**
 * Pitch is released gradually between these zooms. A hard cut-off would snap
 * the camera upright the moment you crossed the threshold.
 */
function allowedPitch(zoom: number): number {
  const t = (zoom - (PITCH_FREE_ZOOM - 2)) / 2;
  return Math.round(Math.min(1, Math.max(0, t)) * MAX_PITCH);
}

/** GeoJSON for the shape currently being drawn, including the rubber band. */
function draftData(
  points: LngLat[],
  kind: ItemKind | null,
  cursor: LngLat | null,
): GeoJSON.FeatureCollection {
  const features: GeoJSON.Feature[] = [];
  if (!kind || kind === 'pin') return { type: 'FeatureCollection', features };

  if (points.length >= 2) {
    features.push({
      type: 'Feature',
      properties: { kind: 'line' },
      geometry: { type: 'LineString', coordinates: points },
    });
  }
  if (kind === 'field' && points.length >= 3) {
    features.push({
      type: 'Feature',
      properties: { kind: 'fill' },
      geometry: { type: 'Polygon', coordinates: [[...points, points[0]]] },
    });
  }
  if (cursor && points.length >= 1) {
    const band: LngLat[] = [points[points.length - 1], cursor];
    if (kind === 'field' && points.length >= 2) band.push(points[0]);
    features.push({
      type: 'Feature',
      properties: { kind: 'rubber' },
      geometry: { type: 'LineString', coordinates: band },
    });
  }
  return { type: 'FeatureCollection', features };
}

export default function MapCanvas(props: MapCanvasProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<MLMap | null>(null);
  const vertexMarkers = useRef<maplibregl.Marker[]>([]);
  const pinMarkers = useRef<maplibregl.Marker[]>([]);
  const cursorRef = useRef<LngLat | null>(null);
  /** Set by the map effect; lets the prop effects re-evaluate terrain. */
  const applyTerrainRef = useRef<(() => void) | null>(null);
  /**
   * Padding is held at zero until the intro plays, so the globe first sits in
   * the middle of the window; the intro then eases it into the frame.
   */
  const introPlayedRef = useRef(false);
  const insetsRef = useRef<Insets>(NO_INSETS);
  const readyRef = useRef(false);
  /**
   * Mirrors readyRef as state. The effects below run once on mount, before
   * the style has loaded, and would otherwise bail out and never run again —
   * leaving layers stuck at whatever the style declared.
   */
  const [styleReady, setStyleReady] = useState(false);

  // Latest props, so map handlers never close over stale values.
  const p = useRef(props);
  p.current = props;

  const refreshDraft = useCallback(() => {
    const map = mapRef.current;
    if (!map || !readyRef.current) return;
    const src = map.getSource(SOURCE.draft) as maplibregl.GeoJSONSource | undefined;
    src?.setData(draftData(p.current.draftPoints, p.current.draftKind, cursorRef.current));
  }, []);

  // ----------------------------------------------------------- map creation
  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;

    const lite = p.current.lite;
    const map = new maplibregl.Map({
      container: containerRef.current,
      style: buildStyle(),
      center: INTRO_VIEW.center as LngLatLike,
      zoom: INTRO_VIEW.zoom,
      pitch: HOME_VIEW.pitch,
      bearing: HOME_VIEW.bearing,
      attributionControl: false,
      minZoom: MIN_ZOOM,
      maxZoom: MAX_ZOOM,
      maxPitch: MAX_PITCH,
      dragRotate: true,
      pitchWithRotate: true,
      // A deeper cache means a fast zoom out and back in reuses tiles instead
      // of refetching them, which is what made quick scrolling look empty.
      maxTileCacheSize: lite ? 60 : 320,
      fadeDuration: lite ? 0 : 120,
      refreshExpiredTiles: false,
      // Keeps the flat map touching both edges of the screen when zoomed out,
      // so there is never a black band beside the world.
      renderWorldCopies: true,
    });
    mapRef.current = map;

    map.touchZoomRotate.enableRotation();
    map.keyboard.enable();

    // ------------------------------------------------------------ telemetry
    let lastEmit = 0;
    let pendingFrame = 0;

    const emit = () => {
      pendingFrame = 0;
      lastEmit = performance.now();
      const c = map.getCenter();
      const zoom = map.getZoom();
      const metersPerPixel =
        (156543.03392 * Math.cos((c.lat * Math.PI) / 180)) / 2 ** zoom;

      // Camera height above the centre point. MapLibre's default field of
      // view puts the camera 1.5 viewport-heights back from the centre, so the
      // ground resolution converts that straight into metres.
      const pitchRad = (map.getPitch() * Math.PI) / 180;
      const altitude =
        1.5 * map.getCanvas().clientHeight * metersPerPixel * Math.cos(pitchRad);

      setTelemetry({
        center: [c.lng, c.lat],
        zoom,
        bearing: map.getBearing(),
        pitch: map.getPitch(),
        metersPerPixel,
        altitude,
      });
    };

    const scheduleEmit = () => {
      if (pendingFrame) return;
      const wait = Math.max(0, TELEMETRY_INTERVAL_MS - (performance.now() - lastEmit));
      pendingFrame = window.setTimeout(emit, wait);
    };

    // ------------------------------------------------------------- terrain
    let terrainOn = false;
    let terrainExaggeration = 0;
    const applyTerrain = () => {
      const want =
        !p.current.lite &&
        p.current.exaggeration > 0 &&
        map.getZoom() >= TERRAIN_MIN_ZOOM;
      if (want === terrainOn && p.current.exaggeration === terrainExaggeration) return;
      terrainOn = want;
      terrainExaggeration = p.current.exaggeration;
      map.setTerrain(
        want ? { source: SOURCE.terrain, exaggeration: p.current.exaggeration } : null,
      );
    };
    applyTerrainRef.current = applyTerrain;

    // -------------------------------------------------- camera-limit guards
    let lastPitchLimit = -1;
    const applyPitchLimit = () => {
      const limit = allowedPitch(map.getZoom());
      if (limit === lastPitchLimit) return;
      lastPitchLimit = limit;
      map.setMaxPitch(Math.max(limit, 0.0001));
    };

    map.on('move', () => {
      scheduleEmit();
      applyPitchLimit();
      applyTerrain();
    });
    map.on('moveend', emit);
    map.on('zoomend', emit);

    // A panel that opened while the camera was moving is caught up here, once
    // the movement is over, rather than by cutting the movement short.
    // Deferred a frame so that a drag which interrupted the camera is already
    // under way, and is not fought.
    map.on('moveend', () => {
      if (!introPlayedRef.current) return;
      requestAnimationFrame(() => {
        if (mapRef.current !== map || map.isMoving()) return;
        const want = insetsRef.current;
        if (sameInsets(map.getPadding(), want)) return;
        map.easeTo({ padding: want, duration: PADDING_EASE_MS, easing: easeInOutCubic });
      });
    });

    map.on('load', () => {
      readyRef.current = true;
      setStyleReady(true);
      applyPitchLimit();
      applyTerrain();
      emit();
      p.current.onLoaded();
      refreshDraft();

      // 'idle' means every tile for this view has been fetched and drawn.
      // A timeout guards against a slow or offline network stalling the intro.
      let revealed = false;
      const reveal = () => {
        if (revealed) return;
        revealed = true;
        map.off('idle', reveal);
        p.current.onReady();
      };
      map.on('idle', reveal);
      window.setTimeout(reveal, 4500);
    });

    // MapLibre reports which source failed, which is more reliable than
    // matching on the error text.
    let demWarned = false;
    map.on('error', (e) => {
      const sourceId = (e as unknown as { sourceId?: string }).sourceId;
      if (sourceId === SOURCE.terrain && !demWarned) {
        demWarned = true;
        p.current.onDemError();
      }
    });

    // --------------------------------------------------------- pointer work
    let elevTimer: ReturnType<typeof setTimeout> | undefined;
    let moving = false;
    map.on('movestart', () => {
      moving = true;
    });
    map.on('moveend', () => {
      moving = false;
    });

    map.on('mousemove', (e) => {
      const lngLat: LngLat = [e.lngLat.lng, e.lngLat.lat];
      cursorRef.current = lngLat;
      setTelemetry({ cursor: lngLat, cursorElevation: null });
      if (p.current.draftKind && p.current.draftKind !== 'pin') refreshDraft();

      // Elevation lookups wait for the pointer to settle, and never run while
      // the camera is moving.
      clearTimeout(elevTimer);
      elevTimer = setTimeout(async () => {
        if (moving) return;
        const z = Math.min(14, Math.max(8, Math.round(map.getZoom())));
        const elevation = await sampleElevation(lngLat, z);
        if (cursorRef.current === lngLat) setTelemetry({ cursorElevation: elevation });
      }, 280);
    });

    map.on('mouseout', () => {
      cursorRef.current = null;
      setTelemetry({ cursor: null, cursorElevation: null });
      refreshDraft();
    });

    map.on('click', (e) => {
      if (!p.current.draftKind) return;
      p.current.onDraftPointsChange([
        ...p.current.draftPoints,
        [e.lngLat.lng, e.lngLat.lat],
      ]);
    });

    map.on('dblclick', (e) => {
      if (!p.current.draftKind || p.current.draftKind === 'pin') return;
      e.preventDefault();
      p.current.onFinishDraft();
    });

    return () => {
      clearTimeout(elevTimer);
      applyTerrainRef.current = null;
      if (pendingFrame) clearTimeout(pendingFrame);
      vertexMarkers.current.forEach((m) => m.remove());
      pinMarkers.current.forEach((m) => m.remove());
      vertexMarkers.current = [];
      pinMarkers.current = [];
      map.remove();
      mapRef.current = null;
      readyRef.current = false;
      setStyleReady(false);
    };
  }, [refreshDraft]);

  // -------------------------------------------------- controller for parent
  useEffect(() => {
    const controller: MapController = {
      playIntro: () => {
        const map = mapRef.current;
        if (!map || introPlayedRef.current) return;
        introPlayedRef.current = true;
        // The padding rides along with the turn: the globe glides from the
        // middle of the window into the frame between the panels as one
        // camera movement, with no resize of the canvas at any point.
        const padding = frameInsets(containerRef.current, p.current.frameRef.current);
        insetsRef.current = padding;
        const still = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
        if (still || p.current.lite) {
          map.jumpTo({ center: HOME_VIEW.center as LngLatLike, zoom: HOME_VIEW.zoom, padding });
          return;
        }
        map.easeTo({
          center: HOME_VIEW.center as LngLatLike,
          zoom: HOME_VIEW.zoom,
          padding,
          duration: INTRO_SPIN_MS,
          easing: easeInOutCubic,
          essential: true,
        });
      },
      zoomIn: () => mapRef.current?.zoomIn({ duration: 280 }),
      zoomOut: () => mapRef.current?.zoomOut({ duration: 280 }),
      resetNorth: () => mapRef.current?.easeTo({ bearing: 0, duration: 420 }),
      setPitch: (pitch) => {
        const map = mapRef.current;
        if (!map) return;
        // Tilting from a world view is meaningless, so come closer first.
        const zoom = Math.max(map.getZoom(), pitch > 0 ? PITCH_FREE_ZOOM + 1 : map.getZoom());
        map.easeTo({ pitch, zoom, duration: 700 });
      },
      flyTo: (center, zoom) =>
        mapRef.current?.flyTo({
          center: center as LngLatLike,
          zoom: zoom ?? Math.max(mapRef.current.getZoom(), 13),
          duration: 2400,
          curve: 1.4,
          essential: true,
        }),
      fitPoints: (points) => {
        const map = mapRef.current;
        if (!map || !points.length) return;
        if (points.length === 1) {
          map.flyTo({ center: points[0] as LngLatLike, zoom: 16, duration: 1800 });
          return;
        }
        const bounds = points.reduce(
          (b, point) => b.extend(point as LngLatLike),
          new maplibregl.LngLatBounds(points[0] as LngLatLike, points[0] as LngLatLike),
        );
        map.fitBounds(bounds, { padding: 120, duration: 1600, maxZoom: 17 });
      },
      locate: (onError) => {
        if (!navigator.geolocation) return onError('unavailable');
        navigator.geolocation.getCurrentPosition(
          (pos) =>
            mapRef.current?.flyTo({
              center: [pos.coords.longitude, pos.coords.latitude],
              zoom: 16,
              duration: 2400,
              essential: true,
            }),
          () => onError('denied'),
          { enableHighAccuracy: true, timeout: 10000 },
        );
      },
    };
    props.onController(controller);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ------------------------------------------------------ frame padding
  // The map never changes size when a panel opens; the frame does, and the
  // camera padding follows it. Measured once per frame at most.
  useEffect(() => {
    const map = mapRef.current;
    const frame = props.frameRef.current;
    if (!map || !readyRef.current || !frame) return;

    let raf = 0;
    const measure = () => {
      raf = 0;
      const next = frameInsets(containerRef.current, frame);
      if (sameInsets(insetsRef.current, next)) return;
      insetsRef.current = next;
      // Before the intro the globe stays centred in the window; afterwards a
      // moving camera is left alone and caught up on 'moveend'.
      if (!introPlayedRef.current || map.isMoving()) return;
      map.easeTo({ padding: next, duration: PADDING_EASE_MS, easing: easeInOutCubic });
    };
    const schedule = () => {
      if (!raf) raf = requestAnimationFrame(measure);
    };

    const observer = new ResizeObserver(schedule);
    observer.observe(frame);
    window.addEventListener('resize', schedule);
    return () => {
      if (raf) cancelAnimationFrame(raf);
      observer.disconnect();
      window.removeEventListener('resize', schedule);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [styleReady]);

  // --------------------------------------------------------- projection
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !readyRef.current) return;
    map.setProjection({ type: props.lite ? 'mercator' : 'globe' });
    map.setMinZoom(props.lite ? MIN_ZOOM_FLAT : MIN_ZOOM);
  }, [props.lite, styleReady]);

  // ------------------------------------------------------------- terrain
  useEffect(() => {
    if (!readyRef.current) return;
    applyTerrainRef.current?.();
  }, [props.exaggeration, props.lite, styleReady]);

  // --------------------------------------------------- layer visibility
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !readyRef.current) return;

    const preset = BASEMAP_LAYERS[props.basemap];
    const show = (id: string, on: boolean) =>
      map.getLayer(id) && map.setLayoutProperty(id, 'visibility', on ? 'visible' : 'none');

    const labelsOn = props.showLabels || props.basemap === 'hybrid';
    show(LAYER.satellite, preset.satellite);
    show(LAYER.topo, preset.topo);
    show(LAYER.hillshade, preset.hillshade || props.blendHillshade);
    show(LAYER.roads, props.showRoads);

    // Place names are drawn text, so the same layers serve every basemap:
    // only the ink and halo flip for the pale ones.
    for (const id of LABEL_LAYERS) show(id, labelsOn);
    const ink = preset.darkLabels ? '#202124' : '#ffffff';
    const halo = preset.darkLabels ? 'rgba(255, 255, 255, 0.85)' : 'rgba(0, 0, 0, 0.75)';
    for (const id of LABEL_TEXT_LAYERS) {
      if (!map.getLayer(id)) continue;
      map.setPaintProperty(id, 'text-color', ink);
      map.setPaintProperty(id, 'text-halo-color', halo);
    }
    if (map.getLayer(LAYER.placeDot)) {
      map.setPaintProperty(LAYER.placeDot, 'circle-color', ink);
      map.setPaintProperty(LAYER.placeDot, 'circle-stroke-color', halo);
    }
    map.setPaintProperty(LAYER.background, 'background-color', preset.bg);

    // Over imagery the hillshade is a subtle overlay, not the picture itself.
    const overImagery = preset.satellite || preset.topo;
    map.setPaintProperty(
      LAYER.hillshade,
      'hillshade-exaggeration',
      props.shadowStrength * (overImagery ? 0.55 : 1),
    );
    map.setPaintProperty(
      LAYER.hillshade,
      'hillshade-shadow-color',
      overImagery ? 'rgba(10,16,24,0.85)' : '#1b2430',
    );
    map.setPaintProperty(
      LAYER.hillshade,
      'hillshade-highlight-color',
      overImagery ? 'rgba(255,245,225,0.5)' : '#ffffff',
    );
  }, [
    props.basemap,
    props.showLabels,
    props.showRoads,
    props.blendHillshade,
    props.shadowStrength,
    styleReady,
  ]);

  // -------------------------------------------------------- label language
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !readyRef.current) return;
    // Names only exist in Turkish where they differ, so fall back to English.
    const field = ['coalesce', ['get', props.lang], ['get', 'en']];
    for (const id of LABEL_TEXT_LAYERS) {
      if (map.getLayer(id)) {
        map.setLayoutProperty(id, 'text-field', field as never);
      }
    }
  }, [props.lang, styleReady]);

  // ------------------------------------------------------------ sun angle
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !readyRef.current || !map.getLayer(LAYER.hillshade)) return;
    map.setPaintProperty(LAYER.hillshade, 'hillshade-illumination-direction', props.sunAzimuth);
    map.setLight({ anchor: 'map', position: [1.4, props.sunAzimuth, 32], intensity: 0.3 });
  }, [props.sunAzimuth, styleReady]);

  // ------------------------------------------------- saved items + pins
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !readyRef.current) return;

    const visible = props.items.filter((item) => item.visible);
    const shapes = visible.filter((item) => item.kind !== 'pin');
    const src = map.getSource(SOURCE.items) as maplibregl.GeoJSONSource | undefined;
    src?.setData(itemsToCollection(shapes));

    pinMarkers.current.forEach((m) => m.remove());
    pinMarkers.current = visible
      .filter((item) => item.kind === 'pin')
      .map((item) => {
        const el = document.createElement('div');
        el.className = 'map-pin';
        el.innerHTML =
          '<span class="map-pin-label"></span>' +
          '<svg viewBox="0 0 24 24" width="26" height="26" aria-hidden="true">' +
          '<path d="M12 23s8-7.3 8-13a8 8 0 10-16 0c0 5.7 8 13 8 13z" fill="#8ab4f8" ' +
          'stroke="#202124" stroke-width="1.2"/>' +
          '<circle cx="12" cy="10" r="2.8" fill="#202124"/></svg>';
        const label = el.querySelector('.map-pin-label');
        if (label) label.textContent = item.name;
        return new maplibregl.Marker({ element: el, anchor: 'bottom' })
          .setLngLat(item.points[0])
          .addTo(map);
      });
  }, [props.items, styleReady]);

  // -------------------------------------------------- draft interaction
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const drawing = props.draftKind !== null;
    if (drawing) {
      map.doubleClickZoom.disable();
      map.getCanvas().style.cursor = 'crosshair';
    } else {
      map.doubleClickZoom.enable();
      map.getCanvas().style.cursor = '';
    }
  }, [props.draftKind]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !readyRef.current) return;

    vertexMarkers.current.forEach((m) => m.remove());
    vertexMarkers.current = [];

    if (props.draftKind && props.draftKind !== 'pin') {
      props.draftPoints.forEach((point, i) => {
        const el = document.createElement('button');
        el.className = 'vertex';
        el.type = 'button';
        el.textContent = String(i + 1);
        el.setAttribute('aria-label', `Point ${i + 1}`);
        el.addEventListener('click', (ev) => {
          ev.stopPropagation();
          // Clicking the first point closes the shape, as in most map editors.
          if (i === 0 && p.current.draftPoints.length >= 3) p.current.onFinishDraft();
        });

        const marker = new maplibregl.Marker({ element: el, draggable: true })
          .setLngLat(point)
          .addTo(map);

        marker.on('drag', () => {
          const { lng, lat } = marker.getLngLat();
          const next = [...p.current.draftPoints];
          next[i] = [lng, lat];
          p.current.onDraftPointsChange(next);
        });

        vertexMarkers.current.push(marker);
      });
    }

    refreshDraft();
  }, [props.draftPoints, props.draftKind, refreshDraft, styleReady]);

  return <div ref={containerRef} className="map-root" />;
}
