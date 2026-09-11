/**
 * Column sorting on the shared board.
 *
 * The interesting decisions are not the comparison itself but the two rules
 * around it: sorting stays inside a trading day, and a row with nothing to
 * compare sinks rather than winning either end.
 */
import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { SignalBoard, sortSignals, nextSort, DEFAULT_SORT } from '../SignalBoard';
import type { BoardSignal } from '../boardTypes';

const IST = (5 * 60 + 30) * 60_000;
const NOW = Date.UTC(2026, 7, 21, 10, 30) - IST;

function sig(over: Partial<BoardSignal> = {}): BoardSignal {
  return {
    id: 'a', engine: 'intraday', underlying: 'NIFTY',
    instrument: { symbol: 'NIFTY26AUG24000CE', exchange: 'NFO', kind: 'option', optionType: 'CE', quoteKey: null },
    direction: 'long', status: 'running', atMs: NOW,
    levels: { ltp: 100, entry: 100, stop: 80, trail: null, target: null, exit: null },
    sizing: { lots: null, quantity: null, atRiskInr: null, deployedInr: null },
    score: null, reason: null, sections: [],
    ...over,
  };
}

const ids = (list: readonly BoardSignal[]) => list.map((s) => s.id);

describe('sortSignals', () => {
  it('orders numbers, both ways', () => {
    const rows = [sig({ id: 'lo', levels: { ...sig().levels, ltp: 10 } }), sig({ id: 'hi', levels: { ...sig().levels, ltp: 90 } })];
    expect(ids(sortSignals(rows, { column: 'ltp', direction: 'desc' }))).toEqual(['hi', 'lo']);
    expect(ids(sortSignals(rows, { column: 'ltp', direction: 'asc' }))).toEqual(['lo', 'hi']);
  });

  it('orders names alphabetically', () => {
    const rows = [sig({ id: 'z', underlying: 'ZEEL' }), sig({ id: 'a', underlying: 'AXISBANK' })];
    expect(ids(sortSignals(rows, { column: 'instrument', direction: 'asc' }))).toEqual(['a', 'z']);
  });

  it('sinks rows with nothing to compare, in BOTH directions', () => {
    // A row with no stop is not "the smallest stop". Flipping the direction
    // must not promote a row that has nothing to say to the top of the board.
    const rows = [
      sig({ id: 'none', levels: { ...sig().levels, stop: null } }),
      sig({ id: 'low', levels: { ...sig().levels, stop: 10 } }),
      sig({ id: 'high', levels: { ...sig().levels, stop: 90 } }),
    ];
    expect(ids(sortSignals(rows, { column: 'stop', direction: 'desc' }))).toEqual(['high', 'low', 'none']);
    expect(ids(sortSignals(rows, { column: 'stop', direction: 'asc' }))).toEqual(['low', 'high', 'none']);
  });

  it('orders status by where a trade is in its life, not alphabetically', () => {
    const rows = [
      sig({ id: 'ended', status: 'ended' }),
      sig({ id: 'armed', status: 'armed' }),
      sig({ id: 'weak', status: 'weakening' }),
      sig({ id: 'running', status: 'running' }),
    ];
    expect(ids(sortSignals(rows, { column: 'status', direction: 'asc' })))
      .toEqual(['armed', 'running', 'weak', 'ended']);
  });

  it('does not mutate the array it was given', () => {
    const rows = [sig({ id: 'b', levels: { ...sig().levels, ltp: 1 } }), sig({ id: 'a', levels: { ...sig().levels, ltp: 2 } })];
    sortSignals(rows, { column: 'ltp', direction: 'asc' });
    expect(ids(rows)).toEqual(['b', 'a']);
  });
});

describe('nextSort', () => {
  it('flips direction when the same column is clicked again', () => {
    expect(nextSort({ column: 'ltp', direction: 'desc' }, 'ltp')).toEqual({ column: 'ltp', direction: 'asc' });
    expect(nextSort({ column: 'ltp', direction: 'asc' }, 'ltp')).toEqual({ column: 'ltp', direction: 'desc' });
  });

  it('starts a numeric column at biggest-first and a name at A-first', () => {
    expect(nextSort(DEFAULT_SORT, 'risk')).toEqual({ column: 'risk', direction: 'desc' });
    expect(nextSort(DEFAULT_SORT, 'instrument')).toEqual({ column: 'instrument', direction: 'asc' });
  });

  it('defaults the board to newest first', () => {
    expect(DEFAULT_SORT).toEqual({ column: 'time', direction: 'desc' });
  });
});

describe('sorting on the rendered board', () => {
  const show = (props: Partial<React.ComponentProps<typeof SignalBoard>> = {}) =>
    render(<SignalBoard signals={[sig()]} openId={null} onToggle={() => {}} nowMs={NOW} {...props} />);

  it('reports the sorted column to assistive tech', () => {
    show({ sort: { column: 'ltp', direction: 'asc' }, onSortChange: vi.fn() });
    expect(screen.getByRole('button', { name: /^LTP/ })).toHaveAttribute('aria-sort', 'ascending');
    expect(screen.getByRole('button', { name: /^Entry/ })).toHaveAttribute('aria-sort', 'none');
  });

  it('asks the caller to change sort rather than owning it', () => {
    const onSortChange = vi.fn();
    show({ onSortChange });
    fireEvent.click(screen.getByRole('button', { name: /^LTP/ }));
    expect(onSortChange).toHaveBeenCalledWith({ column: 'ltp', direction: 'desc' });
  });

  it('leaves the header inert when the board is not sortable', () => {
    show();
    expect(screen.getByRole('button', { name: /^LTP/ })).toBeDisabled();
  });

  it('keeps sorting inside a trading day', () => {
    // The day grouping is the board's primary organisation. Sorting by price
    // must not lift yesterday's expensive row above today's cheap one.
    const rows = [
      sig({ id: 'today-cheap', atMs: NOW, levels: { ...sig().levels, ltp: 1 } }),
      sig({ id: 'yesterday-dear', atMs: NOW - 86_400_000, levels: { ...sig().levels, ltp: 999 } }),
    ];
    render(
      <SignalBoard
        signals={rows}
        openId={null}
        onToggle={() => {}}
        nowMs={NOW}
        sort={{ column: 'ltp', direction: 'desc' }}
        onSortChange={vi.fn()}
      />,
    );
    // Only the newest band opens by default; yesterday's row has to be revealed
    // before the two can be ordered against each other.
    fireEvent.click(screen.getByText('Yesterday'));
    const text = document.body.textContent ?? '';
    // The day bands are gone -- each row carries its own date now -- so this
    // reads the property off the ROWS. That is the property that mattered all
    // along: a descending price sort must not lift yesterday's 999 above today's
    // 1, because the day grouping is the primary organisation and sorting works
    // within it.
    expect(text.indexOf('1.00'), "today's cheap row comes first")
      .toBeLessThan(text.indexOf('999.00'));
  });
});
