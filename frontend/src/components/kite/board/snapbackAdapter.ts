/**
 * Snapback snapshot -> BoardSignal.
 *
 * Two things this engine puts on the board that no other one has, and both are
 * on the row rather than behind a click because both change whether you would
 * take the trade:
 *
 * **How stretched it is.** The signal is a distance from a mean, so the size of
 * that distance IS the conviction. A 3-ATR push and a 1.5-ATR one are the same
 * rule at very different strengths.
 *
 * **Whether the premium is a price or an assumption.** With the market shut, or
 * before any contract resolves, the only premium available is a Black-Scholes
 * one computed at a modelled vol. Every other engine here would show that as a
 * number; this one marks it, because an operator cannot size a trade off a
 * figure whose provenance is ambiguous, and this repo has shipped exactly that
 * bug before.
 */
import type {
  BoardInstrument, BoardOrigin, BoardSection, BoardSignal, BoardStatus,
} from './boardTypes';
import type { SnapbackRow, SnapbackSide } from '../../../hooks/useSnapback';

/** A tradable price, or nothing. Zero is not a level. */
const price = (v: number | null | undefined): number | null =>
  v == null || !Number.isFinite(v) || v <= 0 ? null : v;

const n = (v: number | null | undefined, dp = 2): string =>
  v == null || !Number.isFinite(v) ? '—' : v.toFixed(dp);

const SIDE_LABEL: Record<SnapbackSide, string> = {
  fade_up: 'Fade push',
  fade_down: 'Fade flush',
};

const SIDE_TONE: Record<SnapbackSide, BoardOrigin['tone']> = {
  fade_up: 'purple',
  fade_down: 'amber',
};

function instrument(row: SnapbackRow): BoardInstrument {
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
    moneyness: c.moneyness ?? null,
    quoteKey: c.symbol ? `${c.exchange || 'NFO'}:${c.symbol}` : null,
  };
}

function origin(row: SnapbackRow): BoardOrigin {
  return {
    label: SIDE_LABEL[row.side] ?? row.side,
    hint: row.side === 'fade_up'
      ? `Closed above its ${row.metrics?.lookback_days ?? 20}-session high while `
        + 'stretched above its own mean — buys the put.'
      : 'Closed below its 20-session low while stretched below its own mean — '
        + 'buys the call.',
    tone: SIDE_TONE[row.side] ?? 'dim',
  };
}

/**
 * What else is worth seeing WITHOUT opening the row.
 *
 * The stretch is first because it is the conviction. The modelled-premium mark
 * is second because it changes what the price column means.
 */
