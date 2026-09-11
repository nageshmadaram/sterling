/**
 * Intraday pack -> board.
 *
 * The rows this adapter has to get right are the ones that stop an operator
 * misreading a setup: which of three unrelated strategies produced it, that the
 * levels are the UNDERLYING's points rather than premium, and that a watching
 * row always says why it is not firing.
 */
import { describe, it, expect } from 'vitest';
import {
  intradayPositionToBoard, intradayRowToBoard, intradayToBoard,
} from '../intradayAdapter';
import type {
  IntradayPosition, IntradayRow, IntradaySnapshot, IntradayStrategyId,
} from '../../../../hooks/useIntraday';

function row(over: Partial<IntradayRow> = {}): IntradayRow {
  return {
    signal_id: 'pivot_break:NIFTY:1789009200000',
    strategy: 'pivot_break',
    strategy_name: 'Pivot Break',
    symbol: 'NIFTY',
    state: 'armed',
    blockers: [],
    spot: 24800.5,
    timeframe: '5m',
    contract: {
      symbol: 'NIFTY26SEP24800CE', strike: 24800, option_type: 'CE',
      expiry: '2026-09-17', dte: 6, lot_size: 75, token: 1234, exchange: 'NFO',
    },
    generated_at_ms: 1_789_009_200_000,
    signal: {
      strategy: 'pivot_break', symbol: 'NIFTY', direction: 'BULLISH', opt_type: 'CE',
      timestamp_ms: 1_789_009_200_000, entry: 24800.5, stop: 24780.0,
      target: 24841.5, target2: 24862.0, risk: 20.5, rr: 2.0,
      strength: 'STRONG', origin: 'R1 + EMA9',
      reasons: ['strong green candle', 'closed through pivot R1'],
      metrics: { pivots: { P: 24700, R1: 24790 } },
    },
    metrics: {},
    ...over,
  };
}

function position(over: Partial<IntradayPosition> = {}): IntradayPosition {
  return {
    strategy: 'pivot_break', signal_id: 's1', underlying: 'NIFTY',
    contract: {
      tradingsymbol: 'NIFTY26SEP24800CE', exchange: 'NFO', token: 1234,
      option_type: 'CE', strike: 24800, expiry: '2026-09-17',
      lot_size: 75, tick_size: 0.05,
    },
    thesis: 'BULLISH', side: 'long',
    spot_entry: 24800, spot_stop: 24780, spot_target: 24840, spot_target2: null,
    spot_risk: 20,
    entry: 100, stop: 120, initial_stop: 70, target: 160, target2: 190,
    quantity: 75, lots: 1, fill_price: 100, effective_entry: 100, peak: 160,
    breakeven_done: true, target1_done: false, scaled_qty: 0, scaled_price: 0,
    exiting: false, order_id: 'O1', gtt_id: 901,
    broker_stop: true, stop_mode: 'both', status: 'open', is_open: true,
    entered_ms: 1_789_009_200_000, entry_day: '2026-09-11',
    exit_price: 0, exit_reason: '', realised_inr: 0,
    ...over,
  };
}


function snapshot(rows: IntradayRow[], over: Partial<IntradaySnapshot> = {}): IntradaySnapshot {
  return {
    strategy: {
      id: 'intraday', name: 'Intraday Pack', contract_version: 'A400.1',
      tagline: '', strategies: [], provenance: '', validated: false,
      calibration: {}, calibrated_fields: [],
    },
    config: {} as IntradaySnapshot['config'],
    warnings: [], enabled_strategies: [], rows, armed: 0, scanned: 0,
    scanning: false, failures: [], last_scan_ms: 0, last_error: null,
    generated_at_ms: 0,
    ...over,
  };
}

