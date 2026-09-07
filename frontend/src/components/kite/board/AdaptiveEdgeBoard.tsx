/**
 * Adaptive Edge, on the shared board.
 *
 * The standalone Adaptive Edge page keeps its own richer panel — it has the
 * width for per-underlying grouping and the mode-history detail. This is the
 * sidebar view, where the point is that all three engines read the same way.
 */
import React from 'react';
import { useAdaptiveEdgeSnapshot } from '../../../hooks/useAdaptiveEdge';
import { rowsFromSnapshot } from '../AdaptiveEdgePanel';
import { adaptiveEdgeToBoard } from './adaptiveEdgeAdapter';
import { BOARD_COLUMNS, DEFAULT_SORT, SignalBoard } from './SignalBoard';
import { useBoardRowActions } from './useBoardRowActions';
import { BoardFilters } from './BoardFilters';
import { BoardTicket } from './BoardTicket';
import { useBoardView } from './useBoardView';
import type { BoardSignal } from './boardTypes';
import { k } from '../../../styles/kiteUI';

export function AdaptiveEdgeBoard({ nowMs, onOpenDetail, onOpenChart }: {
  /** Opens this row's instrument in the chart pane. Without it the Chart column is empty. */
  onOpenChart?: (quoteKey: string) => void;
  nowMs?: number;
  onOpenDetail?: (signal: BoardSignal) => void;
}) {
  // Buy/Sell and the chart, built from the signal alone — same on every board.
  const rowActions = useBoardRowActions({ onOpenChart });
  const snapshot = useAdaptiveEdgeSnapshot();
  const signals = React.useMemo(
    () => (snapshot.data ? adaptiveEdgeToBoard(rowsFromSnapshot(snapshot.data)) : []),
    [snapshot.data],
  );

  const view = useBoardView(signals, { endedByDefault: true, storageKey: 'adaptive_edge', nowMs });
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

  return (
    <div>
      <BoardFilters view={view} columns={BOARD_COLUMNS} />
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
        emptyLabel={
          view.counts.total
            ? 'Every row is filtered out. Clear the search or include ended positions.'
            : 'Adaptive Edge has not surfaced a signal yet.'
        }
      />
    </div>
  );
}