function flags(row: SnapbackRow): BoardOrigin[] {
  const o = row.outcome;
  const out: BoardOrigin[] = [{
    label: `${row.stretch >= 0 ? '+' : ''}${row.stretch.toFixed(1)} ATR`,
    hint: `Distance from the ${row.metrics?.mean_ema ?? 20}-session mean, in `
      + 'ATR(14). This is the size of the move being faded.',
    tone: Math.abs(row.stretch) >= 3 ? 'green' : 'dim',
  }];
  if (row.premium_is_modelled) {
    out.push({
      label: 'modelled',
      hint: `No live quote, so the premium is Black-Scholes at `
        + `${row.assumed_iv_pct.toFixed(1)}% vol — the instrument's own `
        + `${row.realized_vol_pct.toFixed(1)}% realised vol times `
        + `${row.assumed_vrp.toFixed(2)}. It is an assumption, not a price.`,
      tone: 'amber',
    });
  }
  if (row.runner_premium != null) {
    out.push({
      label: `runs above \u20b9${row.runner_premium.toFixed(0)}`,
      hint: 'Above this premium the position is held PAST the horizon, under a '
        + 'give-back ratchet, instead of being closed on the session count. '
        + 'The edge here is a right tail — the top 1% of trades carry 148% of '
        + 'the P&L — so a fixed horizon closes the few trades that pay for the '
        + 'rest. Out of sample this moved the book from +3.46% to +4.03% per '
        + 'entry day on the same 409 entry days.',
      tone: 'blue',
    });
  }
  if (row.contract?.delta != null) {
    out.push({
      label: `Δ ${Math.abs(row.contract.delta).toFixed(2)}`,
      hint: 'Delta is the leverage. This engine picks the strike BY delta '
        + 'rather than by a moneyness rung, because a rung is a different '
        + 'amount of leverage at every vol level and tenor.',
      tone: 'dim',
    });
  }
  if (o) {
    out.push({
      label: `${o.return_pct >= 0 ? '+' : ''}${o.return_pct.toFixed(1)}%`,
      hint: o.open
        ? `Open ${o.held_days} of ${o.held_days + o.sessions_left} sessions — `
          + 'unrealised, at the replay\u2019s own valuation.'
        : `Closed ${o.reason.replace(/_/g, ' ')} after ${o.held_days} sessions.`,
      tone: o.return_pct >= 0 ? 'green' : 'amber',
    });
  }
  if (o?.beta != null) {
    out.push({
      label: `β ${o.beta.toFixed(2)} hedged`,
      hint: 'The market was hedged out with an index future, sized by this '
        + 'beta. The signal\u2019s edge is RELATIVE — a bought put is a large '
        + 'SHORT position in the market, and over nine rising years that cost '
        + 'more than the edge earned. A future rather than a bought index call: '
        + 'a call is convex and would quietly become a second long-market bet.',
      tone: 'blue',
    });
  }
  if (o?.open) {
    out.push({
      label: `${o.sessions_left}d left`,
      hint: 'Sessions remaining in the holding period. The measured edge is a '
        + 'MEAN over the whole horizon, so an early exit collects a different '
        + 'distribution.',
      tone: 'dim',
    });
  }
  if (row.historical) {
    // Never let a replayed row read as a live one. Same rules, same bars,
    // nobody traded it.
    out.push({
      label: 'replay',
      hint: 'Replayed from stored daily bars — the same rule on the same '
        + 'sessions, but this was not traded. The live scan only looks at the '
        + 'last few closed sessions, so without this the board is blank almost '
        + 'always.',
      tone: 'dim',
    });
  }
  if (row.min_outlay_inr != null) {
    // The number that decides whether a row is actionable at all. A monthly
    // in-the-money option on this universe is ₹30,000 to ₹200,000 of premium
    // per lot, and no percentage budget on a small account reaches it.
    out.push({
      label: `₹${Math.round(row.min_outlay_inr).toLocaleString('en-IN')}/lot`,
      hint: 'Premium for ONE lot — the minimum this trade can be taken in. '
        + 'A bought option cannot be sized below one lot, so this is the real '
        + 'capital requirement.',
      tone: 'dim',
    });
  }
  if (row.contract?.dte != null) {
    out.push({
      label: `${row.contract.dte}d`,
      hint: 'Days to expiry. Theta per rupee of premium runs at about 1/(2T), '
        + 'so a shorter contract spends the edge on decay.',
      tone: 'dim',
    });
  }
  out.push({
    label: `hold ${row.hold_days}d`,
    hint: 'Sessions held. The measured edge is a MEAN over this horizon, so an '
      + 'early exit collects a different distribution from the one measured.',
    tone: 'dim',
  });
  return out;
}

function sections(row: SnapbackRow): BoardSection[] {
  const out: BoardSection[] = [];
  out.push({
    title: 'Why it fired',
    layout: 'rows',
    summary: `${SIDE_LABEL[row.side]} — back towards ${n(row.mean_target)}`,
    stats: row.reasons.map((r, i) => ({ label: `${i + 1}`, value: r })),
  });
  out.push({
    title: 'The move being faded',
    layout: 'tiles',
    stats: [
      { label: 'Spot', value: n(row.spot) },
      { label: 'Level broken', value: n(row.level) },
      { label: 'Mean target', value: n(row.mean_target) },
      { label: 'Distance', value: `${n(row.distance_pct, 2)}%` },
      { label: 'Stretch', value: `${row.stretch.toFixed(2)} ATR` },
      { label: 'Strength', value: row.strength },
    ],
  });
  out.push({
    title: 'Vol and what the premium assumes',
    layout: 'tiles',
    summary: 'The exit is valued at the ENTRY’s vol in every measurement '
      + 'behind this engine, so the vega a successful fade would earn is '
      + 'credited at exactly zero.',
    stats: [
      { label: 'Realised vol', value: `${n(row.realized_vol_pct, 1)}%` },
      { label: 'Modelled IV', value: `${n(row.assumed_iv_pct, 1)}%` },
      { label: 'Assumed VRP', value: `${row.assumed_vrp.toFixed(2)}x` },
      {
        label: 'Premium',
        value: row.premium == null ? '—'
          : `${n(row.premium)}${row.premium_is_modelled ? ' (modelled)' : ''}`,
      },
      { label: 'Quoted', value: n(row.quote?.premium) },
      {
        label: 'One lot',
        value: row.min_outlay_inr == null ? '—'
          : `₹${Math.round(row.min_outlay_inr).toLocaleString('en-IN')}`,
      },
      {
        label: 'Spread',
        value: row.quote?.spread_pct == null ? '—'
          : `${n(row.quote.spread_pct, 1)}%`,
      },
    ],
  });
  if (row.outcome) {
    const o2 = row.outcome;
    out.push({
      title: o2.open ? 'Position, so far' : 'How it ended',
      layout: 'tiles',
      summary: o2.open
        ? `Open ${o2.held_days} of ${o2.held_days + o2.sessions_left} sessions.`
        : `Closed ${o2.reason.replace(/_/g, ' ')} after ${o2.held_days} sessions.`,
      stats: [
        { label: 'Entry', value: n(o2.entry_premium) },
        { label: o2.open ? 'Now' : 'Exit', value: n(o2.exit_premium) },
        { label: 'Return', value: `${o2.return_pct >= 0 ? '+' : ''}${n(o2.return_pct, 1)}%` },
        { label: 'Per lot', value: `₹${Math.round(o2.net).toLocaleString('en-IN')}` },
        { label: 'Sessions', value: String(o2.held_days) },
        { label: 'Spot now', value: n(o2.spot_out) },
      ],
    });
    if (o2.beta != null) {
      out.push({
        title: 'The hedge',
        layout: 'tiles',
        summary: 'Shown apart from the result on purpose: a single hedged '
          + 'figure hides whether the money came from the edge or from the '
          + 'hedge being on the right side of the tape.',
        stats: [
          { label: 'Beta', value: n(o2.beta) },
          {
            label: 'Market removed',
            value: o2.market_pnl == null ? '—'
              : `₹${Math.round(o2.market_pnl).toLocaleString('en-IN')}`,
          },
          {
            label: 'Hedge cost',
            value: o2.hedge_cost == null ? '—'
              : `₹${Math.round(o2.hedge_cost).toLocaleString('en-IN')}`,
          },
          {
            label: 'Unhedged it would be',
            value: o2.net_unhedged == null ? '—'
              : `₹${Math.round(o2.net_unhedged).toLocaleString('en-IN')}`,
          },
        ],
      });
    }
  }
  if (row.reason) {
    out.push({
      title: 'Not armed because',
      layout: 'rows',
      stats: [{ label: '1', value: row.reason }],
    });
  }
  return out;
}

