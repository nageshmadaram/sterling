/**
 * Direction colours on the SuperTrend board.
 *
 * `showPriceDirection` tints the live numbers green or red by the move. The
 * switch for it used to live in the watchlist's gear panel; this table stopped
 * offering that gear when it got a COLUMNS menu, so the setting became
 * unreachable — on by default and impossible to turn off.
 *
 * It is now an entry in the COLUMNS menu, and this checks the whole path: the
 * menu item exists, pressing it flips the store, and the rendered cells follow.
 * Asserting only that the item is in the menu would not have caught a toggle
 * wired to nothing.
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


/**
 * The store must be reached through the SAME module instance the pane uses.
 *
 * `vi.resetModules()` in `beforeEach` clears the module registry, so the
 * `await import()` in `renderPane` builds a fresh `useKiteSettings` — a
 * different zustand store from anything imported statically at the top of this
 * file. A test that writes to the static instance and reads it back passes or
 * fails for reasons unrelated to the component, which is exactly what happened
 * on my first pass here: it reported the menu toggle as broken when the toggle
 * was fine and the test was reading a store nothing had touched.
 */
async function store() {
  return (await import('../../../store/useKiteSettings')).useKiteSettings;
}

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
const QUOTE_KEY = `NFO:${SYMBOL}`;

function makeRow() {
  return {
    underlying: 'NIFTY BANK', token: 1, exchange: 'NFO', regime: 'BULL',
    alignment: { fast: 1, mid: 1, slow: 1 }, direction: 'long', option_type: 'CE',
    spot: 57147.5, stop_loss: 56891.3, entry_sl: 56500, exit_state: '0/3 red',
    score: 85, timestamp_ms: 1_785_404_700_000, source: 'spot',
    is_active: true, is_fresh: false, target: null,
    legs: [{
      moneyness: 'ITM1', option_type: 'CE', option_symbol: SYMBOL, strike: 57000,
      expiry: '2026-08-25', lot_size: 35, token: 1001, is_active: true,
      premium_spot: 1000, premium_sl: null, entry_sl: null, premium_target: null,
    }],
  };
}

function mockPane(quotes: Record<string, any>) {
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
  vi.doMock('../../../hooks/useKite', async () => {
    const actual: any = await vi.importActual('../../../hooks/useKite');
    return { ...actual, useKiteQuote: () => ({ data: quotes }) };
  });
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

/** The LTP cell, found by the formatted price it renders. */
function ltpCell(text: string): HTMLElement {
  const el = screen.getByText(text);
  return el;
}

describe('direction colours', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.resetModules();
  });

  it('tints a leg green when the premium is up on the day', async () => {
    // last_price above the day's close is a green move.
    mockPane({ [QUOTE_KEY]: { last_price: 1100, ohlc: { close: 1000, open: 1000 } } });
    await renderPane();
    expect(ltpCell('1,100.00').style.color).toContain('green');
  });

  it('tints it red when the premium is down', async () => {
    mockPane({ [QUOTE_KEY]: { last_price: 900, ohlc: { close: 1000, open: 1000 } } });
    await renderPane();
    expect(ltpCell('900.00').style.color).toContain('red');
  });

  it('drops the tint entirely when the setting is off', async () => {
    (await store()).setState({ showPriceDirection: false });
    mockPane({ [QUOTE_KEY]: { last_price: 1100, ohlc: { close: 1000, open: 1000 } } });
    await renderPane();
    const colour = ltpCell('1,100.00').style.color;
    expect(colour).not.toContain('green');
    expect(colour).not.toContain('red');
  });

  it('offers the switch in the COLUMNS menu, and pressing it flips the store', async () => {
    mockPane({ [QUOTE_KEY]: { last_price: 1100, ohlc: { close: 1000, open: 1000 } } });
    await renderPane();

    fireEvent.click(screen.getAllByRole('button', { name: /Columns/i })[0]);
    const item = screen.getByText('Direction colours');
    expect(item, 'the switch is reachable at all').toBeInTheDocument();

    // Click the real control, not the label wrapping it: jsdom's forwarding of a
    // click from an implicitly-associated label to its checkbox is unreliable,
    // and a test that clicks the label proves nothing either way.
    const box = item.querySelector('input[type=checkbox]') as HTMLInputElement | null;
    expect(box, 'the menu item is a checkbox').not.toBeNull();
    expect(box!.checked).toBe(true);

    const s = await store();
    expect(s.getState().showPriceDirection).toBe(true);
    fireEvent.click(box!);
    expect(s.getState().showPriceDirection,
           'pressing the menu item must actually change the setting').toBe(false);
  });

  it('tints the change columns too, not just the price', async () => {
    // The real complaint behind this file: toggling the setting appeared to do
    // nothing, because Chg. was hardcoded dim and Chg. % plain text. The two
    // columns named after the price change were the two it did not reach.
    mockPane({ [QUOTE_KEY]: { last_price: 1100, ohlc: { close: 1000, open: 1000 } } });
    await renderPane();
    expect(screen.getByText('100.00').style.color, 'Chg. follows the move').toContain('green');
    expect(screen.getByText('10.00%').style.color, 'Chg. % follows the move').toContain('green');
  });

  it('leaves the change columns dim when the setting is off', async () => {
    (await store()).setState({ showPriceDirection: false });
    mockPane({ [QUOTE_KEY]: { last_price: 1100, ohlc: { close: 1000, open: 1000 } } });
    await renderPane();
    for (const text of ['100.00', '10.00%']) {
      const colour = screen.getByText(text).style.color;
      expect(colour, `${text} is not tinted`).not.toContain('green');
      expect(colour, `${text} is not tinted`).not.toContain('red');
    }
  });
});
