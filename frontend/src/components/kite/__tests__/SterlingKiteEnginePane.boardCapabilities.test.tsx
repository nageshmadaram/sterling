/**
 * The three board capabilities, as settings.
 *
 * SuperTrend's table grew three things the shared board never had: draggable
 * column headings, rows that scroll sideways on their own, and order buttons in
 * the row. They were the reason for keeping a second table implementation, and
 * the reason a wholesale swap meant choosing for the operator which of them
 * they could live without.
 *
 * They are choices now. What matters here is that each one actually reaches the
 * DOM — a settings checkbox wired to nothing is worse than no checkbox, because
 * it reads as a decision that has been taken.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';

/*
 * These tests write to the PERSISTED settings store, which means localStorage.
 *
 * Left behind, that is a booby trap for whatever file runs next: a stray
 * `boardRenderer: 'shared'` makes a later test render a different component than
 * it was written against, and the failure surfaces far from its cause. This
 * suite already has a history of order-dependent flakes for exactly this reason,
 * so each file clears up after itself rather than only before itself.
 */
afterEach(() => localStorage.clear());


const cfg = {
  engine_enabled: true, trail_target: 'fast', exit_mode: 'one_red',
  strike_moneyness: ['ATM'], scan_source: 'derivatives',
  scan_expiries: ['weekly'], scan_expiries_indices: null, scan_expiries_stocks: null,
  scan_indices: ['NIFTY 50'], scan_stocks: [], scan_all_stocks: false,
  auto_execute: false, risk_sizing: true, risk_pct: 1, max_lots: 10,
  stop_mode: 'both', directional_mode: false, vehicle: 'otm_options',
  enabled_vehicles: ['otm_options'], itm_depth: 'ITM10', target_delta: null,
  futures_expiry: 'near', adx_min: null, atr_pct_min: null, wire_risk_infra: false,
};

const SYMBOL = 'BANKNIFTY26AUG57000CE';

function makeRow() {
  return {
    underlying: 'NIFTY BANK', token: 1, exchange: 'NFO', regime: 'BULL',
    alignment: { fast: 1, mid: 1, slow: 1 }, direction: 'long', option_type: 'CE',
    spot: 57147.5, stop_loss: 56891.3, entry_sl: 56500, exit_state: '0/3 red',
    score: 85, timestamp_ms: Date.now(), source: 'spot',
    is_active: true, is_fresh: false, target: null,
    legs: [{
      moneyness: 'ITM1', option_type: 'CE', option_symbol: SYMBOL, strike: 57000,
      expiry: '2026-08-25', lot_size: 35, token: 1001, is_active: true,
      premium_spot: 1000, premium_sl: 900, entry_sl: 850, premium_target: null,
    }],
  };
}

function mockPane() {
  vi.doMock('../../../hooks/useSterlingKiteEngine', () => ({
    useEngineConfig: () => ({ data: cfg }),
    useSetEngineConfig: () => ({ mutate: vi.fn() }),
    usePatchEngineConfig: () => ({ mutate: vi.fn() }),
    useResetEngineConfig: () => ({ mutate: vi.fn(), isPending: false }),
    useEngineSignals: () => ({
      data: {
        generated_ms: 1, scanning: false, scanning_label: '', rows: [makeRow()],
        next_scan_ms: 0, auto_scan: false, market_open: true,
      },
    }),
    useRunScan: () => ({ mutate: vi.fn(), mutateAsync: vi.fn(() => Promise.resolve()), isPending: false }),
    useCancelScan: () => ({ mutate: vi.fn(), isPending: false }),
    useStockRegistry: () => ({ data: [] }),
  }));
  vi.doMock('../../../hooks/useNavigator', () => ({
    useNavigatorConfig: () => ({ data: { record: { config: { enabled: true } } } }),
    useSetNavigatorConfig: () => ({ mutate: vi.fn() }),
    useRunNavigatorScan: () => ({ mutate: vi.fn(), isPending: false }),
    useCancelNavigatorScan: () => ({ mutate: vi.fn(), isPending: false }),
  }));
  vi.doMock('../../../hooks/useGammaMove', () => ({ useGammaMoveConfig: () => ({ data: { config: { enabled: true } } }), useUpdateGammaMove: () => ({ mutate: vi.fn() }) }));
  vi.doMock('../../../hooks/useAdaptiveEdge', () => ({ useAdaptiveEdgeEngineConfig: () => ({ data: { config: { enabled: true } } }), useSetAdaptiveEdgeEngineConfig: () => ({ mutate: vi.fn() }) }));
  vi.doMock('../../../hooks/useKite', async () => {
    const actual: any = await vi.importActual('../../../hooks/useKite');
    return { ...actual, useKiteQuote: () => ({ data: { [`NFO:${SYMBOL}`]: { last_price: 1100 } } }) };
  });
}

