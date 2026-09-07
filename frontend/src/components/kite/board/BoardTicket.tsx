/**
 * What an expanded row shows, on every board.
 *
 * SuperTrend's expanded leg was the one that got this right, so this is not a
 * lookalike of it — it mounts the same components:
 *
 *   1. AdaptiveEdgePositionCalculator — sizing and P&L
 *   2. QuoteDetail — Buy / Sell, the five-level book, session stats, Greeks
 *
 * The other boards previously showed a depth ladder and a quote-stats card
 * side by side with no way to place an order from them, which is the gap that
 * made them feel like a different product from SuperTrend.
 *
 * Greeks are computed from the live quote rather than taken from the adapter,
 * because a board that has been open for an hour should not be quoting the
 * delta a scan produced an hour ago.
 */
import React from 'react';
import { useKiteQuote } from '../../../hooks/useKite';
import { useOrderWindowStore } from '../../../store/useOrderWindowStore';
import { QuoteDetail } from '../SterlingWatchList';
import { InstrumentLabel } from '../InstrumentLabel';
import { AdaptiveEdgePositionCalculator } from '../AdaptiveEdgePositionCalculator';
import { computeGreeksFromLeg } from '../../../utils/computeGreeks';
import { roundToTick } from '../../../utils/fmt';
import type { BoardSignal } from './boardTypes';

/**
 * Stop and target as a percentage of the working price.
 *
 * The order window takes percentages, not levels, so a Buy raised from here
 * arrives with the signal's own risk already filled in rather than blank.
 * Returns undefined when either end is missing — a bracket derived from a
 * guess is worse than no bracket.
 */
function bracketPct(from: number | null, level: number | null): number | undefined {
  if (!from || from <= 0 || !level || level <= 0) return undefined;
  return Number((((level - from) / from) * 100).toFixed(1));
}

export function BoardTicket({ signal, tag }: {
  signal: BoardSignal;
  /** Marks the order's origin in the broker record. */
  tag?: string;
}) {
  const key = signal.instrument.quoteKey;
  // 'full' carries the five-level book. Fetched per contract on expand, never
  // for the whole universe, so opening a row does not subscribe everything.
  const { data } = useKiteQuote(key ? [key] : [], !!key, 5_000, 'full');
  const quote = key ? (data as Record<string, any> | undefined)?.[key] : undefined;
  const openOrderWindow = useOrderWindowStore((s) => s.openOrderWindow);

  const live = quote?.last_price ?? signal.levels.ltp ?? null;
  const { strike, expiry, optionType, lotSize, symbol, exchange } = signal.instrument;

  // The underlying's price, which the Greeks need. Adapters put it on the
  // parent's spot section; falling back to the premium would be nonsense, so
  // when it is absent the Greeks are simply not computed.
  const spot = React.useMemo(() => {
    for (const section of signal.sections) {
      for (const stat of section.stats) {
        if (!/^spot( entry)?$/i.test(stat.label)) continue;
        const n = Number(String(stat.value ?? '').replace(/[^\d.-]/g, ''));
        if (Number.isFinite(n) && n > 0) return n;
      }
    }
    return null;
  }, [signal.sections]);

  const greeks = React.useMemo(() => {
    if (strike == null || !expiry || !optionType || !spot) return null;
    return computeGreeksFromLeg(strike, expiry, optionType, spot, quote, lotSize ?? null);
  }, [strike, expiry, optionType, spot, quote, lotSize]);

  const ended = signal.status === 'ended';
  const orb = signal.engine === 'orb';
  // ORB Manual must open the scan ticket: qty and SL from the plan, priced
  // off the plan entry so a live premium move cannot silently resize the GTT.
  const planPx = (orb ? signal.levels.entry : null) || live || 0;
  const raiseOrder = (side: 'BUY' | 'SELL') => openOrderWindow({
    symbol,
    exchange,
    initialSide: side,
    lotSize: lotSize || 1,
    initialQty: signal.sizing.quantity ?? undefined,
    lastPrice: planPx,
    initialSlPct: bracketPct(orb ? (signal.levels.entry ?? live) : live, signal.levels.stop),
    initialTgtPct: bracketPct(orb ? (signal.levels.entry ?? live) : live, signal.levels.target),
    tag,
  });

  return (
    <>
      <AdaptiveEdgePositionCalculator
        key={signal.id}
        symbol={symbol}
        tradingsymbol={symbol}
        exchange={exchange}
        expiry={expiry ?? undefined}
        lotSize={lotSize ?? undefined}
        defaultLots={signal.sizing.lots ?? undefined}
        defaultEntryPrice={roundToTick(signal.levels.entry) ?? undefined}
        defaultSl={roundToTick(signal.levels.stop) ?? undefined}
        defaultTsl={roundToTick(signal.levels.trail) ?? undefined}
        defaultExit={roundToTick(signal.levels.target) ?? undefined}
        currentLtp={roundToTick(live) ?? undefined}
        optionType={(optionType ?? 'CE') as 'CE' | 'PE'}
        exitState={signal.status === 'weakening' ? 'EXIT' : 'HOLD'}
        tag={tag}
        hideTsl={signal.engine === 'orb'}
      />

      <QuoteDetail
        sym={key ?? undefined}
        q={quote}
        expiry={expiry ?? undefined}
        spotName={signal.underlying}
        spotPx={spot ?? undefined}
        instrumentName={<InstrumentLabel symbol={symbol} />}
        greeks={greeks ?? undefined}
        // A closed position has nothing to buy. Selling stays available,
        // because a leg can end on the board while the broker still holds it.
        onBuy={ended ? undefined : () => raiseOrder('BUY')}
        onSell={orb ? undefined : () => raiseOrder('SELL')}
      />
    </>
  );
}
