import { useEffect } from 'react';
import {
  selectFilteredEvents,
  selectFilteredTrades,
  useReplayStore,
} from '../../../hooks/useReplayStore';
import type { ReplayTransport } from '../../../hooks/useReplayTransport';
import { SIGNAL_CSV_COLUMNS, replayHasFriction, tradeCsvColumns } from './replayColumns';
import { exportCsv, replayCsvName } from './replayCsv';
import { REPLAY_SPEEDS, stepSpeed } from './replaySpeeds';

/**
 * True when the event should be left alone because the user is typing.
 *
 * The handler this replaces guarded only `input`, `textarea` and `select`, so
 * Space and the arrow keys were captured inside `contenteditable` regions and
 * custom widgets elsewhere in the app.
 */
function isTextEntry(target: EventTarget | null): boolean {
  const el = target as HTMLElement | null;
  if (!el || !el.closest) return false;
  if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement || el instanceof HTMLSelectElement) {
    return true;
  }
  return !!el.closest('[contenteditable="true"], [role="textbox"], [data-swallow-keys]');
}

/**
 * Replay keyboard shortcuts, scoped to the dock.
 *
 * The previous handler was bound to `window` whenever the dock was merely OPEN,
 * so Space, the arrows and Home/End were stolen from every other pane in the
 * workspace — including every scrollable region.
 *
 * Exactly one shortcut is global, and it is documented as such.
 */
export function useReplayShortcuts(
  rootRef: React.RefObject<HTMLElement | null>,
  transport: ReplayTransport,
) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const store = useReplayStore.getState();

      // The one global binding.
      if ((e.ctrlKey || e.metaKey) && e.shiftKey && (e.key === 'R' || e.key === 'r')) {
        e.preventDefault();
        store.setOpen(!store.open);
        return;
      }

      if (!store.open) return;
      if (isTextEntry(e.target)) return;

      // The dock owns the keyboard when in fullscreen, or when focus/target is
      // inside the dock. Otherwise another pane's keys are none of our business.
      const isInside = rootRef.current?.contains(document.activeElement) ?? false;
      const isEventInside = rootRef.current?.contains(e.target as Node) ?? false;
      const owned = store.mode === 'fullscreen' || isInside || isEventInside;
      if (!owned) return;

      // While a dialog is up, only Escape (handled by its own trap) applies.
      if (store.summaryOpen || store.shortcutsOpen) return;

      const step = e.altKey ? 30 : e.shiftKey ? 5 : 1;
      const speed = store.status.config?.speed ?? store.draft.speed;

      switch (e.key) {
        case ' ':
        case 'k':
        case 'K':
          e.preventDefault();
          void transport.toggle();
          return;
        case 'ArrowLeft':
          e.preventDefault();
          void transport.stepBars(-step);
          return;
        case 'ArrowRight':
          e.preventDefault();
          void transport.stepBars(step);
          return;
        case 'Home':
          e.preventDefault();
          void transport.jumpStart();
          return;
        case 'End':
          e.preventDefault();
          void transport.jumpEnd();
          return;
        case '+':
        case '=':
          e.preventDefault();
          void transport.setSpeed(stepSpeed(speed, 1));
          return;
        case '-':
        case '_':
          e.preventDefault();
          void transport.setSpeed(stepSpeed(speed, -1));
          return;
        case 's':
        case 'S':
          store.setTab('signals');
          return;
        case 't':
        case 'T':
          store.setTab('trades');
          return;
        case 'e':
        case 'E': {
          e.preventDefault();
          const date = store.status.config?.date ?? store.draft.date;
          const startTime = store.status.config?.start_time ?? store.draft.startTime;
          const endTime = store.status.config?.end_time ?? store.draft.endTime;
          // Export WHAT IS ON SCREEN. Reading the raw ledger here handed the
          // user a file that did not match the table they were looking at.
          const trades = selectFilteredTrades(store);
          const events = selectFilteredEvents(store);
          const withFriction = replayHasFriction(store.status.config, store.status.capabilities, trades);
          const exportTrades = () =>
            exportCsv(
              replayCsvName('trades', date, startTime, endTime),
              trades,
              tradeCsvColumns(withFriction),
            );
          if (store.tab === 'trades' && trades.length > 0) {
            exportTrades();
          } else if (events.length > 0) {
            exportCsv(
              replayCsvName('signals', date, startTime, endTime),
              events,
              SIGNAL_CSV_COLUMNS,
            );
          } else if (trades.length > 0) {
            exportTrades();
          }
          return;
        }
        case 'f':
        case 'F':
          store.cycleMode();
          return;
        case '?':
          store.setShortcutsOpen(true);
          return;
        case 'Escape':
          e.preventDefault();
          store.escapeMode();
          return;
        default:
      }

      // Speed presets, one digit per rung of the ladder the dropdown renders.
      const digit = Number(e.key);
      if (Number.isInteger(digit) && digit >= 1 && digit <= REPLAY_SPEEDS.length) {
        e.preventDefault();
        void transport.setSpeed(REPLAY_SPEEDS[digit - 1]);
      }
    };

    // `document`, not `window`, and in the bubble phase so a focused control
    // can stopPropagation first.
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [rootRef, transport]);
}
