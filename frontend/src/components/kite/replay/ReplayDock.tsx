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
import {
  ReplayHoursDropdown,
  ReplaySessionDropdown,
  ReplaySizingDropdown,
  ReplayStrategyDropdown,
} from './ReplayBarControls';
import { ReplayFilters } from './ReplayFilters';
import { ReplayMetricsCard } from './ReplayMetricsStrip';
import { ReplayShellBar, ReplayWindowControls } from './ReplayShellBar';
import { ReplayShortcuts } from './ReplayShortcuts';
import { ReplaySignalsTable } from './ReplaySignalsTable';
import { ReplaySummaryModal } from './ReplaySummaryModal';
import { ReplayTimeline } from './ReplayTimeline';
import { ReplayToastHost } from './ReplayToastHost';
import { ReplayTradesTable } from './ReplayTradesTable';
import { ReplayTransport } from './ReplayTransport';
import { fmtSessionDate, fmtTime } from './replayFormat';
import { useReplayAnnouncer } from './useReplayAnnouncer';
import { useReplayShortcuts } from './useReplayShortcuts';
import { useReplaySignalToasts } from './useReplaySignalToasts';
import * as Icons from './ReplayIcons';
import './replay.css';

type WidthBucket = 'xl' | 'lg' | 'md' | 'sm';

/** Expanded height when content is showing */
const ACTIVE_HEIGHT = 480;

/* ═══════════════════════════════════════════════════════════════════════════
   Session row — compact inline controls, sits at the very bottom.
   ═══════════════════════════════════════════════════════════════════════════ */
