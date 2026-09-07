import React from 'react';
import {
  useBearToBearishScan,
  useBearToBearishSnapshot,
  useExecuteBearToBearishOrder,
  useUpdateBearToBearishConfig,
} from '../../../hooks/useBearToBearish';
import { bearToBearishToBoard } from './bearToBearishAdapter';
import { BOARD_COLUMNS, DEFAULT_SORT, SignalBoard, type ColumnId, type SortState } from './SignalBoard';
import { SIGNAL_COL_TO_BOARD, type SignalColKey } from './signalRowSpec';
import { useBoardRowActions } from './useBoardRowActions';
import { BoardFilters } from './BoardFilters';
import { BoardTicket } from './BoardTicket';
import { useBoardView } from './useBoardView';
import type { BoardSignal } from './boardTypes';
import { useKiteSettings } from '../../../store/useKiteSettings';
import { KiteActionButtons } from '../KiteActionButtons';
import { k } from '../../../styles/kiteUI';

const note: React.CSSProperties = {
  padding: '12px 14px',
  margin: 0,
  fontSize: 11,
  color: k.dim,
  lineHeight: 1.6,
};

export function BearToBearishBoard({
  nowMs,
  onOpenDetail,
  onOpenChart,
}: {
  onOpenChart?: (quoteKey: string) => void;
  nowMs?: number;
  onOpenDetail?: (signal: BoardSignal) => void;
}) {
  const s = useKiteSettings();
  const rowActions = useBoardRowActions({ onOpenChart });
  const [openId, setOpenId] = React.useState<string | null>(null);
  const [sort, setSort] = React.useState<SortState>(DEFAULT_SORT);
  const [collapsedGroups, setCollapsedGroups] = React.useState<ReadonlySet<string>>(new Set());

  const snapshot = useBearToBearishSnapshot(true, 3000);
  const executeOrder = useExecuteBearToBearishOrder();

  const data = snapshot.data;
  const signals = React.useMemo(() => bearToBearishToBoard(data), [data]);
  const view = useBoardView(signals, { endedByDefault: true, storageKey: 'bear_to_bearish', nowMs });

  const columns = BOARD_COLUMNS;

  const hidden = React.useMemo(
    () => new Set(
      s.hiddenSignalCols
        .map((key) => SIGNAL_COL_TO_BOARD[key as SignalColKey])
        .filter(Boolean) as ColumnId[],
    ),
    [s.hiddenSignalCols],
  );

  const toggleGroup = React.useCallback((id: string) => {
    setCollapsedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  if (snapshot.isLoading && !data) return <p style={note}>Loading Bear to Bearish Strategy...</p>;
  if (snapshot.error) {
    return (
      <p style={{ ...note, color: k.red }}>
        Unavailable: {(snapshot.error as Error).message}
      </p>
    );
  }

  return (
    <div>
      <BoardFilters view={view} columns={columns} />

      <SignalBoard
        renderTrade={rowActions.renderTrade}
        renderChart={rowActions.renderChart}
        signals={view.visible}
        columns={columns}
        hidden={hidden}
        openId={openId}
        onToggle={(id) => setOpenId((p) => (p === id ? null : id))}
        sort={sort}
        onSortChange={setSort}
        collapsedGroups={collapsedGroups}
        onToggleGroup={toggleGroup}
        renderDetail={(sig) => (
          <div style={{ padding: '8px 0' }}>
            <BoardTicket signal={sig} tag="BEAR_TO_BEARISH" />
            {sig.status === 'armed' && (
              <div style={{ padding: '8px 12px', display: 'flex', alignItems: 'center', gap: 10 }}>
                <button
                  type="button"
                  onClick={() => executeOrder.mutate(sig.id)}
                  disabled={executeOrder.isPending}
                  style={{
                    background: 'rgba(46,160,67,0.18)',
                    border: `1px solid ${k.green}`,
                    color: k.green,
                    borderRadius: 6,
                    padding: '5px 14px',
                    fontSize: 11,
                    fontWeight: 700,
                    cursor: 'pointer',
                  }}
                >
                  {executeOrder.isPending ? 'Executing...' : `⚡ Execute Order: ${sig.direction.toUpperCase()} 1 Lot ${sig.instrument.symbol}`}
                </button>
              </div>
            )}
          </div>
        )}
        onOpenDetail={onOpenDetail}
        nowMs={nowMs}
        emptyLabel={
          view.counts.total
            ? 'Every row is filtered out. Clear the search or include ended positions.'
            : 'No active Bear to Bearish setup found.'
        }
      />
    </div>
  );
}
