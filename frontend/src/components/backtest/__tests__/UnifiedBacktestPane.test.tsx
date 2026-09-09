import { describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';
import { UnifiedBacktestPane } from '../UnifiedBacktestPane';

const mockStrategies = [
  { id: 'adaptive_edge', name: 'Adaptive Edge', category: 'Microstructure' },
  { id: 'supertrend', name: 'Triple SuperTrend', category: 'Trend' },
  { id: 'navigator', name: 'Value-Flow Navigator', category: 'Volatility' },
];

const mockPresets = [
  {
    name: 'NIFTY 50 • Adaptive Edge Intraday',
    strategy: 'adaptive_edge',
    symbol: 'NIFTY 50',
    timeframe: '5m',
    lookback_days: 30,
    lot_size: 25,
    num_lots: 2,
    starting_capital: 150000.0,
    stop_points: 40.0,
    target_points: 80.0,
    trail_points: 25.0,
    slippage_points: 0.5,
  },
];

const mockMutate = vi.fn();

vi.mock('../../../hooks/useUnifiedBacktest', () => ({
  useUnifiedStrategies: () => ({ data: mockStrategies, isLoading: false }),
  useUnifiedPresets: () => ({ data: mockPresets, isLoading: false }),
  useRunUnifiedBacktest: () => ({
    mutate: mockMutate,
    isPending: false,
  }),
  useRunAdaptiveEdgeComparison: () => ({
    mutate: vi.fn(),
    isPending: false,
  }),
}));

function renderComponent() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <UnifiedBacktestPane />
    </QueryClientProvider>
  );
}

describe('UnifiedBacktestPane', () => {
  it('renders strategy selector, universe scopes, contracts options, and run button', () => {
    renderComponent();

    expect(screen.getByText(/REAL DATA:/i)).toBeInTheDocument();
    expect(screen.getByText('NIFTY 50 • Adaptive Edge Intraday')).toBeInTheDocument();
    expect(screen.getByText(/Execution & Risk Mode/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Dynamic \(Live\)/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Manual Override/i })).toBeInTheDocument();
    expect(screen.getByText(/⚡ Dynamic Risk Engine Active/i)).toBeInTheDocument();
    expect(screen.getByText(/Instruments & Universe Scope/i)).toBeInTheDocument();
    expect(screen.getByText(/Contracts & Expiry Specs/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Single/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Indices/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Selected F&O/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /All F&O/i })).toBeInTheDocument();
    expect(screen.getAllByRole('button', { name: /Run Backtest/i })[0]).toBeInTheDocument();
  });

  it('allows switching between Instrument scopes: Single, Indices, Selected F&O, and All F&O', () => {
    renderComponent();

    // Click Indices scope
    const indicesBtn = screen.getByRole('button', { name: /Indices/i });
    fireEvent.click(indicesBtn);
    expect(screen.getByText(/Scan Indices/i)).toBeInTheDocument();
    expect(screen.getByText('BANKNIFTY')).toBeInTheDocument();

    // Click Selected F&O scope
    const fnoSelectedBtn = screen.getByRole('button', { name: /Selected F&O/i });
    fireEvent.click(fnoSelectedBtn);
    expect(screen.getByText(/Selected F&O Stocks/i)).toBeInTheDocument();
    expect(screen.getByText('RELIANCE')).toBeInTheDocument();

    // Click All F&O scope
    const fnoAllBtn = screen.getByRole('button', { name: /All F&O/i });
    fireEvent.click(fnoAllBtn);
    expect(screen.getByText(/Full NSE F&O Universe/i)).toBeInTheDocument();
  });

  it('allows selecting contract type and expiry cycle', () => {
    renderComponent();

    expect(screen.getByText(/Contract Type/i)).toBeInTheDocument();
    expect(screen.getByText(/Expiry Cycle/i)).toBeInTheDocument();
  });

  it('clicking run backtest submits configured payload with universe and contract parameters', () => {
    renderComponent();

    const runBtn = screen.getAllByRole('button', { name: /Run Backtest/i })[0];
    fireEvent.click(runBtn);

    expect(mockMutate).toHaveBeenCalledWith(
      expect.objectContaining({
        strategy: 'adaptive_edge',
        contract_type: 'futures',
        expiry_cycle: 'weekly',
        dynamic_mode: true,
      }),
      expect.any(Object)
    );
  });
});