describe('intraday adapter', () => {
  it('names the strategy on the row, because three unrelated rules share one board', () => {
    const ids: IntradayStrategyId[] = ['pivot_break', 'ma_ribbon', 'vwap_supertrend'];
    const tags = ids.map((id) => intradayRowToBoard(row({ strategy: id })).origin?.label);
    expect(tags).toEqual(['PB', 'MR', 'VS']);
    // And the ids stay distinct, so two strategies on one symbol are two rows.
    const both = intradayToBoard(snapshot([row(), row({ strategy: 'ma_ribbon' })]));
    expect(new Set(both.map((s) => s.id)).size).toBe(2);
  });

  it('carries the spot ladder the strategy actually stated', () => {
    const s = intradayRowToBoard(row());
    expect(s.levels.entry).toBe(24800.5);
    expect(s.levels.stop).toBe(24780.0);
    expect(s.levels.target).toBe(24841.5);
    expect(s.underlyingPrice).toBe(24800.5);
    // Nothing here is a premium, so the sizing columns stay empty rather than
    // showing a fabricated zero.
    expect(s.sizing.quantity).toBeNull();
    expect(s.levels.ltp).toBeNull();
  });

  it('trades the resolved contract, not the underlying', () => {
    const s = intradayRowToBoard(row());
    expect(s.instrument.symbol).toBe('NIFTY26SEP24800CE');
    expect(s.instrument.kind).toBe('option');
    expect(s.instrument.quoteKey).toBe('NFO:NIFTY26SEP24800CE');
  });

  it('charts the underlying when no contract resolved, never a minted symbol', () => {
    const s = intradayRowToBoard(row({ contract: null }));
    expect(s.instrument.symbol).toBe('NIFTY');
    expect(s.instrument.quoteKey).toBe('NSE:NIFTY');
    expect(s.instrument.kind).toBe('equity');
  });

  it('a watching row always says why it is not firing', () => {
    const s = intradayRowToBoard(row({
      state: 'watching', signal: null,
      blockers: ['body 31% of range < 55%', 'no pivot level broken on this bar'],
    }));
    expect(s.status).toBe('watching');
    expect(s.reason).toBe('body 31% of range < 55%');
    const why = s.sections.find((sec) => sec.title === 'Not firing because');
    expect(why?.stats).toHaveLength(2);
  });

  it('falls back to a sentence rather than an empty reason', () => {
    const s = intradayRowToBoard(row({ state: 'watching', signal: null, blockers: [] }));
    expect(s.reason).toMatch(/No setup/);
  });

  it('flags the timeframe, because this is the only 5-minute engine on the board', () => {
    const labels = intradayRowToBoard(row()).flags?.map((f) => f.label) ?? [];
    expect(labels).toContain('5m');
    expect(labels).toContain('1:2.0');
    expect(labels).toContain('runner');
  });

  it('marks a thin reward-to-risk amber and a fat one green', () => {
    const thin = intradayRowToBoard(row({
      signal: { ...row().signal!, rr: 1.1, target2: null },
    }));
    expect(thin.flags?.find((f) => f.label === '1:1.1')?.tone).toBe('amber');
    expect(thin.flags?.some((f) => f.label === 'runner')).toBe(false);
  });

  it('maps direction to the option side the engine chose', () => {
    const short = intradayRowToBoard(row({
      strategy: 'vwap_supertrend',
      signal: { ...row().signal!, direction: 'BEARISH', opt_type: 'PE' },
    }));
    expect(short.direction).toBe('short');
  });

  it('publishes the pivot ladder for a break, so the level can be checked', () => {
    const s = intradayRowToBoard(row());
    const pivots = s.sections.find((sec) => sec.title === 'Fibonacci pivots');
    expect(pivots?.stats.map((st) => st.label)).toEqual(['P', 'R1']);
  });

  it('a held position shows the PREMIUM ladder, and the trail beside the original stop', () => {
    const s = intradayPositionToBoard(position());
    expect(s.status).toBe('running');
    expect(s.levels.entry).toBe(100);
    // `stop` stays the risk taken at entry; `trail` is where it has got to —
    // a board showing only one cannot say whether a trade is still at full risk.
    expect(s.levels.stop).toBe(70);
    expect(s.levels.trail).toBe(120);
    expect(s.sizing.quantity).toBe(75);
    expect(s.sizing.deployedInr).toBe(7500);
  });

  it('says whether the stop is actually resting at the broker', () => {
    const rested = intradayPositionToBoard(position());
    expect(rested.flags?.map((f) => f.label)).toContain('GTT');
    const monitorOnly = intradayPositionToBoard(position({ broker_stop: false }));
    const mark = monitorOnly.flags?.find((f) => f.label === 'monitor only');
    expect(mark?.tone).toBe('amber');
  });

  it('a bought put is still drawn from the thesis, not the order side', () => {
    const s = intradayPositionToBoard(position({
      thesis: 'BEARISH',
      contract: { ...position().contract, option_type: 'PE' },
    }));
    expect(s.direction).toBe('short');
    expect(s.instrument.optionType).toBe('PE');
  });

  it('a closed position keeps its reason and its realised number', () => {
    const s = intradayPositionToBoard(position({
      is_open: false, status: 'closed', exit_reason: 'trailing stop',
      exit_price: 130, realised_inr: 2250,
    }));
    expect(s.status).toBe('ended');
    expect(s.reason).toBe('trailing stop');
    expect(s.levels.exit).toBe(130);
  });

  it('a contract already held is not also offered as a candidate', () => {
    // Two rows for one contract is how an operator buys what they already hold.
    const board = intradayToBoard(snapshot([row()], { positions: [position()] }));
    expect(board).toHaveLength(1);
    expect(board[0].id).toMatch(/^intraday:pos:/);
  });

  it('positions come before candidates', () => {
    const other = row({ contract: { ...row().contract!, symbol: 'OTHER26SEP1CE' } });
    const board = intradayToBoard(snapshot([other], { positions: [position()] }));
    expect(board[0].id).toMatch(/^intraday:pos:/);
    expect(board[1].id).toMatch(/^intraday:pivot_break:/);
  });

  it('an empty snapshot is an empty board, not a crash', () => {
    expect(intradayToBoard(undefined)).toEqual([]);
    expect(intradayToBoard(snapshot([]))).toEqual([]);
  });
});

