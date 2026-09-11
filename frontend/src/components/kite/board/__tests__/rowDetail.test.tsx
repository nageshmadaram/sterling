/**
 * The row-level detail SuperTrend had and the other boards did not.
 *
 * All of it is generic now — driven off BoardSignal, so every engine gets it —
 * except the origin badge, which is deliberately per-engine: the same slot on
 * the row means four different things, because the four engines have four
 * different answers to "where did this come from".
 */
import React from 'react';
import { describe, it, expect } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import { SignalBoard, BOARD_COLUMNS } from '../SignalBoard';
import { markLegs, trailBreached, type BoardSignal } from '../boardTypes';

const IST = (5 * 60 + 30) * 60_000;
const NOW = Date.UTC(2026, 7, 21, 10, 30) - IST;

function sig(over: Partial<BoardSignal> = {}): BoardSignal {
  return {
    id: 'a', engine: 'intraday', underlying: 'NIFTY',
    instrument: {
      symbol: 'NIFTY26AUG24000CE', exchange: 'NFO', kind: 'option', optionType: 'CE',
      strike: 24000, expiry: '2026-08-27', lotSize: 75, quoteKey: null,
    },
    direction: 'long', status: 'running', atMs: NOW,
    levels: { ltp: 22, entry: 18, stop: 14, trail: 16, target: 26, exit: null },
    sizing: { lots: null, quantity: null, atRiskInr: null, deployedInr: null },
    score: null, reason: null, sections: [],
    ...over,
  };
}

const show = (signals: BoardSignal[], props: Partial<React.ComponentProps<typeof SignalBoard>> = {}) =>
  render(
    <SignalBoard signals={signals} columns={BOARD_COLUMNS} openId={null} onToggle={() => {}} nowMs={NOW} {...props} />,
  );

describe('entry shows what the position has done', () => {
  it('brackets the move from entry to live', () => {
    show([sig({ levels: { ltp: 22, entry: 18, stop: 14, trail: null, target: null, exit: null } })]);
    expect(screen.getByText('(+4.00)')).toBeInTheDocument();
  });

  it('marks a loss as a loss', () => {
    show([sig({ levels: { ltp: 15, entry: 18, stop: 14, trail: null, target: null, exit: null } })]);
    expect(screen.getByText('(-3.00)')).toBeInTheDocument();
  });

  it('says nothing when there is no live price to compare', () => {
    show([sig({ levels: { ltp: null, entry: 18, stop: 14, trail: null, target: null, exit: null } })]);
    expect(screen.queryByText(/^\([+-]/)).not.toBeInTheDocument();
  });
});

describe('a closed row reads as a record', () => {
  it('dims and strikes it, so it cannot be mistaken for actionable', () => {
    const { container } = show([sig({ status: 'ended' })]);
    const row = container.querySelector('.sb-row') as HTMLElement;
    expect(row.style.textDecoration).toBe('line-through');
    expect(Number(row.style.opacity)).toBeLessThan(1);
  });

  it('leaves a live row alone', () => {
    const { container } = show([sig({ status: 'running' })]);
    const row = container.querySelector('.sb-row') as HTMLElement;
    expect(row.style.textDecoration).toBe('none');
  });
});

describe('a premium through its own trail', () => {
  it('is flagged, because the engine has not closed it yet', () => {
    // On a counter-based exit the leg still counts as running while this is
    // true, and that is exactly where an open drawdown builds.
    show([sig({ levels: { ltp: 15, entry: 18, stop: 14, trail: 16, target: null, exit: null } })]);
    expect(screen.getByText('TSL HIT')).toBeInTheDocument();
  });

  it('is not flagged while the price is above the trail', () => {
    show([sig()]);
    expect(screen.queryByText('TSL HIT')).not.toBeInTheDocument();
  });

  it('is not flagged on a position that has already closed', () => {
    const closed = sig({ status: 'ended', levels: { ltp: 15, entry: 18, stop: 14, trail: 16, target: null, exit: 15 } });
    expect(trailBreached(closed)).toBe(false);
  });

  it('is not flagged when the engine does not trail at all', () => {
    expect(trailBreached(sig({ levels: { ltp: 1, entry: 18, stop: 14, trail: null, target: null, exit: null } }))).toBe(false);
  });
});

describe('the leg cell carries moneyness and delta', () => {
  it('shows both when the engine knows them', () => {
    // The Leg column is moneyness and delta only — the contract itself is
    // named by the instrument cell, and repeating it there costs the width
    // these two need.
    const { container } = show([sig({ instrument: { ...sig().instrument, moneyness: 'ATM' }, delta: 0.564 })]);
    const row = container.querySelector('.sb-row') as HTMLElement;
    expect(row).toHaveTextContent('ATM');
    expect(row).toHaveTextContent('Δ0.56');
  });

  it('leaves the cell empty when it knows neither', () => {
    const { container } = show([sig()]);
    expect(container.querySelector('.sb-row')).not.toHaveTextContent('Δ');
  });

  it('still names the traded contract on a standalone row', () => {
    // No group header above it, so if this row does not name the contract,
    // nothing does.
    const { container } = show([sig()]);
    expect(container.querySelector('.sb-row')?.textContent).toMatch(/24000/);
  });
});

describe('best-of markers compare siblings only', () => {
  const leg = (id: string, over: Partial<BoardSignal> = {}) => sig({ id, ...over });

  it('marks the strike that pays most for its risk', () => {
    // a: (26-18)/(18-14) = 2.0   b: (40-18)/(18-14) = 5.5
    const marks = markLegs([
      leg('a'),
      leg('b', { levels: { ltp: 22, entry: 18, stop: 14, trail: 16, target: 40, exit: null } }),
    ]);
    expect(marks.get('b')?.has('bestRR')).toBe(true);
    expect(marks.get('a')?.has('bestRR')).toBeUndefined();
  });

  it('marks the strike that moves most with the underlying', () => {
    const marks = markLegs([leg('a', { delta: 0.3 }), leg('b', { delta: 0.7 })]);
    expect(marks.get('b')?.has('bestDelta')).toBe(true);
  });

  it('marks nothing when there is only one leg', () => {
    // "Best of one" is not information.
    expect(markLegs([leg('a', { delta: 0.5 })]).size).toBe(0);
  });

  it('ignores a leg whose ladder is incomplete', () => {
    const marks = markLegs([
      leg('a'),
      leg('b', { levels: { ltp: 22, entry: 18, stop: null, trail: null, target: 99, exit: null } }),
    ]);
    expect(marks.get('a')?.has('bestRR')).toBe(true);
  });
});

describe('the origin badge says something different on each engine', () => {
  const withOrigin = (label: string, hint: string) =>
    sig({ origin: { label, hint, tone: 'brand' } });

  it('renders the engine’s own words', () => {
    show([withOrigin('BOTH AGREE', 'The underlying fired and the premium confirmed it.')]);
    expect(screen.getByText('BOTH AGREE')).toBeInTheDocument();
  });

  it('is absent when an engine has no provenance to state', () => {
    const { container } = show([sig()]);
    const row = container.querySelector('.sb-row') as HTMLElement;
    expect(within(row).queryByText(/SPOT|PREMIUM|KITE|AE MODEL/)).not.toBeInTheDocument();
  });
});
