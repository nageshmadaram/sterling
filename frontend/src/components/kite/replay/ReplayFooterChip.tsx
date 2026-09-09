import React from 'react';
import { useReplayState, useReplayStore } from '../../../hooks/useReplayStore';
import { fmtTime } from './replayFormat';
import * as Icons from './ReplayIcons';
import './replay.css';

/**
 * The single footer surface for the replay.
 *
 * There used to be three: a `REPLAY DOCK (10:47:05)` toggle, a pulsing
 * `REPLAYING` badge beside it, and a `▶ SIMULATION (10:47:05)` chip inside the
 * engine status cluster — two of which rendered the same clock, 40px apart, in
 * different colours.
 *
 * Clicking only ever toggles the dock. A footer chip that can start a
 * simulation is a footer chip that will start one by accident.
 */
export function ReplayFooterChip() {
  const open = useReplayStore((s) => s.open);
  const setOpen = useReplayStore((s) => s.setOpen);
  const state = useReplayState();
  const clock = useReplayStore((s) => s.status.current_time_iso);
  const date = useReplayStore((s) => s.status.current_date || s.status.config?.date);
  const speed = useReplayStore((s) => s.status.config?.speed);
  const signals = useReplayStore((s) => s.status.stats.signals_fired);
  const errorMsg = useReplayStore((s) => s.error?.message);

  const active = state === 'running' || state === 'paused';

  const hint = errorMsg
    ? `Replay failed — ${errorMsg}`
    : active
      ? `Replaying ${date ?? ''} · ${fmtTime(clock)} IST · ${speed ?? ''}× · ${signals} signals`
      : open
        ? 'Minimise the replay dock'
        : 'Open the replay dock';

  return (
    <button
      type="button"
      className="kw-dock-chip rd-footer-chip"
      data-active={open || active}
      data-state={state}
      aria-pressed={open}
      aria-label={open ? 'Minimise replay dock' : 'Open replay dock'}
      title={hint}
      data-testid="replay-footer-chip"
      onClick={() => {
        const next = !open;
        setOpen(next);
        if (next) {
          window.dispatchEvent(new CustomEvent('kite-restore-slot', { detail: 'center' }));
        }
      }}
    >
      <span style={{ display: 'inline-flex' }}>
        {state === 'loading' ? <Icons.Spinner size={12} />
          : active ? <Icons.Signal size={12} />
          : state === 'error' ? <Icons.Alert size={12} />
          : <Icons.Play size={12} />}
      </span>
      REPLAY
      {state === 'loading' && <span style={{ color: 'var(--k-dim)' }}>starting…</span>}
      {state === 'error' && <span style={{ color: 'var(--k-red)' }}>failed</span>}
      {active && <span className="rd-footer-clock">{fmtTime(clock)}</span>}
      {active && <span className="rd-footer-dot" data-pulse aria-hidden="true" />}
    </button>
  );
}
