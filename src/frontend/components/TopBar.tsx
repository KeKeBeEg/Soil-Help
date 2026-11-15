'use client';

import { useEffect, useRef, useState } from 'react';
import type { BasemapId } from '@/lib/config';
import { translator, type Lang } from '@/lib/i18n';
import type { ItemKind } from '@/lib/items';
import { ChevronDown, Info, Mark } from './Icons';

export interface MenuAction {
  id: string;
  label: string;
  onSelect: () => void;
  disabled?: boolean;
  checked?: boolean;
  separatorBefore?: boolean;
}

interface Props {
  lang: Lang;
  onLangChange: (lang: Lang) => void;
  basemap: BasemapId;
  onBasemapChange: (basemap: BasemapId) => void;
  showLabels: boolean;
  onToggleLabels: () => void;
  showRoads: boolean;
  onToggleRoads: () => void;
  lite: boolean;
  onToggleLite: () => void;
  hasItems: boolean;
  hasProfile: boolean;
  onExportGeoJson: () => void;
  onExportCsv: () => void;
  onClearAll: () => void;
  onStartDraft: (kind: ItemKind) => void;
  onAbout: () => void;
}

export default function TopBar(props: Props) {
  const t = translator(props.lang);
  const [open, setOpen] = useState<string | null>(null);
  const barRef = useRef<HTMLDivElement>(null);

  // A menu closes on an outside click or Escape, like any desktop menu bar.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (!barRef.current?.contains(e.target as Node)) setOpen(null);
    };
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(null);
    document.addEventListener('mousedown', onDown);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDown);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  const menus: { id: string; label: string; items: MenuAction[] }[] = [
    {
      id: 'file',
      label: t('menuFile'),
      items: [
        {
          id: 'geojson',
          label: t('exportGeoJson'),
          onSelect: props.onExportGeoJson,
          disabled: !props.hasItems,
        },
        {
          id: 'csv',
          label: t('exportProfileCsv'),
          onSelect: props.onExportCsv,
          disabled: !props.hasProfile,
        },
        {
          id: 'clear',
          label: t('clearAll'),
          onSelect: props.onClearAll,
          disabled: !props.hasItems,
          separatorBefore: true,
        },
      ],
    },
    {
      id: 'view',
      label: t('menuView'),
      items: [
        {
          id: 'satellite',
          label: t('satellite'),
          onSelect: () => props.onBasemapChange('satellite'),
          checked: props.basemap === 'satellite',
        },
        {
          id: 'hybrid',
          label: t('hybrid'),
          onSelect: () => props.onBasemapChange('hybrid'),
          checked: props.basemap === 'hybrid',
        },
        {
          id: 'relief',
          label: t('relief'),
          onSelect: () => props.onBasemapChange('relief'),
          checked: props.basemap === 'relief',
        },
        {
          id: 'topo',
          label: t('topo'),
          onSelect: () => props.onBasemapChange('topo'),
          checked: props.basemap === 'topo',
        },
        {
          id: 'labels',
          label: t('placeLabels'),
          onSelect: props.onToggleLabels,
          checked: props.showLabels || props.basemap === 'hybrid',
          separatorBefore: true,
        },
        {
          id: 'roads',
          label: t('roads'),
          onSelect: props.onToggleRoads,
          checked: props.showRoads,
        },
        {
          id: 'lite',
          label: t('liteMode'),
          onSelect: props.onToggleLite,
          checked: props.lite,
          separatorBefore: true,
        },
      ],
    },
    {
      id: 'tools',
      label: t('menuTools'),
      items: [
        { id: 'pin', label: t('dropPin'), onSelect: () => props.onStartDraft('pin') },
        { id: 'field', label: t('drawField'), onSelect: () => props.onStartDraft('field') },
        { id: 'path', label: t('drawProfile'), onSelect: () => props.onStartDraft('path') },
      ],
    },
    {
      id: 'help',
      label: t('menuHelp'),
      items: [{ id: 'about', label: t('about'), onSelect: props.onAbout }],
    },
  ];

  return (
    <header className="topbar" ref={barRef}>
      <span className="topbar-brand">
        <span className="brand-mark">
          <Mark size={20} />
        </span>
        <span className="brand-name">Soil Help</span>
        <span className="brand-sub">{t('tagline')}</span>
      </span>

      <nav className="menubar" aria-label={t('menuFile')}>
        {menus.map((menu) => (
          <div className="menu" key={menu.id}>
            <button
              className={`menu-trigger${open === menu.id ? ' open' : ''}`}
              aria-haspopup="menu"
              aria-expanded={open === menu.id}
              onClick={() => setOpen(open === menu.id ? null : menu.id)}
              onMouseEnter={() => open && setOpen(menu.id)}
            >
              {menu.label}
              <ChevronDown size={11} />
            </button>

            {open === menu.id && (
              <div className="menu-pop" role="menu">
                {menu.items.map((item) => (
                  <div key={item.id}>
                    {item.separatorBefore && <div className="menu-sep" />}
                    <button
                      role="menuitem"
                      className="menu-item"
                      disabled={item.disabled}
                      onClick={() => {
                        item.onSelect();
                        setOpen(null);
                      }}
                    >
                      <span className="menu-check">{item.checked ? '✓' : ''}</span>
                      {item.label}
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        ))}
      </nav>

      <div className="topbar-right">
        <div className="lang-switch" role="group" aria-label={t('language')}>
          <button
            className={props.lang === 'tr' ? 'on' : ''}
            onClick={() => props.onLangChange('tr')}
          >
            TR
          </button>
          <button
            className={props.lang === 'en' ? 'on' : ''}
            onClick={() => props.onLangChange('en')}
          >
            EN
          </button>
        </div>
        <button className="icon-btn" onClick={props.onAbout} aria-label={t('about')} title={t('about')}>
          <Info size={16} />
        </button>
      </div>
    </header>
  );
}
