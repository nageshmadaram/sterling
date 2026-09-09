import React, { useRef, useState } from 'react';
import { useReplayState, useReplayStore } from '../../../hooks/useReplayStore';
import { useReplayTransport } from '../../../hooks/useReplayTransport';
import { ReplayPopover } from './primitives/ReplayPopover';
import { MAX_SPEED, REPLAY_SPEEDS, speedLabel } from './replaySpeeds';
import * as Icons from './ReplayIcons';

/** Click and keyboard agree on step size: plain 1, Shift 5, Alt 30. */
export function stepSizeFor(e: { shiftKey: boolean; altKey: boolean }): number {
  if (e.altKey) return 30;
  if (e.shiftKey) return 5;
  return 1;
}

/**
 * Single speed dropdown element (1×, 5×, 10×, 25×, 50×, 100×, MAX).
 * Matches the inline dropdown design pattern used across the bar.
 */
export function ReplaySpeedDropdown() {
  const speed = useReplayStore((s) => s.status.config?.speed ?? s.draft.speed);
  const transport = useReplayTransport();
  const [open, setOpen] = useState(false);
  const anchor = useRef<HTMLButtonElement>(null);

  return (
    <>
      <button
        ref={anchor}
        type="button"
        className="rd-inline-drop-btn rd-speed-drop-btn"
        data-open={open}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label="Replay speed"
        title="Replay speed (+ / − to step)"
        onClick={() => setOpen((o) => !o)}
        data-testid="replay-speed-trigger"
      >
        <span className="rd-inline-drop-label">SPEED</span>
        <span className="rd-inline-drop-value">{speedLabel(speed)}</span>
        <Icons.ChevronDown size={10} className="rd-inline-drop-caret" />
      </button>

      <ReplayPopover
        open={open}
        onOpenChange={setOpen}
        label="Select replay speed"
        anchorRef={anchor}
        width={130}
        align="start"
      >
        <div className="rd-drop-menu" role="listbox" aria-label="Replay speed options">
          <div className="rd-drop-header">Playback speed</div>
          {REPLAY_SPEEDS.map((s) => {
            const selected = speed === s;
            return (
              <button
                key={s}
                type="button"
                role="option"
                aria-selected={selected}
                aria-pressed={selected}
                className="rd-drop-option"
                data-selected={selected}
                onClick={() => {
                  void transport.setSpeed(s);
                  setOpen(false);
                }}
              >
                <span className="rd-drop-check">{selected ? '✓' : ''}</span>
                <span className="rd-drop-option-title">{speedLabel(s)}</span>
              </button>
            );
          })}
        </div>
      </ReplayPopover>
    </>
  );
}

/**
 * Transport cluster and speed control.
 *
 * The primary play/pause button is the only filled control in the rail, which
 * is what makes it findable without reading. Speeds are integrated into a
 * single dropdown element matching the rest of the control bar.
 */
export function ReplayTransport() {
  const state = useReplayState();
  const transport = useReplayTransport();

  const canSeek = state === 'running' || state === 'paused';
  const primary =
    state === 'running'
      ? { kind: 'pause' as const, icon: <Icons.Pause size={16} />, label: 'Pause replay (Space)' }
      : state === 'paused'
        ? { kind: 'play' as const, icon: <Icons.Play size={16} />, label: 'Resume replay (Space)' }
        : state === 'error'
          ? { kind: 'retry' as const, icon: <Icons.Play size={16} />, label: 'Retry replay (Space)' }
          : state === 'loading'
            ? { kind: 'play' as const, icon: <Icons.Spinner size={16} />, label: 'Loading replay — click to cancel' }
            : { kind: 'play' as const, icon: <Icons.Play size={16} />, label: 'Start replay (Space)' };

  return (
    <div className="rd-transport" data-testid="replay-transport">
      <div className="rd-transport-seek" data-idle={!canSeek}>
        <button
          type="button"
          className="rd-tbtn"
          disabled={!canSeek}
          onClick={() => void transport.jumpStart()}
          aria-label="Jump to session start (Home)"
          title="Jump to session start (Home)"
        >
          <Icons.SkipStart size={15} />
        </button>
        <button
          type="button"
          className="rd-tbtn"
          disabled={!canSeek}
          onClick={(e) => void transport.stepBars(-stepSizeFor(e))}
          aria-label="Step back (Left arrow; Shift 5 bars, Alt 30)"
          title="Step back — 1 bar, Shift 5, Alt 30"
        >
          <Icons.StepBack size={15} />
        </button>
      </div>

      <button
        type="button"
        className="rd-tbtn rd-tbtn-primary"
        data-kind={primary.kind}
        disabled={state === 'loading'}
        onClick={() => void transport.toggle()}
        aria-label={primary.label}
        title={primary.label}
        data-testid="replay-primary"
      >
        {primary.icon}
      </button>

      <div className="rd-transport-seek" data-idle={!canSeek}>
        <button
          type="button"
          className="rd-tbtn"
          disabled={!canSeek}
          onClick={(e) => void transport.stepBars(stepSizeFor(e))}
          aria-label="Step forward (Right arrow; Shift 5 bars, Alt 30)"
          title="Step forward — 1 bar, Shift 5, Alt 30"
        >
          <Icons.StepFwd size={15} />
        </button>
        <button
          type="button"
          className="rd-tbtn"
          disabled={!canSeek}
          onClick={() => void transport.jumpEnd()}
          aria-label="Jump to session end (End)"
          title="Jump to session end (End)"
        >
          <Icons.SkipEnd size={15} />
        </button>
      </div>

      <span className="rd-rail-divider" aria-hidden="true" />

      <button
        type="button"
        className="rd-tbtn"
        data-stop="true"
        disabled={state === 'idle'}
        onClick={() => void transport.stop()}
        aria-label="Stop replay"
        title="Stop replay"
      >
        <Icons.Stop size={14} />
      </button>

      <span className="rd-rail-divider" aria-hidden="true" />

      <ReplaySpeedDropdown />
    </div>
  );
}