/** The store the PANE uses — see the note in the migration test about instances. */
async function store() {
  return (await import('../../../store/useKiteSettings')).useKiteSettings;
}

/**
 * The settings drawer, rendered on its own.
 *
 * Its button moved to the pane's title bar, which the engine-tab shell owns —
 * the drawer is common to every engine, so hunting the button from here would
 * test the shell's plumbing instead of these settings.
 */
async function renderSettingsPanel() {
  const { SignalTableSettingsPanel } = await import('../SterlingKiteEnginePane');
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <SignalTableSettingsPanel />
    </QueryClientProvider>,
  );
}

async function renderPane() {
  const { SterlingKiteEnginePane: Pane } = await import('../SterlingKiteEnginePane');
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <Pane onSelectSignal={vi.fn()} />
    </QueryClientProvider>,
  );
}

const legRow = () => document.querySelector('.st-leg-row') as HTMLElement | null;

describe('board capabilities reach the DOM', { timeout: 15000 }, () => {
  beforeEach(() => { localStorage.clear(); vi.resetModules(); });

  it('scrolls rows sideways by default, and stops when switched off', async () => {
    mockPane();
    await renderPane();
    expect(legRow()?.className, 'on by default').toContain('st-row-scroll');
  });

  it('drops the scroll class when the setting is off', async () => {
    (await store()).setState({ boardRowScroll: false });
    mockPane();
    await renderPane();
    const cls = legRow()?.className ?? '';
    expect(cls).toContain('st-leg-row');
    expect(cls, 'no sideways scroll').not.toContain('st-row-scroll');
  });

  it('makes headings draggable by default and plain when off', async () => {
    mockPane();
    await renderPane();
    const draggable = document.querySelector('[data-col-key]') as HTMLElement | null;
    expect(draggable, 'a column heading is rendered').not.toBeNull();
    expect(draggable!.style.cursor, 'draggable by default').toBe('grab');
  });

  it('leaves the heading a plain sort control when dragging is off', async () => {
    (await store()).setState({ boardDragColumns: false });
    mockPane();
    await renderPane();
    const head = document.querySelector('[data-col-key]') as HTMLElement;
    expect(head.style.cursor, 'no grab cursor').not.toBe('grab');
    // Still laid out identically — a different element shape here would shift
    // every column out from under its heading.
    expect(head.getAttribute('data-col-key')).toBeTruthy();
    expect(head.style.width).toBeTruthy();
  });

  it('keeps the order buttons in the row by default', async () => {
    mockPane();
    await renderPane();
    expect(document.querySelector('.st-trade-cell'),
           'the trade path is in the row unless asked otherwise').not.toBeNull();
  });

  it('removes them from the row when the setting is off', async () => {
    (await store()).setState({ boardRowActions: false });
    mockPane();
    await renderPane();
    expect(document.querySelector('.st-trade-cell')).toBeNull();
  });

  it('MOVES the trade buttons rather than deleting them', async () => {
    // The setting's own description says the buttons move into the row you
    // expand. They were inline closures on the collapsed row only, so switching
    // it off removed Buy from the app entirely — the description promising a
    // relocation that did not happen. On an order path that is the worst version
    // of this bug.
    (await store()).setState({ boardRowActions: false });
    mockPane();
    await renderPane();

    // Expand the leg.
    const row = legRow();
    expect(row).not.toBeNull();
    fireEvent.click(row!);

    const buys = screen.queryAllByTitle(/buy/i);
    expect(buys.length).toBeGreaterThan(0);
  });

  it('offers Buy exactly once, whichever way the setting is set', async () => {
    // The property that matters is not where the strip lives but that the action
    // appears once: never zero (unreachable) and never twice (two Buy buttons on
    // one contract is an invitation to double-fire an order).
    mockPane();
    await renderPane();
    fireEvent.click(legRow()!);
    expect(screen.queryAllByTitle(/buy/i).length, 'row carries it').toBe(1);
  });
});

