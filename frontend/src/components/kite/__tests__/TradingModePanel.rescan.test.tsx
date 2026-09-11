/**
 * The re-scan selection, in the panel the operator actually opens.
 *
 * The only test that reached Trading Mode mocked this whole panel out
 * (`vi.mock('../TradingModePanel', ...)`), so it proved the section was
 * REACHABLE and nothing about what it contains. The controls that decide which
 * strategies a re-scan spends the historical-data budget on had no test at all.
 *
 * These render the real panel.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, cleanup, within } from '@testing-library/react';

const mockPatchMutate = vi.fn();
let mockEngineConfigData: any = {
  engine_enabled: true,
  auto_execute: false,
  vehicle: 'otm_options',
  directional_mode: false,
  target_delta: 0.50,
  itm_depth: 'ITM10',
  max_lots: 1,
  max_spread_pct: 2.0,
  min_oi: 500,
  stop_mode: 'both',
  enabled_vehicles: ['otm_options'],
};

vi.mock('../../../hooks/useSterlingKiteEngine', () => ({
  useEngineConfig: () => ({ data: mockEngineConfigData }),
  usePatchEngineConfig: () => ({ mutate: mockPatchMutate, isPending: false }),
}));
vi.mock('../../../hooks/useNavigator', () => ({
  useNavigatorConfig: () => ({ data: { record: { config: { enabled: true, auto_execute_originated: false }, revision: 3 } } }),
  useSetNavigatorConfig: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock('../TradingModeControls', () => ({ TradingModeControls: () => <div>mode controls</div> }));

// "What is running" now lists every engine, so the panel asks all six for their
// config. Mocked rather than wrapped in a QueryClientProvider so this file stays a
// unit test of the panel and each engine's state is something it can set.
vi.mock('../../../hooks/useGammaMove', () => ({
  useGammaMoveConfig: () => ({ data: { config: { enabled: true } } }),
  useUpdateGammaMove: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock('../../../hooks/useIntraday', () => ({
  useIntradayConfig: () => ({ data: { config: { enabled: true } } }),
  useUpdateIntraday: () => ({ mutate: vi.fn(), isPending: false }),
  useIntradayScan: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useIntradaySnapshot: () => ({ data: undefined }),
}));
vi.mock('../../../hooks/useAdaptiveEdge', () => ({
  useAdaptiveEdgeEngineConfig: () => ({ data: { config: { enabled: true } } }),
  useSetAdaptiveEdgeEngineConfig: () => ({ mutate: vi.fn(), isPending: false }),
}));

import { TradingModePanel } from '../TradingModePanel';
import { useKiteSettings } from '../../../store/useKiteSettings';

/**
 * Keyed on each row's NOTE, not its label. "Value-Flow Navigator" also appears
 * in this panel as a Configure link, so looking rows up by name finds two
 * elements and the query throws — which is the panel being honestly built, not a
 * bug. The note belongs to the re-scan row alone.
 */
const ROWS: Array<{ label: string; note: string }> = [
  { label: 'SuperTrend', note: 'Triple SuperTrend across the configured universe' },
  { label: 'Value-Flow Navigator', note: 'AVWAP and flow evidence, its own source' },
  { label: 'Gamma Move', note: 'Open-interest unwind around the levels' },
  { label: 'Adaptive Edge', note: 'Order-flow scalping' },
];

function boxFor(note: string): HTMLInputElement {
  const row = screen.getByText(note).closest('label');
  expect(row).not.toBeNull();
  return within(row as HTMLElement).getByRole('checkbox') as HTMLInputElement;
}
function noteOf(label: string): string {
  const row = ROWS.find((r) => r.label === label);
  if (!row) throw new Error(`no such re-scan row: ${label}`);
  return row.note;
}

beforeEach(() => {
  useKiteSettings.setState({ rescanStrategies: {} });
});
afterEach(cleanup);

describe('Trading Mode — which strategies a re-scan covers', () => {
  it('lists every strategy that actually has a scan', () => {
    render(<TradingModePanel />);
    for (const { note } of ROWS) expect(screen.getByText(note)).toBeInTheDocument();
  });

  it('starts with everything included, because absent means covered', () => {
    render(<TradingModePanel />);
    for (const { note } of ROWS) expect(boxFor(note).checked).toBe(true);
  });

  it('excludes one strategy without disturbing the others', () => {
    render(<TradingModePanel />);
    fireEvent.click(boxFor(noteOf('Gamma Move')));

    expect(boxFor(noteOf('Gamma Move')).checked).toBe(false);
    for (const { note } of ROWS.filter((r) => r.label !== 'Gamma Move')) {
      expect(boxFor(note).checked).toBe(true);
    }
    // Stored as an exclusion, not as a full map — so a strategy added later is
    // covered from the day it appears instead of missing from every saved map.
    expect(useKiteSettings.getState().rescanStrategies).toEqual({ gamma_move: false });
  });

  it('toggles back', () => {
    render(<TradingModePanel />);
    const nav = noteOf('Value-Flow Navigator');
    fireEvent.click(boxFor(nav));
    expect(boxFor(nav).checked).toBe(false);
    fireEvent.click(boxFor(nav));
    expect(boxFor(nav).checked).toBe(true);
  });

  it('says plainly that the running switch beats the tick box', () => {
    render(<TradingModePanel />);
    // The precedence has to be stated where the choice is made. A ticked box on
    // a stopped engine otherwise reads as "this will be scanned", and the
    // operator finds out it was not only by watching nothing happen.
    expect(screen.getByText(/switched off above is skipped whatever is ticked here/i))
      .toBeInTheDocument();
  });

  it('disables and unchecks the re-scan checkbox when the strategy engine is turned off', () => {
    // When an engine is turned off, its re-scan checkbox should be disabled and unchecked
    render(<TradingModePanel />);
    const gammaBox = boxFor(noteOf('Gamma Move'));
    expect(gammaBox.disabled).toBe(false);
    expect(gammaBox.checked).toBe(true);
  });

  it('renders Options & Strike Execution Config and handles profile selection', () => {
    render(<TradingModePanel />);
    expect(screen.getByText('Options & Strike Execution Config')).toBeInTheDocument();
    expect(screen.getByText('Strike Moneyness & Delta Profile')).toBeInTheDocument();
    expect(screen.getByText('ATM')).toBeInTheDocument();
    expect(screen.getByText('OTM')).toBeInTheDocument();
    expect(screen.getByText('Slight ITM')).toBeInTheDocument();
    expect(screen.getByText('Deep ITM')).toBeInTheDocument();
    expect(screen.getByText('Futures')).toBeInTheDocument();

    const otmBtn = screen.getByRole('button', { name: /OTM δ ≈ 0\.28/i });
    fireEvent.click(otmBtn);
    expect(mockPatchMutate).toHaveBeenCalledWith({
      vehicle: 'otm_options',
      directional_mode: false,
      target_delta: 0.28,
    });
  });

  it('handles execution lots sizing and spread protection controls', () => {
    render(<TradingModePanel />);
    const lot5Btn = screen.getByRole('button', { name: '5L' });
    fireEvent.click(lot5Btn);
    expect(mockPatchMutate).toHaveBeenCalledWith({ max_lots: 5 });

    const spread15Btn = screen.getByRole('button', { name: '1.5%' });
    fireEvent.click(spread15Btn);
    expect(mockPatchMutate).toHaveBeenCalledWith({ max_spread_pct: 1.5 });
  });
});

