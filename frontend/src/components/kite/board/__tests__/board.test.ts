/**
 * The board's shared logic: day grouping, column selection, and the two
 * adapters that have to agree about what a level means.
 */
import { describe, it, expect } from 'vitest';
import {
  groupByDay, sessionDayKey, sessionDayLabel, ACTIONABLE, ENGINE_TAG, ENGINE_LABEL, STATUS_LABEL,
  type BoardSignal,
} from '../boardTypes';
import { visibleColumns, isMixedEngine, COLUMNS } from '../SignalBoard';
import { supertrendLegToBoard } from '../supertrendAdapter';
import type { EngineSignalRow, OptionLeg } from '../../../../types/kiteEngine';

const IST_OFFSET = (5 * 60 + 30) * 60_000;
/** 2026-08-21 10:30 IST. */
const NOW = Date.UTC(2026, 7, 21, 10, 30) - IST_OFFSET;

function signal(over: Partial<BoardSignal> = {}): BoardSignal {
  return {
    id: 'x', engine: 'supertrend', underlying: 'NIFTY',
    instrument: { symbol: 'NIFTY26AUG24000CE', exchange: 'NFO', kind: 'option', quoteKey: 'NFO:X' },
    direction: 'long', status: 'armed', atMs: NOW,
    levels: { ltp: null, entry: null, stop: null, trail: null, target: null, exit: null },
    sizing: { lots: null, quantity: null, atRiskInr: null, deployedInr: null },
    score: null, reason: null, sections: [],
    ...over,
  };
}

describe('trading-day grouping', () => {
  it('buckets by the IST day, not UTC', () => {
    // 22:00 UTC on the 20th is already 03:30 IST on the 21st. A UTC bucket
    // would file an early-morning IST signal under the previous session.
    const lateUtc = Date.UTC(2026, 7, 20, 22, 0);
    expect(sessionDayKey(lateUtc)).toBe('2026-08-21');
  });

  it('labels today, yesterday and older days', () => {
    expect(sessionDayLabel(sessionDayKey(NOW), NOW)).toBe('Today');
    expect(sessionDayLabel(sessionDayKey(NOW - 86_400_000), NOW)).toBe('Yesterday');
    expect(sessionDayLabel('older', NOW)).toBe('Older');
  });

  it('collapses anything before yesterday into Older', () => {
    const days = groupByDay([
      signal({ id: 'today', atMs: NOW }),
      signal({ id: 'yest', atMs: NOW - 86_400_000 }),
      signal({ id: 'old', atMs: NOW - 3 * 86_400_000 }),
    ], { nowMs: NOW });
    expect(days.map((d) => d.key)).toEqual(['2026-08-21', '2026-08-20', 'older']);
    expect(days[2].signals.map((s) => s.id)).toEqual(['old']);
  });

  it('orders days newest first and rows newest first inside a day', () => {
    const days = groupByDay([
      signal({ id: 'old', atMs: NOW - 86_400_000 }),
      signal({ id: 'early', atMs: NOW - 3_600_000 }),
      signal({ id: 'late', atMs: NOW }),
    ]);
    expect(days.map((d) => d.key)).toEqual(['2026-08-21', '2026-08-20']);
    expect(days[0].signals.map((s) => s.id)).toEqual(['late', 'early']);
  });

  it('keeps undated signals, sorted last', () => {
    // An engine that failed to stamp a signal still has something to say;
    // dropping the row would hide a scan failure.
    const days = groupByDay([signal({ id: 'nodate', atMs: null }), signal({ id: 'dated' })]);
    expect(days.map((d) => d.key)).toEqual(['2026-08-21', 'unknown']);
    expect(sessionDayLabel('unknown', NOW)).toBe('Undated');
  });
});

describe('column selection', () => {
  const all = COLUMNS.map((c) => c.id);

  it('keeps a requested column even when nothing fills it', () => {
    // Emptiness used to remove a column, which meant every board showed a
    // different set and moving between them meant re-finding the stop. An
    // empty cell says "this engine does not produce that", which is true.
    const ids = visibleColumns([signal()], all).map((c) => c.id);
    expect(ids).toContain('target');
    expect(ids).toContain('trail');
  });

  it('gives two boards with different data the same columns', () => {
    const rich = signal({ levels: { ltp: 1, entry: 2, stop: 3, trail: 4, target: 5, exit: 6 } });
    expect(visibleColumns([signal()], all).map((c) => c.id))
      .toEqual(visibleColumns([rich], all).map((c) => c.id));
  });

  it('always keeps the row identity columns', () => {
    for (const id of ['instrument', 'status', 'time']) {
      expect(visibleColumns([signal()], all).map((c) => c.id)).toContain(id);
    }
  });

  it('honours the caller’s column request', () => {
    const ids = visibleColumns([signal({ score: 80 })], ['instrument', 'status', 'time']).map((c) => c.id);
    expect(ids).not.toContain('score');
  });

  it('only tags the engine when more than one is on the board', () => {
    expect(isMixedEngine([signal(), signal()])).toBe(false);
    expect(isMixedEngine([signal(), signal({ engine: 'gamma_move' })])).toBe(true);
  });
});

