import React, { useCallback, useMemo, useRef } from 'react';
import { createPortal } from 'react-dom';
import {
  ReplaySignal,
  ReplayTrade,
  useReplayStore,
} from '../../../hooks/useReplayStore';
import { useReplayTransport } from '../../../hooks/useReplayTransport';
import { Sparkline } from './primitives/Sparkline';
import { useFocusTrap, useScrollLock } from './primitives/useFocusTrap';
import { SIGNAL_CSV_COLUMNS, tradeCsvColumns, tradesHaveFriction } from './replayColumns';
import { exportCsv, replayCsvName } from './replayCsv';
import {
  ABSENT,
  fmtElapsed,
  fmtInr,
  fmtPct,
  fmtSessionDate,
  fmtSignedInr,
  fmtTime,
} from './replayFormat';
import { strategyKey, strategyLabel, strategyTone } from './replayStrategies';
import * as Icons from './ReplayIcons';
import './replay.css';

/* ═══════════════════════════════════════════════════════════════════════════
   End-of-replay summary.

   The component this replaces referenced `.sim-summary-overlay` and
   `.sim-summary-card`, neither of which was defined in ANY stylesheet. It was
   mounted inside the workspace's flex column, so it rendered as an unstyled
   block that shoved the footer down — no scrim, no centring, no focus trap, no
   Escape. This one is a real dialog, body-portalled above the fullscreen dock.
   ═══════════════════════════════════════════════════════════════════════════ */

