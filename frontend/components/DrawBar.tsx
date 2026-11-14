'use client';

import {
  formatArea,
  formatDistance,
  pathLength,
  polygonArea,
  type LngLat,
  type Units,
} from '@/lib/geo';
import { translator, type Lang } from '@/lib/i18n';
import { MIN_POINTS, type ItemKind } from '@/lib/items';
import { Undo, X } from './Icons';

interface Props {
  lang: Lang;
  units: Units;
  kind: ItemKind;
  points: LngLat[];
  onUndo: () => void;
  onClear: () => void;
  onDone: () => void;
  onCancel: () => void;
}

export default function DrawBar(props: Props) {
  const t = translator(props.lang);
  const n = props.points.length;
  const canFinish = n >= MIN_POINTS[props.kind];

  const closed = props.kind === 'field' && n >= 3;
  const metric = closed
    ? `${t('area')} ${formatArea(polygonArea(props.points), props.units)}`
    : n >= 2
      ? `${t('length')} ${formatDistance(pathLength(props.points), props.units)}`
      : null;

  const hint =
    props.kind === 'pin'
      ? t('pinHint')
      : n === 0
        ? t('firstPointHint')
        : null;

  return (
    <div className="draw-bar" role="toolbar" aria-label={t('tools')}>
      <span className="status">
        {hint ?? (
          <>
            <b>{n}</b> {t('points')} · {t('finishHint')}
          </>
        )}
      </span>

      {metric && (
        <>
          <span className="divider" />
          <span className="metric">{metric}</span>
        </>
      )}

      {props.kind !== 'pin' && (
        <>
          <span className="divider" />
          <button className="btn" onClick={props.onUndo} disabled={n === 0}>
            <Undo size={13} style={{ marginRight: 5, verticalAlign: -2 }} />
            {t('undo')}
          </button>
          <button className="btn" onClick={props.onClear} disabled={n === 0}>
            {t('clear')}
          </button>
          <button className="btn primary" onClick={props.onDone} disabled={!canFinish}>
            {t('done')}
          </button>
        </>
      )}

      <button className="icon-btn" onClick={props.onCancel} aria-label={t('cancel')}>
        <X size={15} />
      </button>
    </div>
  );
}
