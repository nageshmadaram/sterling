/**
 * The intraday board: the scan control, the strategy filter, and what it says
 * when it is empty.
 *
 * The load-bearing assertions are the two warnings. Nothing in this pack was
 * measured and its levels are spot points rather than premium, and both facts
 * have to sit above the rows rather than only in a document — a board that
 * renders a confident-looking signal without them is the failure mode.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import React from 'react';
import { IntradayBoard } from '../IntradayBoard';

let snap: any = { data: undefined, isLoading: false, error: null };
let scanState: any = { mutate: vi.fn(), isPending: false, error: null, data: undefined };
let armState: any = { mutate: vi.fn(), isPending: false, error: null, data: undefined };
let exitState: any = { mutate: vi.fn(), isPending: false, error: null, data: undefined };
let squareState: any = { mutate: vi.fn(), isPending: false, error: null, data: undefined };
let reconcileState: any = { mutate: vi.fn(), isPending: false, error: null, data: undefined };

vi.mock('../../../../hooks/useIntraday', () => ({
  useIntradaySnapshot: () => snap,
  useIntradayScan: () => scanState,
  useIntradayArm: () => armState,
  useIntradayExit: () => exitState,
  useIntradaySquareOff: () => squareState,
  useIntradayReconcile: () => reconcileState,
}));

const META = [
  { id: 'pivot_break', name: 'Pivot Break', tag: 'PB', tagline: '', how_it_works: 'A strong candle.' },
  { id: 'ma_ribbon', name: 'MA Ribbon', tag: 'MR', tagline: '', how_it_works: 'The 55 clears the ribbon.' },
  { id: 'vwap_supertrend', name: 'VWAP SuperTrend', tag: 'VS', tagline: '', how_it_works: 'A flip past VWAP.' },
];

function row(over: any = {}) {
  return {
    strategy: 'pivot_break', strategy_name: 'Pivot Break', symbol: 'NIFTY',
    state: 'armed', blockers: [], spot: 24800, timeframe: '5m',
    contract: {
      symbol: 'NIFTY26SEP24800CE', strike: 24800, option_type: 'CE',
      expiry: '2026-09-17', dte: 6, lot_size: 75, token: 1, exchange: 'NFO',
    },
    generated_at_ms: 1_789_009_200_000,
    signal: {
      strategy: 'pivot_break', symbol: 'NIFTY', direction: 'BULLISH', opt_type: 'CE',
      timestamp_ms: 1_789_009_200_000, entry: 24800, stop: 24780, target: 24840,
      target2: 24860, risk: 20, rr: 2, strength: 'STRONG', origin: 'R1 + EMA9',
      reasons: [], metrics: {},
    },
    metrics: {},
    ...over,
  };
}

function snapshot(over: any = {}) {
  return {
    strategy: {
      id: 'intraday', name: 'Intraday Pack', contract_version: 'A400.1',
      tagline: '', strategies: META, provenance: '', validated: false,
      calibration: {}, calibrated_fields: [],
    },
    config: { enabled: true, timeframe: '5m' },
    warnings: [], enabled_strategies: ['pivot_break', 'ma_ribbon', 'vwap_supertrend'],
    rows: [], armed: 0, scanned: 14, scanning: false, failures: [],
    last_scan_ms: 0, last_error: null, generated_at_ms: 0,
    ...over,
  };
}

function position(over: any = {}) {
  return {
    strategy: 'pivot_break', signal_id: 's1', underlying: 'NIFTY',
    contract: {
      tradingsymbol: 'NIFTY26SEP24800CE', exchange: 'NFO', token: 1234,
      option_type: 'CE', strike: 24800, expiry: '2026-09-17',
      lot_size: 75, tick_size: 0.05,
    },
    thesis: 'BULLISH', side: 'long',
    spot_entry: 24800, spot_stop: 24780, spot_target: 24840, spot_target2: null,
    spot_risk: 20, entry: 100, stop: 120, initial_stop: 70, target: 160, target2: 190,
    quantity: 75, lots: 1, fill_price: 100, effective_entry: 100, peak: 160,
    breakeven_done: true, target1_done: false, scaled_qty: 0, scaled_price: 0,
    exiting: false, order_id: 'O1', gtt_id: 901,
    broker_stop: true, stop_mode: 'both', status: 'open', is_open: true,
    entered_ms: 1_789_009_200_000, entry_day: '2026-09-11',
    exit_price: 0, exit_reason: '', realised_inr: 0,
    ...over,
  };
}

beforeEach(() => {
  snap = { data: snapshot(), isLoading: false, error: null };
  scanState = { mutate: vi.fn(), isPending: false, error: null, data: undefined };
  armState = { mutate: vi.fn(), isPending: false, error: null, data: undefined };
  exitState = { mutate: vi.fn(), isPending: false, error: null, data: undefined };
  squareState = { mutate: vi.fn(), isPending: false, error: null, data: undefined };
  reconcileState = { mutate: vi.fn(), isPending: false, error: null, data: undefined };
});

describe('IntradayBoard', () => {
  it('says nothing here was measured, above the rows', () => {
    render(<IntradayBoard />);
    expect(screen.getByText('NOT VALIDATED')).toBeTruthy();
    expect(screen.getByText(/judgement call/)).toBeTruthy();
  });

  it('says the levels are the underlying, not premium', () => {
    render(<IntradayBoard />);
    expect(screen.getByText(/UNDERLYING's points, not premium/)).toBeTruthy();
  });

  it('runs a scan on demand', () => {
    render(<IntradayBoard />);
    fireEvent.click(screen.getByText('Scan now'));
    expect(scanState.mutate).toHaveBeenCalled();
  });

  it('refuses to scan while a replay is driving the board', () => {
    snap = { data: snapshot({ source: 'simulation' }), isLoading: false, error: null };
    render(<IntradayBoard />);
    fireEvent.click(screen.getByText('Scan now'));
    expect(scanState.mutate).not.toHaveBeenCalled();
  });

  it('offers a filter per strategy with its own count', () => {
    snap = {
      data: snapshot({ rows: [row(), row({ strategy: 'ma_ribbon', strategy_name: 'MA Ribbon' })] }),
      isLoading: false, error: null,
    };
    render(<IntradayBoard />);
    expect(screen.getByText('All 2')).toBeTruthy();
    expect(screen.getByText('Pivot Break 1')).toBeTruthy();
    expect(screen.getByText('MA Ribbon 1')).toBeTruthy();
    expect(screen.getByText('VWAP SuperTrend 0')).toBeTruthy();
  });

  it('narrows to one strategy and back', () => {
    snap = {
      data: snapshot({ rows: [row(), row({ strategy: 'ma_ribbon', strategy_name: 'MA Ribbon' })] }),
      isLoading: false, error: null,
    };
    render(<IntradayBoard />);
    fireEvent.click(screen.getByText('MA Ribbon 1'));
    // The counts are of everything scanned, so they do not move with the filter.
    expect(screen.getByText('All 2')).toBeTruthy();
    fireEvent.click(screen.getByText('MA Ribbon 1'));
    expect(screen.getByText('All 2')).toBeTruthy();
  });

  it('publishes the server warnings rather than hiding them', () => {
    snap = {
      data: snapshot({ warnings: ['auto_execute is ON and none of these three is validated'] }),
      isLoading: false, error: null,
    };
    render(<IntradayBoard />);
    expect(screen.getByText(/auto_execute is ON/)).toBeTruthy();
  });

  it('names the reason a scan failed rather than rendering an empty board', () => {
    snap = {
      data: snapshot({ last_error: 'No active Kite account' }),
      isLoading: false, error: null,
    };
    render(<IntradayBoard />);
    expect(screen.getByText(/No active Kite account/)).toBeTruthy();
  });

  it('offers a square off only while something is held', () => {
    expect(screen.queryByText(/Square off/)).toBeNull();
    snap = {
      data: snapshot({ positions: [position()], open_positions: 1 }),
      isLoading: false, error: null,
    };
    render(<IntradayBoard />);
    fireEvent.click(screen.getByText('Square off 1'));
    expect(squareState.mutate).toHaveBeenCalled();
  });

  it('says why the next entry would be refused, before any click', () => {
    snap = {
      data: snapshot({ entry_blocker: '3 positions open, cap is 3' }),
      isLoading: false, error: null,
    };
    render(<IntradayBoard />);
    expect(screen.getByText(/No new entries: 3 positions open/)).toBeTruthy();
  });

  it('shows paper or live from the account, not from this page', () => {
    snap = {
      data: snapshot({ mode: { is_paper: false, auto_execute: true } }),
      isLoading: false, error: null,
    };
    render(<IntradayBoard />);
    expect(screen.getByText('LIVE')).toBeTruthy();
    expect(screen.getByText('AUTO')).toBeTruthy();
  });

  it('reports the day record once there is one', () => {
    snap = {
      data: snapshot({
        record: { day: '2026-09-11', trades: 3, wins: 2, realised_inr: 1450,
                  consecutive_losses: 0, win_rate: 66.6 },
      }),
      isLoading: false, error: null,
    };
    render(<IntradayBoard />);
    expect(screen.getByText('₹1450')).toBeTruthy();
    expect(screen.getByText('67%')).toBeTruthy();
  });

  it('surfaces an unavailable snapshot instead of a blank pane', () => {
    snap = { data: undefined, isLoading: false, error: new Error('boom') };
    render(<IntradayBoard />);
    expect(screen.getByText(/Unavailable: boom/)).toBeTruthy();
  });
});
