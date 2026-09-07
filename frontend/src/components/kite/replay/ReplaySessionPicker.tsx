import React, { useMemo, useRef, useState } from 'react';
import { ReplaySessionPolicy, useReplaySessionPolicy, useReplayState, useReplayStore } from '../../../hooks/useReplayStore';
import { getDynamicMarketPresets } from '../../../lib/replay/marketSessions';
import { ReplayPopover } from './primitives/ReplayPopover';
import { fmtSessionDate, fmtSmartDate, fmtTime } from './replayFormat';
import { useAvailableDates, verdictForDate } from './useAvailableDates';
import * as Icons from './ReplayIcons';

/**
 * Quick ranges, built from the backend's versioned session policy.
 *
 * These used to be hardcoded at a 15:30 close, which has been wrong for F&O
 * since the Closing Auction Session began on 2026-08-03 — derivatives run to
 * 15:40 and F&O cash stops at 15:15. A replay drives option legs, so "Regular"
 * follows the derivatives clock; "F&O cash" is offered separately because that
 * is where the CAS takes over.
 */
function hourRanges(policy: ReplaySessionPolicy | null) {
  const open = policy?.continuous_open ?? '09:15:00';
  const close = policy?.continuous_close ?? '15:40:00';
  const foCash = policy?.fo_cash_close ?? '15:15:00';
  const preopen = policy?.preopen_start ?? '09:00:00';
  const lastHourStart = shiftBack(close, 60);
  return [
    { id: 'regular', label: 'Regular', start: open, end: close },
    { id: 'full', label: 'With pre-open', start: preopen, end: close },
    { id: 'focash', label: 'F&O cash', start: open, end: foCash },
    { id: 'first', label: 'First hour', start: open, end: shiftForward(open, 60) },
    { id: 'last', label: 'Last hour', start: lastHourStart, end: close },
  ];
}

function shiftForward(time: string, mins: number): string {
  const [h, m] = time.split(':').map(Number);
  const t = h * 60 + m + mins;
  return `${String(Math.floor(t / 60)).padStart(2, '0')}:${String(t % 60).padStart(2, '0')}:00`;
}
function shiftBack(time: string, mins: number): string {
  return shiftForward(time, -mins);
}

/**
 * Session date, range and market hours.
 *
 * One disabled entry point while a replay runs, rather than the nineteen
 * individually-disabled controls the previous configuration tab carried.
 */
export function ReplaySessionPicker({ widthBucket }: { widthBucket: string }) {
  const draft = useReplayStore((s) => s.draft);
  const setDraft = useReplayStore((s) => s.setDraft);
  const state = useReplayState();
  const [open, setOpen] = useState(false);
  const policy = useReplaySessionPolicy();
  const HOUR_RANGES = useMemo(() => hourRanges(policy), [policy]);
  const anchor = useRef<HTMLButtonElement>(null);

  const { data: available } = useAvailableDates();
  const presets = useMemo(() => getDynamicMarketPresets(), []);
  const verdict = verdictForDate(draft.date, available);
  const rangeInvalid = draft.endDate < draft.date;
  const timesInvalid = draft.endTime <= draft.startTime;

  const locked = state !== 'idle';

  const label =
    widthBucket === 'sm'
      ? fmtSmartDate(draft.date)
      : widthBucket === 'lg' || widthBucket === 'md'
        ? fmtSmartDate(draft.date)
        : `${fmtSmartDate(draft.date)} · ${fmtTime(draft.startTime, 5)}–${fmtTime(draft.endTime, 5)}`;

  const applyPreset = (date: string) => setDraft({ date, endDate: date });

  return (
    <>
      <button
        type="button"
        ref={anchor}
        className="rd-btn"
        disabled={locked}
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
        title={locked ? 'Stop the replay to change the session' : 'Choose the replay session'}
        data-testid="replay-session-trigger"
      >
        <Icons.Calendar size={13} />
        {label}
        <Icons.ChevronDown size={11} />
      </button>

      <ReplayPopover
        open={open}
        onOpenChange={setOpen}
        label="Choose replay session"
        anchorRef={anchor}
        width={320}
      >
        <div className="rd-pop-section">
          <div className="rd-pop-head">Quick</div>
          {presets.map((p) => (
            <label className="rd-opt" key={p.id}>
              <input
                type="radio"
                name="rd-session-preset"
                checked={draft.date === p.date}
                onChange={() => applyPreset(p.date)}
              />
              <span>{p.label}</span>
                <span className="rd-opt-hint">{fmtSmartDate(p.date)}</span>
            </label>
          ))}
        </div>

        <div className="rd-pop-section">
          <div className="rd-pop-head">Session date</div>
          <div className="rd-grid-2">
            <div className="rd-field">
              <label className="rd-field-label" htmlFor="rd-date-start">Start</label>
              <input
                id="rd-date-start"
                type="date"
                className="rd-input"
                value={draft.date}
                min={available?.earliest ?? undefined}
                max={available?.latest ?? undefined}
                onChange={(e) => setDraft({ date: e.target.value, endDate: e.target.value })}
              />
            </div>
            <div className="rd-field">
              <label className="rd-field-label" htmlFor="rd-date-end">End (range)</label>
              <input
                id="rd-date-end"
                type="date"
                className="rd-input"
                value={draft.endDate}
                min={draft.date}
                max={available?.latest ?? undefined}
                aria-invalid={rangeInvalid}
                aria-describedby={rangeInvalid ? 'rd-range-err' : undefined}
                onChange={(e) => setDraft({ endDate: e.target.value })}
              />
            </div>
          </div>
          {rangeInvalid && (
            <div className="rd-field-error" id="rd-range-err">End date is before the start date.</div>
          )}
          {verdict.level === 'warn' && <div className="rd-field-warn">{verdict.message}</div>}
          {verdict.level === 'error' && <div className="rd-field-error">{verdict.message}</div>}
        </div>

        <div className="rd-pop-section">
          <div className="rd-pop-head">Market hours</div>
          <div className="rd-grid-2">
            <div className="rd-field">
              <label className="rd-field-label" htmlFor="rd-time-start">From</label>
              <input
                id="rd-time-start"
                type="time"
                step={1}
                className="rd-input"
                value={draft.startTime}
                onChange={(e) => setDraft({ startTime: e.target.value })}
              />
            </div>
            <div className="rd-field">
              <label className="rd-field-label" htmlFor="rd-time-end">To</label>
              <input
                id="rd-time-end"
                type="time"
                step={1}
                className="rd-input"
                value={draft.endTime}
                aria-invalid={timesInvalid}
                onChange={(e) => setDraft({ endTime: e.target.value })}
              />
            </div>
          </div>
          {timesInvalid && <div className="rd-field-error">End time must be after the start time.</div>}
          <div className="rd-chip-row" style={{ marginTop: 8 }}>
            {HOUR_RANGES.map((r) => (
              <button
                key={r.id}
                type="button"
                className="rd-btn rd-btn-sm"
                data-variant={draft.startTime === r.start && draft.endTime === r.end ? 'primary' : undefined}
                onClick={() => setDraft({ startTime: r.start, endTime: r.end })}
              >
                {r.label}
              </button>
            ))}
          </div>
        </div>
      </ReplayPopover>
    </>
  );
}