describe('SuperTrend adapter', () => {
  const leg = (over: Partial<OptionLeg> = {}): OptionLeg => ({
    moneyness: 'ATM', option_type: 'CE', option_symbol: 'NIFTY26AUG24000CE',
    strike: 24000, expiry: '2026-08-27', lot_size: 75,
    premium_spot: 200, entry_sl: 160, premium_sl: 185, ...over,
  });
  const row = (over: Partial<EngineSignalRow> = {}): EngineSignalRow => ({
    underlying: 'NIFTY', token: 1, exchange: 'NFO', regime: 'BULL',
    alignment: { fast: 1, mid: 1, slow: -1 }, direction: 'long', option_type: 'CE',
    legs: [leg()], spot: 24100, stop_loss: 24000, score: 82, timestamp_ms: NOW,
    is_active: true, ...over,
  });

  it('keeps the hard stop and the trail apart', () => {
    // One says what was risked at entry, the other where the ratchet reached.
    // Collapsing them loses whether a trade is still at full risk.
    const s = supertrendLegToBoard(row(), leg(), 0);
    expect(s.levels.stop).toBe(160);
    expect(s.levels.trail).toBe(185);
  });

  it('routes a navigator-sourced row to the navigator engine', () => {
    expect(supertrendLegToBoard(row({ source: 'navigator' }), leg(), 0).engine).toBe('navigator');
    expect(supertrendLegToBoard(row({ source: 'spot' }), leg(), 0).engine).toBe('supertrend');
  });

  it('calls a fresh active row armed, and a quiet active row running', () => {
    expect(supertrendLegToBoard(row({ is_fresh: true }), leg(), 0).status).toBe('armed');
    expect(supertrendLegToBoard(row({ is_fresh: false }), leg(), 0).status).toBe('running');
  });

  it('calls a row with reds against it weakening, not running', () => {
    const s = supertrendLegToBoard(row(), leg({ exit_state: '1/2 red' }), 0);
    expect(s.status).toBe('weakening');
  });

  it('ends a row that has an exit reason even if it still looks active', () => {
    const s = supertrendLegToBoard(row({ exit_reason: 'trail breach' }), leg(), 0);
    expect(s.status).toBe('ended');
    expect(s.reason).toBe('trail breach');
  });

  it('sizes risk off the hard stop, in units', () => {
    // (200 - 160) x 75.
    expect(supertrendLegToBoard(row(), leg(), 0).sizing.atRiskInr).toBe(3000);
  });

  it('reports no risk rather than a wrong one when the stop is unknown', () => {
    const s = supertrendLegToBoard(row(), leg({ entry_sl: undefined }), 0);
    expect(s.sizing.atRiskInr).toBeNull();
  });

  it('renders alignment as fast/mid/slow arrows', () => {
    const s = supertrendLegToBoard(row(), leg(), 0);
    const trend = s.sections.find((x) => x.title === 'Trend & volatility')!;
    expect(trend.stats.find((x) => x.label === 'Alignment')!.value).toBe('▲▲▼');
  });
});

describe('shared vocabulary', () => {
  it('names every status and engine', () => {
    // The Record<> types already force every key to exist, so a count here
    // would only break when an engine is added. What can actually go wrong is
    // a blank name, or a tag and a label that disagree about which engines
    // exist — the header would then render an unlabelled column.
    expect(Object.keys(ENGINE_TAG).sort()).toEqual(Object.keys(ENGINE_LABEL).sort());
    for (const [id, tag] of Object.entries(ENGINE_TAG)) {
      expect(tag, id).toBeTruthy();
      expect(ENGINE_LABEL[id as keyof typeof ENGINE_LABEL], id).toBeTruthy();
    }
    for (const [status, label] of Object.entries(STATUS_LABEL)) {
      expect(label, status).toBeTruthy();
    }
  });

  it('counts armed, running and weakening as live', () => {
    // 'ended' and 'watching' are records, not calls to action; a board that
    // counted them as live would overstate exposure.
    expect([...ACTIONABLE]).toEqual(['armed', 'running', 'weakening']);
  });
});