export function ReplaySummaryModal() {
  const open = useReplayStore((s) => s.summaryOpen);
  const setOpen = useReplayStore((s) => s.setSummaryOpen);
  const status = useReplayStore((s) => s.status);
  const draft = useReplayStore((s) => s.draft);
  const transport = useReplayTransport();

  const cardRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);

  const close = useCallback(() => setOpen(false), [setOpen]);

  useFocusTrap(cardRef, open, {
    onEscape: close,
    initialFocus: () => closeRef.current,
  });
  useScrollLock(open);

  const stats = status.stats;
  const trades = stats.trades;
  const events = stats.events;

  const derived = useMemo(() => {
    const closed = trades.filter((t) => t.status === 'WIN' || t.status === 'LOSS');
    const decided = stats.wins + stats.losses;
    // Divide by DECIDED trades, not by every trade entered. The previous
    // summary used `trades_entered`, which understates the rate whenever a
    // position is still open at the session close.
    const winRate = decided > 0 ? (stats.wins / decided) * 100 : null;
    const avg = closed.length ? stats.pnl / closed.length : null;
    const pnls = closed
      .map((t) => t.pnl_usd)
      .filter((v): v is number => typeof v === 'number' && Number.isFinite(v));

    // Gross profit over gross loss. `null` rather than Infinity when nothing
    // lost — "no losses yet" is not a ratio.
    const grossWin = pnls.filter((v) => v > 0).reduce((a, b) => a + b, 0);
    const grossLoss = Math.abs(pnls.filter((v) => v < 0).reduce((a, b) => a + b, 0));
    const profitFactor = grossLoss > 0 ? grossWin / grossLoss : null;

    // Worst peak-to-trough on the realised curve — the same run the equity
    // sparkline shades, surfaced as a number.
    let peak = 0;
    let cum = 0;
    let maxDd = 0;
    closed.forEach((t) => {
      cum += t.pnl_usd || 0;
      peak = Math.max(peak, cum);
      maxDd = Math.max(maxDd, peak - cum);
    });

    return {
      closed,
      winRate,
      avg,
      best: pnls.length ? Math.max(...pnls) : null,
      worst: pnls.length ? Math.min(...pnls) : null,
      open: trades.length - closed.length,
      profitFactor,
      maxDd: closed.length ? maxDd : null,
    };
  }, [trades, stats.wins, stats.losses, stats.pnl]);

  const equity = useMemo(() => {
    const pts = [0];
    let acc = 0;
    derived.closed.forEach((t) => {
      acc += t.pnl_usd || 0;
      pts.push(Number(acc.toFixed(2)));
    });
    return pts;
  }, [derived.closed]);

  if (!open) return null;

  const date = status.config?.date ?? draft.date;
  const startTime = status.config?.start_time ?? draft.startTime;
  const endTime = status.config?.end_time ?? draft.endTime;
  const hasFriction = tradesHaveFriction(trades);
  // Name the execution model the engine actually ran, not the one requested.
  const frictionMode: 'realistic' | 'ideal' | 'none' = hasFriction
    ? 'realistic'
    : status.config?.friction_mode === 'ideal'
      ? 'ideal'
      : 'none';
  const FRICTION_LABEL = {
    realistic: 'Realistic fills — spread and slippage applied',
    ideal: 'Ideal fills — zero friction',
    none: 'Friction not modelled by this engine',
  } as const;

  const verdict = [
    `${fmtSignedInr(stats.pnl)} across ${trades.length} trade${trades.length === 1 ? '' : 's'}`,
    derived.winRate != null ? `${fmtPct(derived.winRate)} win rate` : null,
    status.elapsed_real_s ? `${fmtElapsed(status.elapsed_real_s)} of real time` : null,
  ]
    .filter(Boolean)
    .join(' · ');

  const body = (
    <div
      className="rd-overlay"
      onMouseDown={(e) => {
        // Only a press that both starts and ends on the scrim closes it —
        // otherwise a text selection dragged out of the card dismisses it.
        if (e.target === e.currentTarget) close();
      }}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="rd-summary-title"
        className="rd-modal"
        ref={cardRef}
        data-testid="replay-summary"
      >
        <header className="rd-modal-head">
          <div style={{ minWidth: 0 }}>
            <h2 className="rd-modal-title" id="rd-summary-title">
              Replay complete
            </h2>
            <div className="rd-modal-verdict">{verdict}</div>
          </div>
          <div className="rd-modal-meta">
            {fmtSessionDate(date)}
            <br />
            {fmtTime(startTime, 5)}–{fmtTime(endTime, 5)}
            {status.config?.speed ? ` · ${status.config.speed}×` : ''}
          </div>
          <button
            type="button"
            ref={closeRef}
            className="rd-btn rd-btn-sm"
            data-variant="ghost"
            onClick={close}
            aria-label="Close replay summary"
          >
            <Icons.Close size={13} />
          </button>
        </header>

        <div className="rd-modal-body">
          <section>
            <div className="rd-chip-row" style={{ marginBottom: 10 }}>
              <span
                className="rd-status-chip"
                data-tone={frictionMode === 'realistic' ? 'open' : 'neutral'}
                data-testid="replay-friction-badge"
              >
                {frictionMode.toUpperCase()}
              </span>
              <span style={{ fontSize: 'var(--rd-fs-body)', color: 'var(--k-dim)' }}>
                {FRICTION_LABEL[frictionMode]}
              </span>
            </div>
            <div className="rd-statgrid">
              <Stat label="Signals" value={String(stats.signals_fired)} />
              <Stat
                label="Trades"
                value={String(trades.length)}
                sub={derived.open > 0 ? `${derived.open} still open` : 'all settled'}
              />
              <Stat
                label="Win rate"
                value={derived.winRate == null ? ABSENT : fmtPct(derived.winRate)}
                sub={`${stats.wins}W · ${stats.losses}L`}
                tone={derived.winRate == null ? undefined : derived.winRate >= 50 ? 'profit' : 'loss'}
              />
              <Stat
                label="Net P&L"
                value={fmtSignedInr(stats.pnl)}
                tone={stats.pnl >= 0 ? 'profit' : 'loss'}
                sub={hasFriction ? 'after execution friction' : 'friction not modelled'}
              />
              <Stat label="Wins" value={String(stats.wins)} tone="profit" />
              <Stat label="Losses" value={String(stats.losses)} tone="loss" />
              <Stat
                label="Avg trade"
                value={derived.avg == null ? ABSENT : fmtSignedInr(derived.avg)}
                tone={derived.avg == null ? undefined : derived.avg >= 0 ? 'profit' : 'loss'}
              />
              <Stat
                label="Profit factor"
                value={derived.profitFactor == null ? ABSENT : derived.profitFactor.toFixed(2)}
                tone={derived.profitFactor == null ? undefined : derived.profitFactor >= 1 ? 'profit' : 'loss'}
                sub={derived.profitFactor == null ? 'no losing trade yet' : 'gross win ÷ gross loss'}
              />
              <Stat
                label="Max drawdown"
                value={derived.maxDd == null ? ABSENT : fmtInr(derived.maxDd)}
                tone={derived.maxDd ? 'loss' : undefined}
                sub="worst peak to trough"
              />
              <Stat
                label="Best / worst"
                value={
                  derived.best == null
                    ? ABSENT
                    : `${fmtSignedInr(derived.best)} / ${fmtSignedInr(derived.worst)}`
                }
              />
            </div>
          </section>

          {equity.length > 1 && (
            <section>
              <h3 className="rd-section-title">Equity curve</h3>
              <Sparkline
                values={equity}
                caption="Cumulative realised P&L, in trade order. The shaded band is the worst peak-to-trough run."
              />
            </section>
          )}

          <StrategyBreakdown events={events} trades={trades} />
          <TradeLog trades={trades} />
        </div>

        <footer className="rd-modal-foot">
          {events.length > 0 && (
            <button
              type="button"
              className="rd-btn"
              onClick={() =>
                exportCsv(
                  replayCsvName('signals', date, startTime, endTime),
                  events,
                  SIGNAL_CSV_COLUMNS,
                )
              }
            >
              <Icons.Export size={13} /> Export signals
            </button>
          )}
          {trades.length > 0 && (
            <button
              type="button"
              className="rd-btn"
              onClick={() =>
                exportCsv(
                  replayCsvName('trades', date, startTime, endTime),
                  trades,
                  tradeCsvColumns(hasFriction),
                )
              }
            >
              <Icons.Export size={13} /> Export trades
            </button>
          )}
          <div className="rd-modal-foot-right">
            <button type="button" className="rd-btn" onClick={close}>
              Close
            </button>
            <button
              type="button"
              className="rd-btn"
              data-variant="primary"
              onClick={() => {
                // Actually re-run it. The previous version only reopened the
                // dock and left the user to find the play button.
                close();
                useReplayStore.getState().setOpen(true);
                void transport.start();
              }}
            >
              <Icons.Play size={12} /> Replay again
            </button>
          </div>
        </footer>
      </div>
    </div>
  );

  return createPortal(body, document.body);
}

