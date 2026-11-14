'use client';

import { PITCH_FREE_ZOOM } from '@/lib/config';
import { translator, type Lang } from '@/lib/i18n';
import { useTelemetry } from '@/lib/telemetry';
import { CompassNeedle, CompassRing, Globe, Locate, Minus, Plus } from './Icons';

interface Props {
  lang: Lang;
  lite: boolean;
  onZoomIn: () => void;
  onZoomOut: () => void;
  onResetNorth: () => void;
  onSetPitch: (pitch: number) => void;
  onLocate: () => void;
  onToggleLite: () => void;
}

export default function MapControls(props: Props) {
  const t = translator(props.lang);
  const { bearing, pitch, zoom } = useTelemetry();
  const is3D = pitch > 8;
  // Below this zoom the camera is held flat, so the button would do nothing.
  const canTilt = zoom >= PITCH_FREE_ZOOM - 2;

  return (
    <div className="mapctl">
      <button
        className="ctrl"
        onClick={props.onLocate}
        aria-label={t('myLocation')}
        title={t('myLocation')}
      >
        <Locate size={17} />
      </button>

      <button
        className={`ctrl${props.lite ? ' on' : ''}`}
        onClick={props.onToggleLite}
        aria-label={t('liteMode')}
        title={t('liteMode')}
      >
        <Globe size={17} />
      </button>

      <button
        className={`ctrl${is3D ? ' on' : ''}`}
        onClick={() => props.onSetPitch(is3D ? 0 : 62)}
        aria-label={is3D ? t('view2d') : t('view3d')}
        title={is3D ? t('view2d') : t('view3d')}
        disabled={!canTilt && !is3D}
      >
        <span className="mode-text">{is3D ? t('view2d') : t('view3d')}</span>
      </button>

      <button
        className="ctrl"
        onClick={props.onResetNorth}
        aria-label={t('resetNorth')}
        title={t('resetNorth')}
      >
        <span className="compass">
          <CompassRing size={22} style={{ gridArea: '1/1' }} />
          <CompassNeedle
            size={22}
            style={{ gridArea: '1/1', transform: `rotate(${-bearing}deg)` }}
          />
        </span>
      </button>

      <div className="ctrl-stack">
        <button className="ctrl" onClick={props.onZoomIn} aria-label={t('zoomIn')} title={t('zoomIn')}>
          <Plus size={17} />
        </button>
        <button className="ctrl" onClick={props.onZoomOut} aria-label={t('zoomOut')} title={t('zoomOut')}>
          <Minus size={17} />
        </button>
      </div>
    </div>
  );
}
