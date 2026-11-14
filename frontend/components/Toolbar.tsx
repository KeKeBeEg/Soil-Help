'use client';

import { useState } from 'react';
import { parseLatLng, type LngLat } from '@/lib/geo';
import { translator, type Lang, type StringKey } from '@/lib/i18n';
import type { ItemKind } from '@/lib/items';
import { Layers, Pin, Polygon, Profile, Redo, Search, Sidebar, Sun, Undo } from './Icons';

interface Props {
  lang: Lang;
  draftKind: ItemKind | null;
  canUndo: boolean;
  canRedo: boolean;
  sidebarOpen: boolean;
  stylePanelOpen: boolean;
  reliefPanelOpen: boolean;
  onStartDraft: (kind: ItemKind) => void;
  onUndo: () => void;
  onRedo: () => void;
  onToggleSidebar: () => void;
  onToggleStylePanel: () => void;
  onToggleReliefPanel: () => void;
  onGoTo: (center: LngLat, zoom?: number) => void;
}

export default function Toolbar(props: Props) {
  const t = translator(props.lang);
  const [query, setQuery] = useState('');
  const [status, setStatus] = useState<StringKey | null>(null);
  const [busy, setBusy] = useState(false);

  async function search() {
    const text = query.trim();
    if (!text) return;

    const direct = parseLatLng(text);
    if (direct) {
      setStatus(null);
      props.onGoTo(direct, 15);
      return;
    }

    setBusy(true);
    setStatus(null);
    try {
      const res = await fetch(`/api/geocode?q=${encodeURIComponent(text)}`);
      if (!res.ok) throw new Error('geocode failed');
      const hits = (await res.json()) as { lon: number; lat: number }[];
      if (!hits.length) setStatus('noResults');
      else props.onGoTo([hits[0].lon, hits[0].lat], 14);
    } catch {
      setStatus('searchFailed');
    } finally {
      setBusy(false);
    }
  }

  const tool = (
    kind: ItemKind,
    icon: React.ReactNode,
    label: string,
  ) => (
    <button
      className={`tbtn${props.draftKind === kind ? ' active' : ''}`}
      onClick={() => props.onStartDraft(kind)}
      aria-label={label}
      aria-pressed={props.draftKind === kind}
      title={label}
    >
      {icon}
    </button>
  );

  return (
    <div className="toolbar">
      <button
        className={`tbtn${props.sidebarOpen ? ' on' : ''}`}
        onClick={props.onToggleSidebar}
        aria-label={t('toggleSidebar')}
        title={t('toggleSidebar')}
      >
        <Sidebar size={17} />
      </button>

      <div className="tsep" />

      <div className="searchbox">
        <Search size={15} className="searchbox-icon" />
        <input
          className="searchbox-input"
          placeholder={t('goToPlaceholder')}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && search()}
          aria-label={t('goTo')}
        />
        <button className="searchbox-go" onClick={search} disabled={busy}>
          {busy ? '…' : t('go')}
        </button>
        {status && <span className="searchbox-status">{t(status)}</span>}
      </div>

      <div className="tsep edit-only" />

      <button
        className="tbtn edit-only"
        onClick={props.onUndo}
        disabled={!props.canUndo}
        aria-label={t('undo')}
        title={t('undo')}
      >
        <Undo size={17} />
      </button>
      <button
        className="tbtn edit-only"
        onClick={props.onRedo}
        disabled={!props.canRedo}
        aria-label={t('redo')}
        title={t('redo')}
      >
        <Redo size={17} />
      </button>

      <div className="tsep edit-only" />

      {tool('pin', <Pin size={17} />, t('dropPin'))}
      {tool('path', <Profile size={17} />, t('drawProfile'))}
      {tool('field', <Polygon size={17} />, t('drawField'))}

      <div className="tsep panel-only" />

      <button
        className={`tbtn panel-only${props.stylePanelOpen ? ' on' : ''}`}
        onClick={props.onToggleStylePanel}
        aria-label={t('layers')}
        title={t('layers')}
      >
        <Layers size={17} />
      </button>
      <button
        className={`tbtn panel-only${props.reliefPanelOpen ? ' on' : ''}`}
        onClick={props.onToggleReliefPanel}
        aria-label={t('reliefShadows')}
        title={t('reliefShadows')}
      >
        <Sun size={17} />
      </button>
    </div>
  );
}
