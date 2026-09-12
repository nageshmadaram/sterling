import { describe, expect, it } from 'vitest';
import { snapbackRowToBoard, snapbackRowsToBoard } from '../snapbackAdapter';
import { ENGINE_LABEL, ENGINE_TAG } from '../boardTypes';
import type { SnapbackRow } from '../../../../hooks/useSnapback';

/**
 * The adapter's job is to lose nothing that changes a decision.
 *
 * Two of these are about a specific failure mode this codebase has shipped: a
 * MODELLED premium rendered as if it were a price, and a level fabricated as
 * zero where the honest answer is "unknown".
 */
const base: SnapbackRow = {
  signal_id: 'snapback:fade_up:NIFTY:1757000000000',
  strategy: 'snapback',
  side: 'fade_up',
  symbol: 'NIFTY',
  state: 'armed',
  reason: null,
  direction: 'BEARISH',
  opt_type: 'PE',
  timestamp_ms: 1757000000000,
  spot: 23437,
  mean_target: 23100,
  distance_pct: 1.44,
  stretch: 2.6,
  level: 23380,
  strength: 'MODERATE',
  realized_vol_pct: 11.4,
  assumed_iv_pct: 13.9,
  assumed_vrp: 1.22,
  hold_days: 10,
  underlying_token: 256265,
  contract: {
    symbol: 'NIFTY25OCT23800PE', strike: 23800, option_type: 'PE',
    expiry: '2026-10-28', dte: 36, lot_size: 75, token: 1234,
    exchange: 'NFO', delta: -0.7, moneyness: 'ITM',
  },
  premium: 520.5,
  premium_is_modelled: false,
  modelled_premium: 511.0,
  quote: {
    premium: 520.5, bid: 519, ask: 522, ltp: 520, oi: 120000,
    spread_pct: 0.58, blockers: [],
  },
  stop_premium: 338.3,
  target_premium: 690.0,
  trail_premium: null,
  runner_premium: null,
  outcome: null,
  lots: 1,
  quantity: 75,
  deployed_inr: 39037.5,
  min_outlay_inr: 39037.5,
  reasons: ['Closed above its 20-session high (23,380.00).'],
  metrics: { stretch_atr: 2.6, lookback_days: 20 },
};

