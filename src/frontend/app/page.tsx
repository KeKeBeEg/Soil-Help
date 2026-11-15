'use client';

import dynamic from 'next/dynamic';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import AboutDialog from '@/components/AboutDialog';
import DrawBar from '@/components/DrawBar';
import MapControls from '@/components/MapControls';
import MiniMap from '@/components/MiniMap';
import ProfilePanel, { type Sample } from '@/components/ProfilePanel';
import Starfield from '@/components/Starfield';
import Sidebar, { type LayerToggles } from '@/components/Sidebar';
import Splash from '@/components/Splash';
import StatusBar from '@/components/StatusBar';
import Toolbar from '@/components/Toolbar';
import TopBar from '@/components/TopBar';
import type { MapController } from '@/components/MapCanvas';

import { type BasemapId } from '@/lib/config';
import { sampleElevations } from '@/lib/elevation';
import { interpolatePath, pathLength, type LngLat, type Units } from '@/lib/geo';
import {
  MIN_POINTS,
  defaultName,
  itemsToCollection,
  newId,
  type ItemKind,
  type MapItem,
} from '@/lib/items';
import { translator, type Lang } from '@/lib/i18n';

// MapLibre touches `window` on import, so it must stay out of the server build.
const MapCanvas = dynamic(() => import('@/components/MapCanvas'), { ssr: false });

const PROFILE_SAMPLES = 180;

/** Flags phones that will struggle with a 3D globe and terrain tiles. */
function detectLite(): boolean {
  if (typeof navigator === 'undefined') return false;
  const nav = navigator as Navigator & {
    connection?: { saveData?: boolean; effectiveType?: string };
    deviceMemory?: number;
  };
  if (nav.connection?.saveData) return true;
  if (nav.connection?.effectiveType && /(^|-)2g$/.test(nav.connection.effectiveType)) return true;
  if (typeof nav.deviceMemory === 'number' && nav.deviceMemory <= 2) return true;
  if (nav.hardwareConcurrency && nav.hardwareConcurrency <= 2) return true;
  return false;
}

