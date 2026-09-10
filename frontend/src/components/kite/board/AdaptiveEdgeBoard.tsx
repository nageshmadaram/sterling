/**
 * Adaptive Edge, on the shared board.
 *
 * The standalone Adaptive Edge page keeps its own richer panel — it has the
 * width for per-underlying grouping and the mode-history detail. This is the
 * sidebar view, where the point is that all three engines read the same way.
 */
import React from 'react';
import {
  useAdaptiveEdgeEngineConfig,
  useAdaptiveEdgeSnapshot,
  useSetAdaptiveEdgeEngineConfig,
} from '../../../hooks/useAdaptiveEdge';
import { rowsFromSnapshot } from '../AdaptiveEdgePanel';
import { adaptiveEdgeToBoard } from './adaptiveEdgeAdapter';
import { BOARD_COLUMNS, DEFAULT_SORT, SignalBoard } from './SignalBoard';
import { useBoardRowActions } from './useBoardRowActions';
import { BoardFilters } from './BoardFilters';
import { BoardTicket } from './BoardTicket';
import { useBoardView } from './useBoardView';
import { sessionDayKey, type BoardSignal } from './boardTypes';
import { useSimNowMs } from '../../../hooks/useReplayStore';
import { k } from '../../../styles/kiteUI';

export type AdaptiveEdgeSourceFilter = 'all' | 'ae' | 'spot';

