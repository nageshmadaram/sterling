/**
 * The settings panel.
 *
 * What matters here is provenance. Seven of this engine's defaults were
 * measured, and one of them is set to an unconventional value because the
 * conventional one inverted the gate. A number whose reason lives only in a
 * document gets changed by the next person to open the page, so the panel has
 * to carry the measurement next to the control.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render as rtlRender, screen, fireEvent } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';
import { GammaMoveSettings } from '../GammaMoveSettings';

/** The shared Option contracts picker this page now hosts uses react-query, so
 *  the panel is rendered the way the app renders it rather than bare. */
function render(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return rtlRender(<QueryClientProvider client={qc}>{ui}</QueryClientProvider>);
}

let cfgQuery: any;
let updateState: any;

vi.mock('../../hooks/useGammaMove', () => ({
  useGammaMoveConfig: () => cfgQuery,
  useUpdateGammaMove: () => updateState,
}));

const DEFAULTS = {
  enabled: false,
  scan_indices: [] as string[], scan_stocks: [] as string[],
  scan_all_stocks: true, stock_contracts: true, include_indices: false, max_universe: 150, explicit_symbols: [],
  min_option_oi: 50000, min_option_volume: 1000, min_option_premium: 10,
  max_spread_pct: 3, level_timeframe: 'day', level_lookback_days: 120,
  pivot_lookback: 5, level_cluster_pct: 0.75, min_level_touches: 2,
  level_proximity_pct: 1.0, strike_window_pct: 2, max_candidates: 25,
  expiry_selection: 'nearest', expiry_dte_min: 0, expiry_dte_max: 14,
  avoid_expiry_day: true,
  scan_expiries_indices: ['weekly', 'monthly'], scan_expiries_stocks: ['monthly'],
  scan_weekly_series_indices: [0, 1, 2, 3], scan_monthly_series_indices: [0, 1],
  scan_monthly_series_stocks: [0, 1], trigger_timeframe: '15minute',
  volume_lookback: 20, min_oi_drop_pct: 3, volume_spike_mult: 2.5,
  min_price_gain_pct: 2, confirm_bars: 1, regime_enabled: true,
  regime_timeframe: 'day', regime_period: 10, regime_multiplier: 2,
  stop_basis: 'PERCENT', swing_lookback: 6, stop_percent: 30, stop_points: 0,
  exit_policy: 'TIME_STOP', max_hold_days: 2, target_pct: 0, trail_pct: 0,
  trail_start_pct: 0, close_at_session_end: false, protection_mode: 'NONE',
  session_start: '09:30', session_end: '15:15', scan_interval_seconds: 300,
  sizing_mode: 'RISK_PCT', risk_per_trade_pct: 1, capital_inr: 500000, lots: 0,
  max_concurrent_positions: 3, max_new_trades_per_day: 2,
  max_premium_at_risk_inr: 60000, daily_loss_limit_inr: 10000,
  descale_after_losses: 3, descale_factor: 0.5, rescale_after_wins: 2,
  data_source: 'kite', execution_mode: 'paper',
  require_chain_max_oi: true, require_spot_through_strike: true,
};

const STRATEGY = {
  id: 'gamma_move', name: 'Gamma Move', contract_version: 'A310.2',
  tagline: 'Buys the option that writers are covering at a level.',
  how_it_works: 'Finds F&O stocks at a level…',
  provenance: 'Transcribed from a public podcast walkthrough.',
  live_ready: false, enabled: false,
  calibrated_fields: ['level_proximity_pct', 'min_oi_drop_pct', 'volume_spike_mult',
                      'min_price_gain_pct', 'regime_multiplier', 'stop_percent',
                      'min_option_premium'],
  calibration: {
    sample: '598 contracts / 104 underlyings / 193,135 15m OI bars',
    level_proximity_pct: '1.0 — MFE>=30% 46.2% [31.6,61.4] vs 21.7% baseline',
    regime_multiplier: '2.0 — +5.1pp; multiplier 3.0 measured -3.3pp, i.e. inverted',
    min_oi_drop_pct: '3.0 — the 98.6th percentile',
    volume_spike_mult: '2.5 — the 87th percentile',
    min_price_gain_pct: '2.0 — the 93rd percentile',
    stop_percent: '30.0 — hit by 16% of calibrated signals',
    min_option_premium: '10.0 — below this the tick is a >0.5% quantum',
  },
  headline_finding: 'Only the level filter is proven.',
  what_to_do: 'Trust the distance badge on the row.',
  evidence: 'Share of bars reaching +30% within two sessions: 46.2% [31.6, 61.4].',
  source_gates: {
    require_chain_max_oi: '208/598 sampled contracts were the max-OI of their (name, leg)',
    require_spot_through_strike: '369/598 had spot through/at the strike inside the 1% band',
  },
};

