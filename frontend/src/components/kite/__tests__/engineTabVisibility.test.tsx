/**
 * A switched-off engine gets no tab.
 *
 * `EngineTabState.running` used to be documented "a stopped engine still gets a
 * tab — it explains itself", which is a defensible position and the wrong one for
 * this dock: it filled the tab strip with engines the operator had deliberately
 * stopped, and the explanation is already in the switch that stopped them.
 *
 * Two things this must not do, and both are easy to get wrong:
 *  - hide a tab because its config query has not answered yet
 *  - hide SuperTrend's tab when only SuperTrend is off, because that tab is where
 *    NAVIGATOR's rows appear — Navigator has no tab of its own
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, cleanup } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

// A FULL engine config. A minimal `{ engine_enabled }` crashes the pane on the
// first `.length` of a config array it expects to exist — the render throws before
// a single assertion runs, which reads as the test being wrong rather than the
// fixture.
const cfg: Record<string, unknown> = {
  engine_enabled: true, trail_target: 'fast', exit_mode: 'one_red', exit_aligned_trail: false,
  strike_moneyness: ['ATM'], scan_source: 'derivatives',
  scan_expiries: ['weekly', 'monthly'], scan_expiries_indices: null, scan_expiries_stocks: null,
  scan_indices: ['NIFTY 50'], scan_stocks: [], scan_all_stocks: false, auto_execute: false,
  risk_sizing: true, risk_pct: 1.0, max_lots: 10, expiry_square_off_days: 1, time_stop_bars: 0,
  stop_mode: 'both', directional_mode: false, vehicle: 'otm_options',
  enabled_vehicles: ['otm_options', 'deep_itm_options'], itm_depth: 'ITM10', target_delta: null,
  futures_expiry: 'near', adx_min: null, atr_pct_min: null, block_entry_minutes_before_close: 0,
  max_spread_pct: null, min_oi: null, max_daily_loss_pct: null, wire_risk_infra: false,
};

let stEnabled: boolean | undefined = true;
let navEnabled: boolean | undefined = true;
let gmEnabled: boolean | undefined = true;
let aeEnabled: boolean | undefined = true;

vi.mock('../../../hooks/useSterlingKiteEngine', () => ({
  useEngineConfig: () => ({ data: { ...cfg, engine_enabled: stEnabled } }),
  useSetEngineConfig: () => ({ mutate: vi.fn(), isPending: false }),
  usePatchEngineConfig: () => ({ mutate: vi.fn(), isPending: false }),
  useResetEngineConfig: () => ({ mutate: vi.fn(), isPending: false }),
  useEngineSignals: () => ({ data: { generated_ms: 0, scanning: false, scanning_label: '', rows: [], next_scan_ms: 0, auto_scan: false, market_open: true } }),
  useRunScan: () => ({ mutate: vi.fn(), mutateAsync: vi.fn(), isPending: false }),
  useCancelScan: () => ({ mutate: vi.fn(), isPending: false }),
  useStockRegistry: () => ({ data: [] }),
}));
vi.mock('../../../hooks/useNavigator', () => ({
  useNavigatorConfig: () => ({ data: { record: { config: { enabled: navEnabled }, revision: 1 } } }),
  useSetNavigatorConfig: () => ({ mutate: vi.fn(), isPending: false }),
  useRunNavigatorScan: () => ({ mutate: vi.fn(), mutateAsync: vi.fn(), isPending: false }),
  useCancelNavigatorScan: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock('../../../hooks/useKite', () => ({ useKiteQuote: () => ({ data: {} }) }));
vi.mock('../../../hooks/useAdaptiveEdge', () => ({
  useAdaptiveEdgeSnapshot: () => ({ data: null }),
  useAdaptiveEdgeEngineConfig: () => ({ data: { config: { enabled: aeEnabled } } }),
  useSetAdaptiveEdgeEngineConfig: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock('../../../hooks/useGammaMove', () => ({
  useGammaMoveSnapshot: () => ({ data: null }),
  useGammaMoveConfig: () => ({ data: { config: { enabled: gmEnabled } } }),
  useGammaMoveScan: () => ({ mutate: vi.fn(), mutateAsync: vi.fn(), isPending: false }),
  useUpdateGammaMove: () => ({ mutate: vi.fn(), isPending: false }),
}));
vi.mock('../SterlingKiteEngineWithExpiry', () => ({ SterlingKiteEngineWithExpiry: () => <div /> }));

// The SIDEBAR is the shell: it owns the tab strip and renders the active
// engine's pane inside itself. Rendering the pane alone gives you a board with no
// tabs, which is why the first version of this file found none.
import { AdaptiveEdgeRightSidebar } from '../AdaptiveEdgeRightSidebar';

function renderPane() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <AdaptiveEdgeRightSidebar onSelectSignal={vi.fn()} />
    </QueryClientProvider>,
  );
}

const tab = (name: string) => screen.queryAllByRole('tab', { name: new RegExp(name, 'i') });

beforeEach(() => {
  localStorage.clear();
  stEnabled = true; navEnabled = true;
  gmEnabled = true; aeEnabled = true;
});
afterEach(cleanup);

describe('the dock follows the running switches', () => {
  it('shows every engine when all are on', () => {
    renderPane();
    for (const name of ['SuperTrend', 'Adaptive Edge', 'Gamma Move']) {
      expect(tab(name).length, name).toBeGreaterThan(0);
    }
  });

  it('drops Gamma Move when it is switched off', () => {
    gmEnabled = false;
    renderPane();
    expect(tab('Gamma Move')).toHaveLength(0);
    // ...and takes nothing else with it.
    expect(tab('Adaptive Edge').length).toBeGreaterThan(0);
  });

  it('drops Adaptive Edge independently', () => {
    aeEnabled = false;
    renderPane();
    expect(tab('Adaptive Edge')).toHaveLength(0);
    expect(tab('Gamma Move').length).toBeGreaterThan(0);
  });

  it('keeps SuperTrend’s tab when only SuperTrend is off, because Navigator lives there', () => {
    stEnabled = false;
    navEnabled = true;
    renderPane();
    // Navigator has no tab of its own — its rows render in this pane under the
    // signal lens. Hiding this tab would take a running engine's only surface.
    expect(tab('SuperTrend').length).toBeGreaterThan(0);
  });

  it('drops SuperTrend’s tab only when Navigator is off too', () => {
    stEnabled = false;
    navEnabled = false;
    renderPane();
    expect(tab('SuperTrend')).toHaveLength(0);
  });

  it('keeps a tab whose config has not answered yet', () => {
    // `undefined` is "still loading", not "off". Treating it as off would blink
    // every tab out on each page load and look like the operator's own setting.
    gmEnabled = undefined;
    renderPane();
    expect(tab('Gamma Move').length).toBeGreaterThan(0);
  });
});
