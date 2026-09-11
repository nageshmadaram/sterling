/**
 * Intraday snapshot -> BoardSignal.
 *
 * One row per strategy per symbol. What this pack puts on the board that the
 * others have no equivalent for is **which of three unrelated rules** produced
 * the row, so the strategy is the origin badge rather than a footnote — a
 * pivot break and a ribbon cross on the same symbol are two different trades
 * and must never read as one.
 *
 * The watching rows carry their blockers verbatim. A row that declines to trade
 * and will not say why is the thing this codebase keeps having to fix.
 */
import type {
  BoardInstrument, BoardOrigin, BoardSection, BoardSignal, BoardStatus,
} from './boardTypes';
import type {
  IntradayPosition, IntradayRow, IntradaySnapshot, IntradayStrategyId,
} from '../../../hooks/useIntraday';

/** A tradable price, or nothing. Zero is not a level. */
const price = (v: number | null | undefined): number | null =>
  v == null || !Number.isFinite(v) || v <= 0 ? null : v;

const n = (v: number | null | undefined, dp = 2): string =>
  v == null || !Number.isFinite(v) ? '—' : v.toFixed(dp);

const TONE: Record<IntradayStrategyId, BoardOrigin['tone']> = {
  pivot_break: 'amber',
  ma_ribbon: 'green',
  vwap_supertrend: 'blue',
};

const SHORT: Record<IntradayStrategyId, string> = {
  pivot_break: 'PB',
  ma_ribbon: 'MR',
  vwap_supertrend: 'VS',
};

function instrument(row: IntradayRow): BoardInstrument {
  const c = row.contract;
  if (!c) {
    // No listed contract resolved. Chart the underlying rather than mint an
    // NFO symbol that does not exist.
    return {
      symbol: row.symbol, exchange: 'NSE', kind: 'equity',
      moneyness: null, quoteKey: row.symbol ? `NSE:${row.symbol}` : null,
    };
  }
  return {
    symbol: c.symbol,
    exchange: c.exchange || 'NFO',
    kind: 'option',
    optionType: c.option_type,
    strike: c.strike,
    expiry: c.expiry,
    lotSize: c.lot_size,
    moneyness: null,
    quoteKey: c.symbol ? `${c.exchange || 'NFO'}:${c.symbol}` : null,
  };
}

function origin(row: IntradayRow): BoardOrigin {
  return {
    label: SHORT[row.strategy] ?? row.strategy,
    hint: row.signal?.origin
      ? `${row.strategy_name} — ${row.signal.origin}`
      : row.strategy_name,
    tone: TONE[row.strategy] ?? 'dim',
  };
}

/**
 * What else is worth seeing without opening the row.
 *
 * The reward-to-risk is the one number that decides whether a setup is worth
 * taking at all, and the timeframe is here because this pack is the only thing
 * on this board that is not on the hourly chart — a 5-minute stop read as an
 * hourly one is a trade sized four times too large.
 */
function flags(row: IntradayRow): BoardOrigin[] {
  const out: BoardOrigin[] = [{
    label: row.timeframe, hint: 'Candle interval these rules are evaluated on',
    tone: 'dim',
  }];
  if (row.historical) {
    // Never let a replayed row read as a live one. It is the same rules on the
    // same bars, but nobody traded it.
    out.push({
      label: 'replay',
      hint: 'Replayed from stored bars — the same rules on the same candles, '
        + 'but this was not traded.',
      tone: 'dim',
    });
    if (row.outcome) {
      out.push({
        label: `${row.outcome.r >= 0 ? '+' : ''}${row.outcome.r.toFixed(2)}R`,
        hint: `Closed ${row.outcome.reason} after ${row.outcome.bars_held} bars.`,
        tone: row.outcome.r >= 0 ? 'green' : 'amber',
      });
    }
  }
  const rr = row.signal?.rr;
  if (rr != null && Number.isFinite(rr)) {
    out.push({
      label: `1:${rr.toFixed(1)}`,
      hint: 'Reward to risk on the first target',
      tone: rr >= 2 ? 'green' : 'amber',
    });
  }
  if (row.signal?.target2 != null) {
    out.push({ label: 'runner', hint: 'Banks at 1:2 and runs to 1:3 behind a breakeven stop', tone: 'dim' });
  }
  return out;
}

