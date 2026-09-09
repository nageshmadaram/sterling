/**
 * Gamma Move, on the shared board.
 *
 * Two things this board does that the others do not need to:
 *
 *  - it carries the **scan** control, because this engine's rows only exist
 *    after a levels → strikes → trigger pass, and an operator looking at an
 *    empty board needs the reason and the remedy in the same place;
 *  - it states the **calibration finding above the rows**. The entry trigger on
 *    its own showed no edge; the edge was the level filter. That is not a
 *    footnote for a document — it is the thing that should be in front of
 *    someone deciding whether to act on a row.
 *
 * Polling is conditional. A strategy with nothing open has no live state worth
 * a request every few seconds.
 */
import React from 'react';
import {
  useArmGammaMove, useGammaMoveScan, useGammaMoveSnapshot,
} from '../../../hooks/useGammaMove';
import type { GammaScanState, GammaTradeRecord } from '../../../hooks/useGammaMove';
import { gammaMoveToBoard } from './gammaMoveAdapter';
import { BOARD_COLUMNS, SignalBoard } from './SignalBoard';
import { useBoardRowActions } from './useBoardRowActions';
import { BoardFilters } from './BoardFilters';
import { BoardTicket } from './BoardTicket';
import { useBoardView } from './useBoardView';
import type { BoardSignal } from './boardTypes';
import { k } from '../../../styles/kiteUI';

const note: React.CSSProperties = {
  padding: '10px 12px', margin: 0, fontSize: 11, color: k.dim, lineHeight: 1.6,
};

/** What the scan cost, per stage. The funnel is the reason this engine can run
 *  at all, so its shape is worth being able to see degrade. */
function ScanCost({ scan }: { scan?: GammaScanState }) {
  if (!scan?.stage_a) return null;
  const a = scan.stage_a, b = scan.stage_b, c = scan.stage_c;
  return (
    <div style={{
      display: 'flex', flexWrap: 'wrap', gap: '4px 16px', padding: '6px 12px',
      fontSize: 11, color: k.dim, borderBottom: `1px solid ${k.border}`,
      fontVariantNumeric: 'tabular-nums',
    }}>
      <span>Scanned <b style={{ color: k.text }}>{a.scanned}</b> names</span>
      <span>→ <b style={{ color: k.text }}>{a.near_level}</b> at a level</span>
      <span>→ <b style={{ color: k.text }}>{b?.candidates ?? 0}</b> strikes</span>
      <span>→ <b style={{ color: k.text }}>{c?.armed ?? 0}</b> armed of {c?.watched ?? 0}</span>
      <span style={{ marginLeft: 'auto' }}>
        {c?.historical_requests ?? 0} history calls · {scan.total_seconds ?? 0}s
      </span>
    </div>
  );
}

/** The realised record. The break-even threshold travels with the win rate,
 *  because a win rate on its own is not an answer. */
function Record({ record }: { record?: GammaTradeRecord }) {
  if (!record || !record.trades) {
    return <p style={note}>No closed trades yet.</p>;
  }
  const tone = record.realised_inr >= 0 ? k.green : k.red;
  return (
    <div style={{
      display: 'flex', flexWrap: 'wrap', gap: '4px 18px', padding: '8px 12px',
      fontSize: 11, color: k.dim, borderTop: `1px solid ${k.border}`,
      fontVariantNumeric: 'tabular-nums',
    }}>
      <span>Trades <b style={{ color: k.text }}>{record.trades}</b></span>
      <span>Win rate <b style={{ color: k.text }}>
        {record.win_rate == null ? '—' : `${record.win_rate.toFixed(1)}%`}</b></span>
      <span>Realised <b style={{ color: tone }}>₹{record.realised_inr.toFixed(0)}</b></span>
      {record.consecutive_losses > 0 && (
        <span style={{ color: k.amber }}>
          {record.consecutive_losses} consecutive losses — size is de-scaled
        </span>
      )}
    </div>
  );
}