describe('the runner leg', () => {
  it('a banked position says so, and what is still running', () => {
    const s = intradayPositionToBoard(position({
      target1_done: true, scaled_qty: 75, scaled_price: 160, quantity: 75, target2: 190,
    }));
    const labels = s.flags?.map((f) => f.label) ?? [];
    expect(labels).toContain('banked 75');
    const tile = s.sections[0].stats.find((st) => st.label === 'Runner');
    expect(tile?.value).toBe('190.00');
  });

  it('a single lot that could not be halved says that instead', () => {
    const s = intradayPositionToBoard(position({
      target1_done: true, scaled_qty: 0, quantity: 75, target2: 190,
    }));
    expect(s.flags?.map((f) => f.label)).toContain('runner');
  });

  it('a one-target strategy shows no runner at all', () => {
    const s = intradayPositionToBoard(position({ target2: 0 }));
    const tile = s.sections[0].stats.find((st) => st.label === 'Runner');
    expect(tile?.value).toBe('—');
  });
});


describe('the replayed history', () => {
  function historical(over: Partial<IntradayRow> = {}): IntradayRow {
    return {
      ...row(),
      state: 'ended',
      historical: true,
      contract: null,
      outcome: { exit: 24_845, reason: 'target', points: 45, r: 2.1,
                 bars_held: 9, exit_ms: 1_789_012_000_000 },
      ...over,
    };
  }

  it('is drawn as ended, never as something to act on', () => {
    const s = intradayRowToBoard(historical());
    expect(s.status).toBe('ended');
    expect(s.levels.exit).toBe(24_845);
  });

  it('is marked as a replay so it cannot read as a live signal', () => {
    // Same rules on the same bars — but nobody traded it.
    const labels = intradayRowToBoard(historical()).flags?.map((f) => f.label) ?? [];
    expect(labels).toContain('replay');
    expect(labels).toContain('+2.10R');
  });

  it('says how it ended, on the row', () => {
    expect(intradayRowToBoard(historical()).reason).toBe('target · +2.10R');
    const lost = intradayRowToBoard(historical({
      outcome: { exit: 24_780, reason: 'stop', points: -20, r: -1.0,
                 bars_held: 3, exit_ms: 1 },
    }));
    expect(lost.reason).toBe('stop · -1.00R');
    expect(lost.flags?.find((f) => f.label === '-1.00R')?.tone).toBe('amber');
  });

  it('sits BELOW anything an operator can act on', () => {
    // A row nobody can act on must never be above one they can.
    const board = intradayToBoard(snapshot([row()], {
      positions: [position({ contract: { ...position().contract,
                                         tradingsymbol: 'OTHER26SEP1CE' } })],
      history: [historical({
        signal: { ...row().signal!, timestamp_ms: 1_788_000_000_000 },
      })],
    }));
    expect(board[0].id).toMatch(/^intraday:pos:/);
    expect(board[1].status).toBe('armed');
    expect(board[board.length - 1].status).toBe('ended');
  });

  it('does not duplicate a signal the live scan already shows', () => {
    const live = row();
    const board = intradayToBoard(snapshot([live], {
      history: [historical({ signal_id: live.signal_id })],
    }));
    expect(board).toHaveLength(1);
  });
});
