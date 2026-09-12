import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';
import { ConnectPane } from '../ConnectPane';
import gammaPayload from '../../__tests__/fixtures/gamma-move.json';

const setConfig = vi.fn();

vi.mock('../../../hooks/useKite', () => ({
  useKiteAccounts: () => ({ data: { accounts: [], count: 0 }, isLoading: false }),
  useAddKiteAccount: () => ({ mutate: vi.fn(), isPending: false, error: null }),
  useActivateKiteAccount: () => ({ mutate: vi.fn() }),
  useDeleteKiteAccount: () => ({ mutate: vi.fn() }),
  useGenerateKiteSession: () => ({ mutate: vi.fn(), isPending: false }),
  useKiteBasketMargins: () => ({ mutate: vi.fn() }),
  useKiteLoginUrl: () => ({ data: undefined }),
  useKiteLogout: () => ({ mutate: vi.fn() }),
  useKiteOrderCharges: () => ({ mutate: vi.fn() }),
  useKiteOrderMargins: () => ({ mutate: vi.fn() }),
  useKiteMargins: () => ({ data: undefined }),
  useKiteStatus: () => ({ data: undefined }),
  useKiteTickerStatus: () => ({ data: undefined }),
  useKiteTickerSubscribe: () => ({ mutate: vi.fn() }),
  useKiteTickerUnsubscribe: () => ({ mutate: vi.fn() }),
  useRefreshKiteSession: () => ({ mutate: vi.fn() }),
  useTestKiteAccount: () => ({ mutate: vi.fn() }),
  useUpdateKiteAccount: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock('../../../hooks/useSterlingKiteEngine', () => ({
  useEngineConfig: () => ({ data: { engine_enabled: true } }),
  useSetEngineConfig: () => ({ mutate: setConfig, isPending: false }),
  usePatchEngineConfig: () => ({ mutate: setConfig, isPending: false }),
  useEngineSignals: () => ({ data: { rows: [] } }),
  // SuperTrend's contract picker moved onto this page, and it reads the live
  // Kite expiry calendar.
  useExpiryCalendar: () => ({ data: undefined, isLoading: false, isError: false, isFetching: false, refetch: vi.fn() }),
  useRunScan: () => ({ mutate: vi.fn(), isPending: false }),
  // The shared InstrumentsGroup reads the curated stock registry. Omitted, the
  // mock throws inside the component and the whole panel renders as an empty
  // div — which reads exactly like "the settings are missing".
  useStockRegistry: () => ({ data: [{ liquidity: 'Very High', names: ['RELIANCE', 'HDFCBANK'] }] }),
}));

// The rail summarises which engines are running, so ConnectPane itself reads the
// Navigator config. Unmocked, that hook has no QueryClient and every test here fails
// before it renders anything.
vi.mock('../../../hooks/useNavigator', () => ({
  useNavigatorConfig: () => ({ data: { record: { config: { enabled: false } } } }),
}));

vi.mock('../TradingModePanel', () => ({ TradingModePanel: () => <div>Trading mode controls</div> }));
vi.mock('../SuperTrendEnginePanel', () => ({ SuperTrendEnginePanel: () => <div>SuperTrend strategy panel</div> }));
vi.mock('../TradeRulesPanels', () => ({
  ManualRulesPanel: () => <div>Manual rules panel</div>,
  AutomaticRulesPanel: () => <div>Automatic rules panel</div>,
}));
vi.mock('../KiteTelegramPanel', () => ({
  KiteTelegramPanel: () => <div>Kite alert destinations</div>,
  BrandIconPicker: () => <div>Icon picker</div>,
}));
vi.mock('../MotionStyleSettings', () => ({ MotionStyleSettings: () => <div>Motion style choices</div> }));
// Experience now carries the signal-board order card, which asks every engine
// whether it is running. This test is about the RAIL, not about engine state.
vi.mock('../../../hooks/useEngineToggles', () => ({
  useEngineEnabled: () => ({
    supertrend: true, navigator: true, gamma_move: true,
    adaptive_edge: true, intraday: true, snapback: true,
  }),
  useEngineToggles: () => [],
}));
vi.mock('../KiteExchangeSettingsCard', () => ({ KiteExchangeSettingsCard: () => <div>Exchange choices</div> }));
vi.mock('../DailyLossLimitPanel', () => ({ DailyLossLimitPanel: () => <div>Daily loss limit</div> }));
vi.mock('../AdaptiveEdgeSettingsPanel', () => ({ AdaptiveEdgeSettingsPanel: () => <div>Adaptive Edge settings panel</div> }));
vi.mock('../OrbMomentumOptionsSettingsPanel', () => ({ OrbMomentumOptionsSettingsPanel: () => <div>ORB options settings panel</div> }));
vi.mock('../../datalake/DataLakeSettingsPanel', () => ({ DataLakeSettingsPanel: () => <div>Offline data settings</div> }));

describe('ConnectPane settings hub', () => {
  beforeEach(() => {
    localStorage.clear();
    setConfig.mockClear();
  });

  it('uses one category rail and gives each settings family one home', () => {
    render(<ConnectPane />);

    // The page title is now the selected section's own name, under a quiet
    // "Settings" eyebrow — so the heading tracks where you are rather than
    // restating the hub on every page.
    expect(screen.getByText('Settings')).toBeInTheDocument();
    expect(screen.getByRole('navigation', { name: 'Kite settings sections' })).toBeInTheDocument();
    expect(screen.queryAllByRole('tab')).toHaveLength(0);
    expect(screen.getByRole('heading', { name: 'Account & Login' })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /SuperTrend\s*Scan, entry & exit/i }));
    expect(screen.getByRole('heading', { name: 'SuperTrend' })).toBeInTheDocument();
    expect(screen.getByText('SuperTrend strategy panel')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Notifications\s*Kite Telegram alerts/i }));
    expect(screen.getByRole('heading', { name: 'Notifications' })).toBeInTheDocument();
    expect(screen.getByText('Kite alert destinations')).toBeInTheDocument();
    expect(screen.queryByText('Icon picker')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Experience\s*Motion & feedback/i }));
    expect(screen.getByRole('heading', { name: 'Experience' })).toBeInTheDocument();
    expect(screen.getByText('Motion style choices')).toBeInTheDocument();
    expect(screen.getByText('Icon picker')).toBeInTheDocument();
  });

  it('groups the rail by what a setting decides', () => {
    render(<ConnectPane />);
    ['Connection', 'Trading', 'Signal engines', 'Platform']
      .forEach((group) => expect(screen.getByText(group)).toBeInTheDocument());
  });

  it('places Adaptive Edge next to Value-Flow Navigator', () => {
    render(<ConnectPane />);
    const navigator = screen.getByRole('button', { name: /Value-Flow Navigator AVWAP, volatility & options flow/i });
    const adaptive = screen.getByRole('button', { name: /Adaptive Edge Score, modes, TBT structure/i });
    expect(navigator.compareDocumentPosition(adaptive) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    fireEvent.click(adaptive);
    expect(screen.getByRole('heading', { name: 'Adaptive Edge' })).toBeInTheDocument();
    expect(screen.getByText('Adaptive Edge settings panel')).toBeInTheDocument();
  });

  it('gives manual and automatic rules separate homes, not one filtered page', () => {
    // The single page with an All / Manual / Automatic filter made the reader
    // decode a per-row badge to know whether a rule applied to them.
    render(<ConnectPane />);

    fireEvent.click(screen.getByRole('button', { name: /Manual Trade\s*Orders you place/i }));
    expect(screen.getByRole('heading', { name: 'Manual Trade' })).toBeInTheDocument();
    expect(screen.getByText('Manual rules panel')).toBeInTheDocument();
    expect(screen.queryByText('Automatic rules panel')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /Algo Trade\s*Orders the algo places/i }));
    expect(screen.getByRole('heading', { name: 'Algo Trade' })).toBeInTheDocument();
    expect(screen.getByText('Automatic rules panel')).toBeInTheDocument();
    expect(screen.queryByText('Manual rules panel')).not.toBeInTheDocument();
  });

  it('has no All/Manual/Automatic filter left anywhere in the rail', () => {
    render(<ConnectPane />);
    expect(screen.queryByRole('button', { name: 'All rules' })).not.toBeInTheDocument();
  });

  it('lifts paper/live and manual/automatic out of the SuperTrend page onto their own', () => {
    // auto_execute is user-global and Navigator reuses the same placement path,
    // so it never belonged behind a page titled "SuperTrend Engine".
    render(<ConnectPane />);
    fireEvent.click(screen.getByRole('button', { name: /Trading (Config|Mode)/i }));
    expect(screen.getByRole('heading', { name: /Trading (Config|Mode)/i })).toBeInTheDocument();
    expect(screen.getByText('Trading mode controls')).toBeInTheDocument();
    expect(screen.getByText('Exchange choices')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /SuperTrend\s*Scan, entry & exit/i }));
    expect(screen.queryByText('Trading mode controls')).not.toBeInTheDocument();
  });

  it('sends the retired shared-market deep link to the engine that now owns its settings', () => {
    localStorage.setItem('kite_connect_section', 'sharedScan');
    render(<ConnectPane />);
    expect(screen.getByRole('heading', { name: 'SuperTrend' })).toBeInTheDocument();
  });

  it('sends the retired order-selection deep link to Automatic Rules, which absorbed it', () => {
    localStorage.setItem('kite_connect_section', 'orderSelection');
    render(<ConnectPane />);
    expect(screen.getByRole('heading', { name: 'Algo Trade' })).toBeInTheDocument();
  });

  it('follows the one-page Trade Rules deep link to the manual half', () => {
    localStorage.setItem('kite_connect_section', 'rules');
    render(<ConnectPane />);
    expect(screen.getByRole('heading', { name: 'Manual Trade' })).toBeInTheDocument();
  });
});

