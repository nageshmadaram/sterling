/**
 * "Exit" and "Exited" are two different questions.
 *
 * SuperTrend's exit column is a COUNTER — three SuperTrend lines must turn red
 * before it closes, and the count in between ("0/3 red") is the single most
 * useful number on the row. The shared board's `exit` column is a PRICE: where a
 * position actually got out, once it has.
 *
 * Those shared one column id, and `SIGNAL_COL_TO_BOARD` mapped `exit -> exit`.
 * So moving SuperTrend onto the shared board put a counter under a heading that
 * means "where it got out", and then lost the counter entirely — the one number
 * that shows the premium already through its trail while the engine has not
 * closed yet, which is exactly where an open drawdown builds.
 *
 * The tests below hold the distinction rather than the wiring: same row, both
 * columns, two different values, neither borrowing the other's heading.
 */
import React from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { BOARD_COLUMNS, BOARD_COLUMNS_WITH_DAY_MOVE, SignalBoard } from '../SignalBoard';
import { SIGNAL_COL_TO_BOARD } from '../signalRowSpec';
import type { BoardSignal } from '../boardTypes';

const IST = (5 * 60 + 30) * 60_000;
const NOW = Date.UTC(2026, 7, 21, 10, 30) - IST;

function sig(over: Partial<BoardSignal> = {}): BoardSignal {
  return {
    id: 'a',
    engine: 'supertrend',
    underlying: 'NIFTY',
    instrument: {
      symbol: 'NIFTY26AUG24000CE', exchange: 'NFO', kind: 'option',
      optionType: 'CE', strike: 24000, expiry: '2026-08-27', lotSize: 75,
      quoteKey: 'NFO:NIFTY26AUG24000CE',
    },
    direction: 'long',
    // "open, but the exit rule has started counting against it" — the exact
    // state the red counter exists to describe.
    status: 'weakening',
    atMs: NOW,
    levels: { ltp: 193, entry: 971, stop: 900, trail: 940, target: null, exit: null },
    sizing: { lots: 1, quantity: 75, atRiskInr: 5325, deployedInr: 72825 },
    score: null,
    reason: null,
    sections: [],
    ...over,
  };
}


describe('the exit counter and the exit price are separate columns', () => {
  it('routes SuperTrend\'s exit key to the counter column, not the price column', () => {
    // The whole bug in one assertion: SuperTrend's `exit` is a state, so it must
    // not land on the column that means "the price it got out at".
    expect(SIGNAL_COL_TO_BOARD.exit).toBe('exitState');
  });

  it('keeps the counter out of the shared catalogue, so four boards do not gain a dead column', () => {
    // Same rule the day-move columns follow: only the engine that produces the
    // number asks for it. ORB, Gamma, Adaptive Edge and ATM have no red counter,
    // and a column that can only ever be a dash is dead width, not honesty.
    expect(BOARD_COLUMNS).not.toContain('exitState');
    expect(BOARD_COLUMNS_WITH_DAY_MOVE).not.toContain('exitState');
    // The price column stays shared — every engine can eventually exit.
    expect(BOARD_COLUMNS).toContain('exit');
  });

  it('shows the counter under Exit and the realised price under Exited, on one row', () => {
    render(
      <SignalBoard
        signals={[sig({ exitProgress: '0/3 red', levels: { ...sig().levels, exit: 205.5 } })]}
        nowMs={NOW}
        columns={[...BOARD_COLUMNS, 'exitState']}
        openId={null}
        onToggle={() => {}}
      />,
    );
    // Both on the row, and not the same number: one is a rule's progress, the
    // other is a fill.
    expect(screen.getByText('0/3 red')).toBeInTheDocument();
    expect(screen.getByText(/205\.50/)).toBeInTheDocument();
    // Two headings, two elements. `getByText` is an exact match, so 'Exit'
    // cannot resolve to the 'Exited' header.
    expect(screen.getByText('Exit')).not.toBe(screen.getByText('Exited'));
  });

  it('reads as a dash when the engine has no counter, rather than borrowing the price', () => {
    render(
      <SignalBoard
        signals={[sig({ engine: 'intraday', exitProgress: null, levels: { ...sig().levels, exit: 205.5 } })]}
        nowMs={NOW}
        columns={['instrument', 'exitState']}
        openId={null}
        onToggle={() => {}}
      />,
    );
    // 205.5 is a real exit price on this row, but the counter column must not
    // show it — that would report a rule as satisfied on no evidence.
    expect(screen.queryByText(/205\.5/)).toBeNull();
    expect(screen.getByText('—')).toBeInTheDocument();
  });
});