export function GammaMoveBoard({ nowMs, onOpenDetail, onOpenChart }: {
  /** Opens this row's instrument in the chart pane. Without it the Chart column is empty. */
  onOpenChart?: (quoteKey: string) => void;
  nowMs?: number;
  onOpenDetail?: (signal: BoardSignal) => void;
}) {
  // Buy/Sell and the chart, built from the signal alone — same on every board.
  const rowActions = useBoardRowActions({ onOpenChart });
  const [pollMs, setPollMs] = React.useState(0);
  const snapshot = useGammaMoveSnapshot(true, pollMs);
  const scan = useGammaMoveScan();
  const arm = useArmGammaMove();

  const data = snapshot.data;
  const open = (data?.positions?.length ?? 0) > 0;
  React.useEffect(() => { setPollMs(open ? 3000 : 0); }, [open]);

  const signals = React.useMemo(() => gammaMoveToBoard(data), [data]);
  const view = useBoardView(signals, { endedByDefault: true, storageKey: 'gamma_move', nowMs });
  const [openId, setOpenId] = React.useState<string | null>(null);

  if (snapshot.isLoading && !data) return <p style={note}>Loading Gamma Move…</p>;
  if (snapshot.error) {
    return <p style={{ ...note, color: k.red }}>
      Unavailable: {(snapshot.error as Error).message}
    </p>;
  }

  const blockers = data?.blockers ?? [];
  const armedRows = signals.filter((s) => s.status === 'armed');

  return (
    <div>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap',
        padding: '8px 12px', borderBottom: `1px solid ${k.border}`,
      }}>
        <button
          type="button"
          onClick={() => scan.mutate()}
          disabled={scan.isPending}
          title="Run one levels → strikes → trigger pass over the F&O universe"
          style={{
            background: 'transparent', border: `1px solid ${k.border}`, color: k.text,
            borderRadius: 6, padding: '4px 10px', fontSize: 11,
            cursor: scan.isPending ? 'progress' : 'pointer',
          }}
        >
          {scan.isPending ? 'Scanning…' : 'Scan now'}
        </button>
        {data?.universe && (
          <span style={{ fontSize: 11, color: k.dim }}>
            {data.universe.underlyings} names ·{' '}
            {/* Read from the account and the engine, not from this strategy's
                config — there is no copy here to go stale. */}
            <strong style={{ color: data.mode?.is_paper === false ? k.green : k.amber }}>
              {data.mode?.is_paper === false ? 'LIVE' : 'PAPER'}
            </strong>{' · '}
            <strong style={{ color: data.mode?.auto_execute ? k.text : k.dim }}>
              {data.mode?.auto_execute ? 'AUTO' : 'MANUAL'}
            </strong>
            {(data?.config?.enabled ?? data?.strategy?.enabled) !== false ? '' : ' · disabled'}
          </span>
        )}
        {armedRows.length > 0 && (
          <span style={{ fontSize: 11, color: k.green, marginLeft: 'auto' }}>
            {armedRows.length} armed — open a row to buy it
          </span>
        )}
      </div>

      {/* The finding, before the rows, in the order a person reads it: what it
          means, then what to do, then the numbers on hover.
          It used to be a line of confidence intervals across the top of a
          trading screen — a paper abstract in the place where someone is
          deciding whether to click Buy. The evidence has not gone anywhere; it
          has stopped being the first thing shouted. */}
      {data?.strategy?.headline_finding && (
        <div
          title={data.strategy.evidence ?? undefined}
          style={{
            ...note, borderBottom: `1px solid ${k.border}`,
            background: 'color-mix(in srgb, var(--k-amber) 8%, transparent)',
            cursor: data.strategy.evidence ? 'help' : undefined,
          }}
        >
          <div>
            <strong style={{ color: k.amber }}>NOT VALIDATED</strong>{' '}
            {data.strategy.headline_finding}
          </div>
          {data.strategy.what_to_do && (
            <div style={{ marginTop: 3, color: k.text }}>{data.strategy.what_to_do}</div>
          )}
          {data.strategy.evidence && (
            <div style={{ marginTop: 3, color: k.dim, fontSize: 10.5 }}>
              Hover for the measurement.
            </div>
          )}
        </div>
      )}

      <ScanCost scan={data?.scan} />

      {scan.error && (
        <p style={{ ...note, color: k.red }}>Scan failed: {(scan.error as Error).message}</p>
      )}
      {arm.data && !arm.data.ok && (
        <p style={{ ...note, color: k.amber }}>Not entered — {arm.data.message}</p>
      )}
      {arm.error && (
        <p style={{ ...note, color: k.red }}>Entry failed: {(arm.error as Error).message}</p>
      )}

      {signals.length === 0 && blockers.length > 0 && (
        <ul style={{ ...note, paddingLeft: 26 }}>
          {blockers.map((b) => <li key={b}>{b}</li>)}
        </ul>
      )}

      <Record record={data?.record} />

      {signals.length > 0 && <BoardFilters view={view} columns={BOARD_COLUMNS} />}
      <SignalBoard
        renderTrade={rowActions.renderTrade}
        renderChart={rowActions.renderChart}
        signals={view.visible}
        columns={BOARD_COLUMNS}
        hidden={view.hidden}
        openId={openId}
        onToggle={(id) => setOpenId((p) => (p === id ? null : id))}
        renderDetail={(sig) => (
          <div>
            <BoardTicket signal={sig} tag="GAMMA_MOVE" />
            {sig.status === 'armed' && (
              <button
                type="button"
                onClick={() => arm.mutate(sig.id)}
                disabled={arm.isPending}
                style={{
                  margin: '8px 12px', background: 'transparent',
                  border: `1px solid ${k.green}`, color: k.green,
                  borderRadius: 6, padding: '4px 12px', fontSize: 11, cursor: 'pointer',
                }}
              >
                {arm.isPending ? 'Buying…' : `Buy ${sig.sizing.quantity ?? ''} ${sig.instrument.symbol}`}
              </button>
            )}
          </div>
        )}
        onOpenDetail={onOpenDetail}
        nowMs={nowMs}
        emptyLabel="Nothing at a level right now. Run a scan, or widen the universe in settings."
      />
    </div>
  );
}
