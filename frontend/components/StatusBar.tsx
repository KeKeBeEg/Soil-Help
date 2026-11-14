'use client';

import { ATTRIBUTION_SHORT } from '@/lib/config';
import { formatElevation, type Units } from '@/lib/geo';
import { translator, type Lang } from '@/lib/i18n';
import { useTelemetry } from '@/lib/telemetry';

/**
 * Degrees, minutes, seconds — the way Google Earth writes coordinates.
 *
 * Rounding happens first, in hundredths of a second, so the parts can never
 * disagree with each other: rounding the seconds on their own produces
 * readings like 41°02'60.00".
 */
function dms(value: number, positive: string, negative: string): string {
  const hemisphere = value >= 0 ? positive : negative;
  let hundredths = Math.round(Math.abs(value) * 3600 * 100);

  const sec = (hundredths % 6000) / 100;
  hundredths = Math.floor(hundredths / 6000);
  const min = hundredths % 60;
  const deg = Math.floor(hundredths / 60);

  return `${deg}°${String(min).padStart(2, '0')}'${sec.toFixed(2).padStart(5, '0')}"${hemisphere}`;
}

function niceScale(metersPerPixel: number, units: Units) {
  const target = metersPerPixel * 86;
  if (units === 'imperial') {
    const feet = target * 3.280839895;
    const steps = [10, 25, 50, 100, 250, 500, 1000, 2000, 5280, 10560, 26400, 52800, 264000, 1056000];
    const pick = steps.find((s) => s >= feet) ?? steps[steps.length - 1];
    return {
      px: pick / 3.280839895 / metersPerPixel,
      label: pick >= 5280 ? `${+(pick / 5280).toFixed(1)} mi` : `${pick} ft`,
    };
  }
  const steps = [5, 10, 25, 50, 100, 250, 500, 1000, 2000, 5000, 10000, 25000, 50000, 100000, 250000, 1000000];
  const pick = steps.find((s) => s >= target) ?? steps[steps.length - 1];
  return {
    px: pick / metersPerPixel,
    label: pick >= 1000 ? `${pick / 1000} km` : `${pick} m`,
  };
}

export default function StatusBar({
  lang,
  units,
  onUnitsChange,
  onAbout,
}: {
  lang: Lang;
  units: Units;
  onUnitsChange: (units: Units) => void;
  onAbout: () => void;
}) {
  const t = translator(lang);
  const { center, zoom, altitude, metersPerPixel, cursor, cursorElevation } = useTelemetry();
  const scale = niceScale(metersPerPixel, units);
  const point = cursor ?? center;

  return (
    <footer className="statusbar">
      <button className="status-credit" onClick={onAbout} title={t('dataSources')}>
        {ATTRIBUTION_SHORT}
      </button>

      <span className="status-spacer" />

      <div className="unit-switch" role="group" aria-label="Units">
        <button className={units === 'imperial' ? 'on' : ''} onClick={() => onUnitsChange('imperial')}>
          Imperial
        </button>
        <button className={units === 'metric' ? 'on' : ''} onClick={() => onUnitsChange('metric')}>
          Metric
        </button>
      </div>

      <div className="status-scale">
        <span className="status-scale-bar" style={{ width: Math.round(scale.px) }} />
        <span>{scale.label}</span>
      </div>

      <span className="status-field">
        {t('zoomLevel')} <b>{zoom.toFixed(1)}</b>
      </span>

      <span className="status-field">
        {t('camera')} <b>{formatElevation(altitude, units)}</b>
      </span>

      <span className="status-coords">
        {dms(point[1], 'N', 'S')} {dms(point[0], 'E', 'W')}
        {cursorElevation !== null && <b> {formatElevation(cursorElevation, units)}</b>}
      </span>
    </footer>
  );
}
