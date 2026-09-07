/**
 * ORB feed entries -> BoardSignal.
 *
 * ORB is long-options-only: a LONG underlying thesis is expressed by buying a
 * call, a SHORT one by buying a put. It never sells, so the whole premium is at
 * risk and `atRiskInr` is the outlay, not a stop-distance calculation.
 *
 * It quotes no trailing stop. That is not a gap in the adapter — trailing is
 * owned by the universal Trading Mode once a position is open — so `trail` is
 * null and the board drops the TSL column rather than printing a number the
 * engine never produced.
 */
import type { OrbFeedEntry } from '../../../utils/niftyOrbSignalAdapter';
import type { BoardOrigin, BoardSection, BoardSignal, BoardStatus } from './boardTypes';
import { parseTimestampMs } from './boardTypes';

/** A tradable price, or nothing. A zero premium is not a level. */
const price = (v: number | null | undefined): number | null =>
  v == null || !Number.isFinite(v) || v <= 0 ? null : v;

/**
 * Which feed the numbers came from.
 *
 * ORB is configurable between Kite and TrueData, and the two do not agree —
 * they fetch different bar counts, so identical prices can produce different
 * ATR and therefore different signals. Which one spoke is worth stating.
 */
function originOf(entry: OrbFeedEntry): BoardOrigin | undefined {
  if (entry.dataSource === 'kite') {
    return { label: 'KITE', tone: 'brand', hint: 'Bars and quotes from the broker feed — the same source that executes.' };
  }
  if (entry.dataSource === 'truedata') {
    return { label: 'TRUEDATA', tone: 'blue', hint: 'Bars and quotes from TrueData. Execution still goes through Kite.' };
  }
  return undefined;
}

/**
 * `SIGNAL_UNRESOLVED` is an error, not a quiet row.
 *
 * It means the strategy fired and then could not resolve a contract to express
 * it — an expiry window that reaches no listed expiry, a chain that fails the
 * liquidity floor. Mapping it to `watching` filed a live breakout alongside
 * underlyings that had no setup at all, and since the board only promotes
 * ACTIONABLE rows, the one row that needed attention was the one collapsed out
 * of sight. The reason string already says what blocked it; this makes sure
 * somebody sees it.
 */
function status(entry: OrbFeedEntry): BoardStatus {
  if (entry.state === 'ENDED') return 'ended';
  if (entry.state === 'ERROR' || entry.state === 'SIGNAL_UNRESOLVED') return 'error';
  if (entry.state === 'SIGNAL') return 'armed';
  return 'watching';
}

function atMs(entry: OrbFeedEntry): number | null {
  return parseTimestampMs(
    entry.timestamp ?? (entry as any).timestamp_ms ?? (entry as any).atMs ?? (entry as any).time ?? (entry as any).created_at ?? (entry as any).session_date
  );
}

/**
 * The underlying thesis: the levels the option was chosen to express.
 *
 * An index carries no traded volume of its own, so its average-price line is a
 * time-weighted mean, not a volume-weighted one. They are different lines and
 * the tile is named for whichever one the signal was actually measured against
 * — printing "VWAP" over a TWAP would misdescribe the level the trade was taken
 * from. The volume tile says "no feed" for the same reason: on an index the
 * 1.00× ratio is a placeholder for a gate that never ran, not a measurement.
 */
function setupSection(entry: OrbFeedEntry): BoardSection {
  const line = entry.vwapBasis === 'time' ? 'TWAP' : 'VWAP';
  return {
    title: `Opening range & ${line}`,
    layout: 'tiles',
    summary: entry.dataSource ?? undefined,
    stats: [
      { label: 'Spot', value: entry.spot?.toFixed(2), hint: 'Underlying last price at scan' },
      { label: 'ORB high', value: entry.orbHigh?.toFixed(2), hint: 'High of the opening range' },
      { label: 'ORB low', value: entry.orbLow?.toFixed(2), hint: 'Low of the opening range' },
      {
        label: line,
        value: entry.vwap?.toFixed(2),
        estimated: entry.vwapBasis === 'time',
        hint: entry.vwapBasis === 'time'
          ? 'Time-weighted average typical price. This feed reports no volume, so a volume-weighted line is not available.'
          : 'Session volume-weighted average price',
      },
      { label: 'ATR', value: entry.atr?.toFixed(2), hint: 'Average true range, session-scoped' },
      {
        label: 'Volume',
        value: !entry.volumeConfirmed ? 'no feed'
          : entry.volumeRatio == null ? undefined : `${entry.volumeRatio.toFixed(2)}×`,
        estimated: !entry.volumeConfirmed,
        hint: !entry.volumeConfirmed
          ? 'This instrument reports no traded volume, so the participation gate was not evaluated.'
          : 'Bar volume against this session’s baseline',
      },
      { label: 'U. entry', value: entry.underlyingEntry?.toFixed(2), hint: 'Underlying level the breakout triggers at' },
      { label: 'U. stop', value: entry.underlyingStop?.toFixed(2), hint: 'Underlying stop the premium stop is derived from' },
    ],
  };
}