const STATUS: Record<string, BoardStatus> = {
  armed: 'armed', watching: 'watching', running: 'running', ended: 'ended',
};

export function snapbackRowToBoard(row: SnapbackRow): BoardSignal {
  const status: BoardStatus = STATUS[row.state] ?? 'watching';
  const o = row.outcome;
  const premium = price(row.premium);
  return {
    id: row.signal_id,
    engine: 'snapback',
    underlying: row.symbol,
    instrument: instrument(row),
    // The OPTION is bought either way, so the row's direction is the thesis
    // about the underlying: a put into strength is a short thesis.
    direction: row.direction === 'BEARISH' ? 'short' : 'long',
    status,
    atMs: row.timestamp_ms ?? null,
    levels: {
      // A replayed row has no live quote; its "last price" is what the replay
      // last valued the contract at, which for an OPEN one is exactly that.
      ltp: price(row.quote?.ltp) ?? (o ? price(o.exit_premium) : null),
      // The PREMIUM ladder. The spot thesis lives in `sections` — what an
      // operator acts on here is what the contract costs and where it stops.
      entry: o ? price(o.entry_premium) : premium,
      stop: price(row.stop_premium),
      trail: price(row.trail_premium),
      // The thesis' own objective: what this contract is worth if spot returns
      // to the mean. Priced at the entry's vol, like every other number here.
      target: price(row.target_premium),
      // Only a CLOSED trade has a realised exit. Putting one on a position
      // still inside its holding period would claim it was over.
      exit: o && !o.open ? price(o.exit_premium) : null,
    },
    sizing: {
      lots: row.lots || null,
      quantity: row.quantity || null,
      // A bought option's maximum loss IS its premium, so what is deployed and
      // what is at risk are the same number. Reporting a smaller "at risk" off
      // the stop would understate it: a gap through the stop still costs the
      // whole premium and nothing more.
      atRiskInr: row.deployed_inr ?? null,
      deployedInr: row.deployed_inr ?? null,
    },
    score: null,
    reason: row.reason,
    origin: origin(row),
    flags: flags(row),
    underlyingPrice: price(row.spot),
    delta: row.contract?.delta ?? null,
    // Every level on this row was computed against a daily close, so an order
    // must be priced off `levels.entry` rather than off a tick that has moved
    // since — otherwise a trade planned at one size silently becomes another.
    planPriced: true,
    // The exit is a horizon and a premium stop, both enforced server-side. A
    // trailing-stop field in the order window would offer a second, competing
    // trail.
    noTrailingStop: true,
    sections: sections(row),
  };
}

export function snapbackRowsToBoard(rows: readonly SnapbackRow[]): BoardSignal[] {
  return rows.map(snapbackRowToBoard);
}