function Stat({
  label,
  value,
  sub,
  tone,
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: 'profit' | 'loss';
}) {
  return (
    <div className="rd-statbox">
      <div className="rd-statbox-label">{label}</div>
      <div className="rd-statbox-value" data-tone={tone}>
        {value}
      </div>
      {sub && <div className="rd-statbox-sub">{sub}</div>}
    </div>
  );
}

function StrategyBreakdown({
  events,
  trades,
}: {
  events: readonly ReplaySignal[];
  trades: readonly ReplayTrade[];
}) {
  const rows = useMemo(() => {
    // Key case-insensitively. The previous breakdown keyed on the raw string,
    // so a strategy spelled differently between the two ledgers appeared twice.
    const map = new Map<
      string,
      { id: string; signals: number; trades: number; wins: number; losses: number; pnl: number }
    >();
    const get = (id: string) => {
      const k = strategyKey(id);
      let row = map.get(k);
      if (!row) {
        row = { id, signals: 0, trades: 0, wins: 0, losses: 0, pnl: 0 };
        map.set(k, row);
      }
      return row;
    };
    events.forEach((e) => { get(e.strategy).signals += 1; });
    trades.forEach((t) => {
      const row = get(t.strategy);
      row.trades += 1;
      if (t.status === 'WIN') row.wins += 1;
      else if (t.status === 'LOSS') row.losses += 1;
      row.pnl += t.pnl_usd || 0;
    });
    return Array.from(map.values()).sort((a, b) => b.pnl - a.pnl);
  }, [events, trades]);

  const maxAbs = Math.max(1, ...rows.map((r) => Math.abs(r.pnl)));

  return (
    <section>
      <h3 className="rd-section-title">Strategy breakdown</h3>
      <div className="rd-scroll-box">
        <table className="rd-table">
          <thead>
            <tr>
              <th>Strategy</th>
              <th data-align="right">Signals</th>
              <th data-align="right">Trades</th>
              <th data-align="right">W / L</th>
              <th data-align="right">Win %</th>
              <th data-align="right">Net P&L</th>
              <th style={{ width: 90 }}>Share</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const decided = r.wins + r.losses;
              return (
                <tr key={strategyKey(r.id)}>
                  <td>
                    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, color: strategyTone(r.id) }}>
                      <span className="rd-dot-tone" />
                      <span style={{ color: 'var(--k-text)', fontWeight: 600 }}>{strategyLabel(r.id)}</span>
                    </span>
                  </td>
                  <td data-align="right" className="rd-num">{r.signals}</td>
                  <td data-align="right" className="rd-num">{r.trades}</td>
                  <td data-align="right" className="rd-num">{r.wins} / {r.losses}</td>
                  <td data-align="right" className="rd-num">
                    {decided ? fmtPct((r.wins / decided) * 100) : ABSENT}
                  </td>
                  <td data-align="right" className="rd-num rd-pnl" data-tone={r.pnl >= 0 ? 'profit' : 'loss'}>
                    {fmtSignedInr(r.pnl)}
                  </td>
                  <td>
                    <span
                      className="rd-share-bar"
                      style={{
                        display: 'block',
                        width: `${(Math.abs(r.pnl) / maxAbs) * 100}%`,
                        color: r.pnl >= 0 ? 'var(--k-green)' : 'var(--k-red-brick)',
                      }}
                    />
                  </td>
                </tr>
              );
            })}
            {rows.length === 0 && (
              <tr>
                <td colSpan={7} style={{ textAlign: 'center', color: 'var(--k-dim)' }}>
                  No strategies triggered
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </section>
  );
}

