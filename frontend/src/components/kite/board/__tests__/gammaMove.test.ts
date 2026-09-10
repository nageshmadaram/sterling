/**
 * Gamma Move -> board.
 *
 * The rows this adapter has to get right are the ones that stop an operator
 * misreading a refusal: a missing level must render as "—" and never as 0, the
 * trigger detail must say which of the three conditions is short, and a held
 * position must survive its candidate dropping out of the scan.
 */
import { describe, it, expect } from 'vitest';
import { gammaMoveToBoard } from '../gammaMoveAdapter';
import type {
  GammaMoveConfig, GammaMoveSnapshot, GammaPositionRow, GammaSignalRow, TriggerMetrics,
} from '../../../../hooks/useGammaMove';

const CFG = {
  level_proximity_pct: 1.0, min_oi_drop_pct: 3.0, volume_spike_mult: 2.5,
  min_price_gain_pct: 2.0, volume_lookback: 20, level_timeframe: 'day',
  stop_mode: 'both', enabled: true,
} as unknown as GammaMoveConfig;

/** Broker-side fields every position row now carries. */
const BROKER = {
  status: 'open' as const, order_id: 'o-1', fill_price: 0, effective_entry: 0,
  gtt_id: 111, stop_mode: 'both' as const,
};

function metrics(over: Partial<TriggerMetrics> = {}): TriggerMetrics {
  return {
    oi_drop_pct: 4.0, volume_ratio: 5.0, price_gain_pct: 6.0,
    unwinding: true, abnormal: true, rising: true,
    bars_confirmed: 1, bars_required: 1, triggered: true, ...over,
  };
}

function row(over: Partial<GammaSignalRow> = {}): GammaSignalRow {
  return {
    id: 'RELIANCE26SEP1300CE@resistance:1300',
    state: 'armed', at_ms: 1789009200000, underlying: 'RELIANCE', regime: 'up',
    reason: null, exit_reason: null, entry_day: null,
    instrument: {
      instrument_id: '12345', tradingsymbol: 'RELIANCE26SEP1300CE', option_type: 'CE',
      strike: 1300, expiry: '2026-09-29', lot_size: 500, tick_size: 0.05, exchange: 'NFO',
    },
    level: { price: 1300, kind: 'resistance', touches: 3, distance_pct: 0.15 },
    oi: 6_000_000, days_to_expiry: 9, spot: 1298,
    metrics: metrics(),
    levels: { ltp: 53, entry: 53, stop: 45, trail: null, target: null, exit: null },
    sizing: { lots: 1, quantity: 500, at_risk_inr: 4000, deployed_inr: 26500 },
    ...over,
  };
}

function snap(over: Partial<GammaMoveSnapshot> = {}): GammaMoveSnapshot {
  return {
    strategy: {} as GammaMoveSnapshot['strategy'],
    config: CFG, scan: {}, session: null, simulation: null,
    candidates: [row()], positions: [], orphan_positions: [], blockers: [],
    record: { trades: 0, wins: 0, losses: 0, win_rate: null, consecutive_losses: 0,
              consecutive_wins: 0, realised_inr: 0, day_realised_inr: 0, day: '',
              verdict: 'no realised trades yet' },
    ...over,
  };
}

