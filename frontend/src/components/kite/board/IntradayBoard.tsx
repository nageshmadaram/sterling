/**
 * The intraday pack, on the shared board.
 *
 * Three strategies share one tab because they share a universe, a timeframe and
 * a contract picker, and differ only in what makes them fire. Three tabs would
 * be three copies of the same board; instead the row says which rule produced
 * it, and a filter above the rows narrows to one when that is what you want.
 *
 * Two things this board states that the others do not need to:
 *
 *  - **Nothing here is calibrated.** No walk-forward run has been done on any of
 *    the three, so every number on every row came from a judgement call. That is
 *    not a footnote for a document; it belongs in front of someone deciding
 *    whether to act on a row.
 *  - **The levels are in SPOT points, not premium.** Each strategy states its
 *    stop in the underlying — a candle's low, the slow EMA, VWAP — and a
 *    5-minute stop read as an hourly one is a trade sized several times too
 *    large.
 */
import React from 'react';
import {
  useIntradayArm, useIntradayExit, useIntradayReconcile, useIntradayScan,
  useIntradaySnapshot, useIntradaySquareOff, type IntradayStrategyId,
} from '../../../hooks/useIntraday';
import { intradayToBoard } from './intradayAdapter';
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

/**
 * The server's signal id, back out of the board row id.
 *
 * The row id is prefixed so positions and candidates cannot collide; the arm
 * route takes the id the SCAN minted, because arming something the scan did not
 * produce is exactly what it refuses.
 */
function signalIdOf(rowId: string): string {
  return rowId.replace(/^intraday:/, '');
}

const chip = (on: boolean): React.CSSProperties => ({
  background: on ? 'color-mix(in srgb, var(--k-blue) 16%, transparent)' : 'transparent',
  border: `1px solid ${on ? k.blue : k.border}`,
  color: on ? k.text : k.dim,
  borderRadius: 999, padding: '2px 9px', fontSize: 10.5, cursor: 'pointer',
});