export function AdaptiveEdgeBoard({
  nowMs,
  onOpenDetail,
  onOpenChart,
  sourceFilter: controlledSourceFilter,
  onSourceFilterChange,
}: {
  /** Opens this row's instrument in the chart pane. Without it the Chart column is empty. */
  onOpenChart?: (quoteKey: string) => void;
  nowMs?: number;
  onOpenDetail?: (signal: BoardSignal) => void;
  sourceFilter?: AdaptiveEdgeSourceFilter;
  onSourceFilterChange?: (src: AdaptiveEdgeSourceFilter) => void;
}) {
  // Buy/Sell and the chart, built from the signal alone — same on every board.
  const rowActions = useBoardRowActions({ onOpenChart });
  const snapshot = useAdaptiveEdgeSnapshot();
  const { data: engineCfg } = useAdaptiveEdgeEngineConfig();
  const setEngineCfg = useSetAdaptiveEdgeEngineConfig();
  const rawSignals = React.useMemo(
    () => (snapshot.data ? adaptiveEdgeToBoard(rowsFromSnapshot(snapshot.data)) : []),
    [snapshot.data],
  );

  const [internalSourceFilter, setInternalSourceFilter] = React.useState<AdaptiveEdgeSourceFilter>(() => {
    if (typeof localStorage === 'undefined') return 'all';
    try {
      const saved = localStorage.getItem('sterling.board.source.adaptive_edge');
      if (saved === 'ae' || saved === 'spot' || saved === 'all') return saved;
    } catch {}
    return 'all';
  });

  const activeSourceFilter = controlledSourceFilter ?? internalSourceFilter;

  const handleSourceChange = React.useCallback(
    (next: AdaptiveEdgeSourceFilter) => {
      const resolved = activeSourceFilter === next && next !== 'all' ? 'all' : next;
      if (onSourceFilterChange) {
        onSourceFilterChange(resolved);
      }
      setInternalSourceFilter(resolved);
      try {
        localStorage.setItem('sterling.board.source.adaptive_edge', resolved);
      } catch {}
    },
    [activeSourceFilter, onSourceFilterChange],
  );

  const { aeCount, spotCount, totalCount } = React.useMemo(() => {
    let ae = 0;
    let spot = 0;
    for (const s of rawSignals) {
      if (s.origin?.label === 'SPOT SCAN') spot++;
      else ae++;
    }
    return { aeCount: ae, spotCount: spot, totalCount: rawSignals.length };
  }, [rawSignals]);

  const filteredSignals = React.useMemo(() => {
    if (activeSourceFilter === 'ae') {
      return rawSignals.filter((s) => s.origin?.label !== 'SPOT SCAN');
    }
    if (activeSourceFilter === 'spot') {
      return rawSignals.filter((s) => s.origin?.label === 'SPOT SCAN');
    }
    return rawSignals;
  }, [rawSignals, activeSourceFilter]);

  const view = useBoardView(filteredSignals, { endedByDefault: true, storageKey: 'adaptive_edge', nowMs });
  const [openId, setOpenId] = React.useState<string | null>(null);
  const [sort, setSort] = React.useState(DEFAULT_SORT);
  // Which signals are showing their contracts. Separate from openId, which is
  // a row's own detail — a parent opens its legs, a leg opens its detail.
  const [collapsedGroups, setCollapsedGroups] = React.useState<ReadonlySet<string>>(new Set());
  const toggleGroup = React.useCallback((id: string) => {
    setCollapsedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  if (snapshot.isLoading && !snapshot.data) {
    return <p style={{ padding: 12, margin: 0, fontSize: 11, color: k.dim }}>Loading Adaptive Edge…</p>;
  }
  if (snapshot.error) {
    return <p style={{ padding: 12, margin: 0, fontSize: 11, color: k.red }}>Adaptive Edge unavailable: {(snapshot.error as Error).message}</p>;
  }

  const sourceToggles = (
    <div
      role="group"
      aria-label="Signal source filter"
      data-testid="ae-board-source-toggle-group"
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 2,
        background: k.surfaceHover,
        padding: 1,
        borderRadius: 4,
        border: `1px solid ${k.border}`,
        height: 22,
        boxSizing: 'border-box',
        flexShrink: 0,
      }}
    >
      {([
        { id: 'all' as const, label: 'Both', count: totalCount },
        { id: 'ae' as const, label: 'AE Model', count: aeCount },
        { id: 'spot' as const, label: 'Spot Scan', count: spotCount },
      ]).map((item) => {
        const active = activeSourceFilter === item.id;
        return (
          <button
            key={item.id}
            type="button"
            role="tab"
            aria-selected={active}
            aria-label={`${item.label} filter`}
            onClick={() => handleSourceChange(item.id)}
            style={{
              border: 0,
              background: active ? k.blue : 'transparent',
              color: active ? '#ffffff' : k.dim,
              fontWeight: active ? 700 : 500,
              borderRadius: 3,
              padding: '0 7px',
              height: 18,
              fontSize: 9,
              fontFamily: 'inherit',
              letterSpacing: '.04em',
              textTransform: 'uppercase',
              cursor: 'pointer',
              whiteSpace: 'nowrap',
              display: 'inline-flex',
              alignItems: 'center',
              gap: 4,
              transition: 'all 0.12s ease',
            }}
            data-testid={`ae-board-source-${item.id}`}
            data-active={active}
            title={`Filter signals: ${item.label} (${item.count}). Click to toggle.`}
          >
            <span>{item.label}</span>
            <span style={{ opacity: active ? 0.9 : 0.6, fontSize: 8.5, fontVariantNumeric: 'tabular-nums' }}>
              {item.count}
            </span>
          </button>
        );
      })}
    </div>
  );

  const currentVersion = engineCfg?.config?.strategy_version === 'v1_baseline' ? 'v1_baseline' : 'v2_hardened';
  const isV2 = currentVersion === 'v2_hardened';

  const versionToggle = (
    <button
      type="button"
      title={`Adaptive Edge Engine: ${isV2 ? 'V2 Production-Hardened (Click to switch to V1 Legacy)' : 'V1 Legacy Baseline (Click to switch to V2 Hardened)'}`}
      data-testid="ae-board-version-toggle"
      onClick={() => {
        if (!setEngineCfg.isPending && engineCfg?.config) {
          setEngineCfg.mutate({
            ...engineCfg.config,
            strategy_version: isV2 ? 'v1_baseline' : 'v2_hardened',
          });
        }
      }}
      style={{
        border: `1px solid ${isV2 ? 'rgba(46, 125, 50, 0.4)' : k.border}`,
        background: isV2 ? 'rgba(46, 125, 50, 0.12)' : k.surfaceHover,
        color: isV2 ? 'var(--k-green, #2e7d32)' : k.dim,
        fontWeight: 750,
        borderRadius: 4,
        padding: '0 8px',
        height: 22,
        fontSize: 9.5,
        letterSpacing: '.04em',
        cursor: 'pointer',
        display: 'inline-flex',
        alignItems: 'center',
        gap: 4,
        boxSizing: 'border-box',
        flexShrink: 0,
        transition: 'all 0.12s ease',
      }}
    >
      <span
        style={{
          width: 5,
          height: 5,
          borderRadius: '50%',
          background: isV2 ? 'var(--k-green, #2e7d32)' : 'var(--k-amber, #f57c00)',
          display: 'inline-block',
        }}
      />
      {isV2 ? 'V2 HARDENED' : 'V1 LEGACY'}
    </button>
  );

  const simNowMs = useSimNowMs();
  const isHistoricalSim = simNowMs != null && sessionDayKey(simNowMs) !== sessionDayKey(Date.now());

  return (
    <div>
      <BoardFilters view={view} columns={BOARD_COLUMNS}>
        {sourceToggles}
        {versionToggle}
      </BoardFilters>
      <SignalBoard
        renderTrade={rowActions.renderTrade}
        renderChart={rowActions.renderChart}
        signals={view.visible}
        columns={BOARD_COLUMNS}
        hidden={view.hidden}
        openId={openId}
        onToggle={(id) => setOpenId((p) => (p === id ? null : id))}
        renderDetail={(sig) => <BoardTicket signal={sig} tag="ADAPTIVE_EDGE" />}
        onOpenDetail={onOpenDetail}
        sort={sort}
        onSortChange={setSort}
        collapsedGroups={collapsedGroups}
        onToggleGroup={toggleGroup}
        collapseOlderDays={true}
        nowMs={nowMs}
        isHistoricalSim={isHistoricalSim}
        emptyLabel={
          view.counts.total
            ? 'Every row is filtered out. Clear the search or include ended positions.'
            : activeSourceFilter !== 'all'
              ? `No ${activeSourceFilter === 'ae' ? 'AE Model' : 'Spot Scan'} signals found. Try selecting Both.`
              : 'Adaptive Edge has not surfaced a signal yet.'
        }
      />
    </div>
  );
}