describe('gammaMoveToBoard', () => {
  it('returns nothing without a snapshot', () => {
    expect(gammaMoveToBoard(undefined)).toEqual([]);
    expect(gammaMoveToBoard(null)).toEqual([]);
  });

  it('does not invent 0D to expiry when the replay has no chain', () => {
    const rows = gammaMoveToBoard({
      candidates: [{
        id: 'sim-1', state: 'watching', at_ms: 1, underlying: 'RELIANCE',
        regime: 'up', reason: 'replay has no option open-interest tape',
        instrument: { instrument_id: 'RELIANCE', tradingsymbol: 'RELIANCE',
          option_type: 'CE', strike: null, expiry: null, lot_size: null,
          tick_size: 0.05, exchange: 'NFO' },
        level: { price: 1290, kind: 'resistance', touches: 3, distance_pct: 0.62 },
        oi: 0, days_to_expiry: null, spot: 1298, metrics: null,
      }],
    } as unknown as GammaMoveSnapshot);
    expect(rows).toHaveLength(1);
    const flags = (rows[0].flags || []).map((f) => f.label);
    expect(flags.some((l) => l.includes('0D'))).toBe(false);
    expect(flags).toContain('NO CHAIN');
    expect(rows[0].origin?.hint).toMatch(/open-interest tape/);
    expect(rows[0].instrument.kind).toBe('equity');
    expect(rows[0].instrument.quoteKey).toBe('NSE:RELIANCE');
    expect(rows[0].instrument.quoteKey).not.toContain('NFO:RELIANCE');
  });

  it('does not throw when a simulation payload omits config or nested levels', () => {
    const rows = gammaMoveToBoard({
      candidates: [{
        id: 'sim-1', state: 'watching', at_ms: 1, underlying: 'RELIANCE',
        regime: 'up', reason: 'replay has no option open-interest tape',
        instrument: { instrument_id: 'x', tradingsymbol: 'RELIANCE26AUG1300CE',
          option_type: 'CE', strike: 1300, expiry: '2026-08-28', lot_size: 500,
          tick_size: 0.05, exchange: 'NFO' },
        level: { price: 1300, kind: 'resistance', touches: 2, distance_pct: 0.15 },
        oi: 0, days_to_expiry: 2, spot: 1298, metrics: null,
      }],
    } as unknown as GammaMoveSnapshot);
    expect(rows).toHaveLength(1);
    expect(rows[0].status).toBe('watching');
    expect(rows[0].levels.entry).toBeNull();
  });

  it('maps an armed candidate onto the shared contract', () => {
    const [s] = gammaMoveToBoard(snap());
    expect(s.engine).toBe('gamma_move');
    expect(s.status).toBe('armed');
    expect(s.underlying).toBe('RELIANCE');
    expect(s.instrument.symbol).toBe('RELIANCE26SEP1300CE');
    expect(s.instrument.quoteKey).toBe('NFO:RELIANCE26SEP1300CE');
    expect(s.levels.entry).toBe(53);
    expect(s.levels.stop).toBe(45);
    expect(s.sizing.quantity).toBe(500);
  });

  it('is always long — this strategy never writes an option', () => {
    const [s] = gammaMoveToBoard(snap());
    expect(s.direction).toBe('long');
  });

  it('publishes no score rather than inventing one', () => {
    expect(gammaMoveToBoard(snap())[0].score).toBeNull();
  });

  it('renders missing levels as null, never as zero', () => {
    const [s] = gammaMoveToBoard(snap({
      candidates: [row({ levels: { ltp: null, entry: null, stop: 0, trail: null,
                                    target: null, exit: null } })],
    }));
    expect(s.levels.stop).toBeNull();
    expect(s.levels.entry).toBeNull();
    expect(s.levels.ltp).toBeNull();
  });

  it('names the condition carrying the signal', () => {
    expect(gammaMoveToBoard(snap())[0].origin?.label).toBe('OI UNWIND');
  });

  it('distinguishes a partial setup from a quiet one', () => {
    const partial = gammaMoveToBoard(snap({
      candidates: [row({ state: 'watching', reason: 'volume is not abnormal',
                         metrics: metrics({ abnormal: false, triggered: false }) })],
    }))[0];
    expect(partial.origin?.label).toBe('OI FALLING');

    const quiet = gammaMoveToBoard(snap({
      candidates: [row({ state: 'watching', reason: 'open interest is not unwinding',
                         metrics: metrics({ unwinding: false, triggered: false,
                                            oi_drop_pct: 0.1 }) })],
    }))[0];
    expect(quiet.origin?.label).toBe('QUIET');
  });

  it('marks a candidate outside the proximity band', () => {
    const inside = gammaMoveToBoard(snap())[0];
    expect(inside.flags?.[0].tone).toBe('green');
    const outside = gammaMoveToBoard(snap({
      candidates: [row({ level: { price: 1400, kind: 'resistance', touches: 3,
                                   distance_pct: 7.5 } })],
    }))[0];
    expect(outside.flags?.[0].tone).toBe('dim');
    expect(outside.flags?.[0].label).toContain('7.50%');
  });

  it('shows each trigger condition against its configured threshold', () => {
    const [s] = gammaMoveToBoard(snap());
    const trigger = s.sections.find((x) => x.title === 'Trigger');
    expect(trigger).toBeTruthy();
    const labels = trigger!.stats.map((st) => String(st.label));
    expect(labels.some((l) => l.includes('OI unwinding'))).toBe(true);
    expect(labels.some((l) => l.includes('Volume'))).toBe(true);
    expect(labels.some((l) => l.includes('Premium'))).toBe(true);
    expect(trigger!.stats.some((st) => String(st.hint).includes('3'))).toBe(true);
  });

  it('says so when there are not enough bars to judge', () => {
    const [s] = gammaMoveToBoard(snap({
      candidates: [row({ state: 'watching', metrics: null,
                         reason: "not enough of today's bars to judge the trigger" })],
    }));
    expect(s.origin?.label).toBe('NO DATA');
    expect(s.sections.find((x) => x.title === 'Trigger')?.stats).toEqual([]);
  });

  it('shows a held position as running and prefers its live levels', () => {
    const position: GammaPositionRow = {
      symbol: 'RELIANCE26SEP1300CE', signal_id: row().id, entry: 54.2, stop: 46.1,
      trail: 60, target: null, quantity: 500, lots: 1, entered_ms: 1789009200000,
      entry_day: '2026-09-20', sessions_held: 0, exiting: false, high_water: 62,
      ...BROKER, fill_price: 54.2, effective_entry: 54.2,
    };
    const [s] = gammaMoveToBoard(snap({ positions: [position] }));
    expect(s.status).toBe('running');
    expect(s.levels.entry).toBe(54.2);
    expect(s.levels.trail).toBe(60);
  });

  it('shows the real fill, not the intended entry', () => {
    const position: GammaPositionRow = {
      symbol: 'RELIANCE26SEP1300CE', signal_id: row().id, entry: 53, stop: 45,
      trail: null, target: null, quantity: 500, lots: 1, entered_ms: 1789009200000,
      entry_day: '2026-09-20', sessions_held: 0, exiting: false, high_water: 56,
      ...BROKER, fill_price: 56.4, effective_entry: 56.4,
    };
    const [s] = gammaMoveToBoard(snap({ positions: [position] }));
    expect(s.levels.entry).toBe(56.4);
  });

  it('warns when a position has no broker-side stop', () => {
    const base = {
      symbol: 'RELIANCE26SEP1300CE', signal_id: row().id, entry: 53, stop: 45,
      trail: null, target: null, quantity: 500, lots: 1, entered_ms: 1789009200000,
      entry_day: '2026-09-20', sessions_held: 0, exiting: false, high_water: 53,
      ...BROKER, fill_price: 53, effective_entry: 53,
    };
    const armed = gammaMoveToBoard(snap({ positions: [base] }))[0];
    expect(armed.flags?.some((f) => f.label === 'GTT ARMED')).toBe(true);

    const bare = gammaMoveToBoard(snap({ positions: [{ ...base, gtt_id: 0 }] }))[0];
    expect(bare.flags?.some((f) => f.label === 'NO BROKER STOP')).toBe(true);
  });

  it('marks an unconfirmed order as not yet a position', () => {
    const pending: GammaPositionRow = {
      symbol: 'RELIANCE26SEP1300CE', signal_id: row().id, entry: 53, stop: 45,
      trail: null, target: null, quantity: 500, lots: 1, entered_ms: 1789009200000,
      entry_day: '2026-09-20', sessions_held: 0, exiting: false, high_water: 53,
      ...BROKER, status: 'pending', fill_price: 0, effective_entry: 53,
    };
    const [s] = gammaMoveToBoard(snap({ positions: [pending] }));
    expect(s.flags?.some((f) => f.label === 'UNCONFIRMED')).toBe(true);
  });

  it('keeps a held position visible after its candidate leaves the scan', () => {
    const position: GammaPositionRow = {
      symbol: 'ORPHAN26SEP1CE', signal_id: 'gone', entry: 20, stop: 14, trail: null,
      target: null, quantity: 100, lots: 1, entered_ms: 1789009200000,
      entry_day: '2026-09-19', sessions_held: 1, exiting: false, high_water: 20,
      ...BROKER, fill_price: 20, effective_entry: 20,
    };
    const rows = gammaMoveToBoard(snap({ candidates: [], positions: [position] }));
    expect(rows).toHaveLength(1);
    expect(rows[0].status).toBe('running');
    expect(rows[0].reason).toContain('held since');
  });
});