/**
 * Greeks, marked as modelled.
 *
 * Kite publishes none of these — they are solved from the traded premium — and
 * the delta in particular is what the premium stop rests on, so a delta the
 * model guessed must not look like one the broker quoted.
 */
function greeksSection(entry: OrbFeedEntry): BoardSection | null {
  const has = entry.impliedVol != null || entry.delta != null || entry.gamma != null;
  if (!has) return null;
  const modelled = entry.deltaSource !== 'broker';
  return {
    title: 'Greeks',
    layout: 'tiles',
    summary: entry.deltaSource === 'assumed' ? 'assumed' : modelled ? 'solved from premium' : 'from broker',
    stats: [
      { label: 'IV', value: entry.impliedVol == null ? undefined : `${(entry.impliedVol * 100).toFixed(1)}%`, estimated: modelled, hint: 'Implied volatility solved from the traded premium' },
      {
        label: 'Δ delta',
        value: entry.delta == null ? undefined
          : entry.deltaSource === 'assumed' ? `${entry.delta.toFixed(3)} assumed` : entry.delta.toFixed(3),
        estimated: modelled,
        hint: entry.deltaSource === 'assumed'
          ? 'Neither quoted nor solved — a default. The premium stop rests on it.'
          : 'The premium stop is derived from this',
      },
      { label: 'Γ gamma', value: entry.gamma?.toFixed(5), estimated: modelled },
      { label: 'Θ theta/day', value: entry.thetaPerDay?.toFixed(1), estimated: modelled, hint: 'Premium lost per calendar day, all else equal' },
      { label: 'V vega', value: entry.vegaPerPoint?.toFixed(1), estimated: modelled, hint: 'Premium change per volatility point' },
      { label: 'Lot', value: entry.lotSize ?? undefined },
    ],
  };
}

export function orbToBoard(entry: OrbFeedEntry): BoardSignal {
  const sections = [setupSection(entry), greeksSection(entry)].filter(Boolean) as BoardSection[];
  const exchange = entry.exchange ?? 'NFO';
  const symbol = entry.optionSymbol ?? entry.underlying;

  return {
    id: entry.id,
    engine: 'orb',
    underlying: entry.underlying,
    instrument: {
      symbol,
      exchange,
      kind: entry.optionSymbol ? 'option' : 'index',
      optionType: (entry.optionType ?? undefined) as 'CE' | 'PE' | undefined,
      strike: entry.optionStrike ?? null,
      expiry: entry.optionExpiry ?? null,
      lotSize: entry.lotSize ?? null,
      quoteKey: entry.optionSymbol ? `${exchange}:${entry.optionSymbol}` : null,
    },
    direction: entry.direction === 'short' ? 'short' : 'long',
    status: status(entry),
    atMs: atMs(entry),
    levels: {
      ltp: price(entry.optionPremium),
      entry: price(entry.optionPremium),
      stop: price(entry.stopPremium),
      // Trailing belongs to Trading Mode, not to ORB.
      trail: null,
      target: price(entry.targetPremium),
      exit: null,
    },
    sizing: {
      lots: entry.lotSize && entry.quantity ? Math.round(entry.quantity / entry.lotSize) : null,
      quantity: entry.quantity ?? null,
      // A bought option can expire worthless, so the whole outlay is the risk.
      atRiskInr: entry.maxLossInr ?? null,
      deployedInr: entry.maxLossInr ?? null,
    },
    score: null,
    origin: originOf(entry),
    flags: [
      ...(entry.state === 'ENDED'
        ? [{ label: 'PAST', tone: 'dim' as const, hint: 'Generated earlier — kept on the board like SuperTrend history. Not a live ticket.' }]
        : []),
      ...(entry.autoBlock
        ? [{ label: 'AUTO BLOCK', tone: 'amber' as const, hint: entry.autoBlock }]
        : []),
      ...(entry.deltaSource === 'assumed'
        ? [{ label: 'Δ ASSUMED', tone: 'amber' as const, hint: 'Delta is 0.50 by default — the premium stop rests on it.' }]
        : []),
      ...(entry.quoteAgeS != null && entry.quoteAgeS > 15
        ? [{ label: 'STALE', tone: 'dim' as const, hint: `Quote is ${Math.round(entry.quoteAgeS)}s old` }]
        : []),
    ],
    ticketFingerprint: entry.ticketFingerprint ?? null,
    delta: entry.delta,
    reason: entry.autoBlock ?? entry.reason ?? null,
    quoteAgeS: entry.quoteAgeS ?? null,
    sections,
  };
}
