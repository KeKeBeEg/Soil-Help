'use client';

import { useState, type ReactNode } from 'react';
import { type BasemapId } from '@/lib/config';
import { formatArea, formatDistance, pathLength, polygonArea, type Units } from '@/lib/geo';
import { translator, type Lang } from '@/lib/i18n';
import type { MapItem } from '@/lib/items';
import { Eye, EyeOff, Pin, Polygon, Profile, Trash } from './Icons';
import { Checkbox, Field, Section, Slider } from './ui';

export interface LayerToggles {
  labels: boolean;
  roads: boolean;
  blendHillshade: boolean;
}

interface Props {
  lang: Lang;
  units: Units;
  items: MapItem[];
  onItemFly: (item: MapItem) => void;
  onItemToggle: (id: string) => void;
  onItemRemove: (id: string) => void;
  onItemRename: (id: string, name: string) => void;
  /** The field the Analysis section is about. */
  selectedId: string | null;
  onItemSelect: (id: string) => void;
  /** Contents of the Analysis section. */
  analysis: ReactNode;
  basemap: BasemapId;
  onBasemapChange: (basemap: BasemapId) => void;
  exaggeration: number;
  onExaggerationChange: (value: number) => void;
  sunAzimuth: number;
  onSunAzimuthChange: (value: number) => void;
  shadowStrength: number;
  onShadowStrengthChange: (value: number) => void;
  layers: LayerToggles;
  onLayersChange: (layers: LayerToggles) => void;
  styleOpen: boolean;
  onStyleOpenChange: (open: boolean) => void;
  reliefOpen: boolean;
  onReliefOpenChange: (open: boolean) => void;
  lite: boolean;
}

const ICONS = { pin: Pin, field: Polygon, path: Profile };

export default function Sidebar(props: Props) {
  const t = translator(props.lang);
  const [contentOpen, setContentOpen] = useState(true);
  const [analysisOpen, setAnalysisOpen] = useState(true);
  const [editing, setEditing] = useState<string | null>(null);

  const setLayer = (key: keyof LayerToggles, value: boolean) =>
    props.onLayersChange({ ...props.layers, [key]: value });

  const exagLabel = props.exaggeration === 0 ? t('off') : `${props.exaggeration.toFixed(1)}×`;

  /** A one-line measurement, so the list says something useful at a glance. */
  function measure(item: MapItem): string {
    if (item.kind === 'field' && item.points.length >= 3) {
      const area = formatArea(polygonArea(item.points), props.units);
      const state = item.analysis?.state;
      if (state === 'done') return `${area} · ${t('analysis').toLowerCase()} ✓`;
      if (state === 'queued' || state === 'running') return `${area} · ${t('stateRunning').toLowerCase()}…`;
      return area;
    }
    if (item.kind === 'path' && item.points.length >= 2) {
      return formatDistance(pathLength(item.points), props.units);
    }
    const [lng, lat] = item.points[0] ?? [0, 0];
    return `${lat.toFixed(4)}, ${lng.toFixed(4)}`;
  }

  return (
    <aside className="sidebar">
      <div className="sidebar-scroll">
        <Section
          title={`${t('mapContent')}${props.items.length ? ` (${props.items.length})` : ''}`}
          open={contentOpen}
          onToggle={() => setContentOpen(!contentOpen)}
        >
          {props.items.length === 0 ? (
            <p className="hint">{t('emptyContent')}</p>
          ) : (
            <ul className="item-list">
              {props.items.map((item) => {
                const Icon = ICONS[item.kind];
                return (
                  <li className={`item${item.id === props.selectedId ? ' selected' : ''}`} key={item.id}>
                    <button
                      className="item-vis"
                      onClick={() => props.onItemToggle(item.id)}
                      aria-label={item.visible ? t('hide') : t('show')}
                      title={item.visible ? t('hide') : t('show')}
                    >
                      {item.visible ? <Eye size={14} /> : <EyeOff size={14} />}
                    </button>

                    <Icon size={14} className="item-icon" />

                    {editing === item.id ? (
                      <input
                        className="item-rename"
                        autoFocus
                        defaultValue={item.name}
                        onBlur={(e) => {
                          props.onItemRename(item.id, e.target.value.trim() || item.name);
                          setEditing(null);
                        }}
                        onKeyDown={(e) => {
                          if (e.key === 'Enter') e.currentTarget.blur();
                          if (e.key === 'Escape') setEditing(null);
                        }}
                      />
                    ) : (
                      <button
                        className="item-name"
                        onClick={() => {
                          props.onItemFly(item);
                          if (item.kind === 'field') props.onItemSelect(item.id);
                        }}
                        onDoubleClick={() => setEditing(item.id)}
                        title={t('flyHere')}
                      >
                        <span className="item-title">{item.name}</span>
                        <span className="item-measure">{measure(item)}</span>
                      </button>
                    )}

                    <button
                      className="item-del"
                      onClick={() => props.onItemRemove(item.id)}
                      aria-label={t('remove')}
                      title={t('remove')}
                    >
                      <Trash size={14} />
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </Section>

        <Section title={t('analysis')} open={analysisOpen} onToggle={() => setAnalysisOpen(!analysisOpen)}>
          {props.analysis}
        </Section>

        <Section
          title={t('mapStyle')}
          open={props.styleOpen}
          onToggle={() => props.onStyleOpenChange(!props.styleOpen)}
        >
          <Field label={t('rendering')}>
            <select
              className="select"
              value={props.basemap}
              onChange={(e) => props.onBasemapChange(e.target.value as BasemapId)}
              aria-label={t('rendering')}
            >
              <option value="satellite">{t('satellite')}</option>
              <option value="hybrid">{t('hybrid')}</option>
              <option value="relief">{t('relief')}</option>
              <option value="topo">{t('topo')}</option>
            </select>
          </Field>

          <Field label={t('reliefHeight')} value={props.lite ? t('off') : exagLabel}>
            <Slider
              min={0}
              max={3}
              step={0.1}
              value={props.lite ? 0 : props.exaggeration}
              onChange={props.onExaggerationChange}
              ariaLabel={t('reliefHeight')}
            />
          </Field>

          <div>
            <Checkbox
              label={t('blendIntoImagery')}
              checked={props.layers.blendHillshade}
              onChange={(v) => setLayer('blendHillshade', v)}
            />
            <Checkbox
              label={t('placeLabels')}
              checked={props.layers.labels || props.basemap === 'hybrid'}
              onChange={(v) => setLayer('labels', v)}
            />
            <Checkbox
              label={t('roads')}
              checked={props.layers.roads}
              onChange={(v) => setLayer('roads', v)}
            />
          </div>

        </Section>

        <Section
          title={t('reliefShadows')}
          open={props.reliefOpen}
          onToggle={() => props.onReliefOpenChange(!props.reliefOpen)}
        >
          <Field label={t('sunDirection')} value={`${props.sunAzimuth}°`}>
            <Slider
              min={0}
              max={359}
              step={1}
              value={props.sunAzimuth}
              onChange={props.onSunAzimuthChange}
              ariaLabel={t('sunDirection')}
            />
          </Field>
          <Field
            label={t('shadowStrength')}
            value={`${Math.round(props.shadowStrength * 100)}%`}
          >
            <Slider
              min={0}
              max={1}
              step={0.05}
              value={props.shadowStrength}
              onChange={props.onShadowStrengthChange}
              ariaLabel={t('shadowStrength')}
            />
          </Field>
        </Section>
      </div>
    </aside>
  );
}
