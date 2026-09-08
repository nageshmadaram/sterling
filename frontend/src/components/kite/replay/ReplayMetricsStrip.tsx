import React, { memo } from 'react';
import { useReplayStore } from '../../../hooks/useReplayStore';
import { ABSENT, fmtInt, fmtPct, fmtSignedInr } from './replayFormat';

/**
 * Flat inline metrics strip.
 *
 * Renders as a single-line status bar:
 *   P&L +₹3,20,022.50  ·  Win 100% (6W · 0L)  ·  6 trades (1 open)  ·  Open +₹340.00  ·  6 signals
 *
 * No borders, no cards, no rounded corners — just clean inline text
 * styled like the table header row for consistency.
 */
export const ReplayMetricsCard = memo(function ReplayMetricsCard() {
  const pnl = useReplayStore((s) => s.status.stats.pnl);
  const wins = useReplayStore((s) => s.status.stats.wins);
  const losses = useReplayStore((s) => s.status.stats.losses);
  const signals = useReplayStore((s) => s.status.stats.signals_fired);
  const drag = useReplayStore((s) => s.status.stats.slippage_total);
  const trades = useReplayStore((s) => s.status.stats.trades);
  const unrealised = useReplayStore((s) => s.status.unrealised_pnl);
  const openPositions = useReplayStore((s) => s.status.open_positions);

  const decided = wins + losses;
  const closed = trades.filter((t) => t.status === 'WIN' || t.status === 'LOSS');
  const openCount = openPositions ?? (trades.length - closed.length);
  const winRate = decided > 0 ? (wins / decided) * 100 : null;

  return (
    <div className="rd-metrics-strip" role="region" aria-label="Replay performance" data-testid="replay-metrics">
      <span className="rd-metric" data-tone={pnl >= 0 ? 'profit' : 'loss'}>
        <span className="rd-metric-label">P&L</span>
        <span className="rd-metric-value">{fmtSignedInr(pnl)}</span>
      </span>
      <span className="rd-metric-sep" aria-hidden>·</span>
      <span className="rd-metric">
        <span className="rd-metric-label">Win</span>
        <span className="rd-metric-value" data-tone={winRate == null ? 'dim' : winRate >= 50 ? 'profit' : 'loss'}>
          {winRate == null ? ABSENT : fmtPct(winRate)}
        </span>
        {decided > 0 && (
          <span className="rd-metric-sub">({wins}W · {losses}L)</span>
        )}
      </span>
      <span className="rd-metric-sep" aria-hidden>·</span>
      <span className="rd-metric">
        <span className="rd-metric-value">{fmtInt(trades.length)}</span>
        <span className="rd-metric-label">trades</span>
        {openCount > 0 && <span className="rd-metric-sub">({openCount} open)</span>}
      </span>
      <span className="rd-metric-sep" aria-hidden>·</span>
      <span className="rd-metric">
        <span className="rd-metric-label">Open</span>
        <span
          className="rd-metric-value"
          data-tone={unrealised == null || unrealised === 0 ? 'dim' : unrealised > 0 ? 'profit' : 'loss'}
        >
          {unrealised == null || openCount === 0 ? ABSENT : fmtSignedInr(unrealised)}
        </span>
      </span>
      <span className="rd-metric-sep" aria-hidden>·</span>
      <span className="rd-metric">
        <span className="rd-metric-value">{fmtInt(signals)}</span>
        <span className="rd-metric-label">signals</span>
      </span>
      <span className="rd-metric-sep" aria-hidden>·</span>
      <span className="rd-metric">
        <span className="rd-metric-label">Slippage</span>
        <span className="rd-metric-value" data-tone={drag == null ? 'dim' : 'loss'}>
          {drag == null ? ABSENT : fmtSignedInr(-drag)}
        </span>
      </span>
    </div>
  );
});

export const ReplayMetricsStrip = ReplayMetricsCard;
