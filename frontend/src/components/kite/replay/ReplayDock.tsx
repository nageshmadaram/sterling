import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import {
  MIN_DOCK_HEIGHT,
  ReplayMode,
  useFilteredReplayEvents,
  useFilteredReplayTrades,
  useReplayHostHidden,
  useReplayIsHistorical,
  useReplaySessionPolicy,
  useReplayState,
  useReplayStore,
} from '../../../hooks/useReplayStore';
import { useReplayStream } from '../../../hooks/useReplayStream';
import { useReplayTransport } from '../../../hooks/useReplayTransport';
import { getDynamicMarketPresets } from '../../../lib/replay/marketSessions';
import { FOOTER_HEIGHT } from '../layoutConstants';
import { ReplayConfigSheet } from './ReplayConfigPanel';
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
import { SIGNAL_CSV_COLUMNS } from './replayColumns';
import { exportCsv, replayCsvName } from './replayCsv';
import { fmtSmartDate, fmtTime } from './replayFormat';
import { useReplayAnnouncer } from './useReplayAnnouncer';
import { useReplayShortcuts } from './useReplayShortcuts';
import { useReplaySignalToasts } from './useReplaySignalToasts';
import * as Icons from './ReplayIcons';
import './replay.css';

type WidthBucket = 'xl' | 'lg' | 'md' | 'sm';

/** Compact height when idle — just header + player + session row */
const IDLE_HEIGHT = MIN_DOCK_HEIGHT;
/** Expanded height when content is showing */
const ACTIVE_HEIGHT = 480;

/* ═══════════════════════════════════════════════════════════════════════════
   Session row — compact inline controls, sits at the very bottom.
   ═══════════════════════════════════════════════════════════════════════════ */
function shiftTime(time: string, mins: number): string {
  const [h, m] = time.split(':').map(Number);
  const t = h * 60 + m + mins;
  return `${String(Math.floor(t / 60)).padStart(2, '0')}:${String(t % 60).padStart(2, '0')}:00`;
}