describe('snapback board adapter', () => {
  it('is registered as an engine the board knows', () => {
    expect(ENGINE_LABEL.snapback).toBe('Snapback');
    expect(ENGINE_TAG.snapback).toBe('SB');
  });

  it('carries the PREMIUM ladder, not the spot one', () => {
    const s = snapbackRowToBoard(base);
    expect(s.levels.entry).toBe(520.5);
    expect(s.levels.stop).toBe(338.3);
    // The spot thesis belongs in the detail, not in the price columns.
    expect(s.underlyingPrice).toBe(23437);
  });

  it('a put into strength is a SHORT thesis on the underlying', () => {
    expect(snapbackRowToBoard(base).direction).toBe('short');
    expect(snapbackRowToBoard({
      ...base, side: 'fade_down', direction: 'BULLISH', opt_type: 'CE',
    }).direction).toBe('long');
  });

  it('what is deployed and what is at risk are the SAME number', () => {
    // A bought option cannot lose more than its premium, and a gap through the
    // stop costs exactly that and not a rupee more — so reporting a smaller
    // "at risk" off the stop distance would understate it.
    const s = snapbackRowToBoard(base);
    expect(s.sizing.atRiskInr).toBe(39037.5);
    expect(s.sizing.deployedInr).toBe(s.sizing.atRiskInr);
  });

  it('marks a MODELLED premium so it cannot read as a price', () => {
    const s = snapbackRowToBoard({
      ...base, premium: 511, premium_is_modelled: true, quote: null,
    });
    const mark = s.flags?.find((f) => f.label === 'modelled');
    expect(mark).toBeTruthy();
    expect(mark!.hint).toContain('assumption, not a price');
  });

  it('does not mark a quoted premium', () => {
    const s = snapbackRowToBoard(base);
    expect(s.flags?.some((f) => f.label === 'modelled')).toBe(false);
  });

  it('leads the row flags with how stretched it is', () => {
    expect(snapbackRowToBoard(base).flags?.[0].label).toBe('+2.6 ATR');
  });

  it('charts the UNDERLYING when no contract resolved', () => {
    // There is nothing at the broker to quote, so minting an NFO key would
    // read as "this instrument has no price" rather than as a lookup miss.
    const s = snapbackRowToBoard({
      ...base, contract: null, state: 'watching',
      reason: 'no listed PE between 35 and 60 days out',
    });
    expect(s.instrument.kind).toBe('equity');
    expect(s.instrument.quoteKey).toBe('NSE:NIFTY');
    expect(s.status).toBe('watching');
    expect(s.reason).toContain('no listed PE');
  });

  it('never fabricates a level it does not have', () => {
    const s = snapbackRowToBoard({
      ...base, premium: null, stop_premium: null, target_premium: null,
    });
    expect(s.levels.entry).toBeNull();
    expect(s.levels.stop).toBeNull();
    // Null, never zero: a fabricated 0 on a stop or a target is a
    // trade-destroying lie.
    expect(s.levels.target).toBeNull();
    expect(s.levels.trail).toBeNull();
  });

  it('prices orders off the plan, not off a tick that has moved', () => {
    const s = snapbackRowToBoard(base);
    expect(s.planPriced).toBe(true);
    expect(s.noTrailingStop).toBe(true);
  });

  it('keeps the vol assumption visible in the detail', () => {
    const s = snapbackRowToBoard(base);
    const vol = s.sections.find((x) => x.title.startsWith('Vol and'));
    expect(vol).toBeTruthy();
    expect(vol!.stats.map((st) => st.label)).toContain('Assumed VRP');
    expect(vol!.summary).toContain('credited at exactly zero');
  });

  it('puts the per-lot cost on the row', () => {
    // The number that decides whether a row is actionable at all: a monthly
    // in-the-money option on this universe is ₹30,000 to ₹200,000 per lot, and
    // no percentage budget on a small account reaches it.
    const s = snapbackRowToBoard(base);
    expect(s.flags?.some((f) => f.label.includes('/lot'))).toBe(true);
  });

  it('marks a replayed row as ended so it cannot read as live', () => {
    const s = snapbackRowToBoard({ ...base, historical: true, state: 'ended' });
    expect(s.status).toBe('ended');
    expect(s.flags?.some((f) => f.label === 'replay')).toBe(true);
  });

  it('prices the thesis as a premium TARGET rather than leaving it empty', () => {
    // A target column that is always "—" says nothing about what the trade is
    // for. This is what the contract is worth if spot returns to the mean.
    expect(snapbackRowToBoard(base).levels.target).toBe(690);
  });

  it('an OPEN replayed trade is running, with no realised exit', () => {
    const s = snapbackRowToBoard({
      ...base, historical: true, state: 'running', quote: null,
      outcome: {
        open: true, entry_premium: 500, exit_premium: 540, exit_day: '2026-09-11',
        exit_ms: 1, reason: 'tape_ended', held_days: 2, sessions_left: 8,
        net: 3000, return_pct: 8, spot_out: 23100,
        beta: null, market_pnl: null, hedge_cost: null, net_unhedged: null,
      },
    });
    expect(s.status).toBe('running');
    // Claiming a realised exit on a position still inside its horizon would
    // say the trade was over.
    expect(s.levels.exit).toBeNull();
    expect(s.levels.ltp).toBe(540);
    expect(s.flags?.some((f) => f.label === '8d left')).toBe(true);
  });

  it('a CLOSED replayed trade carries its exit and its result', () => {
    const s = snapbackRowToBoard({
      ...base, historical: true, state: 'ended',
      outcome: {
        open: false, entry_premium: 500, exit_premium: 610, exit_day: '2026-08-20',
        exit_ms: 1, reason: 'horizon', held_days: 10, sessions_left: 0,
        net: 8250, return_pct: 22, spot_out: 22900,
        beta: 1.2, market_pnl: -1700, hedge_cost: 210, net_unhedged: 6340,
      },
    });
    expect(s.status).toBe('ended');
    expect(s.levels.exit).toBe(610);
    expect(s.levels.entry).toBe(500);
    expect(s.flags?.some((f) => f.label === '+22.0%')).toBe(true);
    expect(s.sections.some((x) => x.title === 'How it ended')).toBe(true);
  });

  it('shows what the hedge did, apart from the result', () => {
    // A single hedged figure hides whether the money came from the edge or
    // from the hedge being on the right side of the tape.
    const s = snapbackRowToBoard({
      ...base, historical: true, state: 'ended',
      outcome: {
        open: false, entry_premium: 500, exit_premium: 610, exit_day: '2026-08-20',
        exit_ms: 1, reason: 'horizon', held_days: 10, sessions_left: 0,
        net: 8250, return_pct: 22, spot_out: 22900,
        beta: 1.2, market_pnl: -1700, hedge_cost: 210, net_unhedged: 6340,
      },
    });
    expect(s.flags?.some((f) => f.label === 'β 1.20 hedged')).toBe(true);
    const sec = s.sections.find((x) => x.title === 'The hedge');
    expect(sec).toBeTruthy();
    expect(sec!.stats.map((st) => st.label)).toContain('Unhedged it would be');
  });

  it('says nothing about a hedge on an unhedged trade', () => {
    const s = snapbackRowToBoard({
      ...base, historical: true, state: 'ended',
      outcome: {
        open: false, entry_premium: 500, exit_premium: 610, exit_day: '2026-08-20',
        exit_ms: 1, reason: 'horizon', held_days: 10, sessions_left: 0,
        net: 8250, return_pct: 22, spot_out: 22900,
        beta: null, market_pnl: null, hedge_cost: null, net_unhedged: null,
      },
    });
    expect(s.flags?.some((f) => f.label.includes('hedged'))).toBe(false);
    expect(s.sections.some((x) => x.title === 'The hedge')).toBe(false);
  });

  it('shows a trail only when one is configured', () => {
    expect(snapbackRowToBoard(base).levels.trail).toBeNull();
    expect(snapbackRowToBoard({ ...base, trail_premium: 430 }).levels.trail).toBe(430);
  });

  it('says where the trade stops being a horizon trade', () => {
    // A right-tail book closes its best trades on a session count unless
    // something tells it not to. The number is on the row because the rule is
    // not visible from the levels.
    const off = snapbackRowToBoard(base).flags ?? [];
    expect(off.some((f) => f.label.includes('runs above'))).toBe(false);
    const on = snapbackRowToBoard({ ...base, runner_premium: 900 }).flags ?? [];
    expect(on.some((f) => f.label === 'runs above \u20b9900')).toBe(true);
  });

  it('maps a list without dropping rows', () => {
    expect(snapbackRowsToBoard([base, { ...base, signal_id: 'b' }])).toHaveLength(2);
  });
});
