/**
 * The Snapback board: the scorecard, the daily-cadence note, and the empty state.
 *
 * The load-bearing assertion is the scorecard. This engine passes six of nine
 * gate checks and misses two sample-size ones, and a flat "NOT VALIDATED" banner
 * would read identically to a strategy whose entries lose money. Those are not
 * the same thing to someone deciding whether to click Buy, so the board has to
 * say which checks passed and why the others did not.
 *
 * The second is the cadence note. Every rule here reads a daily CLOSE, so a
 * board watched through the session does not move — and without saying so, quiet
 * reads as broken.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import React from 'react';
import { SnapbackBoard } from '../SnapbackBoard';

let snap: any = { data: undefined, isLoading: false, error: null };
let scanState: any = { mutate: vi.fn(), isPending: false, error: null };

let historyState: any = { data: { sessions: 30, signals: [], count: 0 } };

vi.mock('../../../../hooks/useSnapback', () => ({
  useSnapbackSnapshot: () => snap,
  useRunSnapbackScan: () => scanState,
  useSnapbackHistory: () => historyState,
}));

const VALIDATION = {
  promoted: false, measured_at: '2026-09-12',
  span: '2023-09-13..2026-09-11 (739 sessions)',
  universe: ['NIFTY', 'RELIANCE'],
  oos_trades: 229, oos_entry_days: 121, oos_mean_day_return_pct: 6.282,
  oos_ci_pct: [-1.71, 17.17], sharpe: 1.595, deflated_sharpe: 0.0443,
  permutation_p: 0.0287, permutation_p_full_sample: 0.0012,
  breakeven_vrp: 2.243, max_drawdown_pct: -10.38, allocation_pct: 2.0,
  total_return_pct: 36.96,
  per_year_pct: { 2024: 14.55, 2025: 5.48, 2026: 6.9 },
  checks: {
    enough_trades: true, enough_days: true, profitable: true,
    beats_random_timing: true, deflated_sharpe: false, priced_edge: true,
    consistent_across_years: true, survivable_drawdown: true,
    mean_proven: false,
  },
  reasons: [
    'deflated Sharpe 0.044 < 0.5 — the result does not survive how many variants were tried',
    'day-clustered 95% interval [-1.71%, +17.17%] includes zero — the DIRECTION is established, the SIZE is not',
  ],
  slippage_pct: 0.5, passed: 7, total_checks: 9,
};

function row(over: any = {}) {
  return {
    signal_id: 'snapback:fade_up:NIFTY:1757000000000',
    strategy: 'snapback', side: 'fade_up', symbol: 'NIFTY', state: 'armed',
    reason: null, direction: 'BEARISH', opt_type: 'PE',
    timestamp_ms: 1_789_009_200_000, spot: 23437, mean_target: 23100,
    distance_pct: 1.44, stretch: 2.6, level: 23380, strength: 'MODERATE',
    realized_vol_pct: 11.4, assumed_iv_pct: 13.9, assumed_vrp: 1.22,
    hold_days: 10, underlying_token: 256265,
    contract: {
      symbol: 'NIFTY25OCT23800PE', strike: 23800, option_type: 'PE',
      expiry: '2026-10-28', dte: 36, lot_size: 75, token: 1, exchange: 'NFO',
      delta: -0.7, moneyness: 'ITM',
    },
    premium: 520.5, premium_is_modelled: false, modelled_premium: 511,
    quote: { premium: 520.5, bid: 519, ask: 522, ltp: 520, oi: 1, spread_pct: 0.58, blockers: [] },
    stop_premium: 338.3, target_premium: 690, trail_premium: null,
    outcome: null, lots: 1, quantity: 75, deployed_inr: 39037.5,
    reasons: ['Closed above its 20-session high (23,380.00).'],
    metrics: { lookback_days: 20 },
    ...over,
  };
}

function snapshot(over: any = {}) {
  return {
    strategy: { name: 'Snapback', validated: false, validation: VALIDATION },
    config: { enabled: true },
    rows: [row()], armed: 1, scanned: 19, scanning: false,
    last_scan_ms: 1_789_009_200_000, last_error: null, failures: [],
    warnings: [], auto_execution_blocker: null, catchup_sessions: 3,
    ...over,
  };
}

beforeEach(() => {
  snap = { data: snapshot(), isLoading: false, error: null };
  scanState = { mutate: vi.fn(), isPending: false, error: null };
  historyState = { data: { sessions: 30, signals: [], count: 0 } };
});

describe('Snapback board', () => {
  it('reports the scorecard, not a bare verdict', () => {
    render(<SnapbackBoard nowMs={1_789_009_200_000} />);
    expect(screen.getByText(/PASSES 7 OF 9 CHECKS/)).toBeInTheDocument();
    expect(screen.getByText(/✓ Beats random entry timing/)).toBeInTheDocument();
    expect(screen.getByText(/✗ Survives the variant count/)).toBeInTheDocument();
    expect(screen.getByText(/✗ Size of the edge proven/)).toBeInTheDocument();
  });

  it('gives the harness’s own reasons verbatim', () => {
    render(<SnapbackBoard nowMs={1_789_009_200_000} />);
    expect(screen.getByText(/the DIRECTION is established, the SIZE is not/))
      .toBeInTheDocument();
  });

  it('states the break-even vol multiple against what the market charges', () => {
    render(<SnapbackBoard nowMs={1_789_009_200_000} />);
    expect(screen.getByText(/2.243x/)).toBeInTheDocument();
    expect(screen.getByText(/1.15–1.30x/)).toBeInTheDocument();
  });

  it('says the rows change once a session, so quiet does not read as broken', () => {
    render(<SnapbackBoard nowMs={1_789_009_200_000} />);
    expect(screen.getByText(/once a session and not once a tick/))
      .toBeInTheDocument();
  });

  it('explains an empty board rather than showing nothing', () => {
    snap = { data: snapshot({ rows: [], armed: 0 }), isLoading: false, error: null };
    render(<SnapbackBoard nowMs={1_789_009_200_000} />);
    expect(screen.getByText(/deliberately rare/)).toBeInTheDocument();
    expect(screen.getByText(/below its own 50-session mean/)).toBeInTheDocument();
  });

  it('surfaces a failed scan instead of an empty-board explanation', () => {
    snap = {
      data: snapshot({ rows: [], last_error: 'No active Kite account' }),
      isLoading: false, error: null,
    };
    render(<SnapbackBoard nowMs={1_789_009_200_000} />);
    expect(screen.getByText(/No active Kite account/)).toBeInTheDocument();
  });

  it('shows the auto-execution blocker when there is one', () => {
    snap = {
      data: snapshot({ auto_execution_blocker: 'Snapback did not pass the harness' }),
      isLoading: false, error: null,
    };
    render(<SnapbackBoard nowMs={1_789_009_200_000} />);
    expect(screen.getByText(/MANUAL ONLY/)).toBeInTheDocument();
    expect(screen.getByText(/did not pass the harness/)).toBeInTheDocument();
  });

  it('counts the instruments that could not be scanned rather than dropping them', () => {
    snap = {
      data: snapshot({ failures: ['TCS: no daily candles'] }),
      isLoading: false, error: null,
    };
    render(<SnapbackBoard nowMs={1_789_009_200_000} />);
    expect(screen.getByText(/1 instrument could not be scanned/))
      .toBeInTheDocument();
  });

  it('disables the scan control while the engine is off', () => {
    snap = {
      data: snapshot({ config: { enabled: false } }), isLoading: false, error: null,
    };
    render(<SnapbackBoard nowMs={1_789_009_200_000} />);
    expect(screen.getByRole('button', { name: /Scan now/ })).toBeDisabled();
  });

  it('shows replayed history so a quiet board is not a broken one', () => {
    historyState = {
      data: {
        sessions: 30, count: 2,
        signals: [row({ signal_id: 'h1', historical: true, state: 'ended' }),
                  row({ signal_id: 'h2', historical: true, state: 'ended' })],
      },
    };
    snap = { data: snapshot({ rows: [], armed: 0 }), isLoading: false, error: null };
    render(<SnapbackBoard nowMs={1_789_009_200_000} />);
    expect(screen.getByText(/rows marked/)).toBeInTheDocument();
    // The empty-state text must NOT appear when history filled the board.
    expect(screen.queryByText(/deliberately rare/)).toBeNull();
  });

  it('runs a scan on demand', () => {
    render(<SnapbackBoard nowMs={1_789_009_200_000} />);
    screen.getByRole('button', { name: /Scan now/ }).click();
    expect(scanState.mutate).toHaveBeenCalled();
  });
});
