/**
 * The Snapback settings page.
 *
 * Two things worth a test rather than a look. The page must show the gate's
 * SCORECARD, for the same reason the board does — "not validated" and "passes
 * six of nine" are different facts. And it must send only what CHANGED: a
 * full-object write silently reverts whatever moved since the cache was
 * fetched, which is a bug this repo has already shipped and had to remove
 * everywhere.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import React from 'react';

const mutate = vi.fn();
let cfgState: any;

vi.mock('../../../hooks/useSnapback', () => ({
  useSnapbackConfig: () => cfgState,
  useUpdateSnapback: () => ({ mutate, isPending: false }),
}));
vi.mock('../config/ScanSettings', () => ({
  InstrumentsGroup: () => <div>instruments</div>,
}));
vi.mock('../../../hooks/useStockRegistry', () => ({
  useStockRegistry: () => ({ data: undefined }),
}));

import { SnapbackSettings } from '../../SnapbackSettings';

const DEFAULTS = {
  enabled: false, auto_execute: false,
  universe_mode: 'fno', max_universe: 200,
  market_filter: 'bearish', market_ema: 50, hedge_mode: 'index_futures',
  scan_indices: ['NIFTY'], scan_stocks: ['RELIANCE'], scan_stock_contracts: true,
  lookback_days: 20, min_stretch_atr: 1.5, allow_fade_down: false,
  cooldown_days: 5, min_atr_bp: 0,
  target_delta: 0.7, min_dte: 40, max_dte: 60,
  expiry_series_indices: ['monthly'], expiry_series_stocks: ['monthly'],
  min_option_premium: 10, max_spread_pct: 2, min_option_oi: 0,
  exit_mode: 'horizon', hold_days: 15, premium_stop_pct: 35,
  premium_trail_pct: 0, mean_touch_ema: 20,
  sizing_mode: 'LOTS', premium_pct_of_capital: 2, capital_inr: 100000,
  lots: 1, max_lots: 5, max_open_positions: 5,
  one_position_per_underlying: true, stop_mode: 'both',
  rv_window: 20, assumed_vrp: 1.22,
  smile_slope: 1.6, smile_itm_slope: 0.5, short_leg_delta: 0,
};

beforeEach(() => {
  mutate.mockReset();
  cfgState = {
    isLoading: false,
    data: {
      config: { ...DEFAULTS },
      defaults: { ...DEFAULTS },
      vocabularies: {},
      vrp_band: [1.15, 1.3],
      warnings: [],
      strategy: {
        name: 'Snapback', tagline: 'Buys premium into an over-extension.',
        provenance: 'Measured in backend/study/snapback_research.py',
        calibrated_fields: ['target_delta', 'min_dte', 'hold_days',
                            'min_stretch_atr', 'lookback_days', 'allow_fade_down'],
        validated: false,
        validation: {
          promoted: false, measured_at: '2026-09-12',
          span: '739 sessions', universe: [], oos_trades: 229,
          oos_entry_days: 121, oos_mean_day_return_pct: 6.282,
          oos_ci_pct: [-1.71, 17.17], sharpe: 1.595, deflated_sharpe: 0.0443,
          permutation_p: 0.0287, permutation_p_full_sample: 0.0012,
          breakeven_vrp: 2.243, max_drawdown_pct: -10.38, allocation_pct: 2,
          total_return_pct: 36.96, per_year_pct: {},
          checks: { beats_random_timing: true, deflated_sharpe: false },
          reasons: ['deflated Sharpe 0.044 < 0.5'],
          slippage_pct: 0.5, passed: 7, total_checks: 9,
        },
      },
    },
  };
});

describe('Snapback settings', () => {
  it('shows the scorecard rather than a bare verdict', () => {
    render(<SnapbackSettings />);
    expect(screen.getByText(/Passes 7 of 9 gate checks/)).toBeInTheDocument();
    expect(screen.getByText(/✓ Beats random entry timing/)).toBeInTheDocument();
    expect(screen.getByText(/✗ Survives the variant count/)).toBeInTheDocument();
  });

  it('puts the break-even multiple beside what the market charges', () => {
    render(<SnapbackSettings />);
    expect(screen.getByText(/2.243x against a market that charges/))
      .toBeInTheDocument();
    expect(screen.getByText(/1.15–1.3x/)).toBeInTheDocument();
  });

  it('marks which defaults were measured and which were not', () => {
    render(<SnapbackSettings />);
    expect(screen.getByText('Target delta')).toBeInTheDocument();
    // `target_delta` is in calibrated_fields; `max_dte` is not.
    expect(screen.getAllByText(/MEASURED\./).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/Not measured — a judgement call\./).length)
      .toBeGreaterThan(0);
  });

  it('sends only what changed, never the whole object', () => {
    render(<SnapbackSettings />);
    const input = screen.getAllByRole('spinbutton')
      .find((el) => (el as HTMLInputElement).value === '20');
    expect(input).toBeTruthy();
    fireEvent.change(input!, { target: { value: '30' } });
    fireEvent.click(screen.getByRole('button', { name: /Apply/i }));
    expect(mutate).toHaveBeenCalledTimes(1);
    const sent = mutate.mock.calls[0][0];
    expect(Object.keys(sent)).toHaveLength(1);
  });

  it('renders a loading state rather than crashing without data', () => {
    cfgState = { isLoading: true, data: undefined };
    render(<SnapbackSettings />);
    expect(screen.getByText(/Loading strategy settings/)).toBeInTheDocument();
  });
});
