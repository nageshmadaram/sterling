import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup } from '@testing-library/react';
import React from 'react';
import { AdaptiveEdgeBoard } from '../AdaptiveEdgeBoard';
import type { AdaptiveEdgeSnapshot } from '../../../../types/adaptiveEdge';

let snapshotState: { data: AdaptiveEdgeSnapshot | undefined; isLoading: boolean; error: Error | null } = {
  data: undefined,
  isLoading: false,
  error: null,
};

let mockVersion = 'v2_hardened';
const mockMutate = vi.fn((vars) => {
  if (vars?.strategy_version) {
    mockVersion = vars.strategy_version;
  }
});

vi.mock('../../../../hooks/useAdaptiveEdge', () => ({
  useAdaptiveEdgeSnapshot: () => snapshotState,
  useAdaptiveEdgeEngineConfig: () => ({ data: { config: { strategy_version: mockVersion } } }),
  useSetAdaptiveEdgeEngineConfig: () => ({ mutate: mockMutate, isPending: false }),
}));

function makeSnapshot(): AdaptiveEdgeSnapshot {
  return {
    label: 'TEST_SESSION',
    software_complete: true,
    production_gate_authorized: true,
    meets_a197: true,
    registry_locked: true,
    live_trading: false,
    settings: {
      symbol: 'NIFTY',
      symbols: ['NIFTY', 'BANKNIFTY'],
    } as any,
    readiness: [],
    session: {} as any,
    legs: [],
    signals: [
      {
        id: 'ae-signal-1',
        underlying: 'NIFTY 50',
        tape_symbol: 'NIFTY-I',
        side: 'BUY',
        option_type: 'CE',
        spot_entry: 24500,
        spot_exit: null,
        spot_sl: 24400,
        spot_tsl: 24450,
        entry_time: '2026-08-28T09:15:00+05:30',
        score: 92.0,
        scanned: true,
        flattened: false,
        quantity: 1,
        overlays: [],
        thesis: 'BULLISH',
        entry_mode: 'MICRO',
        scan_origin: 'adaptive_edge',
        legs: [
          {
            moneyness: 'ATM',
            option_type: 'CE',
            option_symbol: 'NIFTY26AUG24500CE',
            strike: 24500,
            expiry: '2026-08-28',
            lot_size: 75,
            token: 1,
            exchange: 'NSE',
            entry_premium: 150,
            stop_premium: 120,
            trail_premium: 130,
            ltp: 155,
            resolution_reason: null,
          },
        ],
      },
      {
        id: 'spot-signal-2',
        underlying: 'BANKNIFTY',
        tape_symbol: 'BANKNIFTY-I',
        side: 'SELL',
        option_type: 'PE',
        spot_entry: 51200,
        spot_exit: null,
        spot_sl: 51350,
        spot_tsl: 51300,
        entry_time: '2026-08-28T09:20:00+05:30',
        score: 85.0,
        scanned: true,
        flattened: false,
        quantity: 1,
        overlays: [],
        thesis: 'BEARISH',
        entry_mode: 'BREAKOUT',
        scan_origin: 'spot_scan',
        legs: [
          {
            moneyness: 'ATM',
            option_type: 'PE',
            option_symbol: 'BANKNIFTY26AUG51200PE',
            strike: 51200,
            expiry: '2026-08-28',
            lot_size: 15,
            token: 2,
            exchange: 'NSE',
            entry_premium: 250,
            stop_premium: 200,
            trail_premium: 220,
            ltp: 260,
            resolution_reason: null,
          },
        ],
      },
    ],
  } as unknown as AdaptiveEdgeSnapshot;
}