describe('the settings panel offers all three', () => {
  beforeEach(() => { localStorage.clear(); vi.resetModules(); });

  it('names each one in the board settings drawer, wired to the store', async () => {
    mockPane();
    await renderSettingsPanel();

    const s = await store();
    for (const [label, key] of [
      ['Drag columns to reorder', 'boardDragColumns'],
      ['Scroll rows sideways', 'boardRowScroll'],
      ['Order buttons in the row', 'boardRowActions'],
    ] as const) {
      const row = screen.getByText(label);
      const box = row.querySelector('input[type=checkbox]') as HTMLInputElement | null;
      expect(box, `${label} is a checkbox`).not.toBeNull();
      expect(box!.checked, `${label} starts on`).toBe(true);

      // And it is wired: pressing it changes the setting the board reads.
      fireEvent.click(box!);
      expect(s.getState()[key], `${label} is wired to ${key}`).toBe(false);
    }
  });

  it('stops promising draggable columns once dragging is off', async () => {
    (await store()).setState({ boardDragColumns: false });
    mockPane();
    await renderSettingsPanel();
    // The footer hint used to state it unconditionally.
    expect(screen.queryByText(/drag column headers to reorder/i)).not.toBeInTheDocument();
    expect(screen.getByText(/Column dragging is off/i)).toBeInTheDocument();
  });
});

/**
 * The renderer choice.
 *
 * `shared` draws SuperTrend's rows with the component the other four engines
 * use — which is the whole point of this arc: identical by construction rather
 * than by a list of matched properties.
 *
 * It is NOT the default yet, and that restraint is the thing worth pinning.
 * Flipping it would have been the wrong kind of done: 27 existing tests failed,
 * and they were describing behaviour, not markup — the shared board groups by
 * trading day, so the classic "Active now" and "Today (ended)" buckets vanish.
 * Updating those tests would have deleted behaviour and called the difference
 * gone.
 */
describe('the board renderer', () => {
  beforeEach(() => { localStorage.clear(); vi.resetModules(); });

  it('draws the classic table until the shared one is at parity', async () => {
    mockPane();
    await renderPane();
    expect(legRow(), 'the bespoke row is what renders').not.toBeNull();
  });

  it('switches to the shared board when chosen', async () => {
    (await store()).setState({ boardRenderer: 'shared' });
    mockPane();
    await renderPane();
    // The shared board's own row class, and none of the bespoke one's.
    expect(document.querySelector('.sb-row'), 'shared rows render').not.toBeNull();
    expect(legRow(), 'the bespoke table is gone').toBeNull();
  });

  it('offers the choice in the settings drawer', async () => {
    mockPane();
    await renderSettingsPanel();
    const label = screen.getByText('Shared board renderer');
    const box = label.querySelector('input[type=checkbox]') as HTMLInputElement;
    expect(box.checked, 'off by default').toBe(false);

    const s = await store();
    fireEvent.click(box);
    expect(s.getState().boardRenderer).toBe('shared');
  });

  it('carries the capability settings into the shared board', async () => {
    // The three features exist on both renderers or the choice is not free.
    (await store()).setState({ boardRenderer: 'shared', boardRowScroll: true, boardRowActions: true });
    mockPane();
    await renderPane();
    // The LEG row, not the parent. A parent is a summary — `justify-content:
    // space-between`, no column cells — so it has nothing to keep aligned and
    // correctly does not scroll. Selecting the first `.sb-row` picks the parent.
    const leg = [...document.querySelectorAll('.sb-row')]
      .find((el) => !el.className.includes('sb-parent')) as HTMLElement | undefined;
    expect(leg, 'a contract row rendered').toBeDefined();
    expect(leg!.className, 'sideways scroll follows the setting').toContain('sb-row-scroll');
  });

  it('drops the shared board’s row actions when the setting is off', async () => {
    (await store()).setState({ boardRenderer: 'shared', boardRowActions: false });
    mockPane();
    await renderPane();
    expect(document.querySelector('.sb-row')).not.toBeNull();
    expect(document.querySelector('.st-trade-cell')).toBeNull();
  });
});

