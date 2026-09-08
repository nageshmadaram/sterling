import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import React from 'react';
import { AdaptiveEdgePane } from '../AdaptiveEdgePane';

const optionLegs = [
  { moneyness: 'ITM2', option_type: 'CE', option_symbol: 'NIFTY25AUG24400CE', strike: 24400, expiry: '2026-08-27', lot_size: 75, token: 1, exchange: 'NSE', entry_premium: 210.4, stop_premium: 162.1, trail_premium: 184.2, ltp: 210.4, resolution_reason: null },
  { moneyness: 'ITM1', option_type: 'CE', option_symbol: 'NIFTY25AUG24450CE', strike: 24450, expiry: '2026-08-27', lot_size: 75, token: 2, exchange: 'NSE', entry_premium: 198.2, stop_premium: 151.0, trail_premium: 172.4, ltp: 198.2, resolution_reason: null },
  { moneyness: 'ATM', option_type: 'CE', option_symbol: 'NIFTY25AUG24500CE', strike: 24500, expiry: '2026-08-27', lot_size: 75, token: 3, exchange: 'NSE', entry_premium: 186.4, stop_premium: 142.1, trail_premium: 161.8, ltp: 186.4, resolution_reason: null },
  { moneyness: 'OTM1', option_type: 'CE', option_symbol: 'NIFTY25AUG24550CE', strike: 24550, expiry: '2026-08-27', lot_size: 75, token: 4, exchange: 'NSE', entry_premium: 174.1, stop_premium: 131.0, trail_premium: 150.2, ltp: 174.1, resolution_reason: null },
  { moneyness: 'OTM2', option_type: 'CE', option_symbol: 'NIFTY25AUG24600CE', strike: 24600, expiry: '2026-08-27', lot_size: 75, token: 5, exchange: 'NSE', entry_premium: 161.8, stop_premium: 120.4, trail_premium: 138.6, ltp: 161.8, resolution_reason: null },
];

const snapshot = {
  label: 'RESEARCH_NOT_LIVE',
  software_complete: true,
  production_gate_authorized: false,
  meets_a197: false,
  registry_locked: true,
  live_trading: false,
  settings: {
    enabled: true,
    symbol: 'NIFTY-I',
    symbols: ['NIFTY-I', 'BANKNIFTY-I', 'FINNIFTY-I', 'SENSEX-I'],
    scan_indices: ['NIFTY 50', 'NIFTY BANK', 'NIFTY FIN SERVICE', 'SENSEX'],
    strike_moneyness: ['ITM2', 'ITM1', 'ATM', 'OTM1', 'OTM2'],
    w_short: 5,
    w_long: 15,
    stop_points: 80,
    trail_points: 40,
    profit_lock_activation_points: 50,
    profit_lock_offset_points: 15,
    persistence_bars: 3,
    scalp_favorable_points: 5,
    extended_favorable_points: 15,
    intraday_favorable_points: 25,
    tick_size: 1,
    ib_minutes: 15,
  },
  readiness: [],
  session: {
    entries: 42,
    exits: 41,
    reentries: 41,
    blocked_pyramid: 2215,
    last_mode: 'MICRO',
    last_thesis: 'THESIS_VALID',
    last_protection_stage: 'P0_RISK_CONTROLLED',
    last_overlays: ['AT_LVN'],
    last_operating_mode: 'active',
    last_horizon: 'IMPULSE',
    last_poc: 24405,
    last_cvd: 32055,
    last_location: 'above_value',
    last_bar_delta: 12,
    last_vwap: 24409.83,
    last_or_location: 'inside_or',
    last_poc_migration: 'unchanged',
    peak_pnl: 12,
    current_pnl: 4,
    profit_giveback: 0.2,
    lifecycle_action: 'HOLD',
    last_position_quantity: 1,
    exit_fill_price: null,
    audit_stages: [],
  },
  legs: [{
    session_date: '2026-08-14',
    entry_time: '2026-08-14T08:38:00+00:00',
    exit_time: null,
    symbol: 'NIFTY-I',
    side: 'BUY',
    entry_price: 24500,
    stop_price: 24420,
    trail_price: 24460,
    entry_mode: 'MICRO',
    exit_mode: 'MICRO',
    peak_mode: 'MICRO',
    horizon: 'IMPULSE',
    thesis: 'THESIS_VALID',
    protection_stage: 'P0_RISK_CONTROLLED',
    overlays: ['AT_LVN'],
    quantity: 1,
    flattened: false,
  }],
  signals: [
    {
      id: 'NIFTY-I-2026-08-14T08:38:00+00:00',
      underlying: 'NIFTY 50',
      tape_symbol: 'NIFTY-I',
      side: 'BUY',
      option_type: 'CE',
      spot_entry: 24500,
      spot_exit: null,
      spot_sl: 24420,
      spot_tsl: 24460,
      entry_time: '2026-08-14T08:38:00+00:00',
      exit_time: null,
      score: 0.62,
      poc: 24405,
      vwap: 24409.83,
      cvd: 32055,
      scanned: true,
      skip_reason: null,
      flattened: false,
      quantity: 1,
      overlays: ['AT_LVN'],
      thesis: 'THESIS_VALID',
      entry_mode: 'MICRO',
      legs: optionLegs,
    },
    {
      id: 'BANKNIFTY-I-unscanned',
      underlying: 'NIFTY BANK',
      tape_symbol: 'BANKNIFTY-I',
      side: null,
      option_type: null,
      spot_entry: null,
      spot_exit: null,
      spot_sl: null,
      spot_tsl: null,
      entry_time: null,
      exit_time: null,
      score: null,
      poc: null,
      vwap: null,
      cvd: null,
      scanned: false,
      skip_reason: 'no tape',
      flattened: true,
      quantity: 0,
      overlays: [],
      thesis: null,
      entry_mode: null,
      legs: [],
    },
  ],
  daily: [],
  quality: null,
  holdout: null,
  coverage: { symbol: 'NIFTY-I', trading_days: 7, bar_count: 2577, meets_a197: false },
  walk_forward: null,
  mode_counts: {},
  mode_transitions: [],
  formula_table: {},
  incomplete_reasons: [],
};

