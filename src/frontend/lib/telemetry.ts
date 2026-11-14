'use client';

import { useSyncExternalStore } from 'react';
import type { LngLat } from './geo';

/**
 * Map telemetry lives outside React.
 *
 * The map fires `move` on every animation frame. Routing that through React
 * state re-renders the whole screen 60 times a second while zooming, which is
 * what made fast scroll-wheel zooming stutter and stall tile loading. Here the
 * values sit in a module-level store, updates are throttled, and only the few
 * small readouts that subscribe ever re-render.
 */
export interface Telemetry {
  center: LngLat;
  zoom: number;
  bearing: number;
  pitch: number;
  /** Ground resolution at the centre of the view. */
  metersPerPixel: number;
  /** Camera height above sea level, metres. */
  altitude: number;
  cursor: LngLat | null;
  cursorElevation: number | null;
}

let state: Telemetry = {
  center: [0, 0],
  zoom: 2,
  bearing: 0,
  pitch: 0,
  metersPerPixel: 10,
  altitude: 0,
  cursor: null,
  cursorElevation: null,
};

const listeners = new Set<() => void>();

function changed(patch: Partial<Telemetry>): boolean {
  for (const key of Object.keys(patch) as (keyof Telemetry)[]) {
    const next = patch[key];
    const current = state[key];
    if (key === 'center' || key === 'cursor') {
      const a = current as LngLat | null;
      const b = next as LngLat | null;
      if (a === b) continue;
      if (!a || !b || a[0] !== b[0] || a[1] !== b[1]) return true;
      continue;
    }
    if (current !== next) return true;
  }
  return false;
}

export function setTelemetry(patch: Partial<Telemetry>) {
  if (!changed(patch)) return;
  state = { ...state, ...patch };
  for (const listener of listeners) listener();
}

export function getTelemetry(): Telemetry {
  return state;
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/**
 * The returned object is reference-stable between updates, so it is safe to
 * read straight from `useSyncExternalStore`.
 */
export function useTelemetry(): Telemetry {
  return useSyncExternalStore(subscribe, getTelemetry, getTelemetry);
}
