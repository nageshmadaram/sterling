import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';
import { SterlingKiteEnginePane } from '../SterlingKiteEnginePane';

// Minimal confluence-mode config + one merged confluence row whose single confirmed
// leg carries its own premium entry/stop (premium_spot / premium_sl / entry_sl) and a
// red-counter exit_state. Verifies the 4th scan-source mode and the new signal-table
// columns (SL / TSL / Exit / Target) render and are wired to the new fields.
const cfg = {
  engine_enabled: true, trail_target: 'fast', exit_mode: 'one_red', exit_aligned_trail: false,
  strike_moneyness: ['ATM'], scan_source: 'confluence',
  scan_expiries: ['weekly', 'monthly'], scan_expiries_indices: null, scan_expiries_stocks: null,
  scan_indices: ['NIFTY 50'], scan_stocks: [], scan_all_stocks: false, auto_execute: false,
  risk_sizing: true, risk_pct: 1.0, max_lots: 10, expiry_square_off_days: 1, time_stop_bars: 0,
  stop_mode: 'both', directional_mode: false, vehicle: 'otm_options',
  enabled_vehicles: ['otm_options', 'deep_itm_options'], itm_depth: 'ITM10', target_delta: null,
  futures_expiry: 'near', adx_min: null, atr_pct_min: null, block_entry_minutes_before_close: 0,
  max_spread_pct: null, min_oi: null, max_daily_loss_pct: null, wire_risk_infra: false,
};

const confluenceRow = {
  underlying: 'NIFTY 50', token: 256265, exchange: 'NFO', regime: 'BULL',
  alignment: { fast: 1, mid: 1, slow: 1 }, direction: 'long', option_type: 'CE',
  spot: 24010, stop_loss: 23900, entry_sl: 23890, exit_state: '0/1 red',
  score: 85, timestamp_ms: 1_700_000_000_000, source: 'confluence', is_active: true, is_fresh: true,
  underlying_spot: 24010,
  legs: [
    {
      moneyness: 'ATM', option_type: 'CE', option_symbol: 'NIFTY25JUN24000CE', strike: 24000,
      expiry: '2026-06-26', lot_size: 75, token: 44001,
      premium_spot: 120.5, premium_sl: 100.0, entry_sl: 95.0, is_active: true,
    },
  ],
};

vi.mock('../../../hooks/useSterlingKiteEngine', () => ({
  useEngineConfig: () => ({ data: cfg }),
  useSetEngineConfig: () => ({ mutate: vi.fn((_v: unknown, o?: { onSuccess?: () => void }) => o?.onSuccess?.()) }),
  usePatchEngineConfig: () => ({ mutate: vi.fn((_v: unknown, o?: { onSuccess?: () => void }) => o?.onSuccess?.()) }),
  useResetEngineConfig: () => ({ mutate: vi.fn(), isPending: false }),
  useEngineSignals: () => ({
    data: {
      generated_ms: 1, scanning: false, scanning_label: '', rows: [confluenceRow],
      next_scan_ms: 0, auto_scan: false, market_open: true,
    },
  }),
  useRunScan: () => ({ mutate: vi.fn(), isPending: false }),
  useCancelScan: () => ({ mutate: vi.fn(), isPending: false }),
  useStockRegistry: () => ({ data: [] }),
}));

function renderPane() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <SterlingKiteEnginePane onSelectSignal={vi.fn()} />
    </QueryClientProvider>,
  );
  // List is the product default; layout controls now live inside table settings.
}

describe('SterlingKiteEnginePane — confluence source + signal-table columns', () => {
  beforeEach(() => { localStorage.clear(); });

  it('shows the Confluence source status and the full column header (Entry/SL/TSL/Exit/Target)', () => {
    renderPane();
    // The table is read-only about engine configuration and shows its active source.
    expect(screen.getByText('Confluence')).toBeInTheDocument();
    // Column header carries all seven columns (Entry/SL/TSL/Exit/Target new-or-relabelled;
    // Chg./LTP existing).
    expect(screen.getByText('Entry (Δpts)')).toBeInTheDocument();
    expect(screen.getByText('SL')).toBeInTheDocument();
    expect(screen.getByText('TSL')).toBeInTheDocument();
    expect(screen.getByText('Exit')).toBeInTheDocument();
    expect(screen.getByText('Target')).toBeInTheDocument();
    expect(screen.getByText('LTP')).toBeInTheDocument();
  });

  it('renders the confirmed leg with its premium entry/SL/TSL and the red-counter exit state', () => {
    renderPane();
    // The confirmed leg's own premium entry (premium_spot), initial SL (entry_sl) and
    // ratcheting TSL (premium_sl) render, plus the row-level exit-counter progress.
    expect(screen.getAllByText('120.50').length).toBeGreaterThanOrEqual(1);  // Entry premium
    expect(screen.getByText('95.0')).toBeInTheDocument();    // SL (entry_sl)
    expect(screen.getByText('100.0')).toBeInTheDocument();   // TSL (premium_sl)
    expect(screen.getByText('0/1 red')).toBeInTheDocument(); // Exit (exit_state)
  });
});