beforeEach(() => {
  cfgQuery = {
    isLoading: false,
    data: {
      strategy: STRATEGY,
      config: { ...DEFAULTS },
      defaults: { ...DEFAULTS },
      vocabularies: { scan_stocks: ['RELIANCE', 'HDFCBANK'],
                      expiry_selection: ['any', 'monthly', 'nearest', 'weekly'] },
      research_only: { exit_policy: ['PERCENT_TARGET', 'TRAILING_STOP'] },
      live_requires: {},
    },
  };
  updateState = { mutate: vi.fn(), isPending: false };
});

describe('GammaMoveSettings', () => {
  it('states the finding before any control', () => {
    render(<GammaMoveSettings />);
    expect(screen.getByText(/Not validated/)).toBeTruthy();
    expect(screen.getByText(/Only the level filter is proven/)).toBeTruthy();
  });

  it('carries the measurement next to each calibrated control', () => {
    render(<GammaMoveSettings />);
    const text = document.body.textContent ?? '';
    expect(text).toContain('46.2%');
    // The trap, spelled out where someone would change it.
    expect(text).toContain('inverted');
  });

  it('warns that widening the proximity band trades away the edge', () => {
    render(<GammaMoveSettings />);
    expect(document.body.textContent).toContain('load-bearing');
  });

  it('marks the unsupported exit policies as unable to run live', () => {
    render(<GammaMoveSettings />);
    // ChoiceRow puts an option's hint in its title, not in the visible text.
    const titles = [...document.querySelectorAll('[title]')]
      .map((el) => el.getAttribute('title') ?? '');
    expect(titles.filter((t) => t.includes('Cannot run live.'))).toHaveLength(2);
  });

  it('does not send anything until the draft is applied', () => {
    render(<GammaMoveSettings />);
    fireEvent.click(screen.getByRole('switch', { name: /gamma move engine/i }));
    expect(updateState.mutate).not.toHaveBeenCalled();
  });

  it('applies the whole draft when asked', () => {
    render(<GammaMoveSettings />);
    fireEvent.click(screen.getByRole('switch', { name: /gamma move engine/i }));
    const apply = screen.getAllByRole('button')
      .find((b) => /apply/i.test(b.textContent ?? ''));
    expect(apply).toBeTruthy();
    fireEvent.click(apply!);
    expect(updateState.mutate).toHaveBeenCalledTimes(1);
    expect(updateState.mutate.mock.calls[0][0].enabled).toBe(true);
  });

  it('shows a loading state rather than an empty form', () => {
    cfgQuery = { isLoading: true, data: undefined };
    render(<GammaMoveSettings />);
    expect(screen.getByText(/Loading strategy settings/)).toBeTruthy();
  });
});


