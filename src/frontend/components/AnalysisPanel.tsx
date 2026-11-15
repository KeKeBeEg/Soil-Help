'use client';

import { useEffect, useState } from 'react';
import { translator, type Lang } from '@/lib/i18n';
import type { MapItem } from '@/lib/items';
import {
  ALERT_CLASSES,
  ALERT_LEVELS,
  LAYER_DEFS,
  PRACTICE_CLASSES,
  RESULT_LAYERS,
  layerSource,
  loadRaster,
  loadSummary,
  loadYearStats,
  lossInReportUnits,
  mainPractice,
  periodYears,
  rasterOverlay,
  type FieldSummary,
  type Overlay,
  type Period,
  type ResultLayerId,
  type YearStats,
} from '@/lib/results';
import { Field, Slider } from './ui';

interface Props {
  lang: Lang;
  /** The selected field, or null. */
  item: MapItem | null;
  onAnalyse: (start: string, end: string) => void;
  onOverlay: (overlay: Overlay | null) => void;
}

const LAYER_LABEL = {
  soilLoss: 'soilLossYearly',
  soilLossDates: 'soilLossDates',
  landslide: 'landslideAlert',
  practice: 'practiceLayer',
} as const;

const DEFAULT_RANGE: [string, string] = ['2025-01-01', '2025-12-31'];
const EARLIEST = '2001-01-01';

/** Rain data needs about two days to arrive. */
function latestDate(): string {
  return new Date(Date.now() - 2 * 86400000).toISOString().slice(0, 10);
}