export function IntradayBoard({ nowMs, onOpenDetail, onOpenChart }: {
  /** Opens this row's instrument in the chart pane. Without it the Chart column is empty. */
  onOpenChart?: (quoteKey: string) => void;
  nowMs?: number;
  onOpenDetail?: (signal: BoardSignal) => void;
}) {
  const rowActions = useBoardRowActions({ onOpenChart });
  const snapshot = useIntradaySnapshot(true, 5000);
  const scan = useIntradayScan();
  const arm = useIntradayArm();
  const exitOne = useIntradayExit();
  const squareOff = useIntradaySquareOff();
  const reconcile = useIntradayReconcile();
  const data = snapshot.data;
  const isReplay = data?.source === 'simulation';
  const openCount = data?.open_positions ?? 0;
  const record = data?.record;
  const blocker = data?.entry_blocker ?? null;

  const [only, setOnly] = React.useState<IntradayStrategyId | null>(null);
  const all = React.useMemo(() => intradayToBoard(data), [data]);
  const signals = React.useMemo(
    () => (only ? all.filter((s) => s.id.startsWith(`intraday:${only}:`)) : all),
    [all, only],
  );
  const view = useBoardView(signals, { endedByDefault: true, storageKey: 'intraday', nowMs });
  const [openId, setOpenId] = React.useState<string | null>(null);

  if (snapshot.isLoading && !data) return <p style={note}>Loading intraday strategies…</p>;
  if (snapshot.error) {
    return <p style={{ ...note, color: k.red }}>
      Unavailable: {(snapshot.error as Error).message}
    </p>;
  }

  const metas = data?.strategy?.strategies ?? [];
  const enabled = new Set<string>(data?.enabled_strategies ?? []);
  const armed = signals.filter((s) => s.status === 'armed');
  const countFor = (id: string) => all.filter((s) => s.id.startsWith(`intraday:${id}:`)).length;

  return (
    <div>
      <div style={{
        display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap',
        padding: '8px 12px', borderBottom: `1px solid ${k.border}`,
      }}>
        <button
          type="button"
          onClick={() => scan.mutate()}
          disabled={scan.isPending || isReplay}
          title={isReplay
            ? 'Replay is driving this board — live scan is off'
            : 'Run one candles → rules pass over the selected universe'}
          style={{
            background: 'transparent', border: `1px solid ${k.border}`, color: k.text,
            borderRadius: 6, padding: '4px 10px', fontSize: 11,
            cursor: (scan.isPending || isReplay) ? 'progress' : 'pointer',
            opacity: isReplay ? 0.55 : 1,
          }}
        >
          {scan.isPending ? 'Scanning…' : 'Scan now'}
        </button>
        {openCount > 0 && (
          <button
            type="button"
            onClick={() => squareOff.mutate()}
            disabled={squareOff.isPending || isReplay}
            title="Close every position this engine holds, now"
            style={{
              background: 'transparent', border: `1px solid ${k.red}`, color: k.red,
              borderRadius: 6, padding: '4px 10px', fontSize: 11, cursor: 'pointer',
            }}
          >
            {squareOff.isPending ? 'Closing…' : `Square off ${openCount}`}
          </button>
        )}
        <button
          type="button"
          onClick={() => reconcile.mutate()}
          disabled={reconcile.isPending || isReplay}
          title="Re-sync against Zerodha: close what it no longer holds, re-protect the rest"
          style={{
            background: 'transparent', border: `1px solid ${k.border}`, color: k.dim,
            borderRadius: 6, padding: '4px 10px', fontSize: 11, cursor: 'pointer',
          }}
        >
          {reconcile.isPending ? 'Syncing…' : 'Reconcile'}
        </button>
        <span style={{ fontSize: 11, color: k.dim }}>
          {data?.scanned ?? 0} names · {data?.config?.timeframe ?? '5m'} candles
          {/* Read from the account and the shared engine, not from this
              strategy's config — there is no copy here to go stale. */}
          {data?.mode ? (
            <>
              {' · '}
              <strong style={{ color: data.mode.is_paper === false ? k.green : k.amber }}>
                {data.mode.is_paper === false ? 'LIVE' : 'PAPER'}
              </strong>
              {' · '}
              <strong style={{ color: data.mode.auto_execute ? k.text : k.dim }}>
                {data.mode.auto_execute ? 'AUTO' : 'MANUAL'}
              </strong>
            </>
          ) : null}
          {data?.config?.enabled === false ? ' · disabled' : ''}
        </span>
        {armed.length > 0 && (
          <span style={{ fontSize: 11, color: k.green, marginLeft: 'auto' }}>
            {armed.length} armed — open a row to see the trade
          </span>
        )}
      </div>

      {/* Never validated, said once, above the rows. */}
      <div style={{
        ...note, borderBottom: `1px solid ${k.border}`,
        background: 'color-mix(in srgb, var(--k-amber) 8%, transparent)',
      }}>
        <strong style={{ color: k.amber }}>NOT VALIDATED</strong>{' '}
        No walk-forward run has been done on these three, so every threshold is a
        judgement call. Levels below are the UNDERLYING's points, not premium.
      </div>

      {metas.length > 0 && (
        <div style={{
          display: 'flex', gap: 6, flexWrap: 'wrap', padding: '6px 12px',
          borderBottom: `1px solid ${k.border}`,
        }}>
          <button type="button" style={chip(only === null)} onClick={() => setOnly(null)}>
            All {all.length}
          </button>
          {metas.map((m) => (
            <button
              key={m.id}
              type="button"
              title={enabled.has(m.id) ? m.how_it_works : `${m.name} is switched off in settings`}
              style={{ ...chip(only === m.id), opacity: enabled.has(m.id) ? 1 : 0.5 }}
              onClick={() => setOnly((p) => (p === m.id ? null : m.id))}
            >
              {m.name} {countFor(m.id)}
            </button>
          ))}
        </div>
      )}

      {record && record.trades > 0 && (
        <div style={{
          display: 'flex', flexWrap: 'wrap', gap: '4px 18px', padding: '6px 12px',
          fontSize: 11, color: k.dim, borderBottom: `1px solid ${k.border}`,
          fontVariantNumeric: 'tabular-nums',
        }}>
          <span>Today <b style={{ color: k.text }}>{record.trades}</b> trades</span>
          <span>Win rate <b style={{ color: k.text }}>
            {record.win_rate == null ? '—' : `${record.win_rate.toFixed(0)}%`}</b></span>
          <span>Realised <b style={{ color: record.realised_inr >= 0 ? k.green : k.red }}>
            ₹{Math.round(record.realised_inr)}</b></span>
          {record.consecutive_losses > 0 && (
            <span style={{ color: k.amber }}>
              {record.consecutive_losses} consecutive losses — size is de-scaled
            </span>
          )}
        </div>
      )}

      {/* Why the next entry would be refused, answered BEFORE a click rather
          than after one. */}
      {blocker && (
        <p style={{ ...note, color: k.amber }}>No new entries: {blocker}</p>
      )}

      {arm.data && !arm.data.ok && (
        <p style={{ ...note, color: k.amber }}>Not entered — {arm.data.message}</p>
      )}
      {arm.error && (
        <p style={{ ...note, color: k.red }}>Entry failed: {(arm.error as Error).message}</p>
      )}
      {squareOff.error && (
        <p style={{ ...note, color: k.red }}>
          Square off failed: {(squareOff.error as Error).message}
        </p>
      )}
      {reconcile.data?.error && (
        <p style={{ ...note, color: k.red }}>Reconcile: {reconcile.data.error}</p>
      )}

      {data?.warnings?.length ? (
        <ul style={{ ...note, paddingLeft: 26, color: k.amber }}>
          {data.warnings.map((w) => <li key={w}>{w}</li>)}
        </ul>
      ) : null}

      {scan.error && (
        <p style={{ ...note, color: k.red }}>Scan failed: {(scan.error as Error).message}</p>
      )}
      {data?.last_error && (
        <p style={{ ...note, color: k.red }}>Last scan: {data.last_error}</p>
      )}
      {data?.failures?.length ? (
        <p style={note}>{data.failures.length} symbol(s) could not be evaluated: {data.failures[0]}</p>
      ) : null}

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
            <BoardTicket signal={sig} tag="INTRADAY" />
            {sig.status === 'armed' && !isReplay && (
              <button
                type="button"
                onClick={() => arm.mutate(signalIdOf(sig.id))}
                disabled={arm.isPending || !!blocker}
                title={blocker || 'Size against capital, buy, and rest a stop at the broker'}
                style={{
                  margin: '8px 12px', background: 'transparent',
                  border: `1px solid ${blocker ? k.border : k.green}`,
                  color: blocker ? k.dim : k.green,
                  borderRadius: 6, padding: '4px 12px', fontSize: 11,
                  cursor: blocker ? 'not-allowed' : 'pointer',
                }}
              >
                {arm.isPending ? 'Buying…' : `Buy ${sig.instrument.symbol}`}
              </button>
            )}
            {sig.status === 'running' && !isReplay && (
              <button
                type="button"
                onClick={() => exitOne.mutate(sig.instrument.symbol)}
                disabled={exitOne.isPending}
                /* Always available. Auto gates OPENING; an operator must
                   always be able to close. */
                style={{
                  margin: '8px 12px', background: 'transparent',
                  border: `1px solid ${k.red}`, color: k.red,
                  borderRadius: 6, padding: '4px 12px', fontSize: 11, cursor: 'pointer',
                }}
              >
                {exitOne.isPending ? 'Closing…' : `Close ${sig.instrument.symbol}`}
              </button>
            )}
          </div>
        )}
        onOpenDetail={onOpenDetail}
        nowMs={nowMs}
        emptyLabel="No setup on the last closed candle. Run a scan, or widen the universe in settings."
      />
    </div>
  );
}