function TradeLog({ trades }: { trades: readonly ReplayTrade[] }) {
  if (!trades.length) return null;
  return (
    <section>
      <h3 className="rd-section-title">Executed trades</h3>
      <div className="rd-scroll-box">
        <table className="rd-table">
          <thead>
            <tr>
              <th>ID</th>
              <th>In</th>
              <th>Strategy</th>
              <th>Contract</th>
              <th data-align="right">Lots</th>
              <th data-align="right">Entry</th>
              <th data-align="right">Exit</th>
              <th data-align="center">Status</th>
              <th data-align="right">P&L</th>
            </tr>
          </thead>
          <tbody>
            {trades.map((t) => (
              <tr key={t.trade_id}>
                <td className="rd-mono" style={{ color: 'var(--k-blue)' }}>{t.trade_id}</td>
                <td className="rd-num">{fmtTime(t.entry_time_iso)}</td>
                <td style={{ color: strategyTone(t.strategy) }}>
                  <span style={{ color: 'var(--k-text)' }}>{strategyLabel(t.strategy)}</span>
                </td>
                <td>{t.symbol}</td>
                <td data-align="right" className="rd-num">{t.lots}L</td>
                <td data-align="right" className="rd-num">{fmtInr(t.entry_price)}</td>
                <td data-align="right" className="rd-num">
                  {t.exit_price == null ? <span className="rd-absent">{ABSENT}</span> : fmtInr(t.exit_price)}
                </td>
                <td data-align="center">
                  <span
                    className="rd-status-chip"
                    data-tone={t.status === 'WIN' ? 'win' : t.status === 'LOSS' ? 'loss' : 'open'}
                  >
                    {t.status}
                  </span>
                </td>
                <td data-align="right" className="rd-num rd-pnl" data-tone={t.pnl_usd >= 0 ? 'profit' : 'loss'}>
                  {fmtSignedInr(t.pnl_usd)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export default ReplaySummaryModal;