vi.mock('../../../hooks/useAdaptiveEdge', () => ({
  useAdaptiveEdgeSnapshot: () => ({ data: snapshot, isLoading: false, error: null, refetch: vi.fn(), isFetching: false }),
}));

vi.mock('../../../hooks/useKite', () => ({
  useKiteQuote: () => ({ data: {} }),
}));

vi.mock('../AdaptiveEdgeSetupChart', () => ({
  AdaptiveEdgeSetupChart: () => <div>Setup chart</div>,
}));

describe('AdaptiveEdgePane', () => {
  it('shows option legs with entry sl tsl time and clean header ribbon', () => {
    render(<AdaptiveEdgePane />);
    expect(screen.getByRole('heading', { name: 'Adaptive Edge' })).toBeInTheDocument();
    expect(screen.getByText('RESEARCH DESK')).toBeInTheDocument();
    expect(screen.getByText('NIFTY25AUG24500CE')).toBeInTheDocument();
    expect(screen.getAllByText('ATM').length).toBeGreaterThan(0);
    expect(screen.getAllByText('Entry').length).toBeGreaterThan(0);
    expect(screen.getAllByText('SL').length).toBeGreaterThan(0);
    expect(screen.getAllByText('TSL').length).toBeGreaterThan(0);
    expect(screen.getAllByText(/24,405/).length).toBeGreaterThan(0);
    expect(screen.queryByText(/Taken as MICRO/)).toBeNull();
    expect(screen.queryByText(/Price Trajectory & Execution Bounds/)).toBeNull();
  });

  it('opens Adaptive Edge settings from the desk', () => {
    const events: string[] = [];
    window.addEventListener('kite-nav-click', (event) => events.push((event as CustomEvent).detail));
    window.addEventListener('kite-connect-section', (event) => events.push((event as CustomEvent).detail));
    render(<AdaptiveEdgePane />);
    fireEvent.click(screen.getByRole('button', { name: 'Settings' }));
    expect(events).toContain('connect');
    expect(events).toContain('adaptiveEdge');
  });

  it('renders source filter tabs and allows switching between Both, AE Model, and Spot Scan', () => {
    render(<AdaptiveEdgePane />);
    const sourceFilterBar = screen.getByTestId('adaptive-edge-source-filter');
    expect(sourceFilterBar).toBeInTheDocument();

    const bothBtn = screen.getByTestId('ae-source-all');
    const aeBtn = screen.getByTestId('ae-source-ae');
    const spotBtn = screen.getByTestId('ae-source-spot');

    expect(bothBtn).toBeInTheDocument();
    expect(aeBtn).toBeInTheDocument();
    expect(spotBtn).toBeInTheDocument();

    // Default is 'all'
    expect(bothBtn).toHaveAttribute('data-active', 'true');
    expect(aeBtn).toHaveAttribute('data-active', 'false');

    // Click AE Model
    fireEvent.click(aeBtn);
    expect(aeBtn).toHaveAttribute('data-active', 'true');
    expect(bothBtn).toHaveAttribute('data-active', 'false');

    // Click Spot Scan
    fireEvent.click(spotBtn);
    expect(spotBtn).toHaveAttribute('data-active', 'true');
    expect(aeBtn).toHaveAttribute('data-active', 'false');
  });
});