/**
 * ATM Premium Imbalance and Gamma Move, reachable and carrying their contract
 * settings.
 *
 * ORB has had this assertion since its panel turned out to be mounted nowhere —
 * "the whole ORB UI was unreachable from the app" — and neither of these two had
 * the equivalent. Their panels are deliberately NOT mocked here: a stub that
 * renders a placeholder proves the rail wiring and nothing about whether the
 * page a person lands on actually has the controls on it.
 */
describe('ConnectPane — every option engine is reachable and complete', () => {
  // These panels are real, so they need a QueryClient and the config payloads
  // the app serves them. Anything less and the test proves the rail wiring only.
  const PAYLOADS: Record<string, unknown> = {
    '/api/v1/config/gamma-move': gammaPayload,
  };

  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input.toString();
      const key = Object.keys(PAYLOADS).find((k) => url.endsWith(k));
      return {
        ok: true, status: 200,
        json: async () => (key ? PAYLOADS[key] : {}),
        text: async () => JSON.stringify(key ? PAYLOADS[key] : {}),
        headers: new Headers({ 'content-type': 'application/json' }),
      } as Response;
    }));
  });
  afterEach(() => { vi.unstubAllGlobals(); });

  const show = (ui: React.ReactElement) => {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    return render(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
  };

  const ENGINES: Array<[string, RegExp, string]> = [
    ['Gamma Move', /Gamma Move\s*OI unwind at a level/i, 'Gamma Move'],
  ];

  it.each(ENGINES)('gives %s a home in the rail', (_label, railName, heading) => {
    show(<ConnectPane />);
    fireEvent.click(screen.getByRole('button', { name: railName }));
    expect(screen.getByRole('heading', { name: heading })).toBeInTheDocument();
  });

  it.each(ENGINES)('%s carries the shared expiry controls', async (label, railName) => {
    show(<ConnectPane />);
    fireEvent.click(screen.getByRole('button', { name: railName }));
    await waitFor(() => expect(document.querySelectorAll('summary').length).toBeGreaterThan(0));
    const text = document.body.textContent ?? '';
    expect(text, `${label} is missing the DTE window`).toContain('Minimum days to expiry');
    expect(text, `${label} is missing the DTE window`).toContain('Maximum days to expiry');
    expect(text, `${label} is missing the expiry-day control`).toContain('Expiry day');
  });

  it.each(ENGINES)('%s opens its Contracts section', async (label, railName) => {
    show(<ConnectPane />);
    fireEvent.click(screen.getByRole('button', { name: railName }));
    await waitFor(() => expect(document.querySelectorAll('summary').length).toBeGreaterThan(0));
    const summary = [...document.querySelectorAll('summary')]
      .find((el) => /Contracts/.test(el.textContent ?? ''));
    expect(summary, `${label} has no Contracts section`).toBeTruthy();
    expect((summary!.closest('details') as HTMLDetailsElement).open,
           `${label}'s Contracts section is collapsed`).toBe(true);
  });
});
