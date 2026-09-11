/**
 * What the board actually renders.
 *
 * The logic tests next door cover grouping and column selection as functions;
 * these cover the parts that only exist once it is on screen — that the header
 * and the rows agree on a column count, that expanding shows the engine's own
 * sections, and that the row is operable from the keyboard. A board that places
 * real orders should not be mouse-only.
 */
import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import { SignalBoard } from '../SignalBoard';
import { STATUS_LABEL, type BoardSignal } from '../boardTypes';

const IST = (5 * 60 + 30) * 60_000;
const NOW = Date.UTC(2026, 7, 21, 10, 30) - IST;

function sig(over: Partial<BoardSignal> = {}): BoardSignal {
  return {
    id: 'a',
    engine: 'intraday',
    underlying: 'NIFTY',
    instrument: {
      symbol: 'NIFTY26AUG24000CE', exchange: 'NFO', kind: 'option',
      optionType: 'CE', strike: 24000, expiry: '2026-08-27', lotSize: 75,
      quoteKey: 'NFO:NIFTY26AUG24000CE',
    },
    direction: 'long',
    status: 'armed',
    atMs: NOW,
    levels: { ltp: 18, entry: 18, stop: 14, trail: null, target: 26, exit: null },
    sizing: { lots: 2, quantity: 150, atRiskInr: 2700, deployedInr: 2700 },
    score: null,
    reason: null,
    sections: [{ title: 'Opening range & VWAP', layout: 'tiles', stats: [{ label: 'ORB high', value: '24012.00' }] }],
    ...over,
  };
}

const show = (signals: BoardSignal[], props: Partial<React.ComponentProps<typeof SignalBoard>> = {}) =>
  render(<SignalBoard signals={signals} openId={null} onToggle={() => {}} nowMs={NOW} {...props} />);

describe('SignalBoard rendering', () => {
  it('names the columns a trader asked for', () => {
    show([sig()]);
    for (const label of ['Instrument', 'Status', 'Exc.', 'Leg (Δ)', 'LTP', 'Entry (Δpts)', 'SL', 'Target', 'Qty', 'At risk', 'Time']) {
      expect(screen.getByText(label), label).toBeInTheDocument();
    }
  });

  it('gives the header and the rows the same grid template', () => {
    // They are separate elements, so a mismatch silently misaligns every
    // number under its heading — the failure a table with a sticky header has.
    const { container } = show([sig()]);
    const header = container.querySelector('[role="row"]') as HTMLElement;
    const row = container.querySelector('.sb-row') as HTMLElement;
    expect(row.style.gridTemplateColumns).toBe(header.style.gridTemplateColumns);
  });

  it('shows a requested column even when nothing fills it', () => {
    // Nothing here trails, and the TSL column stays: an empty cell says this
    // engine does not trail, and every board keeps the same columns so moving
    // between them never means re-finding the stop.
    show([sig()]);
    expect(screen.getByText('TSL')).toBeInTheDocument();
  });








  it('shows the engine tag only when engines are mixed', () => {
    show([sig()]);
    expect(screen.queryByText('Engine')).not.toBeInTheDocument();
    show([sig(), sig({ id: 'b', engine: 'supertrend' })]);
    expect(screen.getByText('Engine')).toBeInTheDocument();
  });

  it('renders a missing level as a dash, never as zero', () => {
    // A fabricated 0 in a stop column is a trade-destroying lie.
    show([sig({ levels: { ltp: 20, entry: 20, stop: null, trail: null, target: null, exit: null } })]);
    expect(screen.getByText('SL')).toBeInTheDocument();
    const row = document.querySelector('.sb-row') as HTMLElement;
    expect(within(row).getAllByText('—').length).toBeGreaterThan(0);
    expect(within(row).queryByText('0.00')).not.toBeInTheDocument();
  });
});

describe('how much colour a row spends', () => {
  // The first version of this board put a direction-coloured band on every
  // row, two tinted pills, and three permanently-coloured number columns. On a
  // full board that is a wall of colour, and a screen where a third of the
  // numbers are always red has nothing left to say when something is wrong.
  const cell = (row: HTMLElement, label: string) => {
    const heads = [...document.querySelectorAll('.sb-head')].map((h) => h.textContent!.trim());
    return row.children[heads.indexOf(label) + 1] as HTMLElement;
  };

  it('leaves the level columns in plain ink', () => {
    const { container } = show([sig({ levels: { ltp: 18, entry: 18, stop: 14, trail: 16, target: 26, exit: null } })]);
    const row = container.querySelector('.sb-row') as HTMLElement;
    for (const label of ['SL', 'TSL', 'Exit']) {
      // Not red / amber / green just for being that column.
      expect(cell(row, label).style.color, label).not.toMatch(/--k-(red|amber|green)\)/);
    }
  });

  it('accents only the row that is open, not every row', () => {
    const { container } = show([sig()], { openId: null });
    expect((container.querySelector('.sb-row') as HTMLElement).style.borderLeft).toContain('transparent');
    const opened = show([sig()], { openId: 'a' });
    expect((opened.container.querySelector('.sb-row') as HTMLElement).style.borderLeft).toContain('--k-blue');
  });

  it('separates rows by alternating shade instead', () => {
    const { container } = show([sig({ id: 'a' }), sig({ id: 'b' })]);
    const [first, second] = [...container.querySelectorAll('.sb-row')] as HTMLElement[];
    expect(first.style.background).not.toBe(second.style.background);
  });

  it('badges a status only when it is an exception', () => {
    // Running / watching / ended are the normal state of a board. If every row
    // carries a badge the badge stops meaning anything.
    for (const status of ['running', 'watching', 'ended'] as const) {
      const { container } = show([sig({ status })]);
      const row = container.querySelector('.sb-row') as HTMLElement;
      expect(within(row).getByText(STATUS_LABEL[status]).tagName, status).toBe('SPAN');
      expect(cell(row, 'Status').querySelector('span[style*="background"]'), status).toBeNull();
    }
  });

  it('still badges the statuses that need acting on', () => {
    for (const status of ['armed', 'weakening', 'error'] as const) {
      const { container } = show([sig({ status })]);
      const row = container.querySelector('.sb-row') as HTMLElement;
      expect(cell(row, 'Status').querySelector('span[style*="background"]'), status).not.toBeNull();
    }
  });
});

