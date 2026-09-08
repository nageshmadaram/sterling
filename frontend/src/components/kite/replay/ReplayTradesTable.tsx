import React, { memo, useMemo, useRef, useState } from 'react';
import {
  ReplayTrade,
  useFilteredReplayTrades,
  useReplayState,
  useReplayStore,
} from '../../../hooks/useReplayStore';
import { useReplayTransport } from '../../../hooks/useReplayTransport';
import { EmptyState } from './primitives/EmptyState';
import { SkeletonRows } from './primitives/Skeleton';
import { tradesHaveFriction } from './replayColumns';
import {
  ABSENT,
  fmtDuration,
  fmtInr,
  fmtInt,
  fmtSignedInr,
  fmtSignedPct,
  fmtTime,
} from './replayFormat';
import { strategyKey, strategyLabel, strategyTone } from './replayStrategies';
import { useVirtualRows } from './useVirtualRows';
import * as Icons from './ReplayIcons';

const ROW_H = 28;
const VIRTUALISE_ABOVE = 200;

export type TradeGroupBy = 'none' | 'strategy' | 'contract';

const TradeRow = memo(function TradeRow({
  t,
  hasFriction,
  colSpanBase: _colSpanBase,
}: {
  t: ReplayTrade;
  hasFriction: boolean;
  colSpanBase?: number;
}) {
  const open = t.status === 'OPEN';
  const win = t.status === 'WIN';

  return (
    <tr
      className="rd-tr"
      data-tone={open ? 'open' : win ? 'profit' : 'loss'}
      data-status={t.status.toLowerCase()}
      tabIndex={0}
    >
      <td data-col="id" className="rd-mono" style={{ color: 'var(--k-blue)' }}>
        {t.trade_id}
      </td>
      <td className="rd-num" style={{ color: 'var(--k-dim)' }}>
        {fmtTime(t.entry_time_iso)}
      </td>
      <td data-col="out" className="rd-num">
        {open ? (
          <span className="rd-status-chip" data-tone="open">OPEN</span>
        ) : (
          fmtTime(t.exit_time_iso)
        )}
      </td>
      <td data-align="right" data-col="held" className="rd-num" style={{ color: 'var(--k-dim)' }}>
        {fmtDuration(t.duration_mins)}
      </td>
      <td>
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, color: strategyTone(t.strategy) }}>
          <span className="rd-dot-tone" />
          <span style={{ color: 'var(--k-text)', fontWeight: 600 }}>{strategyLabel(t.strategy)}</span>
        </span>
      </td>
      <td>
        <strong>{t.symbol}</strong>
        {t.underlying && (
          <span className="rd-sub">
            {t.underlying}{t.strike ? ` · ${t.strike} ${t.opt_type || ''}` : ''}
          </span>
        )}
      </td>
      <td data-align="right" data-col="size" className="rd-num">
        {fmtInt(t.lots)}L<span className="rd-sub">{fmtInt(t.quantity)} qty</span>
      </td>
      <td data-align="right" className="rd-num">
        {fmtInr(t.entry_price)}
        {hasFriction && t.raw_entry != null && t.raw_entry !== t.entry_price && (
          <span className="rd-sub" title="Theoretical signal price before spread and slippage">
            raw {fmtInr(t.raw_entry)}
          </span>
        )}
      </td>
      <td data-align="right" className="rd-num">
        {t.exit_price == null ? (
          open && t.quantity > 0 && t.pnl_usd != null && t.pnl_usd !== 0 ? (
            <span
              className="rd-sub"
              style={{
                color: t.pnl_usd >= 0 ? 'var(--k-green, #10b981)' : 'var(--k-red, #ef4444)',
                fontWeight: 500,
              }}
            >
              ~{t.pnl_usd >= 0 ? '+' : ''}{fmtInr(t.pnl_usd / t.quantity)} pts
            </span>
          ) : (
            <span className="rd-absent">{ABSENT}</span>
          )
        ) : (
          <>
            {fmtInr(t.exit_price)}
            {t.entry_price != null && (
              <span
                className="rd-sub"
                style={{
                  color: (t.exit_price - t.entry_price) >= 0 ? 'var(--k-green, #10b981)' : 'var(--k-red, #ef4444)',
                  fontWeight: 500,
                }}
              >
                {(t.exit_price - t.entry_price) >= 0 ? '+' : ''}{fmtInr(t.exit_price - t.entry_price)} pts
              </span>
            )}
            {hasFriction && t.raw_exit != null && t.raw_exit !== t.exit_price && (
              <span className="rd-sub" title="Theoretical target or stop before spread and slippage">
                raw {fmtInr(t.raw_exit)}
              </span>
            )}
          </>
        )}
      </td>
      <td data-align="right" data-col="sltgt" className="rd-num">
        <span className="rd-sl">{fmtInr(t.stop_loss)}</span>
        <span className="rd-sub rd-tp">{fmtInr(t.target_price)}</span>
      </td>
      {hasFriction && (
        <td data-align="right" data-col="slip" className="rd-num">
          {t.slippage == null || t.slippage === 0 ? (
            <span className="rd-absent">{ABSENT}</span>
          ) : (
            <span className="rd-sl">{fmtSignedInr(-t.slippage)}</span>
          )}
        </td>
      )}
      <td data-align="center">
        <span className="rd-status-chip" data-tone={open ? 'open' : win ? 'win' : 'loss'}>
          {t.status}
        </span>
      </td>
      <td data-align="right" className="rd-num rd-pnl" data-tone={t.pnl_usd >= 0 ? 'profit' : 'loss'}>
        <span className={open ? 'rd-unrealised' : undefined} title={open ? 'Unrealised — the position is still open' : undefined}>
          {open ? '~' : ''}{fmtSignedInr(t.pnl_usd)}
        </span>
        <span className="rd-sub">{fmtSignedPct(t.pnl_pct)}</span>
      </td>
    </tr>
  );
});