/**
 * One heading strip, not two.
 *
 * The shared renderer draws its own header. The first version of this wiring
 * replaced only the ROWS and left the bespoke heading strip in place, so both
 * rendered and every column label appeared twice. It surfaced as "Found multiple
 * elements with the text: Entry (Δpts)" while trial-flipping the default — the
 * kind of thing that is obvious in a test and easy to miss on a screen, because
 * two identical strips stacked read as one slightly tall one.
 */
describe('the two renderers do not both draw furniture', () => {
  beforeEach(() => { localStorage.clear(); vi.resetModules(); });

  it('draws exactly one heading strip on the shared renderer', async () => {
    (await store()).setState({ boardRenderer: 'shared' });
    mockPane();
    await renderPane();
    expect(document.querySelectorAll('.st-header-row').length,
           'the bespoke strip stands down').toBe(0);
    expect(document.querySelectorAll('.sb-head-row').length,
           'the shared strip draws once').toBe(1);
  });

  it('draws exactly one on the classic renderer', async () => {
    mockPane();
    await renderPane();
    expect(document.querySelectorAll('.st-header-row').length).toBe(1);
    expect(document.querySelectorAll('.sb-head-row').length).toBe(0);
  });

  it('never renders both row implementations at once', async () => {
    (await store()).setState({ boardRenderer: 'shared' });
    mockPane();
    await renderPane();
    expect(document.querySelectorAll('.st-leg-row').length).toBe(0);
    expect(document.querySelectorAll('.sb-row').length).toBeGreaterThan(0);
  });
});

/**
 * The shared renderer shows SuperTrend's columns, not the shared board's set.
 *
 * The first version of this composition asked for `BOARD_COLUMNS_WITH_DAY_MOVE`
 * and appended SuperTrend's own on top, which handed the board five columns it
 * has never had: Engine, Status, Qty, At risk, Score. The shared list exists so
 * the four migrated boards agree with each other — it is not a floor every board
 * must carry, and a table that grows columns nobody asked for reads as the wrong
 * table.
 */
describe('the shared renderer asks for this engine’s columns', () => {
  beforeEach(() => { localStorage.clear(); vi.resetModules(); });

  const headings = () =>
    [...document.querySelectorAll('.sb-head-row .sb-head')].map((h) => h.textContent!.trim());

  it('does not add the shared board’s generic columns', async () => {
    (await store()).setState({ boardRenderer: 'shared' });
    mockPane();
    await renderPane();
    const heads = headings();
    for (const generic of ['Engine', 'Status', 'Qty', 'At risk', 'Score']) {
      expect(heads, `${generic} is not one of this table's columns`).not.toContain(generic);
    }
  });

  it('shows the ones it does have, in the operator’s order', async () => {
    (await store()).setState({ boardRenderer: 'shared' });
    mockPane();
    await renderPane();
    const heads = headings();
    expect(heads[0]).toBe('Instrument');
    // Its own set, matching the persisted left-then-right order.
    for (const own of ['Exc.', 'Leg (Δ)', 'Entry (Δpts)', 'LTP', 'Time']) {
      expect(heads, own).toContain(own);
    }
  });
});
