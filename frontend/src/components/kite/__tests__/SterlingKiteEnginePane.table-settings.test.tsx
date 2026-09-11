import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';
import { useKiteSettings } from '../../../store/useKiteSettings';
import { SignalTableSettingsPanel, SterlingKiteEnginePane } from '../SterlingKiteEnginePane';

const signalRows: any[] = [];

const cfg = {
  engine_enabled: true,
  trail_target: 'fast',
  exit_mode: 'one_red',
  strike_moneyness: ['ATM'],
  scan_source: 'derivatives',
  scan_expiries: ['weekly', 'monthly'],
  scan_expiries_indices: null,
  scan_expiries_stocks: null,
  scan_indices: ['NIFTY 50'],
  scan_stocks: [],
  scan_all_stocks: false,
  auto_execute: false,
  risk_sizing: true,
  risk_pct: 1,
  max_lots: 10,
  stop_mode: 'both',
  directional_mode: false,
  vehicle: 'otm_options',
  enabled_vehicles: ['otm_options'],
  itm_depth: 'ITM10',
  target_delta: null,
  futures_expiry: 'near',
  adx_min: null,
  atr_pct_min: null,
  wire_risk_infra: false,
};

vi.mock('../../../hooks/useSterlingKiteEngine', () => ({
  useEngineConfig: () => ({ data: cfg }),
  useSetEngineConfig: () => ({ mutate: vi.fn(), isPending: false }),
  usePatchEngineConfig: () => ({ mutate: vi.fn(), isPending: false }),
  useEngineSignals: () => ({
    data: {
      generated_ms: 0,
      scanning: false,
      scanning_label: '',
      rows: signalRows,
      next_scan_ms: 0,
      auto_scan: false,
      market_open: true,
    },
  }),
  useRunScan: () => ({ mutate: vi.fn(), isPending: false }),
  useCancelScan: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock('../../../hooks/useKite', () => ({
  useKiteQuote: () => ({ data: {} }),
}));

function renderPane() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <SterlingKiteEnginePane onSelectSignal={vi.fn()} />
    </QueryClientProvider>,
  );
}

function renderSettingsPanel() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={queryClient}>
      <SignalTableSettingsPanel />
    </QueryClientProvider>,
  );
}

