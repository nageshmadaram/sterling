/**
 * The board's local view filters.
 *
 * The important behaviour is not the filtering itself but which filters get
 * offered: a control is shown only when it has something to act on, so a board
 * never advertises a toggle that would do nothing.
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useBoardView } from '../useBoardView';
import { DEFAULT_HIDDEN_COLUMNS } from '../SignalBoard';
import type { BoardSignal } from '../boardTypes';

const NOW = Date.UTC(2026, 7, 21, 5, 0);

function sig(over: Partial<BoardSignal> = {}): BoardSignal {
  return {
    id: 'a', engine: 'intraday', underlying: 'NIFTY',
    instrument: { symbol: 'NIFTY26AUG24000CE', exchange: 'NFO', kind: 'option', strike: 24000, quoteKey: null },
    direction: 'long', status: 'armed', atMs: NOW,
    levels: { ltp: 24010, entry: null, stop: null, trail: null, target: null, exit: null },
    sizing: { lots: null, quantity: null, atRiskInr: null, deployedInr: null },
    score: null, reason: null, sections: [],
    ...over,
  };
}

describe('which filters are offered', () => {
  it('offers neither when there is nothing to filter', () => {
    const { result } = renderHook(() => useBoardView([sig()], { nowMs: NOW }));
    expect(result.current.offers).toEqual({ ended: false, best: false, today: false });
  });

  it('offers Ended only once something has ended', () => {
    const { result } = renderHook(() => useBoardView([sig(), sig({ id: 'b', status: 'ended' })], { nowMs: NOW }));
    expect(result.current.offers.ended).toBe(true);
    expect(result.current.counts.ended).toBe(1);
  });

  it('offers Best leg only when an underlying has more than one leg', () => {
    const two = [sig({ id: 'a' }), sig({ id: 'b' })];
    expect(renderHook(() => useBoardView(two, { nowMs: NOW })).result.current.offers.best).toBe(true);
    const different = [sig({ id: 'a' }), sig({ id: 'b', underlying: 'BANKNIFTY' })];
    expect(renderHook(() => useBoardView(different, { nowMs: NOW })).result.current.offers.best).toBe(false);
  });

  it('offers Today only when older signals exist', () => {
    const yesterdayMs = NOW - 24 * 3600 * 1000;
    const sameDay = [sig({ id: 'a', atMs: NOW })];
    expect(renderHook(() => useBoardView(sameDay, { nowMs: NOW })).result.current.offers.today).toBe(false);

    const withOlder = [sig({ id: 'a', atMs: NOW }), sig({ id: 'b', atMs: yesterdayMs })];
    expect(renderHook(() => useBoardView(withOlder, { nowMs: NOW })).result.current.offers.today).toBe(true);
  });
});

describe('hidden columns', () => {
  beforeEach(() => localStorage.clear());

  it('opens on the eleven core columns, extras tucked away', () => {
    // Every board starts identical: symbol, type, exchange, leg, LTP, entry,
    // SL, TSL, exit, time, status. Qty, risk, score and the engine tag are a
    // click away rather than making the sidebar scroll.
    const { result } = renderHook(() => useBoardView([sig()]));
    expect([...result.current.hidden].sort()).toEqual([...DEFAULT_HIDDEN_COLUMNS].sort());
  });

  it('toggles a column off and back on', () => {
    const { result } = renderHook(() => useBoardView([sig()]));
    // 'risk' starts hidden, so the first toggle reveals it.
    act(() => result.current.toggleColumn('risk'));
    expect(result.current.hidden.has('risk')).toBe(false);
    act(() => result.current.toggleColumn('risk'));
    expect(result.current.hidden.has('risk')).toBe(true);
  });

  it('restores every column at once', () => {
    const { result } = renderHook(() => useBoardView([sig()]));
    act(() => result.current.showAllColumns());
    expect(result.current.hidden.size).toBe(0);
    act(() => result.current.resetColumns());
    expect([...result.current.hidden].sort()).toEqual([...DEFAULT_HIDDEN_COLUMNS].sort());
  });

  it('remembers the choice per board, not globally', () => {
    // ORB shows an at-risk figure Adaptive Edge cannot fill. One shared list
    // would mean hiding a column on one board silently changed another.
    const orb = renderHook(() => useBoardView([sig()], { storageKey: 'orb' }));
    act(() => orb.result.current.toggleColumn('stop'));

    const other = renderHook(() => useBoardView([sig()], { storageKey: 'adaptive_edge' }));
    expect(other.result.current.hidden.has('stop')).toBe(false);

    const orbAgain = renderHook(() => useBoardView([sig()], { storageKey: 'orb' }));
    expect(orbAgain.result.current.hidden.has('stop')).toBe(true);
  });

  it('forgets nothing and crashes on nothing when storage holds junk', () => {
    localStorage.setItem('sterling.board.hidden.v2.orb', 'not json');
    const { result } = renderHook(() => useBoardView([sig()], { storageKey: 'orb' }));
    expect([...result.current.hidden].sort()).toEqual([...DEFAULT_HIDDEN_COLUMNS].sort());
  });

  it('keeps the choice out of storage when no board is named', () => {
    const { result } = renderHook(() => useBoardView([sig()]));
    act(() => result.current.toggleColumn('stop'));
    expect(localStorage.length).toBe(0);
  });
});

describe('filtering', () => {
  it('hides ended rows until asked', () => {
    const { result } = renderHook(() => useBoardView([sig(), sig({ id: 'b', status: 'ended' })]));
    expect(result.current.visible).toHaveLength(1);
    act(() => result.current.setShowEnded(true));
    expect(result.current.visible).toHaveLength(2);
  });

  it('searches the underlying, the contract and the exchange', () => {
    const rows = [sig({ id: 'n' }), sig({ id: 'b', underlying: 'BANKNIFTY', instrument: { symbol: 'BANKNIFTY26AUG52000PE', exchange: 'BFO', kind: 'option', quoteKey: null } })];
    const { result } = renderHook(() => useBoardView(rows));
    act(() => result.current.setQuery('bank'));
    expect(result.current.visible.map((s) => s.id)).toEqual(['b']);
    act(() => result.current.setQuery('BFO'));
    expect(result.current.visible.map((s) => s.id)).toEqual(['b']);
    act(() => result.current.setQuery('24000CE'));
    expect(result.current.visible.map((s) => s.id)).toEqual(['n']);
  });

  it('keeps the leg nearest the money when Best is on', () => {
    // Spot is 24010, so the 24000 strike is nearer than the 24500.
    const near = sig({ id: 'near', instrument: { symbol: 'A', exchange: 'NFO', kind: 'option', strike: 24000, quoteKey: null } });
    const far = sig({ id: 'far', instrument: { symbol: 'B', exchange: 'NFO', kind: 'option', strike: 24500, quoteKey: null } });
    const { result } = renderHook(() => useBoardView([far, near]));
    act(() => result.current.setBestOnly(true));
    expect(result.current.visible.map((s) => s.id)).toEqual(['near']);
  });

  it('does not collapse legs of different underlyings or directions', () => {
    const rows = [sig({ id: 'a' }), sig({ id: 'b', direction: 'short' })];
    const { result } = renderHook(() => useBoardView(rows));
    act(() => result.current.setBestOnly(true));
    expect(result.current.visible).toHaveLength(2);
  });

  it('filters to today signals when todayOnly is turned on', () => {
    const yesterdayMs = NOW - 24 * 3600 * 1000;
    const rows = [sig({ id: 'today', atMs: NOW }), sig({ id: 'old', atMs: yesterdayMs })];
    const { result } = renderHook(() => useBoardView(rows, { nowMs: NOW }));
    expect(result.current.visible).toHaveLength(2);
    act(() => result.current.setTodayOnly(true));
    expect(result.current.visible.map((s) => s.id)).toEqual(['today']);
  });

  it('reports shown against total so a filtered board says so', () => {
    const { result } = renderHook(() => useBoardView([sig(), sig({ id: 'b', status: 'ended' })]));
    expect(result.current.counts).toMatchObject({ total: 2, shown: 1, ended: 1 });
  });
});