/**
 * The executed-trades table.
 *
 * The friction columns exist only when at least one trade carries measured
 * friction. The table this replaces rendered a `Slippage` column whose else
 * branch printed a hardcoded `₹0.00`, against a backend that computed nothing —
 * so it reported that every fill was free. A column you cannot fill is a
 * column you do not render.
 */
export const ReplayTradesTable = memo(function ReplayTradesTable() {
  const trades = useFilteredReplayTrades();
  const rawTrades = useReplayStore((s) => s.status.stats.trades);
  const rawPnl = useReplayStore((s) => s.status.stats.pnl);
  const rawWins = useReplayStore((s) => s.status.stats.wins);
  const rawLosses = useReplayStore((s) => s.status.stats.losses);
  const rawDrag = useReplayStore((s) => s.status.stats.slippage_total);
  const isNarrowed = trades.length !== rawTrades.length;

  const closedTrades = trades.filter((t) => t.status === 'WIN' || t.status === 'LOSS');
  const pnl = isNarrowed
    ? Number(closedTrades.reduce((sum, t) => sum + (t.pnl_usd || 0), 0).toFixed(2))
    : rawPnl;
  const wins = isNarrowed ? closedTrades.filter((t) => t.status === 'WIN').length : rawWins;
  const losses = isNarrowed ? closedTrades.filter((t) => t.status === 'LOSS').length : rawLosses;
  const drag = isNarrowed
    ? trades.some((t) => t.slippage != null)
      ? Number(trades.reduce((sum, t) => sum + (t.slippage || 0), 0).toFixed(2))
      : null
    : rawDrag;
  const transport = useReplayTransport();
  const state = useReplayState();

  const [groupBy, setGroupBy] = useState<TradeGroupBy>('none');
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const bodyRef = useRef<HTMLDivElement>(null);

  const hasFriction = tradesHaveFriction(trades);
  const rows = useMemo(() => trades.slice().reverse(), [trades]);

  const groups = useMemo(() => {
    if (groupBy === 'none') return null;
    const map = new Map<string, { label: string; tone?: string; rows: ReplayTrade[]; pnl: number }>();
    rows.forEach((t) => {
      const key = groupBy === 'strategy' ? strategyKey(t.strategy) : t.symbol;
      let g = map.get(key);
      if (!g) {
        g = {
          label: groupBy === 'strategy' ? strategyLabel(t.strategy) : t.symbol,
          tone: groupBy === 'strategy' ? strategyTone(t.strategy) : undefined,
          rows: [],
          pnl: 0,
        };
        map.set(key, g);
      }
      g.rows.push(t);
      g.pnl += t.pnl_usd || 0;
    });
    return Array.from(map.entries()).sort((a, b) => b[1].pnl - a[1].pnl);
  }, [rows, groupBy]);

  const virtual = useVirtualRows(rows.length, ROW_H, !groups && rows.length > VIRTUALISE_ABOVE, 8, bodyRef);

  if (state === 'loading') return <SkeletonRows rows={6} cols={7} />;

  if (!rows.length) {
    return (
      <EmptyState
        icon={<Icons.Trades size={20} />}
        title={state === 'idle' ? 'No replay loaded' : 'No entries yet'}
        detail={
          state === 'idle'
            ? 'Pick a session and press play.'
            : 'Strong signals open positions automatically.'
        }
        action={
          state === 'idle' ? (
            <button type="button" className="rd-btn" data-variant="primary" onClick={() => void transport.start()}>
              <Icons.Play size={13} /> Start Replay
            </button>
          ) : undefined
        }
      />
    );
  }

  const totalLots = trades.reduce((a, t) => a + (t.lots || 0), 0);
  const totalQty = trades.reduce((a, t) => a + (t.quantity || 0), 0);
  const closed = trades.filter((t) => t.status !== 'OPEN').length;
  const cols = hasFriction ? 13 : 12;
  const slice = rows.slice(virtual.start, virtual.end);

  return (
    <>
      {/* Say it once, at the top, rather than implying it with a zero. */}
      {!hasFriction && (
        <div className="rd-pane-note">
          Execution friction is not modelled in this replay — fills are at the signal price.
        </div>
      )}

      <div className="rd-pane-body" ref={bodyRef}>
        <table className="rd-table">
          <thead>
            <tr>
              <th data-col="id">ID</th>
              <th>In</th>
              <th data-col="out">Out</th>
              <th data-align="right" data-col="held">Held</th>
              <th>Strategy</th>
              <th>Contract</th>
              <th data-align="right" data-col="size">Size</th>
              <th data-align="right">Entry</th>
              <th data-align="right">Exit</th>
              <th data-align="right" data-col="sltgt">SL / Target</th>
              {hasFriction && <th data-align="right" data-col="slip">Slippage</th>}
              <th data-align="center">Status</th>
              <th data-align="right">P&L</th>
            </tr>
          </thead>
          <tbody>
            {groups
              ? groups.map(([key, g]) => (
                  <React.Fragment key={key}>
                    <tr className="rd-group-row">
                      <td colSpan={cols - 1}>
                        <button
                          type="button"
                          className="rd-btn rd-btn-sm"
                          data-variant="ghost"
                          aria-expanded={!collapsed[key]}
                          onClick={() => setCollapsed((c) => ({ ...c, [key]: !c[key] }))}
                        >
                          {collapsed[key] ? <Icons.ChevronDown size={11} /> : <Icons.ChevronUp size={11} />}
                          <span style={{ color: g.tone }}>{g.label}</span>
                          <span style={{ color: 'var(--k-dim)' }}>{g.rows.length} trades</span>
                        </button>
                      </td>
                      <td data-align="right" className="rd-num rd-pnl" data-tone={g.pnl >= 0 ? 'profit' : 'loss'}>
                        {fmtSignedInr(g.pnl)}
                      </td>
                    </tr>
                    {!collapsed[key] &&
                      g.rows.map((t) => (
                        <TradeRow key={t.trade_id} t={t} hasFriction={hasFriction} colSpanBase={cols} />
                      ))}
                  </React.Fragment>
                ))
              : (
                <>
                  {virtual.padTop > 0 && <tr style={{ height: virtual.padTop }} aria-hidden="true"><td colSpan={cols} /></tr>}
                  {slice.map((t) => (
                    <TradeRow key={t.trade_id} t={t} hasFriction={hasFriction} colSpanBase={cols} />
                  ))}
                  {virtual.padBottom > 0 && <tr style={{ height: virtual.padBottom }} aria-hidden="true"><td colSpan={cols} /></tr>}
                </>
              )}
          </tbody>
          <tfoot className="rd-tfoot">
            <tr>
              <td colSpan={6}>
                Total · {trades.length} trades ({closed} closed, {trades.length - closed} open)
              </td>
              <td data-align="right" className="rd-num">
                {fmtInt(totalLots)}L<span className="rd-sub">{fmtInt(totalQty)} qty</span>
              </td>
              <td colSpan={3} data-align="right">
                <span className="rd-absent">{ABSENT}</span>
              </td>
              {hasFriction && (
                <td data-align="right" className="rd-num rd-sl">
                  {drag == null ? <span className="rd-absent">{ABSENT}</span> : fmtSignedInr(-drag)}
                </td>
              )}
              <td data-align="center" className="rd-num">{wins}W / {losses}L</td>
              <td data-align="right" className="rd-num rd-pnl" data-tone={pnl >= 0 ? 'profit' : 'loss'}>
                {fmtSignedInr(pnl)}
                {/* Gross vs net is only a distinction when friction was measured;
                    labelling a single number "net" otherwise is a claim. */}
                <span className="rd-sub">{hasFriction ? 'net of friction' : 'no friction modelled'}</span>
              </td>
            </tr>
          </tfoot>
        </table>
      </div>

      <div className="rd-groupby">
        <span style={{ marginRight: 4 }}>Group</span>
        {(['none', 'strategy', 'contract'] as TradeGroupBy[]).map((g) => (
          <button
            key={g}
            type="button"
            className="rd-btn rd-btn-sm"
            data-variant={groupBy === g ? 'primary' : 'ghost'}
            aria-pressed={groupBy === g}
            onClick={() => setGroupBy(g)}
          >
            {g === 'none' ? 'None' : g === 'strategy' ? 'Strategy' : 'Contract'}
          </button>
        ))}
      </div>
    </>
  );
});