describe('SignalBoard expansion', () => {
  it('keeps detail closed until asked, and says so to assistive tech', () => {
    show([sig()]);
    expect(screen.getByRole('button', { name: /NIFTY CE Armed/ })).toHaveAttribute('aria-expanded', 'false');
    expect(screen.queryByText('Opening range & VWAP')).not.toBeInTheDocument();
  });

  it('renders the engine’s own sections when open', () => {
    show([sig()], { openId: 'a' });
    expect(screen.getByText('Opening range & VWAP')).toBeInTheDocument();
    expect(screen.getByText('ORB high')).toBeInTheDocument();
  });

  it('renders the caller’s ticket above the sections', () => {
    show([sig()], { openId: 'a', renderDetail: (s) => <div data-testid="ticket">{s.instrument.symbol}</div> });
    expect(screen.getByTestId('ticket')).toHaveTextContent('NIFTY26AUG24000CE');
  });

  it('opens from the keyboard, both Enter and Space', () => {
    const onToggle = vi.fn();
    show([sig()], { onToggle });
    const row = screen.getByRole('button', { name: /NIFTY CE Armed/ });
    fireEvent.keyDown(row, { key: 'Enter' });
    fireEvent.keyDown(row, { key: ' ' });
    expect(onToggle).toHaveBeenCalledTimes(2);
    expect(onToggle).toHaveBeenCalledWith('a');
  });

  it('shows the reason on an expanded row that has one', () => {
    show([sig({ reason: 'trail breach' })], { openId: 'a' });
    expect(screen.getByText('trail breach')).toBeInTheDocument();
  });
});

describe('SignalBoard empty state', () => {
  it('explains itself rather than rendering an empty grid', () => {
    show([], { emptyLabel: 'Nothing armed right now.' });
    expect(screen.getByText('Nothing armed right now.')).toBeInTheDocument();
    expect(document.querySelector('.sb-row')).toBeNull();
  });
});

/**
 * Day grouping, after the day band was removed.
 *
 * Seven tests used to live here asserting the grouping through the band's own
 * text — "Today", "Live now", "2 signals · 1 live". The band is gone: it spent a
 * row of vertical space restating a date every row already carries in its Time
 * column.
 *
 * Three of those seven were about the HEADING'S CONTENT and describe something
 * that no longer exists, so they went with it. The other four were about
 * grouping and hoisting BEHAVIOUR and merely read it off the labels — that
 * behaviour is unchanged, and it is asserted directly on `groupByDay` in
 * `grouping.test.tsx` (`hoistToday` on and off, ended rows never hoisted, live
 * rows from several days collected together), which is a better place for it
 * than a string match on furniture.
 *
 * What is still worth checking at render level is the part a reader can see: the
 * ORDER.
 */
describe('day order, without the band', () => {
  it('puts the newest row first and the oldest last', () => {
    const DAY = 86_400_000;
    render(
      <SignalBoard
        signals={[
          sig({ id: 'old', atMs: NOW - 3 * DAY, status: 'ended',
                instrument: { ...sig().instrument, symbol: 'SYMOLD' } }),
          sig({ id: 'new', atMs: NOW, status: 'ended',
                instrument: { ...sig().instrument, symbol: 'SYMNEW' } }),
        ]}
        nowMs={NOW}
        openId={null}
        onToggle={() => {}}
      />,
    );
    fireEvent.click(screen.getByText('Older'));
    const body = document.body.textContent ?? '';
    expect(body.indexOf('SYMNEW')).toBeGreaterThanOrEqual(0);
    expect(body.indexOf('SYMNEW'), 'newest first').toBeLessThan(body.indexOf('SYMOLD'));
  });

  it('renders Today, Yesterday and Older headings', () => {
    const DAY = 86_400_000;
    render(
      <SignalBoard
        signals={[
          sig({ id: 'new', atMs: NOW, status: 'ended',
                instrument: { ...sig().instrument, symbol: 'SYMNEW' } }),
          sig({ id: 'yest', atMs: NOW - DAY, status: 'ended',
                instrument: { ...sig().instrument, symbol: 'SYMYEST' } }),
          sig({ id: 'old', atMs: NOW - 3 * DAY, status: 'ended',
                instrument: { ...sig().instrument, symbol: 'SYMOLD' } }),
        ]}
        nowMs={NOW}
        openId={null}
        onToggle={() => {}}
      />,
    );
    expect(screen.getByText('Today')).toBeTruthy();
    expect(screen.getByText('Yesterday')).toBeTruthy();
    expect(screen.getByText('Older')).toBeTruthy();
  });
});
