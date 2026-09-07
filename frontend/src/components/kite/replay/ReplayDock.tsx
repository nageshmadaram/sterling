import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import {
  MIN_DOCK_HEIGHT,
  ReplayMode,
  useFilteredReplayEvents,
  useFilteredReplayTrades,
  useReplayHostHidden,
  useReplayIsHistorical,
  useReplayState,
  useReplayStore,
} from '../../../hooks/useReplayStore';
import { useReplayStream } from '../../../hooks/useReplayStream';
import { useReplayTransport } from '../../../hooks/useReplayTransport';
import { FOOTER_HEIGHT } from '../layoutConstants';
import { Segmented } from './primitives/Segmented';
import { ReplayConfigSheet } from './ReplayConfigPanel';
import { ReplayFilterChips, ReplayFilters } from './ReplayFilters';
import { ReplayMetricsStrip } from './ReplayMetricsStrip';
import { ReplaySessionPicker } from './ReplaySessionPicker';
import { ReplayShellBar, ReplayWindowControls } from './ReplayShellBar';
import { ReplayShortcuts } from './ReplayShortcuts';
import { ReplaySignalsTable } from './ReplaySignalsTable';
import { ReplaySummaryModal } from './ReplaySummaryModal';
import { ReplayTimeline } from './ReplayTimeline';
import { ReplayToastHost } from './ReplayToastHost';
import { ReplayTradesTable } from './ReplayTradesTable';
import { ReplayTransport } from './ReplayTransport';
import { SIGNAL_CSV_COLUMNS, tradeCsvColumns, tradesHaveFriction } from './replayColumns';
import { exportCsv, replayCsvName } from './replayCsv';
import { useReplayAnnouncer } from './useReplayAnnouncer';
import { useReplayShortcuts } from './useReplayShortcuts';
import { useReplaySignalToasts } from './useReplaySignalToasts';
import * as Icons from './ReplayIcons';
import './replay.css';

type WidthBucket = 'xl' | 'lg' | 'md' | 'sm';

/**
 * The replay dock shell.
 *
 * It owns four things and renders no data of its own: which mode it is in and
 * the geometry that implies, its height, the portals for the modes that need
 * them, and the keyboard scope.
 */
