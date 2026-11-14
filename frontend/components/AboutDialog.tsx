'use client';

import { useEffect } from 'react';
import { ATTRIBUTION } from '@/lib/config';
import { translator, type Lang } from '@/lib/i18n';
import { X } from './Icons';

export default function AboutDialog({ lang, onClose }: { lang: Lang; onClose: () => void }) {
  const t = translator(lang);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose();
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  return (
    <div className="scrim" onClick={onClose}>
      <div
        className="dialog"
        role="dialog"
        aria-modal="true"
        aria-label={t('about')}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="dialog-head">
          <span className="dialog-title">{t('about')}</span>
          <button className="icon-btn" onClick={onClose} aria-label={t('closeDialog')}>
            <X size={15} />
          </button>
        </div>

        <div className="dialog-body">
          <p className="dialog-warn">{t('notOfficialWarning')}</p>
          <ul className="credit-list">
            {ATTRIBUTION.map((line) => (
              <li key={line}>{line}</li>
            ))}
            <li>Place search: OpenStreetMap Nominatim</li>
          </ul>
          <p className="hint">
            FABDEM — CC BY-NC-SA 4.0, non-commercial use only.
          </p>
        </div>
      </div>
    </div>
  );
}
