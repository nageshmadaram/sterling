import React, { useMemo, useRef, useState } from 'react';
import {
  useReplaySessionPolicy,
  useReplayState,
  useReplayStore,
} from '../../../hooks/useReplayStore';
import { getDynamicMarketPresets } from '../../../lib/replay/marketSessions';
import { ReplayPopover } from './primitives/ReplayPopover';
import { ensureSeconds, fmtSmartDate, fmtTime } from './replayFormat';
import { MONEYNESS_LEGS, REPLAY_STRATEGIES, strategyLabel } from './replayStrategies';
import * as Icons from './ReplayIcons';

function shiftTime(time: string, mins: number): string {
  const [h, m] = time.split(':').map(Number);
  const t = h * 60 + m + mins;
  return `${String(Math.floor(t / 60)).padStart(2, '0')}:${String(t % 60).padStart(2, '0')}:00`;
}

const LOT_PRESETS = [1, 2, 5, 10, 25];

/* ── 1. Session / Date Dropdown (Source/Exit style) ─────────────────────── */

export function ReplaySessionDropdown() {
  const draft = useReplayStore((s) => s.draft);
  const setDraft = useReplayStore((s) => s.setDraft);
  const state = useReplayState();
  const [open, setOpen] = useState(false);
  const anchor = useRef<HTMLButtonElement>(null);
  const locked = state !== 'idle';
  const presets = useMemo(() => getDynamicMarketPresets(), []);

  const isRange = Boolean(draft.endDate && draft.endDate !== draft.date);
  const activePreset = !isRange ? presets.find((p) => p.date === draft.date) : undefined;
  const displayLabel = isRange
    ? `${fmtSmartDate(draft.date)} – ${fmtSmartDate(draft.endDate)}`
    : (activePreset?.label ?? fmtSmartDate(draft.date));

  return (
    <>
      <button
        ref={anchor}
        type="button"
        className="rd-inline-drop-btn"
        disabled={locked}
        data-open={open}
        aria-haspopup="listbox"
        aria-expanded={open}
        title={locked ? 'Replay is running' : 'Session date'}
        onClick={() => setOpen((o) => !o)}
        data-testid="replay-session-trigger"
      >
        <span className="rd-inline-drop-label">SESSION</span>
        <span className="rd-inline-drop-value">{displayLabel}</span>
        <Icons.ChevronDown size={10} className="rd-inline-drop-caret" />
      </button>

      <ReplayPopover
        open={open}
        onOpenChange={setOpen}
        label="Select session date"
        anchorRef={anchor}
        width={240}
        align="start"
      >
        <div className="rd-drop-menu" role="listbox" aria-label="Session date presets">
          <div className="rd-drop-header">Session date</div>
          {presets.map((p) => {
            const selected = !isRange && draft.date === p.date;
            return (
              <button
                key={p.id}
                type="button"
                role="option"
                aria-selected={selected}
                className="rd-drop-option"
                data-selected={selected}
                onClick={() => {
                  setDraft({ date: p.date, endDate: p.date });
                  setOpen(false);
                }}
              >
                <span className="rd-drop-check">{selected ? '✓' : ''}</span>
                <span className="rd-drop-option-text">
                  <span className="rd-drop-option-title">{p.label}</span>
                  <span className="rd-drop-option-hint">{fmtSmartDate(p.date)}</span>
                </span>
              </button>
            );
          })}
          <div className="rd-drop-sep" />
          <div className="rd-drop-custom-row" style={{ display: 'flex', flexDirection: 'column', gap: 6, padding: '8px 10px' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
              <span className="rd-drop-custom-label">From:</span>
              <input
                type="date"
                className="rd-drop-date-input"
                value={draft.date}
                onChange={(e) => {
                  if (e.target.value) {
                    const newDate = e.target.value;
                    const newEndDate = draft.endDate && draft.endDate < newDate ? newDate : (draft.endDate ?? newDate);
                    setDraft({ date: newDate, endDate: newEndDate });
                  }
                }}
              />
            </div>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
              <span className="rd-drop-custom-label">To:</span>
              <input
                type="date"
                className="rd-drop-date-input"
                value={draft.endDate ?? draft.date}
                min={draft.date}
                onChange={(e) => {
                  if (e.target.value) {
                    const to = e.target.value;
                    setDraft({ endDate: to < draft.date ? draft.date : to });
                  }
                }}
              />
            </div>
          </div>
        </div>
      </ReplayPopover>
    </>
  );
}

/* ── 2. Market Hours Dropdown (Source/Exit style) ───────────────────────── */

export function ReplayHoursDropdown() {
  const draft = useReplayStore((s) => s.draft);
  const setDraft = useReplayStore((s) => s.setDraft);
  const state = useReplayState();
  const [open, setOpen] = useState(false);
  const anchor = useRef<HTMLButtonElement>(null);
  const locked = state !== 'idle';
  const policy = useReplaySessionPolicy();

  const contOpen = policy?.continuous_open ?? '09:15:00';
  const contClose = policy?.continuous_close ?? '15:40:00';
  const preopenStart = policy?.preopen_start ?? '09:00:00';

  const HOUR_OPTIONS = useMemo(() => [
    { id: 'regular', label: 'Regular', start: contOpen, end: contClose, hint: `${fmtTime(contOpen, 5)} – ${fmtTime(contClose, 5)}` },
    { id: 'preopen', label: 'Pre-open', start: preopenStart, end: contClose, hint: `${fmtTime(preopenStart, 5)} – ${fmtTime(contClose, 5)}` },
    { id: 'first', label: '1st hr', start: contOpen, end: shiftTime(contOpen, 60), hint: `${fmtTime(contOpen, 5)} – ${fmtTime(shiftTime(contOpen, 60), 5)}` },
    { id: 'last', label: 'Last hr', start: shiftTime(contClose, -60), end: contClose, hint: `${fmtTime(shiftTime(contClose, -60), 5)} – ${fmtTime(contClose, 5)}` },
  ], [contOpen, contClose, preopenStart]);

  const invalidHours = draft.startTime >= draft.endTime;
  const activeOption = HOUR_OPTIONS.find(
    (o) => o.start === draft.startTime && o.end === draft.endTime,
  );
  const displayLabel = activeOption?.label ?? `${fmtTime(draft.startTime, 5)}–${fmtTime(draft.endTime, 5)}`;

  return (
    <>
      <button
        ref={anchor}
        type="button"
        className="rd-inline-drop-btn"
        disabled={locked}
        data-open={open}
        aria-haspopup="listbox"
        aria-expanded={open}
        title={locked ? 'Replay is running' : invalidHours ? 'Start time must precede end time' : 'Market hours'}
        onClick={() => setOpen((o) => !o)}
        data-testid="replay-hours-trigger"
      >
        <span className="rd-inline-drop-label">HOURS</span>
        <span className="rd-inline-drop-value" style={invalidHours ? { color: 'var(--k-red-brick)' } : undefined}>
          {displayLabel}
        </span>
        <Icons.ChevronDown size={10} className="rd-inline-drop-caret" />
      </button>

      <ReplayPopover
        open={open}
        onOpenChange={setOpen}
        label="Select market hours"
        anchorRef={anchor}
        width={220}
        align="start"
      >
        <div className="rd-drop-menu" role="listbox" aria-label="Market hours presets">
          <div className="rd-drop-header">Market hours</div>
          {HOUR_OPTIONS.map((opt) => {
            const selected = draft.startTime === opt.start && draft.endTime === opt.end;
            return (
              <button
                key={opt.id}
                type="button"
                role="option"
                aria-selected={selected}
                className="rd-drop-option"
                data-selected={selected}
                onClick={() => {
                  setDraft({ startTime: opt.start, endTime: opt.end });
                  setOpen(false);
                }}
              >
                <span className="rd-drop-check">{selected ? '✓' : ''}</span>
                <span className="rd-drop-option-text">
                  <span className="rd-drop-option-title">{opt.label}</span>
                  <span className="rd-drop-option-hint">{opt.hint}</span>
                </span>
              </button>
            );
          })}
          <div className="rd-drop-sep" />
          <div className="rd-drop-time-row">
            <span className="rd-drop-custom-label">Custom:</span>
            <input
              type="time"
              step="1"
              className="rd-drop-time-input"
              value={draft.startTime}
              style={invalidHours ? { borderColor: 'var(--k-red-brick)' } : undefined}
              onChange={(e) => setDraft({ startTime: ensureSeconds(e.target.value, draft.startTime) })}
              title="Start time"
            />
            <span>–</span>
            <input
              type="time"
              step="1"
              className="rd-drop-time-input"
              value={draft.endTime}
              style={invalidHours ? { borderColor: 'var(--k-red-brick)' } : undefined}
              onChange={(e) => setDraft({ endTime: ensureSeconds(e.target.value, draft.endTime) })}
              title="End time"
            />
          </div>
          {invalidHours && (
            <div style={{ fontSize: 9.5, color: 'var(--k-red-brick)', padding: '2px 8px 4px' }}>
              Start time must precede end time
            </div>
          )}
        </div>
      </ReplayPopover>
    </>
  );
}

/* ── 3. Strategy Dropdown (Source/Exit style) ───────────────────────────── */

export function ReplayStrategyDropdown() {
  const draft = useReplayStore((s) => s.draft);
  const toggleStrategy = useReplayStore((s) => s.toggleStrategy);
  const setDraft = useReplayStore((s) => s.setDraft);
  const state = useReplayState();
  const [open, setOpen] = useState(false);
  const anchor = useRef<HTMLButtonElement>(null);
  const locked = state !== 'idle';

  const allStrategies = draft.strategies.includes('all');
  const count = allStrategies ? REPLAY_STRATEGIES.length : draft.strategies.length;
  const displayLabel = allStrategies
    ? 'All'
    : count === 1
      ? strategyLabel(draft.strategies[0])
      : `${count} active`;

  return (
    <>
      <button
        ref={anchor}
        type="button"
        className="rd-inline-drop-btn"
        disabled={locked}
        data-open={open}
        aria-haspopup="dialog"
        aria-expanded={open}
        title={locked ? 'Replay is running' : 'Configure strategy emitters'}
        onClick={() => setOpen((o) => !o)}
        data-testid="replay-strategy-trigger"
      >
        <span className="rd-inline-drop-label">STRATEGY</span>
        <span className="rd-inline-drop-value">{displayLabel}</span>
        <Icons.ChevronDown size={10} className="rd-inline-drop-caret" />
      </button>

      <ReplayPopover
        open={open}
        onOpenChange={setOpen}
        label="Select strategies"
        anchorRef={anchor}
        width={240}
        align="start"
      >
        <div className="rd-drop-menu" role="dialog" aria-label="Strategy selection">
          <div className="rd-drop-header">
            <span>Strategies</span>
            <button
              type="button"
              className="rd-btn rd-btn-sm"
              data-variant="ghost"
              onClick={() => setDraft({ strategies: ['all'] })}
            >
              Select all
            </button>
          </div>
          {REPLAY_STRATEGIES.map((s) => {
            const active = allStrategies || draft.strategies.includes(s.id);
            return (
              <label className="rd-drop-check-row" key={s.id}>
                <input
                  type="checkbox"
                  checked={active}
                  onChange={() => toggleStrategy(s.id)}
                />
                <span style={{ color: s.tone, display: 'inline-flex' }}>
                  <span className="rd-dot-tone" />
                </span>
                <span className="rd-drop-check-label">{s.label}</span>
              </label>
            );
          })}
        </div>
      </ReplayPopover>
    </>
  );
}

/* ── 4. Position Sizing Dropdown (Source/Exit style) ────────────────────── */

export function ReplaySizingDropdown() {
  const draft = useReplayStore((s) => s.draft);
  const setDraft = useReplayStore((s) => s.setDraft);
  const state = useReplayState();
  const [open, setOpen] = useState(false);
  const anchor = useRef<HTMLButtonElement>(null);
  const locked = state !== 'idle';

  const allLegs = draft.moneyness.includes('ALL');
  const legsLabel = allLegs ? 'ALL' : draft.moneyness.join(',');
  const displayLabel = `${legsLabel} · ${draft.lots}L`;

  return (
    <>
      <button
        ref={anchor}
        type="button"
        className="rd-inline-drop-btn"
        disabled={locked}
        data-open={open}
        aria-haspopup="dialog"
        aria-expanded={open}
        title={locked ? 'Replay is running' : 'Strike moneyness and lot sizing'}
        onClick={() => setOpen((o) => !o)}
        data-testid="replay-sizing-trigger"
      >
        <span className="rd-inline-drop-label">SIZING</span>
        <span className="rd-inline-drop-value">{displayLabel}</span>
        <Icons.ChevronDown size={10} className="rd-inline-drop-caret" />
      </button>

      <ReplayPopover
        open={open}
        onOpenChange={setOpen}
        label="Position sizing"
        anchorRef={anchor}
        width={260}
        align="start"
      >
        <div className="rd-drop-menu" role="dialog" aria-label="Position sizing">
          <div className="rd-drop-header">
            <span>Strike Selection</span>
            <button
              type="button"
              className="rd-btn rd-btn-sm"
              data-variant="ghost"
              onClick={() => setDraft({ moneyness: ['ALL'] })}
            >
              All
            </button>
          </div>
          <div className="rd-drop-pills">
            {MONEYNESS_LEGS.map((leg) => {
              const active = allLegs || draft.moneyness.includes(leg.id);
              return (
                <button
                  key={leg.id}
                  type="button"
                  className="rd-btn rd-btn-sm"
                  title={leg.hint}
                  data-variant={active ? 'primary' : undefined}
                  onClick={() => {
                    if (allLegs) {
                      setDraft({ moneyness: [leg.id] });
                    } else {
                      const cur = draft.moneyness;
                      const next = cur.includes(leg.id) ? cur.filter((x) => x !== leg.id) : [...cur, leg.id];
                      setDraft({ moneyness: next.length === 0 || next.length === MONEYNESS_LEGS.length ? ['ALL'] : next });
                    }
                  }}
                >
                  {leg.label}
                </button>
              );
            })}
          </div>

          <div className="rd-drop-sep" />

          <div className="rd-drop-header">Order Lots</div>
          <div className="rd-drop-pills">
            {LOT_PRESETS.map((l) => (
              <button
                key={l}
                type="button"
                className="rd-btn rd-btn-sm"
                data-variant={draft.lots === l ? 'primary' : undefined}
                onClick={() => setDraft({ lots: l })}
              >
                {l}L
              </button>
            ))}
            <input
              type="number"
              className="rd-drop-lot-input"
              min={1}
              max={500}
              value={draft.lots}
              onChange={(e) => {
                const v = e.target.value;
                if (v === '') {
                  setDraft({ lots: 1 });
                } else {
                  const val = parseInt(v, 10);
                  if (!isNaN(val)) setDraft({ lots: Math.max(1, Math.min(500, val)) });
                }
              }}
              title="Custom lots"
            />
          </div>
        </div>
      </ReplayPopover>
    </>
  );
}