function download(name: string, text: string, mime: string) {
  const blob = new Blob([text], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

export default function Page() {
  const controller = useRef<MapController | null>(null);
  /** The visible map area between the panels; the globe is centred in it. */
  const frame = useRef<HTMLDivElement>(null);

  const [lang, setLang] = useState<Lang>('tr');
  const t = translator(lang);

  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [styleOpen, setStyleOpen] = useState(true);
  const [reliefOpen, setReliefOpen] = useState(false);
  const [aboutOpen, setAboutOpen] = useState(false);

  const [basemap, setBasemap] = useState<BasemapId>('satellite');
  const [exaggeration, setExaggeration] = useState(1.4);
  const [sunAzimuth, setSunAzimuth] = useState(315);
  const [shadowStrength, setShadowStrength] = useState(0.55);
  const [layers, setLayers] = useState<LayerToggles>({
    labels: true,
    roads: false,
    blendHillshade: true,
  });
  const [units, setUnits] = useState<Units>('metric');
  const [lite, setLite] = useState(false);

  const [items, setItems] = useState<MapItem[]>([]);
  const [draftKind, setDraftKind] = useState<ItemKind | null>(null);
  const [draftPoints, setDraftPoints] = useState<LngLat[]>([]);
  const [redoStack, setRedoStack] = useState<LngLat[]>([]);

  const [activeProfile, setActiveProfile] = useState<MapItem | null>(null);
  const [samples, setSamples] = useState<Sample[]>([]);
  const [profileLoading, setProfileLoading] = useState(false);
  const [profileFailed, setProfileFailed] = useState(false);

  /** Intro: the globe loads behind the logo, then turns into place. */
  const [globeReady, setGlobeReady] = useState(false);
  const [brandShown, setBrandShown] = useState(false);
  const [introDone, setIntroDone] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  // Device capability and screen size are client-only facts.
  useEffect(() => {
    if (detectLite()) {
      setLite(true);
      setExaggeration(0);
    }
    if (window.matchMedia('(max-width: 900px)').matches) setSidebarOpen(false);
    const timer = setTimeout(() => setBrandShown(true), 1200);
    return () => clearTimeout(timer);
  }, []);

  useEffect(() => {
    document.documentElement.lang = lang;
  }, [lang]);

  useEffect(() => {
    if (!globeReady || !brandShown || introDone) return;
    controller.current?.playIntro();
    // The interface is released in the same tick the globe starts turning;
    // CSS delays stage the rest. Nothing here waits on a timer, because a
    // busy render loop can starve one indefinitely on a slow device — and
    // then the interface would never arrive at all.
    setIntroDone(true);
  }, [globeReady, brandShown, introDone]);

  const showNotice = useCallback((message: string) => {
    setNotice(message);
    setTimeout(() => setNotice((current) => (current === message ? null : current)), 5200);
  }, []);

  // ------------------------------------------------------------- drafting
  const startDraft = (kind: ItemKind) => {
    if (draftKind === kind) {
      setDraftKind(null);
      setDraftPoints([]);
      return;
    }
    setDraftKind(kind);
    setDraftPoints([]);
    setRedoStack([]);
  };

  const cancelDraft = () => {
    setDraftKind(null);
    setDraftPoints([]);
    setRedoStack([]);
  };

  const commitDraft = useCallback(
    (kind: ItemKind, points: LngLat[]) => {
      const index = items.filter((item) => item.kind === kind).length + 1;
      const item: MapItem = {
        id: newId(),
        kind,
        name: defaultName(kind, index, lang),
        points,
        visible: true,
        createdAt: Date.now(),
      };
      setItems((current) => [...current, item]);
      setDraftKind(null);
      setDraftPoints([]);
      setRedoStack([]);
      if (kind === 'path') setActiveProfile(item);
      return item;
    },
    [items, lang],
  );

  const finishDraft = useCallback(() => {
    if (!draftKind) return;
    if (draftPoints.length < MIN_POINTS[draftKind]) return;
    commitDraft(draftKind, draftPoints);
  }, [draftKind, draftPoints, commitDraft]);

  // A pin needs no toolbar: the first click places it and the tool closes.
  const handleDraftPoints = useCallback(
    (points: LngLat[]) => {
      if (draftKind === 'pin' && points.length >= 1) {
        commitDraft('pin', [points[points.length - 1]]);
        return;
      }
      setDraftPoints(points);
      setRedoStack([]);
    },
    [draftKind, commitDraft],
  );

  // --------------------------------------------------------- profile data
  useEffect(() => {
    const item = activeProfile;
    if (!item || item.points.length < 2) return;

    let cancelled = false;
    setProfileLoading(true);
    setProfileFailed(false);

    const total = pathLength(item.points);
    const coords: LngLat[] = Array.from({ length: PROFILE_SAMPLES }, (_, i) =>
      interpolatePath(item.points, i / (PROFILE_SAMPLES - 1)),
    );

    sampleElevations(coords, 14)
      .then((elevations) => {
        if (cancelled) return;
        const next: Sample[] = coords.map((c, i) => ({
          d: (i / (PROFILE_SAMPLES - 1)) * total,
          z: elevations[i],
          lng: c[0],
          lat: c[1],
        }));
        setSamples(next);
        setProfileFailed(next.every((s) => s.z === null));
      })
      .catch(() => !cancelled && setProfileFailed(true))
      .finally(() => !cancelled && setProfileLoading(false));

    return () => {
      cancelled = true;
    };
  }, [activeProfile]);

  const profileCsv = useCallback(() => {
    const rows = [
      'distance_m,elevation_m,longitude,latitude',
      ...samples.map(
        (s) =>
          `${s.d.toFixed(1)},${s.z === null ? '' : s.z.toFixed(2)},${s.lng.toFixed(6)},${s.lat.toFixed(6)}`,
      ),
    ];
    download(`elevation-profile-${new Date().toISOString().slice(0, 10)}.csv`, rows.join('\n'), 'text/csv');
  }, [samples]);

  // ------------------------------------------------------------ item list
  const removeItem = (id: string) => {
    setItems((current) => current.filter((item) => item.id !== id));
    setActiveProfile((current) => (current?.id === id ? null : current));
  };

  const visibleItems = useMemo(() => items, [items]);

  return (
    <div className={`app${introDone ? '' : ' intro'}`}>
      {/* The globe fills the window from the first frame and is never resized
          by the intro: the panels are laid over it, and the camera padding
          (driven by `frame`) keeps it centred in the space between them. */}
      <div className="globe-layer">
        <Starfield lowDetail={lite} />
        <MapCanvas
          frameRef={frame}
          lang={lang}
          basemap={basemap}
          showLabels={layers.labels}
          showRoads={layers.roads}
          blendHillshade={layers.blendHillshade}
          exaggeration={lite ? 0 : exaggeration}
          sunAzimuth={sunAzimuth}
          shadowStrength={shadowStrength}
          lite={lite}
          items={visibleItems}
          draftKind={draftKind}
          draftPoints={draftPoints}
          onDraftPointsChange={handleDraftPoints}
          onFinishDraft={finishDraft}
          onController={(c) => {
            controller.current = c;
          }}
          onLoaded={() => undefined}
          onReady={() => setGlobeReady(true)}
          onDemError={() => showNotice(t('demError'))}
        />
      </div>

      <TopBar
        lang={lang}
        onLangChange={setLang}
        basemap={basemap}
        onBasemapChange={setBasemap}
        showLabels={layers.labels}
        onToggleLabels={() => setLayers((l) => ({ ...l, labels: !l.labels }))}
        showRoads={layers.roads}
        onToggleRoads={() => setLayers((l) => ({ ...l, roads: !l.roads }))}
        lite={lite}
        onToggleLite={() => {
          const next = !lite;
          setLite(next);
          if (next) showNotice(t('liteModeOn'));
          else setExaggeration((v) => (v === 0 ? 1.4 : v));
        }}
        hasItems={items.length > 0}
        hasProfile={samples.length > 0}
        onExportGeoJson={() =>
          download('map-content.geojson', JSON.stringify(itemsToCollection(items), null, 2), 'application/geo+json')
        }
        onExportCsv={profileCsv}
        onClearAll={() => {
          setItems([]);
          setActiveProfile(null);
          cancelDraft();
        }}
        onStartDraft={startDraft}
        onAbout={() => setAboutOpen(true)}
      />

      <Toolbar
        lang={lang}
        draftKind={draftKind}
        canUndo={draftPoints.length > 0}
        canRedo={redoStack.length > 0}
        sidebarOpen={sidebarOpen}
        stylePanelOpen={styleOpen}
        reliefPanelOpen={reliefOpen}
        onStartDraft={startDraft}
        onUndo={() => {
          setDraftPoints((current) => {
            if (!current.length) return current;
            setRedoStack((stack) => [...stack, current[current.length - 1]]);
            return current.slice(0, -1);
          });
        }}
        onRedo={() => {
          setRedoStack((stack) => {
            if (!stack.length) return stack;
            setDraftPoints((current) => [...current, stack[stack.length - 1]]);
            return stack.slice(0, -1);
          });
        }}
        onToggleSidebar={() => setSidebarOpen((open) => !open)}
        onToggleStylePanel={() => {
          setSidebarOpen(true);
          setStyleOpen((open) => !open);
        }}
        onToggleReliefPanel={() => {
          setSidebarOpen(true);
          setReliefOpen((open) => !open);
        }}
        onGoTo={(center, zoom) => controller.current?.flyTo(center, zoom)}
      />

      <div className={`workspace${sidebarOpen ? '' : ' no-sidebar'}`}>
        {sidebarOpen && (
          <Sidebar
            lang={lang}
            units={units}
            items={items}
            onItemFly={(item) => controller.current?.fitPoints(item.points)}
            onItemToggle={(id) =>
              setItems((current) =>
                current.map((item) =>
                  item.id === id ? { ...item, visible: !item.visible } : item,
                ),
              )
            }
            onItemRemove={removeItem}
            onItemRename={(id, name) =>
              setItems((current) =>
                current.map((item) => (item.id === id ? { ...item, name } : item)),
              )
            }
            basemap={basemap}
            onBasemapChange={setBasemap}
            exaggeration={exaggeration}
            onExaggerationChange={(v) => {
              setExaggeration(v);
              if (v > 0 && lite) setLite(false);
            }}
            sunAzimuth={sunAzimuth}
            onSunAzimuthChange={setSunAzimuth}
            shadowStrength={shadowStrength}
            onShadowStrengthChange={setShadowStrength}
            layers={layers}
            onLayersChange={setLayers}
            styleOpen={styleOpen}
            onStyleOpenChange={setStyleOpen}
            reliefOpen={reliefOpen}
            onReliefOpenChange={setReliefOpen}
            lite={lite}
          />
        )}

        <div className="stage">
          <div className="stage-map" ref={frame}>
            {draftKind && (
              <DrawBar
                lang={lang}
                units={units}
                kind={draftKind}
                points={draftPoints}
                onUndo={() => setDraftPoints((current) => current.slice(0, -1))}
                onClear={() => setDraftPoints([])}
                onDone={finishDraft}
                onCancel={cancelDraft}
              />
            )}

            <MapControls
              lang={lang}
              lite={lite}
              onZoomIn={() => controller.current?.zoomIn()}
              onZoomOut={() => controller.current?.zoomOut()}
              onResetNorth={() => controller.current?.resetNorth()}
              onSetPitch={(pitch) => controller.current?.setPitch(pitch)}
              onLocate={() => controller.current?.locate(() => showNotice(t('locationDenied')))}
              onToggleLite={() => {
                const next = !lite;
                setLite(next);
                if (next) showNotice(t('liteModeOn'));
                else setExaggeration((v) => (v === 0 ? 1.4 : v));
              }}
            />

            <MiniMap />
          </div>

          {activeProfile && (
            <ProfilePanel
              lang={lang}
              units={units}
              title={activeProfile.name}
              samples={samples}
              loading={profileLoading}
              failed={profileFailed}
              onAddPoints={() => {
                setDraftKind('path');
                setDraftPoints(activeProfile.points);
                removeItem(activeProfile.id);
              }}
              onSaveCsv={profileCsv}
              onClose={() => setActiveProfile(null)}
            />
          )}

          {notice && <div className="notice">{notice}</div>}
        </div>
      </div>

      <StatusBar
        lang={lang}
        units={units}
        onUnitsChange={setUnits}
        onAbout={() => setAboutOpen(true)}
      />

      {aboutOpen && <AboutDialog lang={lang} onClose={() => setAboutOpen(false)} />}

      <Splash hidden={globeReady && brandShown} />
    </div>
  );
}