function elapsed(ms: number): string {
  const s = Math.max(0, Math.round(ms / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

export default function AnalysisPanel({ lang, item, onAnalyse, onOverlay }: Props) {
  const t = translator(lang);
  const run = item?.analysis;
  const files = run?.state === 'done' ? run.files : undefined;

  const [start, setStart] = useState(DEFAULT_RANGE[0]);
  const [end, setEnd] = useState(DEFAULT_RANGE[1]);
  const [layer, setLayer] = useState<ResultLayerId | null>('soilLoss');
  const [period, setPeriod] = useState<Period>('dates');
  const [summary, setSummary] = useState<FieldSummary | null>(null);
  const [years, setYears] = useState<Map<number, YearStats> | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [layerFailed, setLayerFailed] = useState(false);
  const [now, setNow] = useState(Date.now());

  const fmt = (v: number | null | undefined, digits = 1) =>
    v === null || v === undefined || !Number.isFinite(v)
      ? '–'
      : v.toLocaleString(lang === 'tr' ? 'tr-TR' : 'en-US', { maximumFractionDigits: digits });
  const unit = (u: string) =>
    lang === 'tr' ? u.replace('tons/acre', 'ton/akre').replace('/yr', '/yıl').replace('acres', 'akre') : u;

  // A different field: its own dates, back to the analysed period.
  useEffect(() => {
    setStart(item?.analysis?.start ?? DEFAULT_RANGE[0]);
    setEnd(item?.analysis?.end ?? DEFAULT_RANGE[1]);
    setPeriod('dates');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [item?.id]);

  // The field's numbers, once its results exist.
  useEffect(() => {
    setSummary(null);
    setYears(null);
    setLoadFailed(false);
    if (!files) return;
    let cancelled = false;
    Promise.all([loadSummary(files.view), loadYearStats(files.analysis)])
      .then(([s, y]) => {
        if (cancelled) return;
        setSummary(s);
        setYears(y);
      })
      .catch(() => !cancelled && setLoadFailed(true));
    return () => {
      cancelled = true;
    };
  }, [files?.view, files?.analysis]);

  // The coloured layer on the map.
  useEffect(() => {
    if (!files || !layer || !item?.visible) {
      onOverlay(null);
      return;
    }
    let cancelled = false;
    setLayerFailed(false);
    const { url, band } = layerSource(layer, period, files);
    loadRaster(url)
      .then((r) => {
        if (cancelled) return;
        const index = typeof band === 'number' ? band : r.bandNames.indexOf(band);
        onOverlay(index >= 0 ? rasterOverlay(r, index, layer) : null);
      })
      .catch(() => {
        if (cancelled) return;
        onOverlay(null);
        setLayerFailed(true);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [files?.view, files?.analysis, layer, period, item?.visible]);

  // The layer stays on the map when the sidebar is closed (the panel unmounts
  // then); the page drops it when the field is removed, hidden or unselected.

  // A ticking clock while a run is under way.
  const busy = run?.state === 'queued' || run?.state === 'running';
  useEffect(() => {
    if (!busy) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [busy]);

  if (!item) return <p className="hint">{t('pickField')}</p>;

  const max = latestDate();
  const validDates = start >= EARLIEST && end <= max && start <= end;
  const sameAsRun = run && run.start === start && run.end === end && run.state !== 'failed' && run.state !== 'missing';
  const [y0, y1] = summary ? periodYears(summary) : [2016, 2025];
  const year = typeof period === 'number' ? period : y1;
  const ys = typeof period === 'number' ? years?.get(period) : undefined;
  const def = layer ? LAYER_DEFS[layer] : null;

  return (
    <div className="analysis">
      <div className="item-title analysis-field">{item.name}</div>

      <div className="date-row">
        <label>
          <span className="field-label">{t('from')}</span>
          <input type="date" className="date-input" value={start} min={EARLIEST} max={max} onChange={(e) => setStart(e.target.value)} />
        </label>
        <label>
          <span className="field-label">{t('to')}</span>
          <input type="date" className="date-input" value={end} min={EARLIEST} max={max} onChange={(e) => setEnd(e.target.value)} />
        </label>
      </div>
      {!busy && !sameAsRun && (
        <button className="btn primary analysis-go" disabled={!validDates} onClick={() => onAnalyse(start, end)}>
          {run ? (run.state === 'failed' || run.state === 'missing' ? t('retry') : t('reanalyse')) : t('analyse')}
        </button>
      )}

      {!run && <p className="hint">{t('analyseHint')}</p>}

      {busy && (
        <div className="status-line">
          <span className="spinner" aria-hidden="true" />
          <span>
            {run.state === 'queued' ? t('stateQueued') : t('stateRunning')}
            {lang === 'en' && run.state === 'running' && run.stage ? `: ${run.stage}` : ''} · {elapsed(now - run.startedAt)}
          </span>
        </div>
      )}
      {(run?.state === 'failed' || run?.state === 'missing') && (
        <p className="status-error">
          {run.state === 'failed' ? t('stateFailed') : t('stateMissing')}
          {run.message ? ` (${run.message})` : ''}
          {run.runUrl && (
            <>
              {' '}
              <a href={run.runUrl} target="_blank" rel="noreferrer">
                {t('showRun')}
              </a>
            </>
          )}
        </p>
      )}

      {files && (
        <>
          <Field label={t('mapLayer')}>
            <select className="select" value={layer ?? ''} onChange={(e) => setLayer((e.target.value || null) as ResultLayerId | null)}>
              {RESULT_LAYERS.map((id) => (
                <option key={id} value={id}>
                  {t(LAYER_LABEL[id])}
                </option>
              ))}
              <option value="">{t('layerOff')}</option>
            </select>
          </Field>
          {def && (
            <ul className="legend">
              {def.edges && <li className="legend-units">{def.units[lang]}</li>}
              {def.classes.map((c) => (
                <li key={c.color}>
                  <span className="swatch" style={{ background: c.color }} />
                  {c.label[lang]}
                </li>
              ))}
            </ul>
          )}

          <Field label={t('period')} value={period === 'dates' ? `${run!.start} – ${run!.end}` : String(period)}>
            <div className="seg">
              <button className={period === 'dates' ? 'on' : ''} onClick={() => setPeriod('dates')}>
                {t('chosenDates')}
              </button>
              <button className={period !== 'dates' ? 'on' : ''} onClick={() => setPeriod(year)}>
                {t('byYear')}
              </button>
            </div>
          </Field>
          {period !== 'dates' && (
            <Slider min={y0} max={y1} step={1} value={year} onChange={(v) => setPeriod(v)} ariaLabel={t('year')} />
          )}

          {(loadFailed || layerFailed) && <p className="status-error">{t('resultsFailed')}</p>}
          {!summary && !loadFailed && <p className="hint">{t('loadingResults')}</p>}

          {summary && (
            <div className="report">
              <h4>
                {t('inPeriod')} · {period === 'dates' ? `${summary.t_range[0]} – ${summary.t_range[1]}` : period}
              </h4>
              <dl className="kv">
                <dt>{t('rainTotal')}</dt>
                <dd>{fmt(ys ? ys.rainMm : summary.window.rain_total_mm, 0)} mm</dd>
                <dt>{t('erosiveStorms')}</dt>
                <dd>{fmt(ys ? ys.storms : summary.window.erosive_storms, 0)}</dd>
                <dt>{t('periodSoilLoss')}</dt>
                <dd>
                  {fmt(ys ? lossInReportUnits(ys.soilLossTHa, summary) : summary.window.soil_loss, 2)}{' '}
                  {unit(summary.units.soil_loss_window)}
                </dd>
                <dt>{t('worstAlert')}</dt>
                <dd>
                  {ALERT_CLASSES[ys ? ys.worstAlert : ALERT_LEVELS.indexOf(summary.window.worst_alert)]?.label[lang] ?? '–'}
                </dd>
              </dl>

              <h4>
                {t('longTerm')} · {summary.analysis_period}
              </h4>
              <dl className="kv">
                <dt>{t('avgSoilLoss')}</dt>
                <dd>
                  {fmt(summary.long_term.soil_loss_mean)} {unit(summary.units.soil_loss)}
                </dd>
                <dt>{t('tolerance')}</dt>
                <dd>
                  {fmt(summary.long_term.tolerance)} {unit(summary.units.soil_loss)}
                </dd>
                <dt>{t('overTolerance')}</dt>
                <dd>{lang === 'tr' ? `%${fmt(summary.long_term.share_above_tolerance_pct, 0)}` : `${fmt(summary.long_term.share_above_tolerance_pct, 0)}%`}</dd>
                <dt>{t('mainPractice')}</dt>
                <dd>{PRACTICE_CLASSES[mainPractice(summary)].label[lang]}</dd>
                <dt>{t('dangerDays')}</dt>
                <dd>{fmt(summary.landslide.danger_days_per_year)}</dd>
              </dl>

              {summary.advice.length > 0 && (
                <>
                  <h4>{t('advice')}</h4>
                  <ul className="advice">
                    {summary.advice.map((a) => (
                      <li key={a}>{a}</li>
                    ))}
                  </ul>
                </>
              )}
              {summary.defaults_used.length > 0 && (
                <p className="hint">
                  {t('defaultsUsed')} {summary.defaults_used.join(', ')}
                </p>
              )}
              <p className="hint">{t('mapUnitsNote')}</p>
            </div>
          )}
        </>
      )}
    </div>
  );
}
