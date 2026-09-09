import React, { memo, useEffect, useMemo, useRef, useState } from 'react';
import {
  ReplayTrade,
  useFilteredReplayTrades,
  useReplayState,
  useReplayStore,
} from '../../../hooks/useReplayStore';
import { useReplayTransport } from '../../../hooks/useReplayTransport';
import { InstrumentLabel, parseInstrument } from '../InstrumentLabel';
import { EmptyState } from './primitives/EmptyState';
import { SkeletonRows } from './primitives/Skeleton';
import { tradesHaveFriction } from './replayColumns';
import {
  ABSENT,
  extractDate,
  fmtDuration,
  fmtInr,
  fmtInt,
  fmtPct,
  fmtPnlBracket,
  fmtSessionDate,
  fmtSignedInr,
  fmtSignedPct,
  fmtTime,
} from './replayFormat';
import { strategyKey, strategyLabel, strategyTone } from './replayStrategies';
import { useVirtualRows } from './useVirtualRows';
import * as Icons from './ReplayIcons';

const ROW_H = 28;
const VIRTUALISE_ABOVE = 200;

export type TradeGroupBy = 'none' | 'date' | 'strategy' | 'contract';

interface TradeGroupSummary {
  label: string;
  tone?: string;
  rows: ReplayTrade[];
  pnl: number;
  invested: number;
  wins: number;
  losses: number;
  open: number;
  decided: number;
  winRate: number | null;
  grossProfit: number;
  grossLoss: number;
  profitFactor: number | null;
  avgTrade: number | null;
  bestTrade: number | null;
  worstTrade: number | null;
  maxDrawdown: number;
  totalLots: number;
  totalQty: number;
}