describe('AdaptiveEdgeBoard — Spot / AE source toggles', () => {
  beforeEach(() => {
    localStorage.clear();
    snapshotState = {
      data: makeSnapshot(),
      isLoading: false,
      error: null,
    };
  });

  afterEach(() => {
    cleanup();
  });

  it('renders the source toggle group with counts for Both, AE Model, and Spot Scan', () => {
    render(<AdaptiveEdgeBoard />);

    const group = screen.getByTestId('ae-board-source-toggle-group');
    expect(group).toBeInTheDocument();

    const bothBtn = screen.getByTestId('ae-board-source-all');
    const aeBtn = screen.getByTestId('ae-board-source-ae');
    const spotBtn = screen.getByTestId('ae-board-source-spot');

    expect(bothBtn).toHaveTextContent('Both');
    expect(bothBtn).toHaveTextContent('2');
    expect(aeBtn).toHaveTextContent('AE Model');
    expect(aeBtn).toHaveTextContent('1');
    expect(spotBtn).toHaveTextContent('Spot Scan');
    expect(spotBtn).toHaveTextContent('1');

    expect(bothBtn.getAttribute('data-active')).toBe('true');
  });

  it('filters signals when switching between AE Model and Spot Scan', () => {
    render(<AdaptiveEdgeBoard />);

    // Initially both rows are visible
    expect(screen.getByText('NIFTY')).toBeInTheDocument();
    expect(screen.getByText('BANKNIFTY')).toBeInTheDocument();

    // Click AE Model
    const aeBtn = screen.getByTestId('ae-board-source-ae');
    fireEvent.click(aeBtn);

    expect(aeBtn.getAttribute('data-active')).toBe('true');
    expect(screen.getByText('NIFTY')).toBeInTheDocument();
    expect(screen.queryByText('BANKNIFTY')).not.toBeInTheDocument();

    // Click Spot Scan
    const spotBtn = screen.getByTestId('ae-board-source-spot');
    fireEvent.click(spotBtn);

    expect(spotBtn.getAttribute('data-active')).toBe('true');
    expect(screen.queryByText('NIFTY 50')).not.toBeInTheDocument();
    expect(screen.getByText('BANKNIFTY')).toBeInTheDocument();

    // Click Both
    const bothBtn = screen.getByTestId('ae-board-source-all');
    fireEvent.click(bothBtn);

    expect(bothBtn.getAttribute('data-active')).toBe('true');
    expect(screen.getByText('NIFTY')).toBeInTheDocument();
    expect(screen.getByText('BANKNIFTY')).toBeInTheDocument();
  });

  it('toggles back to Both when clicking the currently active source filter', () => {
    render(<AdaptiveEdgeBoard />);

    const aeBtn = screen.getByTestId('ae-board-source-ae');
    fireEvent.click(aeBtn);
    expect(aeBtn.getAttribute('data-active')).toBe('true');
    expect(screen.queryByText('BANKNIFTY')).not.toBeInTheDocument();

    // Click AE Model again to toggle it off
    fireEvent.click(aeBtn);
    const bothBtn = screen.getByTestId('ae-board-source-all');
    expect(bothBtn.getAttribute('data-active')).toBe('true');
    expect(screen.getByText('BANKNIFTY')).toBeInTheDocument();
  });

  it('respects controlled sourceFilter prop and calls onSourceFilterChange', () => {
    const onFilterChange = vi.fn();
    const { rerender } = render(
      <AdaptiveEdgeBoard sourceFilter="spot" onSourceFilterChange={onFilterChange} />
    );

    const spotBtn = screen.getByTestId('ae-board-source-spot');
    expect(spotBtn.getAttribute('data-active')).toBe('true');
    expect(screen.queryByText('NIFTY')).not.toBeInTheDocument();
    expect(screen.getByText('BANKNIFTY')).toBeInTheDocument();

    const aeBtn = screen.getByTestId('ae-board-source-ae');
    fireEvent.click(aeBtn);
    expect(onFilterChange).toHaveBeenCalledWith('ae');

    rerender(
      <AdaptiveEdgeBoard sourceFilter="ae" onSourceFilterChange={onFilterChange} />
    );
    expect(screen.getByText('NIFTY')).toBeInTheDocument();
    expect(screen.queryByText('BANKNIFTY')).not.toBeInTheDocument();
  });

  it('renders engine version toggle and switches version on click', () => {
    mockVersion = 'v2_hardened';
    render(<AdaptiveEdgeBoard />);

    const versionBtn = screen.getByTestId('ae-board-version-toggle');
    expect(versionBtn).toBeInTheDocument();
    expect(versionBtn).toHaveTextContent('V2 HARDENED');

    fireEvent.click(versionBtn);
    expect(mockMutate).toHaveBeenCalledWith({ strategy_version: 'v1_baseline' });
  });
});
