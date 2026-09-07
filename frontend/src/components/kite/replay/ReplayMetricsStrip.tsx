import React, { memo } from 'react';
import { useReplayStore } from '../../../hooks/useReplayStore';
import {
  ABSENT,
  fmtInt,
  fmtPct,
  fmtSignedInr,
} from './replayFormat';

type Tone = 'profit' | 'loss' | 'dim' | undefined;

function Kpi({
  label,
  value,
  sub,
  tone,
}: {
  label: string;
  value: string;
  sub?: string;
  tone?: Tone;
}) {
  return (
    <div className="rd-kpi">
      <span className="rd-kpi-label">{label}</span>
      <span className="rd-kpi-value" data-tone={tone}>{value}</span>
      {sub && <span className="rd-kpi-sub">{sub}</span>}
    </div>
  );
}

/**
 * Compact metrics card — a clean grid replacing the old horizontal strip.
 *
 * Shows only the 4 key KPIs: P&L, Win Rate, Trades, Signals.
 * No repeated data, no chips. Strategy details are in the settings panel.
 */
export const ReplayMetricsCard = memo(function ReplayMetricsCard() {
  const pnl = useReplayStore((s) => s.status.stats.pnl);
  const wins = useReplayStore((s) => s.status.stats.wins);
  const losses = useReplayStore((s) => s.status.stats.losses);
  const signals = useReplayStore((s) => s.status.stats.signals_fired);
  const trades = useReplayStore((s) => s.status.stats.trades);

  const decided = wins + losses;
  const closed = trades.filter((t) => t.status === 'WIN' || t.status === 'LOSS');
  const openCount = trades.length - closed.length;
  const winRate = decided > 0 ? (wins / decided) * 100 : null;

  // Don't render the card if there's nothing to show
  if (trades.length === 0 && signals === 0) return null;

  return (
    <div className="rd-metrics-card" role="region" aria-label="Replay performance" data-testid="replay-metrics">
      <Kpi
        label="P&L"
        value={fmtSignedInr(pnl)}
        sub={`${closed.length} closed`}
        tone={pnl >= 0 ? 'profit' : 'loss'}
      />
      <Kpi
        label="Win"
        value={winRate == null ? ABSENT : fmtPct(winRate)}
        sub={`${wins}W · ${losses}L`}
        tone={winRate == null ? 'dim' : winRate >= 50 ? 'profit' : 'loss'}
      />
      <Kpi
        label="Trades"
        value={fmtInt(trades.length)}
        sub={openCount > 0 ? `${openCount} open` : 'all settled'}
      />
      <Kpi
        label="Signals"
        value={fmtInt(signals)}
      />
    </div>
  );
});

/**
 * Legacy export — kept for backward compatibility with tests and any
 * other code that imports `ReplayMetricsStrip`. Delegates to the new card.
 */
export const ReplayMetricsStrip = ReplayMetricsCard;
