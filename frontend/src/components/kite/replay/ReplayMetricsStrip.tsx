import React, { memo } from 'react';
import {
  useFilteredReplayEvents,
  useFilteredReplayTrades,
  useReplayStore,
} from '../../../hooks/useReplayStore';
import { replayHasFriction } from './replayColumns';
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
  const rawPnl = useReplayStore((s) => s.status.stats.pnl);
  const rawWins = useReplayStore((s) => s.status.stats.wins);
  const rawLosses = useReplayStore((s) => s.status.stats.losses);
  const rawSignals = useReplayStore((s) => s.status.stats.signals_fired);
  const rawDrag = useReplayStore((s) => s.status.stats.slippage_total);
  const rawTrades = useReplayStore((s) => s.status.stats.trades);
  const rawEvents = useReplayStore((s) => s.status.stats.events);
  const unrealised = useReplayStore((s) => s.status.unrealised_pnl);
  const openPositions = useReplayStore((s) => s.status.open_positions);
  const config = useReplayStore((s) => s.status.config);
  const capabilities = useReplayStore((s) => s.status.capabilities);
  const lotSizeSource = useReplayStore((s) => s.status.lot_size_source);

  const trades = useFilteredReplayTrades();
  const events = useFilteredReplayEvents();
  const isNarrowed = trades.length !== rawTrades.length || events.length !== rawEvents.length;

  const closed = trades.filter((t) => t.status === 'WIN' || t.status === 'LOSS');
  const pnl = isNarrowed
    ? Number(closed.reduce((sum, t) => sum + (t.pnl_usd || 0), 0).toFixed(2))
    : rawPnl;
  const wins = isNarrowed ? closed.filter((t) => t.status === 'WIN').length : rawWins;
  const losses = isNarrowed ? closed.filter((t) => t.status === 'LOSS').length : rawLosses;
  const signals = isNarrowed ? events.length : rawSignals;
  const drag = isNarrowed
    ? trades.some((t) => t.slippage != null)
      ? Number(trades.reduce((sum, t) => sum + (t.slippage || 0), 0).toFixed(2))
      : null
    : rawDrag;
  const openTrades = trades.filter((t) => t.status === 'OPEN');
  const filteredUnrealised = openTrades.length > 0
    ? Number(openTrades.reduce((sum, t) => sum + (t.pnl_usd || 0), 0).toFixed(2))
    : 0;
  const effectiveUnrealised = isNarrowed ? filteredUnrealised : unrealised;

  const modelled = replayHasFriction(config, capabilities, trades);
  const frictionMode = config?.friction_mode;
  const decided = wins + losses;
  const openCount = isNarrowed ? openTrades.length : (openPositions ?? (trades.length - closed.length));
  const winRate = decided > 0 ? (wins / decided) * 100 : null;
  const pnlTone = trades.length === 0 && pnl === 0 ? 'dim' : pnl > 0 ? 'profit' : pnl < 0 ? 'loss' : 'dim';

  return (
    <div className="rd-metrics-strip" role="region" aria-label="Replay performance" data-testid="replay-metrics">
      <span className="rd-metric" data-tone={pnlTone}>
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
          data-tone={effectiveUnrealised == null || effectiveUnrealised === 0 ? 'dim' : effectiveUnrealised > 0 ? 'profit' : 'loss'}
        >
          {effectiveUnrealised == null || openCount === 0 ? ABSENT : fmtSignedInr(effectiveUnrealised)}
        </span>
      </span>
      <span className="rd-metric-sep" aria-hidden>·</span>
      <span className="rd-metric">
        <span className="rd-metric-value">{fmtInt(signals)}</span>
        <span className="rd-metric-label">signals</span>
      </span>
      {lotSizeSource === 'fallback' && (trades.length > 0) && (
        <>
          <span className="rd-metric-sep" aria-hidden>·</span>
          <span className="rd-metric">
            <span
              className="rd-metric-value"
              data-tone="dim"
              title="Lot sizes came from the engine's built-in table, not the broker's instrument master. Every quantity and P&L here scales with them."
            >
              est. lots
            </span>
          </span>
        </>
      )}
      <span className="rd-metric-sep" aria-hidden>·</span>
      <span className="rd-metric">
        <span className="rd-metric-label">Slippage</span>
        <span
          className="rd-metric-value"
          data-tone={!modelled || !drag ? 'dim' : 'loss'}
          title={
            modelled
              ? 'Total execution drag across the session'
              : frictionMode === 'ideal'
                ? 'Ideal execution — friction was modelled and is exactly zero'
                : 'This replay did not model execution friction'
          }
        >
          {/* An em dash for "never measured", a real zero only for a measured
              one. Printing ₹0.00 for a measurement that was never taken is
              what told traders their strategy had no execution cost. */}
          {!modelled ? ABSENT : fmtSignedInr(-(drag ?? 0))}
        </span>
      </span>
    </div>
  );
});

export const ReplayMetricsStrip = ReplayMetricsCard;