function sections(row: IntradayRow): BoardSection[] {
  const out: BoardSection[] = [];
  const sig = row.signal;
  if (sig) {
    out.push({
      title: 'Why it fired',
      layout: 'rows',
      summary: sig.origin,
      stats: sig.reasons.map((r, i) => ({ label: `${i + 1}`, value: r })),
    });
    out.push({
      title: 'Trade',
      layout: 'tiles',
      stats: [
        { label: 'Entry', value: n(sig.entry) },
        { label: 'Stop', value: n(sig.stop) },
        { label: 'Risk', value: `${n(sig.risk)} pts` },
        { label: 'Target', value: n(sig.target) },
        { label: 'Runner', value: sig.target2 == null ? '—' : n(sig.target2) },
        { label: 'R:R', value: sig.rr == null ? '—' : `1:${sig.rr.toFixed(2)}` },
      ],
    });
  }
  if (row.blockers.length) {
    out.push({
      title: 'Not firing because',
      layout: 'rows',
      stats: row.blockers.map((b, i) => ({ label: `${i + 1}`, value: b })),
    });
  }
  const m = row.signal?.metrics ?? row.metrics;
  const pivots = m?.pivots as Record<string, number> | undefined;
  if (pivots) {
    out.push({
      title: 'Fibonacci pivots',
      layout: 'tiles',
      summary: 'From the prior session, so the level cannot move under the trade.',
      stats: Object.entries(pivots).map(([k2, v]) => ({ label: k2, value: n(v) })),
    });
  }
  return out;
}

/** A watching row must say why, in one line, on the row itself. */
function reason(row: IntradayRow): string | null {
  if (row.state === 'armed') return null;
  if (row.outcome) {
    const r = row.outcome.r;
    return `${row.outcome.reason} · ${r >= 0 ? '+' : ''}${r.toFixed(2)}R`;
  }
  return row.blockers[0] ?? 'No setup on the last closed candle.';
}

export function intradayRowToBoard(row: IntradayRow): BoardSignal {
  const sig = row.signal;
  const status: BoardStatus = row.state === 'armed'
    ? 'armed'
    : (row.state === 'ended' ? 'ended' : 'watching');
  return {
    id: `intraday:${row.strategy}:${row.symbol}:${sig?.timestamp_ms ?? row.generated_at_ms}`,
    engine: 'intraday',
    underlying: row.symbol,
    instrument: instrument(row),
    direction: sig?.direction === 'BEARISH' ? 'short' : 'long',
    status,
    atMs: sig?.timestamp_ms ?? row.generated_at_ms ?? null,
    levels: {
      ltp: null,
      // Deliberately the SPOT ladder, and the detail pane says so. These three
      // strategies state their stop in the underlying's points — a candle low,
      // the slow EMA, VWAP — and converting it to a premium here would invent a
      // number no rule produced.
      entry: price(sig?.entry),
      stop: price(sig?.stop),
      trail: null,
      target: price(sig?.target),
      exit: price(row.outcome?.exit),
    },
    sizing: { lots: null, quantity: null, atRiskInr: null, deployedInr: null },
    score: null,
    reason: reason(row),
    origin: origin(row),
    flags: flags(row),
    underlyingPrice: price(row.spot),
    sections: sections(row),
  };
}

/**
 * A held position -> BoardSignal.
 *
 * Deliberately the PREMIUM ladder here, where the scanned row carries the spot
 * one. Once a trade is on, the numbers that matter are what was paid, where the
 * trail has got to and what it is worth — the spot thesis moves to `sections`,
 * because it is now context rather than the trade.
 *
 * `broker_stop` gets its own mark. The difference between "protected" and
 * "protected only while this process lives" is not a detail to bury behind a
 * click.
 */