const TradeRow = memo(function TradeRow({
  t,
  hasFriction,
  showInvested = true,
  onToggleInvested,
  colSpanBase: _colSpanBase,
}: {
  t: ReplayTrade;
  hasFriction: boolean;
  showInvested?: boolean;
  onToggleInvested?: () => void;
  colSpanBase?: number;
}) {
  const open = t.status === 'OPEN';
  const win = t.status === 'WIN';
  const invested = (t.entry_price || 0) * (t.quantity || 0);

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
          {(t.strategy === 'adaptive_edge' || t.scan_origin) && (
            <span
              style={{
                fontSize: '9.5px',
                fontWeight: 600,
                padding: '1px 4px',
                borderRadius: '3px',
                background: 'rgba(255, 255, 255, 0.06)',
                color: t.scan_origin === 'spot_scan' ? 'var(--k-amber, #f59e0b)' : 'var(--k-cyan, #00b4d8)',
                letterSpacing: '0.02em',
              }}
            >
              {t.scan_origin === 'spot_scan' ? 'Spot' : 'AE'}
            </span>
          )}
        </span>
      </td>
      <td>
        <strong style={{ display: 'inline-flex', alignItems: 'center' }}>
          <InstrumentLabel symbol={t.symbol} fallback={t.underlying} />
        </strong>
        {t.underlying && (!parseInstrument(t.symbol) || t.spot_entry != null) ? (
          <span className="rd-sub">
            {t.spot_entry != null
              ? `${t.underlying} spot ${fmtInr(t.spot_entry)}`
              : `${t.underlying}${t.strike ? ` · ${t.strike} ${t.opt_type || ''}` : ''}`}
          </span>
        ) : null}
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
        {t.exit_reason && (
          <span
            className="rd-sub"
            style={{
              display: 'block',
              fontSize: '10px',
              fontWeight: 600,
              marginTop: '2px',
              textTransform: 'uppercase',
              color:
                t.exit_reason === 'TRAILING_STOP'
                  ? 'var(--k-cyan, #06b6d4)'
                  : t.exit_reason === 'TARGET'
                  ? 'var(--k-green, #10b981)'
                  : t.exit_reason === 'STOP_LOSS'
                  ? 'var(--k-red, #ef4444)'
                  : 'var(--k-dim)',
            }}
            title={
              t.exit_reason === 'TRAILING_STOP'
                ? 'Trailing Stop Loss: Ratcheted stop triggered (profit locked)'
                : t.exit_reason === 'TARGET'
                ? 'Target: Take-profit reached'
                : t.exit_reason === 'STOP_LOSS'
                ? 'Stop Loss: Initial protective stop hit'
                : t.exit_reason === 'MAX_HOLD'
                ? 'Max Hold: Intraday bar time limit reached'
                : t.exit_reason === 'SESSION_CLOSE'
                ? 'Session Close: Intraday market close square-off'
                : t.exit_reason
            }
          >
            {t.exit_reason === 'TRAILING_STOP'
              ? 'TSL'
              : t.exit_reason === 'TARGET'
              ? 'TARGET'
              : t.exit_reason === 'STOP_LOSS'
              ? 'SL'
              : t.exit_reason === 'MAX_HOLD'
              ? 'TIME'
              : t.exit_reason === 'SESSION_CLOSE'
              ? 'EOD'
              : t.exit_reason}
          </span>
        )}
      </td>
      <td
        data-align="right"
        className="rd-num rd-pnl"
        data-tone={open ? 'open' : t.pnl_usd >= 0 ? 'profit' : 'loss'}
        onClick={onToggleInvested}
        style={onToggleInvested ? { cursor: 'pointer' } : undefined}
      >
        {showInvested ? (
          <span
            className={`rd-invested-cell ${open ? 'rd-unrealised' : ''}`}
            title={`Invested: ₹${fmtInt(Math.round(invested))} · P&L: ${fmtSignedInr(t.pnl_usd)}${open ? ' (Unrealised)' : ''} (Click to toggle)`}
          >
            <span className="rd-invested-amt">{fmtInt(Math.round(invested))}</span>
            {' '}
            <span
              className="rd-invested-bracket"
              data-tone={open ? 'open' : t.pnl_usd >= 0 ? 'profit' : 'loss'}
            >
              {fmtPnlBracket(t.pnl_usd, open)}
            </span>
          </span>
        ) : (
          <span
            className={open ? 'rd-unrealised' : undefined}
            title={open ? `Unrealised (${fmtSignedInr(t.pnl_usd)}) — the position is still open (Click to toggle)` : `P&L: ${fmtSignedInr(t.pnl_usd)} (Click to toggle)`}
          >
            {open ? '~' : ''}{fmtSignedInr(t.pnl_usd)}
          </span>
        )}
        <span className="rd-sub">
          {t.pnl_pct != null
            ? fmtSignedPct(t.pnl_pct)
            : invested > 0
              ? fmtSignedPct((t.pnl_usd / invested) * 100)
              : ABSENT}
        </span>
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
  const barsPlayed = useReplayStore((s) => s.status.bars_played);
  const cfg = useReplayStore((s) => s.status.config);
  const draft = useReplayStore((s) => s.draft);
  const multiDay = Boolean(
    (cfg?.end_date && cfg.end_date !== cfg.date) ||
    (!cfg && draft.endDate && draft.endDate !== draft.date)
  );

  const [userInteractedGroup, setUserInteractedGroup] = useState(false);
  const [groupBy, setGroupBy] = useState<TradeGroupBy>(multiDay ? 'date' : 'none');
  const [collapsed, setCollapsed] = useState<Record<string, boolean>>({});
  const [showInvested, setShowInvested] = useState<boolean>(() => {
    try {
      const saved = localStorage.getItem('sterling_replay_show_invested');
      return saved !== 'false';
    } catch {
      return true;
    }
  });
  const bodyRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    try {
      localStorage.setItem('sterling_replay_show_invested', String(showInvested));
    } catch {}
  }, [showInvested]);

  useEffect(() => {
    if (!userInteractedGroup) {
      setGroupBy(multiDay ? 'date' : 'none');
    }
  }, [multiDay, userInteractedGroup]);

  const hasFriction = tradesHaveFriction(trades);
  const rows = useMemo(() => trades.slice().reverse(), [trades]);

  const groups = useMemo(() => {
    if (groupBy === 'none') return null;
    const map = new Map<string, TradeGroupSummary>();

    rows.forEach((t) => {
      let key: string;
      let label: string;
      let tone: string | undefined;

      if (groupBy === 'date') {
        const rawDate = extractDate(t.entry_time_iso, t.timestamp_ms);
        key = rawDate || 'unknown';
        label = rawDate ? fmtSessionDate(rawDate, false) : 'Unknown Date';
      } else if (groupBy === 'strategy') {
        key = strategyKey(t.strategy);
        label = strategyLabel(t.strategy);
        tone = strategyTone(t.strategy);
      } else {
        key = t.symbol;
        label = t.symbol;
      }

      let g = map.get(key);
      if (!g) {
        g = {
          label,
          tone,
          rows: [],
          pnl: 0,
          invested: 0,
          wins: 0,
          losses: 0,
          open: 0,
          decided: 0,
          winRate: null,
          grossProfit: 0,
          grossLoss: 0,
          profitFactor: null,
          avgTrade: null,
          bestTrade: null,
          worstTrade: null,
          maxDrawdown: 0,
          totalLots: 0,
          totalQty: 0,
        };
        map.set(key, g);
      }
      g.rows.push(t);
      const p = t.pnl_usd || 0;
      g.pnl += p;
      g.invested += (t.entry_price || 0) * (t.quantity || 0);
      if (t.status === 'WIN') {
        g.wins += 1;
        if (p > 0) g.grossProfit += p;
      } else if (t.status === 'LOSS') {
        g.losses += 1;
        if (p < 0) g.grossLoss += Math.abs(p);
      } else {
        g.open += 1;
      }
      g.totalLots += t.lots || 0;
      g.totalQty += t.quantity || 0;
    });

    map.forEach((g) => {
      g.decided = g.wins + g.losses;
      g.winRate = g.decided > 0 ? (g.wins / g.decided) * 100 : null;
      g.avgTrade = g.decided > 0 ? g.pnl / g.decided : null;
      g.profitFactor =
        g.grossLoss > 0
          ? g.grossProfit / g.grossLoss
          : g.grossProfit > 0
            ? Infinity
            : null;

      const closed = g.rows.filter((t) => t.status === 'WIN' || t.status === 'LOSS');
      const closedPnls = closed
        .map((t) => t.pnl_usd)
        .filter((v): v is number => typeof v === 'number' && Number.isFinite(v));
      if (closedPnls.length > 0) {
        g.bestTrade = Math.max(...closedPnls);
        g.worstTrade = Math.min(...closedPnls);
      }

      // Compute intraday peak-to-trough drawdown chronologically
      const chrono = closed.slice().sort((a, b) => (a.timestamp_ms || 0) - (b.timestamp_ms || 0));
      let peak = 0;
      let cum = 0;
      let maxDd = 0;
      chrono.forEach((t) => {
        cum += t.pnl_usd || 0;
        peak = Math.max(peak, cum);
        maxDd = Math.max(maxDd, peak - cum);
      });
      g.maxDrawdown = maxDd;
    });

    if (groupBy === 'date') {
      return Array.from(map.entries()).sort((a, b) => b[0].localeCompare(a[0]));
    }
    return Array.from(map.entries()).sort((a, b) => b[1].pnl - a[1].pnl);
  }, [rows, groupBy]);

  const virtual = useVirtualRows(rows.length, ROW_H, !groups && rows.length > VIRTUALISE_ABOVE, 8, bodyRef);

  if (state === 'loading') return <SkeletonRows rows={6} cols={7} />;

  if (!rows.length) {
    return (
      <EmptyState
        icon={<Icons.Trades size={20} />}
        title={state === 'idle' ? (barsPlayed > 0 ? 'No trades executed' : 'No replay loaded') : 'No entries yet'}
        detail={
          state === 'idle'
            ? barsPlayed > 0
              ? `${fmtInt(barsPlayed)} bars replayed. No setups met execution criteria during this session.`
              : 'Pick a session and press play.'
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
  const totalInvested = trades.reduce((a, t) => a + (t.entry_price || 0) * (t.quantity || 0), 0);
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
              <th data-align="right">
                <button
                  type="button"
                  className="rd-th-toggle-btn"
                  onClick={() => setShowInvested((s) => !s)}
                  title="Click to toggle between Invested (P&L) and P&L only"
                >
                  {showInvested ? 'Invested (P&L)' : 'P&L'}
                </button>
              </th>
            </tr>
          </thead>
          <tbody>
            {groups
              ? groups.map(([key, g]) => (
                  <React.Fragment key={key}>
                    <tr className="rd-group-row">
                      <td colSpan={cols - 1}>
                        <div className="rd-group-header-cell">
                          <button
                            type="button"
                            className="rd-btn rd-btn-sm rd-group-toggle-btn"
                            data-variant="ghost"
                            aria-expanded={!collapsed[key]}
                            onClick={() => setCollapsed((c) => ({ ...c, [key]: !c[key] }))}
                          >
                            {collapsed[key] ? <Icons.ChevronDown size={11} /> : <Icons.ChevronUp size={11} />}
                            <span style={{ color: g.tone, fontWeight: 700, display: 'inline-flex', alignItems: 'center' }}>
                              {groupBy === 'contract' ? <InstrumentLabel symbol={g.label} /> : g.label}
                            </span>
                            <span style={{ color: 'var(--k-dim)' }}>
                              {g.rows.length} {g.rows.length === 1 ? 'trade' : 'trades'}
                            </span>
                            {(g.wins > 0 || g.losses > 0) && (
                              <span style={{ color: 'var(--k-dim)', fontSize: 'var(--rd-fs-micro)' }}>
                                ({g.wins}W · {g.losses}L)
                              </span>
                            )}
                          </button>

                          <div className="rd-group-pnl-stats" role="group" aria-label={`${g.label} P&L stats`}>
                            <span
                              className="rd-stat-pill rd-stat-pill-invested"
                              title={`${g.label} Total Invested: ₹${fmtInt(Math.round(g.invested))}`}
                            >
                              <span className="rd-stat-pill-lbl">Invested</span>
                              <span className="rd-stat-pill-val">₹{fmtInt(Math.round(g.invested))}</span>
                            </span>

                            {g.winRate != null && (
                              <span
                                className="rd-stat-pill"
                                data-tone={g.winRate >= 50 ? 'profit' : 'loss'}
                                title={`Win Rate: ${fmtPct(g.winRate, 1)} (${g.wins} won / ${g.decided} decided)`}
                              >
                                <span className="rd-stat-pill-lbl">Win</span>
                                <span className="rd-stat-pill-val">{fmtPct(g.winRate)}</span>
                              </span>
                            )}

                            {g.profitFactor != null && (
                              <span
                                className="rd-stat-pill"
                                data-tone={g.profitFactor >= 1 ? 'profit' : 'loss'}
                                title={`Profit Factor: ${g.profitFactor === Infinity ? 'Infinite' : g.profitFactor.toFixed(2)} (Gross Win: ${fmtInr(g.grossProfit)} / Gross Loss: ${fmtInr(g.grossLoss)})`}
                              >
                                <span className="rd-stat-pill-lbl">PF</span>
                                <span className="rd-stat-pill-val">
                                  {g.profitFactor === Infinity ? '∞' : g.profitFactor.toFixed(2)}
                                </span>
                              </span>
                            )}

                            {g.avgTrade != null && (
                              <span
                                className="rd-stat-pill"
                                data-tone={g.avgTrade >= 0 ? 'profit' : 'loss'}
                                title={`Average Trade P&L: ${fmtSignedInr(g.avgTrade)}`}
                              >
                                <span className="rd-stat-pill-lbl">Avg</span>
                                <span className="rd-stat-pill-val">{fmtSignedInr(g.avgTrade)}</span>
                              </span>
                            )}

                            {g.bestTrade != null && g.worstTrade != null && (
                              <span
                                className="rd-stat-pill rd-stat-pill-range"
                                title={`Best Trade: ${fmtSignedInr(g.bestTrade)} · Worst Trade: ${fmtSignedInr(g.worstTrade)}`}
                              >
                                <span className="rd-stat-pill-lbl">Best/Worst</span>
                                <span className="rd-stat-pill-val">
                                  <span style={{ color: g.bestTrade >= 0 ? 'var(--k-green)' : 'inherit' }}>
                                    {fmtSignedInr(g.bestTrade)}
                                  </span>
                                  <span style={{ color: 'var(--k-dim)', margin: '0 2px' }}>/</span>
                                  <span style={{ color: g.worstTrade < 0 ? 'var(--k-red-brick)' : 'inherit' }}>
                                    {fmtSignedInr(g.worstTrade)}
                                  </span>
                                </span>
                              </span>
                            )}

                            {g.maxDrawdown > 0 && (
                              <span
                                className="rd-stat-pill rd-stat-pill-dd"
                                data-tone="loss"
                                title={`Intraday Max Drawdown: ${fmtInr(g.maxDrawdown)}`}
                              >
                                <span className="rd-stat-pill-lbl">MaxDD</span>
                                <span className="rd-stat-pill-val">{fmtInr(g.maxDrawdown)}</span>
                              </span>
                            )}
                          </div>
                        </div>
                      </td>
                      <td
                        data-align="right"
                        className="rd-num rd-pnl"
                        data-tone={g.pnl === 0 ? 'dim' : g.pnl > 0 ? 'profit' : 'loss'}
                        onClick={() => setShowInvested((s) => !s)}
                        style={{ cursor: 'pointer' }}
                      >
                        {showInvested ? (
                          <span
                            className="rd-invested-cell"
                            title={`${g.label} Total Invested: ₹${fmtInt(Math.round(g.invested))} · Net Realised P&L: ${fmtSignedInr(g.pnl)} (Click to toggle view)`}
                          >
                            <span className="rd-invested-amt">{fmtInt(Math.round(g.invested))}</span>
                            {' '}
                            <span
                              className="rd-invested-bracket"
                              data-tone={g.pnl === 0 ? 'dim' : g.pnl > 0 ? 'profit' : 'loss'}
                            >
                              {fmtPnlBracket(g.pnl)}
                            </span>
                          </span>
                        ) : (
                          <span title={`${g.label} Net Realised P&L: ${fmtSignedInr(g.pnl)} (Click to toggle view)`}>
                            {fmtSignedInr(g.pnl)}
                          </span>
                        )}
                      </td>
                    </tr>
                    {!collapsed[key] &&
                      g.rows.map((t) => (
                        <TradeRow
                          key={t.trade_id}
                          t={t}
                          hasFriction={hasFriction}
                          showInvested={showInvested}
                          onToggleInvested={() => setShowInvested((s) => !s)}
                          colSpanBase={cols}
                        />
                      ))}
                  </React.Fragment>
                ))
              : (
                <>
                  {virtual.padTop > 0 && <tr style={{ height: virtual.padTop }} aria-hidden="true"><td colSpan={cols} /></tr>}
                  {slice.map((t) => (
                    <TradeRow
                      key={t.trade_id}
                      t={t}
                      hasFriction={hasFriction}
                      showInvested={showInvested}
                      onToggleInvested={() => setShowInvested((s) => !s)}
                      colSpanBase={cols}
                    />
                  ))}
                  {virtual.padBottom > 0 && <tr style={{ height: virtual.padBottom }} aria-hidden="true"><td colSpan={cols} /></tr>}
                </>
              )}
          </tbody>
          <tfoot className="rd-tfoot">
            <tr>
              <td colSpan={6}>
                Total · {trades.length} trades ({closed} closed, {trades.length - closed} open)
                {' · '}
                <span title={`Total Invested: ₹${fmtInt(Math.round(totalInvested))}`}>
                  ₹{fmtInt(Math.round(totalInvested))} invested
                </span>
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
              <td
                data-align="right"
                className="rd-num rd-pnl"
                data-tone={pnl >= 0 ? 'profit' : 'loss'}
                onClick={() => setShowInvested((s) => !s)}
                style={{ cursor: 'pointer' }}
              >
                {showInvested ? (
                  <span
                    className="rd-invested-cell"
                    title={`Total Invested: ₹${fmtInt(Math.round(totalInvested))} · Net P&L: ${fmtSignedInr(pnl)} (Click to toggle view)`}
                  >
                    <span className="rd-invested-amt">{fmtInt(Math.round(totalInvested))}</span>
                    {' '}
                    <span
                      className="rd-invested-bracket"
                      data-tone={pnl === 0 ? 'dim' : pnl > 0 ? 'profit' : 'loss'}
                    >
                      {fmtPnlBracket(pnl)}
                    </span>
                  </span>
                ) : (
                  fmtSignedInr(pnl)
                )}
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
        {(['none', 'date', 'strategy', 'contract'] as TradeGroupBy[]).map((g) => (
          <button
            key={g}
            type="button"
            className="rd-btn rd-btn-sm"
            data-variant={groupBy === g ? 'primary' : 'ghost'}
            aria-pressed={groupBy === g}
            onClick={() => {
              setUserInteractedGroup(true);
              setGroupBy(g);
            }}
          >
            {g === 'none' ? 'None' : g === 'date' ? 'Date' : g === 'strategy' ? 'Strategy' : 'Contract'}
          </button>
        ))}

        <div className="rd-view-toggles" style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 6 }}>
          <button
            type="button"
            className="rd-btn rd-btn-sm"
            data-variant={showInvested ? 'primary' : 'ghost'}
            aria-pressed={showInvested}
            onClick={() => setShowInvested((s) => !s)}
            data-testid="replay-toggle-invested"
            title="Toggle displaying Amount Invested and P&L in brackets"
          >
            Invested (P&L)
          </button>
        </div>
      </div>
    </>
  );
});