export function ReplayDock() {
  const open = useReplayStore((s) => s.open);
  const mode = useReplayStore((s) => s.mode);
  const height = useReplayStore((s) => s.height);
  // True once the dock has taken the host pane over — either its own `expanded`
  // mode, or a workspace focus (half / maximised / fullscreen) it drives.
  const ownsHostPane = useReplayHostHidden();
  const tab = useReplayStore((s) => s.tab);
  const setTab = useReplayStore((s) => s.setTab);
  const setHeight = useReplayStore((s) => s.setHeight);
  const setConfigOpen = useReplayStore((s) => s.setConfigOpen);
  const state = useReplayState();
  const events = useFilteredReplayEvents();
  const trades = useFilteredReplayTrades();
  const errorMsg = useReplayStore((s) => s.error?.message);
  const setError = useReplayStore((s) => s.setError);
  const cfg = useReplayStore((s) => s.status.config);
  const draft = useReplayStore((s) => s.draft);
  const historical = useReplayIsHistorical();
  const clearSession = useReplayStore((s) => s.clearSession);

  const transport = useReplayTransport();
  const rootRef = useRef<HTMLElement>(null);
  const [bucket, setBucket] = useState<WidthBucket>('xl');
  const [dragging, setDragging] = useState(false);

  // Keep the store in step with the runner whenever the dock is mounted; the
  // hook itself decides whether to stream, poll slowly, or stop entirely.
  useReplayStream(true);
  useReplayShortcuts(rootRef, transport);
  const announcement = useReplayAnnouncer();
  useReplaySignalToasts();

  /**
   * Keep the keyboard alive across a mode change.
   *
   * Changing mode unmounts the focused control (the button you just pressed, or
   * a portal the dock moved out of), so focus falls back to `<body>` — and the
   * shortcut handler's ownership check then rejects everything, including the
   * next Escape. Observed: Escape stepped fullscreen -> overlay -> docked and
   * then went dead, so the dock could not be closed from the keyboard.
   *
   * Only reclaims focus that was actually lost to the body, so a deliberate
   * click into another pane is left alone.
   */
  useEffect(() => {
    if (!open) return;
    const active = document.activeElement;
    if (active && active !== document.body && rootRef.current?.contains(active)) return;
    if (active && active !== document.body && !rootRef.current?.contains(active)) return;
    rootRef.current?.focus({ preventScroll: true });
  }, [mode, open]);

  /* ── Width buckets ─────────────────────────────────────────────────────
     Measured from the dock, never the viewport: it is a pane inside a
     resizable workspace, so the window's width says nothing about its own. */
  useEffect(() => {
    const el = rootRef.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(([entry]) => {
      const w = entry.contentRect.width;
      setBucket(w >= 1100 ? 'xl' : w >= 900 ? 'lg' : w >= 700 ? 'md' : 'sm');
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, [open]);

  /* ── Resize ────────────────────────────────────────────────────────────
     Pointer capture rather than window listeners, so an unmount mid-drag
     cannot leak a handler, and localStorage is written once on release
     rather than on every move. */
  const maxHeight = useCallback(() => {
    if (typeof window === 'undefined') return 900;
    const ceiling = mode === 'overlay' ? window.innerHeight - FOOTER_HEIGHT - 80 : window.innerHeight - 160;
    return Math.max(MIN_DOCK_HEIGHT, ceiling);
  }, [mode]);

  const onResizePointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    const node = e.currentTarget;
    node.setPointerCapture(e.pointerId);
    setDragging(true);
    document.body.style.userSelect = 'none';

    const startY = e.clientY;
    const startH = height;
    let next = height;
    let frame = 0;

    const onMove = (ev: PointerEvent) => {
      next = Math.max(MIN_DOCK_HEIGHT, Math.min(maxHeight(), startH + (startY - ev.clientY)));
      if (frame) return;
      frame = requestAnimationFrame(() => {
        frame = 0;
        setHeight(next);
      });
    };
    const onUp = () => {
      if (frame) cancelAnimationFrame(frame);
      node.removeEventListener('pointermove', onMove);
      node.removeEventListener('pointerup', onUp);
      node.removeEventListener('pointercancel', onUp);
      try {
        node.releasePointerCapture(e.pointerId);
      } catch {
        /* already released */
      }
      document.body.style.userSelect = '';
      setDragging(false);
      setHeight(next);
    };
    node.addEventListener('pointermove', onMove);
    node.addEventListener('pointerup', onUp);
    node.addEventListener('pointercancel', onUp);
  };

  const onResizeKey = (e: React.KeyboardEvent) => {
    const step = e.shiftKey ? 64 : 16;
    if (e.key === 'ArrowUp') { e.preventDefault(); setHeight(Math.min(maxHeight(), height + step)); }
    else if (e.key === 'ArrowDown') { e.preventDefault(); setHeight(Math.max(MIN_DOCK_HEIGHT, height - step)); }
    else if (e.key === 'Home') { e.preventDefault(); setHeight(MIN_DOCK_HEIGHT); }
    else if (e.key === 'End') { e.preventDefault(); setHeight(maxHeight()); }
  };

  const geometry = useMemo<Record<ReplayMode, React.CSSProperties>>(() => ({
    docked: {
      width: '100%',
      flexShrink: 0,
      height: `${height}px`,
      borderTop: '1px solid var(--k-border-strong-4)',
    },
    expanded: { width: '100%', height: '100%', flex: 1, minHeight: 0, borderTop: 'none' },
    overlay: {
      position: 'fixed', left: 0, right: 0, bottom: FOOTER_HEIGHT,
      height: `${height}px`,
      zIndex: 'var(--rd-z-dock)' as unknown as number,
      borderTop: '1px solid var(--k-border-strong-4)',
      boxShadow: '0 -8px 24px color-mix(in srgb, var(--k-text) 10%, transparent)',
    },
    fullscreen: {
      position: 'fixed', inset: 0,
      zIndex: 'var(--rd-z-fullscreen)' as unknown as number,
      background: 'var(--k-surface-sunken)',
    },
  }), [height]);

  const exportCurrent = useCallback(() => {
    const date = cfg?.date ?? draft.date;
    const s = cfg?.start_time ?? draft.startTime;
    const e = cfg?.end_time ?? draft.endTime;
    if (tab === 'trades') {
      exportCsv(replayCsvName('trades', date, s, e), trades, tradeCsvColumns(tradesHaveFriction(trades)));
    } else {
      exportCsv(replayCsvName('signals', date, s, e), events, SIGNAL_CSV_COLUMNS);
    }
  }, [tab, trades, events, cfg, draft]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.key === 'e' || e.key === 'E') && useReplayStore.getState().open) {
        const el = document.activeElement as HTMLElement | null;
        if (el && (el.tagName === 'INPUT' || el.tagName === 'TEXTAREA')) return;
        if (rootRef.current?.contains(el)) exportCurrent();
      }
    };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [exportCurrent]);

  const overlays = (
    <>
      <ReplayToastHost />
      <ReplaySummaryModal />
      <ReplayShortcuts />
    </>
  );

  if (!open) return overlays;

  const resizable = mode === 'docked' || mode === 'overlay';

  const shell = (
    <section
      ref={rootRef}
      tabIndex={-1}
      data-replay-root=""
      data-testid="replay-dock"
      data-mode={mode}
      data-state={state}
      data-width={bucket}
      className="replay-dock kw-pane"
      aria-label="Market replay"
      /* While the dock owns the host pane there is nothing else in it, so a
         stored pixel height would leave dead space below — which is exactly
         what "half screen" looked like. Fill the pane instead. */
      style={ownsHostPane && mode === 'docked' ? geometry.expanded : geometry[mode]}
    >
      {resizable && (
        <div
          role="separator"
          aria-orientation="horizontal"
          aria-label="Resize replay dock"
          aria-valuenow={height}
          aria-valuemin={MIN_DOCK_HEIGHT}
          aria-valuemax={maxHeight()}
          tabIndex={0}
          className="rd-resizer"
          data-active={dragging}
          data-testid="replay-resizer"
          onPointerDown={onResizePointerDown}
          onKeyDown={onResizeKey}
        />
      )}

      {/* TWO bands, not four. Identity, transport and the timeline share one
          row; the numbers share a row with the view controls. The dock used to
          stack shell + rail + metrics + view for 136px before any data. */}
      <div className="rd-bar rd-bar-cmd">
        <ReplayShellBar />
        <ReplayTransport />
        <ReplayTimeline />
        <ReplayWindowControls />
      </div>

      <div className="rd-bar rd-bar-meta">
        <ReplayMetricsStrip />
        <div className="rd-viewbar">
        <Segmented
          idPrefix="replay"
          label="Replay view"
          value={tab}
          onChange={setTab}
          items={[
            { id: 'split', label: 'Split', icon: <Icons.Split size={12} /> },
            { id: 'signals', label: 'Signals', icon: <Icons.Signal size={12} />, count: events.length },
            { id: 'trades', label: 'Trades', icon: <Icons.Trades size={12} />, count: trades.length },
          ]}
        />
        <ReplayFilterChips />
        <div className="rd-viewbar-right">
          <ReplaySessionPicker widthBucket={bucket} />
          <ReplayFilters />
          <button
            type="button"
            className="rd-btn"
            disabled={state !== 'idle'}
            onClick={() => setConfigOpen(true)}
            title={state === 'idle' ? 'Configure the replay (C)' : 'Stop the replay to change its configuration'}
            data-testid="replay-configure"
          >
            <Icons.Config size={13} />
          </button>
        </div>
        </div>
      </div>

      {/* The runner keeps a finished session's ledger so it can be reviewed.
          Saying so is the difference between "here is your last run" and the
          dock appearing to show trades before you pressed play. */}
      {historical && !errorMsg && (
        <div className="rd-session-note" data-testid="replay-historical-note">
          <Icons.Alert size={13} />
          <span>
            Showing the <strong>finished</strong> session
            {cfg?.date ? ` from ${cfg.date}` : ''} — {events.length} signals, {trades.length} trades.
            Nothing is replaying now.
          </span>
          <span className="rd-error-strip-actions">
            <button type="button" className="rd-btn rd-btn-sm" onClick={() => void clearSession()}>
              Clear results
            </button>
          </span>
        </div>
      )}

      {errorMsg && (
        <div className="rd-error-strip" role="alert">
          <Icons.Alert size={14} />
          <span>{errorMsg}</span>
          <span className="rd-error-strip-actions">
            <button type="button" className="rd-btn rd-btn-sm" onClick={() => void transport.start()}>
              Retry
            </button>
            <button type="button" className="rd-btn rd-btn-sm" data-variant="ghost" onClick={() => setError(null)}>
              Dismiss
            </button>
          </span>
        </div>
      )}

      <div className="rd-content">
        {/* Only the active panel is mounted. Keeping all three alive is what
            made a status frame re-render two tables that nobody was looking at. */}
        <div
          className="rd-panel"
          role="tabpanel"
          id={`replay-panel-${tab}`}
          aria-labelledby={`replay-tab-${tab}`}
          key={tab}
        >
          {tab === 'split' && (
            <div className="rd-split">
              <div className="rd-pane">
                <div className="rd-pane-head">
                  <Icons.Signal size={12} /> Signals
                  <span className="rd-seg-count">{events.length}</span>
                </div>
                <ReplaySignalsTable />
              </div>
              <div className="rd-pane">
                <div className="rd-pane-head">
                  <Icons.Trades size={12} /> Trades
                  <span className="rd-seg-count">{trades.length}</span>
                </div>
                <ReplayTradesTable />
              </div>
            </div>
          )}
          {tab === 'signals' && (
            <div className="rd-pane" style={{ margin: 8, borderRadius: 6 }}>
              <div className="rd-pane-head">
                <Icons.Signal size={12} /> Signals
                <span className="rd-seg-count">{events.length}</span>
              </div>
              <ReplaySignalsTable />
            </div>
          )}
          {tab === 'trades' && (
            <div className="rd-pane" style={{ margin: 8, borderRadius: 6 }}>
              <div className="rd-pane-head">
                <Icons.Trades size={12} /> Trades
                <span className="rd-seg-count">{trades.length}</span>
              </div>
              <ReplayTradesTable />
            </div>
          )}
        </div>

        <ReplayConfigSheet />
      </div>

      <div aria-live="polite" aria-atomic="true" className="rd-sr-only" data-testid="replay-live">
        {announcement}
      </div>
    </section>
  );

  const hosted =
    mode === 'overlay' || mode === 'fullscreen'
      ? createPortal(shell, document.body)
      : shell;

  return (
    <>
      {hosted}
      {overlays}
    </>
  );
}

export default ReplayDock;