function ReplaySessionRow({ bucket: _bucket }: { bucket: WidthBucket }) {
  return (
    <div className="rd-session-row" data-testid="replay-session-row">
      <ReplaySessionDropdown />
      <span className="rd-bar-sep" aria-hidden />
      <ReplayHoursDropdown />
      <span className="rd-bar-sep" aria-hidden />
      <ReplayStrategyDropdown />
      <span className="rd-bar-sep" aria-hidden />
      <ReplaySizingDropdown />
      <div className="rd-session-actions" style={{ marginLeft: 'auto' }}>
        <ReplayFilters />
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   Unified table — tabbed signals and trades, defaulting to trades.
   ═══════════════════════════════════════════════════════════════════════════ */
function ReplayUnifiedTable() {
  const events = useFilteredReplayEvents();
  const trades = useFilteredReplayTrades();
  const tab = useReplayStore((s) => s.tab);
  const setTab = useReplayStore((s) => s.setTab);
  const state = useReplayState();
  const historical = useReplayIsHistorical();

  // If idle with no recorded signals or trades and no historical session, keep compact
  if (events.length === 0 && trades.length === 0 && state === 'idle' && !historical && tab !== 'signals') {
    return null;
  }

  const activeTab = tab === 'signals' ? 'signals' : 'trades';

  return (
    <div className="rd-unified-table">
      <div
        className="rd-table-tabs"
        role="tablist"
        aria-label="Replay tables"
        onKeyDown={(e) => {
          if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') {
            e.preventDefault();
            setTab(activeTab === 'trades' ? 'signals' : 'trades');
          }
        }}
      >
        <button
          type="button"
          role="tab"
          id="replay-tab-trades"
          aria-selected={activeTab === 'trades'}
          aria-controls="replay-panel-trades"
          className="rd-tab-btn"
          data-active={activeTab === 'trades'}
          onClick={() => setTab('trades')}
          data-testid="replay-tab-trades"
        >
          <Icons.Trades size={12} />
          <span>Trades</span>
          <span className="rd-tab-count">{trades.length}</span>
        </button>
        <button
          type="button"
          role="tab"
          id="replay-tab-signals"
          aria-selected={activeTab === 'signals'}
          aria-controls="replay-panel-signals"
          className="rd-tab-btn"
          data-active={activeTab === 'signals'}
          onClick={() => setTab('signals')}
          data-testid="replay-tab-signals"
        >
          <Icons.Signal size={12} />
          <span>Signals</span>
          <span className="rd-tab-count">{events.length}</span>
        </button>
      </div>

      <div
        role="tabpanel"
        id={activeTab === 'signals' ? 'replay-panel-signals' : 'replay-panel-trades'}
        aria-labelledby={activeTab === 'signals' ? 'replay-tab-signals' : 'replay-tab-trades'}
        className="rd-tab-panel"
      >
        {activeTab === 'signals' ? <ReplaySignalsTable /> : <ReplayTradesTable />}
      </div>
    </div>
  );
}

/** Isolated clock & percentage display so per-bar ticks only re-render this sub-tree */
function ReplayPlayerBarInfo() {
  const state = useReplayState();
  const clock = useReplayStore((s) => s.status.current_time_iso);
  const currentDate = useReplayStore((s) => s.status.current_date);
  const startTime = useReplayStore((s) => s.draft.startTime);
  const pct = useReplayStore((s) => s.status.progress_pct);
  const cfg = useReplayStore((s) => s.status.config);
  const multiDay = !!cfg?.end_date && cfg.end_date !== cfg?.date;

  const displayTime = clock ? fmtTime(clock) : fmtTime(startTime);
  let datePrefix = '';
  if (multiDay) {
    const rawDate = currentDate || (clock && clock.includes('T') ? clock.split('T')[0] : cfg?.date);
    if (rawDate) {
      datePrefix = `${fmtSessionDate(rawDate, true)} · `;
    }
  }

  return (
    <div className="rd-player-bar-info" data-testid="replay-player-bar-info">
      <span className="rd-player-bar-clock" data-state={state}>
        {datePrefix}{displayTime} IST
      </span>
      <span className="rd-player-bar-pct">{Math.round(pct)}%</span>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   Main dock component.
   ═══════════════════════════════════════════════════════════════════════════ */
export function ReplayDock() {
  const open = useReplayStore((s) => s.open);
  const mode = useReplayStore((s) => s.mode);
  const height = useReplayStore((s) => s.height);
  const ownsHostPane = useReplayHostHidden();
  const setHeight = useReplayStore((s) => s.setHeight);
  const state = useReplayState();
  const events = useFilteredReplayEvents();
  const trades = useFilteredReplayTrades();
  const errorMsg = useReplayStore((s) => s.error?.message);
  const setError = useReplayStore((s) => s.setError);
  const cfg = useReplayStore((s) => s.status.config);
  const multiDay = !!cfg?.end_date && cfg.end_date !== cfg?.date;
  const draft = useReplayStore((s) => s.draft);
  const historical = useReplayIsHistorical();
  const clearSession = useReplayStore((s) => s.clearSession);
  const setSummaryOpen = useReplayStore((s) => s.setSummaryOpen);

  const transport = useReplayTransport();
  const rootRef = useRef<HTMLElement>(null);
  const [bucket, setBucket] = useState<WidthBucket>('xl');
  const [dragging, setDragging] = useState(false);
  const [userInteractedHeight, setUserInteractedHeight] = useState(false);
  const prevStateRef = useRef(state);

  const hasResults = events.length > 0 || trades.length > 0;
  const isCompactIdle =
    (mode === 'docked' || mode === 'overlay') &&
    state === 'idle' &&
    !hasResults &&
    !historical &&
    !userInteractedHeight;

  useReplayStream(true);
  useReplayShortcuts(rootRef, transport);
  const announcement = useReplayAnnouncer();
  useReplaySignalToasts();

  // Auto-expand dock when replay starts running
  useEffect(() => {
    const prev = prevStateRef.current;
    prevStateRef.current = state;
    if (prev === 'idle' && (state === 'loading' || state === 'running') && (mode === 'docked' || mode === 'overlay')) {
      if (height < ACTIVE_HEIGHT) setHeight(ACTIVE_HEIGHT);
    }
  }, [state, mode, height, setHeight]);

  useEffect(() => {
    if (!open) return;
    const active = document.activeElement;
    if (active && active !== document.body && rootRef.current?.contains(active)) return;
    if (active && active !== document.body && !rootRef.current?.contains(active)) return;
    rootRef.current?.focus({ preventScroll: true });
  }, [mode, open]);

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

  const maxHeight = useCallback(() => {
    if (typeof window === 'undefined') return 900;
    const ceiling = mode === 'overlay' ? window.innerHeight - FOOTER_HEIGHT - 80 : window.innerHeight - 160;
    return Math.max(MIN_DOCK_HEIGHT, ceiling);
  }, [mode]);

  const onResizePointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    setUserInteractedHeight(true);
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
      frame = requestAnimationFrame(() => { frame = 0; setHeight(next); });
    };
    const onUp = () => {
      if (frame) cancelAnimationFrame(frame);
      node.removeEventListener('pointermove', onMove);
      node.removeEventListener('pointerup', onUp);
      node.removeEventListener('pointercancel', onUp);
      try { node.releasePointerCapture(e.pointerId); } catch { /* */ }
      document.body.style.userSelect = '';
      setDragging(false);
      setHeight(next);
    };
    node.addEventListener('pointermove', onMove);
    node.addEventListener('pointerup', onUp);
    node.addEventListener('pointercancel', onUp);
  };

  const onResizeKey = (e: React.KeyboardEvent) => {
    setUserInteractedHeight(true);
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
      height: isCompactIdle ? 'auto' : `${height}px`,
      borderTop: '1px solid var(--k-border-strong-4)',
    },
    expanded: { width: '100%', height: '100%', flex: 1, minHeight: 0, borderTop: 'none' },
    overlay: {
      position: 'fixed',
      left: 0,
      right: 0,
      bottom: FOOTER_HEIGHT,
      height: isCompactIdle ? 'auto' : `${height}px`,
      zIndex: 'var(--rd-z-dock)' as unknown as number,
      borderTop: '1px solid var(--k-border-strong-4)',
      boxShadow: '0 -8px 24px color-mix(in srgb, var(--k-text) 10%, transparent)',
    },
    fullscreen: {
      position: 'fixed',
      inset: 0,
      zIndex: 'var(--rd-z-fullscreen)' as unknown as number,
      background: 'var(--k-surface-sunken)',
    },
  }), [height, isCompactIdle]);

  const overlays = (
    <>
      <ReplayToastHost />
      <ReplaySummaryModal />
      <ReplayShortcuts />
    </>
  );

  if (!open) return overlays;

  const resizable = mode === 'docked' || mode === 'overlay';
  const showDetailedReport = (state === 'idle' && hasResults) || historical;

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

      {/* ── Header ───────────────────────────────────────────────── */}
      <div className="rd-clean-header">
        <ReplayShellBar />
        <ReplayWindowControls />
      </div>

      {/* ── Scrollable body ──────────────────────────────────────── */}
      <div className="rd-scroll-content">
        {/* Historical note */}
        {historical && !errorMsg && (
          <div className="rd-session-note" data-testid="replay-historical-note">
            <Icons.Alert size={13} />
            <span>
              Showing results from finished session{cfg?.date ? ` (${cfg.date})` : ''} — Nothing is replaying now. {events.length} signals, {trades.length} trades.
            </span>
            <span className="rd-error-strip-actions">
              <button type="button" className="rd-btn rd-btn-sm" aria-label="Clear results" onClick={() => { void clearSession(); setUserInteractedHeight(false); }}>Clear results</button>
            </span>
          </div>
        )}

        {/* Multi-day range note */}
        {multiDay && (
          <div className="rd-session-note" data-testid="replay-multiday-note">
            <Icons.Alert size={13} />
            <span>Multi-day range — the timeline shows session times only.</span>
          </div>
        )}

        {/* Error strip */}
        {errorMsg && (
          <div className="rd-error-strip" role="alert">
            <Icons.Alert size={14} />
            <span>{errorMsg}</span>
            <span className="rd-error-strip-actions">
              <button type="button" className="rd-btn rd-btn-sm" onClick={() => void transport.start()}>Retry</button>
              <button type="button" className="rd-btn rd-btn-sm" data-variant="ghost" onClick={() => setError(null)}>Dismiss</button>
            </span>
          </div>
        )}

        {/* Metrics strip (flat inline) */}
        <ReplayMetricsCard />

        {/* Unified table */}
        <ReplayUnifiedTable />
      </div>

      {/* ── Player bar (never scrolls) ───────────────────────────── */}
      <div className="rd-player-bar" data-testid="replay-player-bar">
        <ReplayTransport />
        <ReplayTimeline />
        <ReplayPlayerBarInfo />
        {showDetailedReport && (
          <div className="rd-player-bar-detail">
            <button
              type="button"
              className="rd-btn"
              data-variant="report"
              onClick={() => setSummaryOpen(true)}
              data-testid="replay-detailed-report"
            >
              <Icons.Export size={12} /> Detailed Report
            </button>
          </div>
        )}
      </div>

      {/* ── Session row (never scrolls) ──────────────────────────── */}
      <ReplaySessionRow bucket={bucket} />

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