describe('SterlingKiteEnginePane — table-only settings', () => {
  beforeEach(() => {
    localStorage.clear();
    signalRows.length = 0;
    cfg.scan_source = 'derivatives';
    useKiteSettings.getState().resetSignalTableSettings();
  });

  it('keeps table preferences exclusive and routes engine configuration to Connect', () => {
    const navListener = vi.fn();
    window.addEventListener('kite-nav-click', navListener);
    // Rendered directly. The button that opens it lives in the pane's TITLE BAR
    // now, which the engine-tab shell owns because the drawer is common to every
    // engine — so hunting the button here would be testing the shell's plumbing
    // rather than this drawer's contents.
    renderSettingsPanel();

    expect(screen.getByText('Board settings')).toBeInTheDocument();
    expect(screen.getByText(/nothing here changes what is scanned/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'List' })).toHaveAttribute('aria-pressed', 'true');

    // Engine controls must never leak back into the table preferences drawer.
    expect(screen.queryByText('Signal Discovery')).not.toBeInTheDocument();
    expect(screen.queryByText('Exit & Protection')).not.toBeInTheDocument();
    expect(screen.queryByText('Risk & Safeguards')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('checkbox', { name: 'Exchange' }));
    expect(useKiteSettings.getState().showExchange).toBe(false);

    // The drawer now points at Trade Rules — entry, stop, exit and sizing are
    // engine-independent, so that is where they live.
    fireEvent.click(screen.getByRole('button', { name: /trade rules/i }));
    expect(localStorage.getItem('kite_connect_section')).toBe('manualRules');
    expect(navListener).toHaveBeenCalled();

    window.removeEventListener('kite-nav-click', navListener);
  });

  it('keeps the live filters in the toolbar, not duplicated in the drawer', () => {
    // Best leg and Ended used to appear as checkboxes here as well as on the
    // toolbar. Two controls for one setting is a pair that eventually drifts,
    // and the toolbar copy is the one whose state is visible without opening
    // anything.
    signalRows.push({
      underlying: 'NIFTY 50', token: 256265, exchange: 'NFO', regime: 'BULL',
      alignment: { fast: 1, mid: 1, slow: 1 }, direction: 'long', option_type: 'CE',
      legs: [], spot: 25_000, stop_loss: 24_900, score: 85,
      timestamp_ms: Date.now(), source: 'spot', is_active: true, is_fresh: true,
    });
    renderPane();

    fireEvent.click(screen.getByRole('switch', { name: 'BEST LEG' }));
    expect(localStorage.getItem('kite_st_best_only')).toBe('true');

    expect(screen.queryByRole('checkbox', { name: 'Best signal per instrument' })).not.toBeInTheDocument();
    expect(screen.queryByRole('checkbox', { name: 'Show ended setups' })).not.toBeInTheDocument();
  });

  it('reveals retained rows instead of reporting that no signals exist', () => {
    localStorage.setItem('kite_st_show_ended', 'false');
    signalRows.push({
      underlying: 'NIFTY 50',
      token: 256265,
      exchange: 'NFO',
      regime: 'BULL',
      alignment: { fast: 1, mid: 1, slow: 1 },
      direction: 'long',
      option_type: 'CE',
      legs: [],
      spot: 25_000,
      stop_loss: 24_900,
      score: 85,
      timestamp_ms: Date.now(),
      source: 'spot',
      is_active: false,
      is_fresh: false,
    });

    renderPane();

    expect(screen.getByText(/1 recent setup is hidden by the current table filters/i)).toBeInTheDocument();
    expect(screen.queryByText(/No active or recent setups/i)).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Show recent signals' }));

    expect(localStorage.getItem('kite_st_show_ended')).toBe('true');
    expect(screen.getByText('Today')).toBeInTheDocument();
  });

  it('keeps active spot candidate legs visible when ended legs are hidden', () => {
    localStorage.setItem('kite_st_show_ended', 'false');
    signalRows.push({
      underlying: 'NIFTY 50',
      token: 256265,
      exchange: 'NFO',
      regime: 'BULL',
      alignment: { fast: 1, mid: 1, slow: 1 },
      direction: 'long',
      option_type: 'CE',
      legs: [{
        moneyness: 'ATM',
        option_type: 'CE',
        option_symbol: 'NIFTY26JUN25000CE',
        strike: 25_000,
        expiry: '2026-06-26',
        lot_size: 75,
        token: 44001,
        is_active: false,
      }],
      spot: 25_000,
      stop_loss: 24_900,
      score: 85,
      timestamp_ms: Date.now(),
      source: 'spot',
      is_active: true,
      is_fresh: true,
    });

    renderPane();

    expect(screen.getByText('Today')).toBeInTheDocument();
    expect(screen.getByText('25000')).toBeInTheDocument();
    expect(screen.queryByText(/no liquid contract/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/No option contract matched/i)).not.toBeInTheDocument();
  });

  it('shows the backend strike-resolution reason when a setup has no option legs', () => {
    signalRows.push({
      underlying: 'NIFTY 50',
      token: 256265,
      exchange: 'NFO',
      regime: 'BULL',
      alignment: { fast: 1, mid: 1, slow: 1 },
      direction: 'long',
      option_type: 'CE',
      legs: [],
      spot: 25_000,
      stop_loss: 24_900,
      score: 85,
      timestamp_ms: Date.now(),
      source: 'spot',
      is_active: true,
      is_fresh: true,
      resolution_reason: 'No listed contract matched the selected strike and expiry series.',
    });

    renderPane();

    expect(screen.getByText('No listed contract matched the selected strike and expiry series.')).toBeInTheDocument();
    expect(screen.queryByText(/no liquid contract/i)).not.toBeInTheDocument();
  });

  it('renders spot-source premium Entry, SL and TSL snapshots when present', () => {
    cfg.scan_source = 'spot';
    signalRows.push({
      underlying: 'NIFTY 50',
      token: 256265,
      exchange: 'NFO',
      regime: 'BULL',
      alignment: { fast: 1, mid: 1, slow: 1 },
      direction: 'long',
      option_type: 'CE',
      legs: [{
        moneyness: 'ATM',
        option_type: 'CE',
        option_symbol: 'NIFTY26JUN25000CE',
        strike: 25_000,
        expiry: '2026-06-26',
        lot_size: 75,
        token: 44001,
        is_active: true,
        premium_spot: 123.45,
        entry_sl: 101.2,
        premium_sl: 111.3,
      }],
      spot: 25_000,
      stop_loss: 24_900,
      score: 85,
      timestamp_ms: Date.now(),
      source: 'spot',
      is_active: true,
      is_fresh: true,
    });

    renderPane();

    expect(screen.getByText('Entry (Δpts)')).toBeInTheDocument();
    expect(screen.getByText('SL')).toBeInTheDocument();
    expect(screen.getByText('TSL')).toBeInTheDocument();
    expect(screen.getAllByText('123.45').length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('101.2')).toBeInTheDocument();
    expect(screen.getByText('111.3')).toBeInTheDocument();
  });
});