function ReplaySessionRow({ bucket }: { bucket: WidthBucket }) {
  const draft = useReplayStore((s) => s.draft);
  const setDraft = useReplayStore((s) => s.setDraft);
  const state = useReplayState();
  const setConfigOpen = useReplayStore((s) => s.setConfigOpen);
  const policy = useReplaySessionPolicy();
  const presets = useMemo(() => getDynamicMarketPresets(), []);
  const locked = state !== 'idle';

  const open = policy?.continuous_open ?? '09:15:00';
  const close = policy?.continuous_close ?? '15:40:00';
  const preopen = policy?.preopen_start ?? '09:00:00';

  const HOUR_PRESETS = useMemo(() => [
    { id: 'regular', label: 'Regular', start: open, end: close },
    { id: 'preopen', label: 'Pre-open', start: preopen, end: close },
    { id: 'first', label: '1st hr', start: open, end: shiftTime(open, 60) },
    { id: 'last', label: 'Last hr', start: shiftTime(close, -60), end: close },
  ], [open, close, preopen]);

  return (
    <div className="rd-session-row" data-testid="replay-session-row">
      <div className="rd-session-dates">
        {presets.map((p) => (
          <button
            key={p.id}
            type="button"
            className="rd-session-pill"
            disabled={locked}
            aria-pressed={draft.date === p.date}
            data-active={draft.date === p.date}
            title={p.description}
            onClick={() => setDraft({ date: p.date, endDate: p.date })}
          >
            {p.label}
          </button>
        ))}
        <input
          type="date"
          className="rd-session-date-input"
          disabled={locked}
          value={draft.date}
          onChange={(e) => setDraft({ date: e.target.value, endDate: e.target.value })}
          title="Custom date"
        />
      </div>

      <span className="rd-bar-sep" aria-hidden />

      <div className="rd-session-times">
        <span className="rd-session-range">
          {fmtTime(draft.startTime, 5)}–{fmtTime(draft.endTime, 5)}
        </span>
        {HOUR_PRESETS.map((r) => (
          <button
            key={r.id}
            type="button"
            className="rd-session-pill"
            disabled={locked}
            data-active={draft.startTime === r.start && draft.endTime === r.end}
            onClick={() => setDraft({ startTime: r.start, endTime: r.end })}
          >
            {r.label}
          </button>
        ))}
      </div>

      <span className="rd-bar-sep" aria-hidden />

      <div className="rd-session-actions">
        <ReplayFilters />
        <button
          type="button"
          className="rd-btn"
          disabled={locked}
          onClick={() => setConfigOpen(true)}
          title="Configure replay"
          data-testid="replay-configure"
        >
          <Icons.Config size={13} />
        </button>
      </div>
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════════════════
   Unified table — signals and trades combined, with section toggles.
   ═══════════════════════════════════════════════════════════════════════════ */
function ReplayUnifiedTable() {
  const events = useFilteredReplayEvents();
  const trades = useFilteredReplayTrades();
  const [expanded, setExpanded] = useState<'signals' | 'trades' | null>(null);

  if (events.length === 0 && trades.length === 0) return null;

  if (expanded === 'signals') {
    return (
      <div className="rd-unified-table">
        <div className="rd-unified-head">
          <button
            type="button"
            className="rd-btn rd-btn-sm"
            data-variant="ghost"
            onClick={() => setExpanded(null)}
          >
            <Icons.ChevronUp size={10} /> Back
          </button>
          <span className="rd-unified-title">
            <Icons.Signal size={12} /> Signals <span className="rd-dim">{events.length}</span>
          </span>
        </div>
        <ReplaySignalsTable />
      </div>
    );
  }

  if (expanded === 'trades') {
    return (
      <div className="rd-unified-table">
        <div className="rd-unified-head">
          <button
            type="button"
            className="rd-btn rd-btn-sm"
            data-variant="ghost"
            onClick={() => setExpanded(null)}
          >
            <Icons.ChevronUp size={10} /> Back
          </button>
          <span className="rd-unified-title">
            <Icons.Trades size={12} /> Trades <span className="rd-dim">{trades.length}</span>
          </span>
        </div>
        <ReplayTradesTable />
      </div>
    );
  }

  // Default: combined view — both tables stacked with expand buttons
  return (
    <div className="rd-unified-table">
      {/* Signals section */}
      {events.length > 0 && (
        <div className="rd-unified-section">
          <div className="rd-unified-head">
            <span className="rd-unified-title">
              <Icons.Signal size={12} /> Signals <span className="rd-dim">{events.length}</span>
            </span>
            <button
              type="button"
              className="rd-btn rd-btn-sm"
              data-variant="ghost"
              onClick={() => setExpanded('signals')}
              title="View full signals table"
            >
              Expand <Icons.Fullscreen size={10} />
            </button>
          </div>
          <ReplaySignalsTable />
        </div>
      )}

      {/* Trades section */}
      {trades.length > 0 && (
        <div className="rd-unified-section">
          <div className="rd-unified-head">
            <span className="rd-unified-title">
              <Icons.Trades size={12} /> Trades <span className="rd-dim">{trades.length}</span>
            </span>
            <button
              type="button"
              className="rd-btn rd-btn-sm"
              data-variant="ghost"
              onClick={() => setExpanded('trades')}
              title="View full trades table"
            >
              Expand <Icons.Fullscreen size={10} />
            </button>
          </div>
          <ReplayTradesTable />
        </div>
      )}
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
  const draft = useReplayStore((s) => s.draft);
  const historical = useReplayIsHistorical();
  const clearSession = useReplayStore((s) => s.clearSession);
  const setSummaryOpen = useReplayStore((s) => s.setSummaryOpen);
  const clock = useReplayStore((s) => s.status.current_time_iso);
  const pct = useReplayStore((s) => s.status.progress_pct);
  const speed = useReplayStore((s) => s.status.config?.speed ?? s.draft.speed);

  const transport = useReplayTransport();
  const rootRef = useRef<HTMLElement>(null);
  const [bucket, setBucket] = useState<WidthBucket>('xl');
  const [dragging, setDragging] = useState(false);
  const prevStateRef = useRef(state);

  useReplayStream(true);
  useReplayShortcuts(rootRef, transport);
  const announcement = useReplayAnnouncer();
  useReplaySignalToasts();

  // Auto-expand dock when replay starts running
  useEffect(() => {
    const prev = prevStateRef.current;
    prevStateRef.current = state;
    if (prev === 'idle' && (state === 'loading' || state === 'running') && mode === 'docked') {
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
    const step = e.shiftKey ? 64 : 16;
    if (e.key === 'ArrowUp') { e.preventDefault(); setHeight(Math.min(maxHeight(), height + step)); }
    else if (e.key === 'ArrowDown') { e.preventDefault(); setHeight(Math.max(MIN_DOCK_HEIGHT, height - step)); }
    else if (e.key === 'Home') { e.preventDefault(); setHeight(MIN_DOCK_HEIGHT); }
    else if (e.key === 'End') { e.preventDefault(); setHeight(maxHeight()); }
  };

  const geometry = useMemo<Record<ReplayMode, React.CSSProperties>>(() => ({
    docked: { width: '100%', flexShrink: 0, height: `${height}px`, borderTop: '1px solid var(--k-border-strong-4)' },
    expanded: { width: '100%', height: '100%', flex: 1, minHeight: 0, borderTop: 'none' },
    overlay: {
      position: 'fixed', left: 0, right: 0, bottom: FOOTER_HEIGHT,
      height: `${height}px`, zIndex: 'var(--rd-z-dock)' as unknown as number,
      borderTop: '1px solid var(--k-border-strong-4)',
      boxShadow: '0 -8px 24px color-mix(in srgb, var(--k-text) 10%, transparent)',
    },
    fullscreen: {
      position: 'fixed', inset: 0,
      zIndex: 'var(--rd-z-fullscreen)' as unknown as number,
      background: 'var(--k-surface-sunken)',
    },
  }), [height]);

  const overlays = (
    <>
      <ReplayToastHost />
      <ReplaySummaryModal />
      <ReplayShortcuts />
    </>
  );

  if (!open) return overlays;

  const resizable = mode === 'docked' || mode === 'overlay';
  const active = state === 'running' || state === 'paused';
  const hasResults = events.length > 0 || trades.length > 0;
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
              Finished session{cfg?.date ? ` from ${cfg.date}` : ''} — {events.length} signals, {trades.length} trades.
            </span>
            <span className="rd-error-strip-actions">
              <button type="button" className="rd-btn rd-btn-sm" onClick={() => void clearSession()}>Clear</button>
            </span>
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
        <div className="rd-player-bar-info">
          <span className="rd-player-bar-clock" data-state={state}>
            {fmtTime(clock)} IST
          </span>
          <span className="rd-player-bar-pct">{Math.round(pct)}%</span>
          {active && (
            <span style={{ fontSize: 'var(--rd-fs-label)', color: 'var(--k-dim)' }}>{speed}×</span>
          )}
        </div>
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

      {/* Config sheet overlay */}
      <ReplayConfigSheet />

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