describe('GammaMoveSettings — structure and terminology', () => {
  it('presents Instruments before Contracts, like every other engine', () => {
    render(<GammaMoveSettings />);
    const text = document.body.textContent ?? '';
    const instruments = text.indexOf('Instruments');
    const contracts = text.indexOf('Contracts');
    expect(instruments).toBeGreaterThan(-1);
    expect(contracts).toBeGreaterThan(instruments);
    // "Universe" merged the two questions — what is watched, and which contract
    // the signal is expressed through — into one word.
    expect(text).not.toContain('Universe');
  });

  it('opens the Contracts section rather than hiding it behind a disclosure', () => {
    render(<GammaMoveSettings />);
    const heading = [...document.querySelectorAll('summary')]
      .find((el) => /Contracts/.test(el.textContent ?? ''));
    expect(heading).toBeTruthy();
    expect((heading!.closest('details') as HTMLDetailsElement).open).toBe(true);
  });

  it('uses the shared contract vocabulary, not a private one', () => {
    render(<GammaMoveSettings />);
    const text = document.body.textContent ?? '';
    for (const label of ['Strike range', 'Expiry', 'Minimum days to expiry',
                         'Maximum days to expiry', 'Expiry day']) {
      expect(text).toContain(label);
    }
  });

  it('hosts the shared Option contracts picker', () => {
    render(<GammaMoveSettings />);
    expect(document.body.textContent).toContain('Option contracts');
  });

  it('exposes the two source-rule gates as switches, labelled not calibrated', () => {
    render(<GammaMoveSettings />);
    expect(screen.getByRole('switch', { name: /chain-max/i })).toBeTruthy();
    expect(screen.getByRole('switch', { name: /spot through/i })).toBeTruthy();
    const text = document.body.textContent ?? '';
    expect(text).toContain('Source rule, not calibrated');
    expect(text).toContain('208/598');
    expect(text).toContain('369/598');
  });

  it('treats a missing source flag as on, matching the engine default', () => {
    const { require_chain_max_oi: _wall, require_spot_through_strike: _through, ...rest } = DEFAULTS;
    cfgQuery.data.config = rest;
    render(<GammaMoveSettings />);
    expect(screen.getByRole('switch', { name: /chain-max/i }).getAttribute('aria-checked')).toBe('true');
    expect(screen.getByRole('switch', { name: /spot through/i }).getAttribute('aria-checked')).toBe('true');
  });

  it('drafts an expiry-window change like any other field', () => {
    render(<GammaMoveSettings />);
    fireEvent.click(screen.getByRole('switch', { name: /avoid expiry-day entries/i }));
    const apply = screen.getAllByRole('button')
      .find((b) => /apply/i.test(b.textContent ?? ''));
    fireEvent.click(apply!);
    expect(updateState.mutate).toHaveBeenCalledTimes(1);
    expect(updateState.mutate.mock.calls[0][0].avoid_expiry_day).toBe(false);
  });
});


describe('GammaMoveSettings — a renamed section is a different section', () => {
  /**
   * `Section` persists open/closed under `persistKey`, and a stored choice beats
   * `defaultOpen`. So reusing the key of a section that has been renamed hands
   * the new section the old one's collapsed state — and the reader, who
   * collapsed something else entirely, sees an empty page and reports the
   * settings missing. There is no way for them to tell that from a bug.
   */
  beforeEach(() => localStorage.clear());

  it('does not inherit the collapsed state of the old "Universe" section', () => {
    localStorage.setItem('kite-settings-section:gamma-universe', '0');
    render(<GammaMoveSettings />);
    const summary = [...document.querySelectorAll('summary')]
      .find((el) => /Instruments/.test(el.textContent ?? ''));
    expect(summary).toBeTruthy();
    expect((summary!.closest('details') as HTMLDetailsElement).open,
           'Instruments inherited the old Universe key').toBe(true);
  });

  it('still honours a choice made against its own key', () => {
    localStorage.setItem('kite-settings-section:gamma-instruments', '0');
    render(<GammaMoveSettings />);
    const summary = [...document.querySelectorAll('summary')]
      .find((el) => /Instruments/.test(el.textContent ?? ''));
    expect((summary!.closest('details') as HTMLDetailsElement).open).toBe(false);
  });

  it('opens Contracts even when every other section was collapsed', () => {
    for (const k of ['gamma-universe', 'gamma-instruments', 'gamma-levels',
                     'gamma-trigger', 'gamma-regime', 'gamma-exit', 'gamma-risk']) {
      localStorage.setItem(`kite-settings-section:${k}`, '0');
    }
    render(<GammaMoveSettings />);
    const summary = [...document.querySelectorAll('summary')]
      .find((el) => /Contracts/.test(el.textContent ?? ''));
    expect((summary!.closest('details') as HTMLDetailsElement).open).toBe(true);
  });
});


describe('GammaMoveSettings — the finding reads as guidance, not a paper', () => {
  it('states the claim and what it implies, and shows the evidence in full', () => {
    render(<GammaMoveSettings />);
    const text = document.body.textContent ?? '';
    expect(text).toContain('Only the level filter is proven');
    expect(text).toContain('Trust the distance badge');
    // A settings page is read carefully, so the numbers are shown rather than
    // hidden on a hover as they are on the board.
    expect(text).toContain('46.2%');
  });

  it('no longer claims a paper-only lock that was removed', () => {
    /* Paper/live is the account's setting. A page promising a safeguard the code
       no longer has is worse than one promising none. */
    render(<GammaMoveSettings />);
    expect(document.body.textContent).not.toContain('paper-only');
  });
});