export function intradayPositionToBoard(pos: IntradayPosition): BoardSignal {
  const status: BoardStatus = !pos.is_open
    ? 'ended'
    : pos.breakeven_done ? 'running' : 'running';
  const strat = pos.strategy as IntradayStrategyId;
  const marks: BoardOrigin[] = [{
    label: pos.broker_stop ? 'GTT' : 'monitor only',
    hint: pos.broker_stop
      ? 'The stop is resting at Zerodha and survives this process dying.'
      : 'Nothing is resting at the broker — protection lives only while this process does.',
    tone: pos.broker_stop ? 'green' : 'amber',
  }];
  if (pos.breakeven_done) {
    marks.push({ label: 'BE+', hint: 'The stop is at or above what was paid.', tone: 'green' });
  }
  if (pos.target1_done) {
    marks.push({
      label: pos.scaled_qty > 0 ? `banked ${pos.scaled_qty}` : 'runner',
      hint: pos.scaled_qty > 0
        ? `Half was sold at the first target; ${pos.quantity} runs to ${n(pos.target2)}.`
        : 'A single lot cannot be halved, so the whole position rides the trail.',
      tone: 'green',
    });
  }
  if (pos.exiting) {
    marks.push({ label: 'exiting', hint: 'An exit order is out for this position.', tone: 'amber' });
  }
  return {
    id: `intraday:pos:${pos.contract.tradingsymbol}:${pos.entered_ms}`,
    engine: 'intraday',
    underlying: pos.underlying,
    instrument: {
      symbol: pos.contract.tradingsymbol,
      exchange: pos.contract.exchange || 'NFO',
      kind: 'option',
      optionType: pos.contract.option_type,
      strike: pos.contract.strike,
      expiry: pos.contract.expiry,
      lotSize: pos.contract.lot_size,
      moneyness: null,
      quoteKey: `${pos.contract.exchange || 'NFO'}:${pos.contract.tradingsymbol}`,
    },
    direction: pos.thesis === 'BEARISH' ? 'short' : 'long',
    status,
    atMs: pos.entered_ms || null,
    levels: {
      ltp: null,
      entry: price(pos.effective_entry),
      // `stop` is where the trail has got to; `trail` shows the same thing
      // explicitly so a row can say whether risk is still the original one.
      stop: price(pos.initial_stop),
      trail: price(pos.stop),
      target: price(pos.target),
      exit: price(pos.exit_price),
    },
    sizing: {
      lots: pos.lots || null,
      quantity: pos.quantity || null,
      atRiskInr: pos.quantity > 0 && pos.stop > 0
        ? Math.round((pos.effective_entry - pos.stop) * pos.quantity)
        : null,
      deployedInr: pos.quantity > 0 ? Math.round(pos.effective_entry * pos.quantity) : null,
    },
    score: null,
    reason: pos.is_open ? null : (pos.exit_reason || 'closed'),
    origin: {
      label: SHORT[strat] ?? pos.strategy,
      hint: `${pos.strategy} — held since ${new Date(pos.entered_ms).toLocaleTimeString()}`,
      tone: TONE[strat] ?? 'dim',
    },
    flags: marks,
    underlyingPrice: price(pos.spot_entry),
    sections: [
      {
        title: 'Position',
        layout: 'tiles',
        stats: [
          { label: 'Paid', value: n(pos.effective_entry) },
          { label: 'Stop now', value: n(pos.stop) },
          { label: 'Stop at entry', value: n(pos.initial_stop) },
          { label: 'Target', value: n(pos.target) },
          { label: 'Runner', value: pos.target2 > 0 ? n(pos.target2) : '—' },
          { label: 'Peak', value: n(pos.peak) },
          { label: 'Qty', value: `${pos.quantity} (${pos.lots} lot${pos.lots === 1 ? '' : 's'})` },
        ],
      },
      {
        title: 'The thesis it was entered on',
        layout: 'tiles',
        summary: 'The underlying\u2019s points, not premium.',
        stats: [
          { label: 'Spot entry', value: n(pos.spot_entry) },
          { label: 'Spot stop', value: n(pos.spot_stop) },
          { label: 'Spot target', value: n(pos.spot_target) },
        ],
      },
      ...(pos.is_open ? [] : [{
        title: 'Closed',
        layout: 'tiles' as const,
        stats: [
          { label: 'Exit', value: n(pos.exit_price) },
          { label: 'Reason', value: pos.exit_reason || '—' },
          { label: 'Realised', value: `\u20b9${Math.round(pos.realised_inr)}` },
        ],
      }]),
    ],
  };
}

/**
 * Held positions FIRST, then what was scanned.
 *
 * A row for a contract already held would otherwise appear twice — once as a
 * watching candidate and once as a position — so the scanned copy is dropped.
 * Two rows for one contract is how an operator ends up buying what they are
 * already holding.
 */
export function intradayToBoard(snap: IntradaySnapshot | undefined): BoardSignal[] {
  const positions = (snap?.positions ?? []).map(intradayPositionToBoard);
  const heldSymbols = new Set(
    (snap?.positions ?? []).filter((p) => p.is_open)
      .map((p) => p.contract.tradingsymbol),
  );
  const scanned = (snap?.rows ?? [])
    .filter((r) => !r.contract || !heldSymbols.has(r.contract.symbol))
    .map(intradayRowToBoard);
  // Held, then live, then the replayed history. A row an operator can act on
  // must never be below one nobody can.
  const history = (snap?.history ?? []).map(intradayRowToBoard);
  const seen = new Set([...positions, ...scanned].map((s) => s.id));
  return [...positions, ...scanned, ...history.filter((h) => !seen.has(h.id))];
}
