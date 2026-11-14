'use client';

import { useId, type ReactNode } from 'react';
import { Check, ChevronRight } from './Icons';

export function Section({
  title,
  open,
  onToggle,
  children,
}: {
  title: string;
  open: boolean;
  onToggle: () => void;
  children: ReactNode;
}) {
  return (
    <div className="section">
      <button className="section-head" aria-expanded={open} onClick={onToggle}>
        <span>{title}</span>
        <ChevronRight className="chev" size={14} />
      </button>
      <div className={`section-content${open ? ' open' : ''}`}>
        <div>
          <div className="section-inner">{children}</div>
        </div>
      </div>
    </div>
  );
}

export function Field({
  label,
  value,
  children,
}: {
  label: string;
  value?: string;
  children: ReactNode;
}) {
  return (
    <div>
      <div className="field-label">
        <span>{label}</span>
        {value !== undefined && <span className="value">{value}</span>}
      </div>
      {children}
    </div>
  );
}

export function Slider({
  min,
  max,
  step,
  value,
  onChange,
  ariaLabel,
}: {
  min: number;
  max: number;
  step: number;
  value: number;
  onChange: (v: number) => void;
  ariaLabel: string;
}) {
  const pct = ((value - min) / (max - min)) * 100;
  return (
    <input
      type="range"
      className="slider"
      min={min}
      max={max}
      step={step}
      value={value}
      aria-label={ariaLabel}
      style={{ ['--pct' as string]: `${pct}%` }}
      onChange={(e) => onChange(parseFloat(e.target.value))}
    />
  );
}

export function Checkbox({
  label,
  checked,
  onChange,
  disabled,
  tag,
}: {
  label: string;
  checked: boolean;
  onChange?: (v: boolean) => void;
  disabled?: boolean;
  tag?: string;
}) {
  const id = useId();
  return (
    <label className={`check${disabled ? ' disabled' : ''}`} htmlFor={id}>
      <input
        id={id}
        type="checkbox"
        checked={checked}
        disabled={disabled}
        onChange={(e) => onChange?.(e.target.checked)}
      />
      <span className="box">
        <Check size={11} />
      </span>
      <span>{label}</span>
      {tag && <span className="tag">{tag}</span>}
    </label>
  );
}
