/**
 * The scan control, the funnel readout, and what the board says when empty.
 *
 * The load-bearing assertion here is the last one: this engine's calibration
 * found its entry trigger has no edge on its own, and that has to be visible
 * above the rows rather than only in a document. A board that renders a
 * confident-looking signal without it is the failure mode.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import React from 'react';
import { GammaMoveBoard } from '../GammaMoveBoard';

let snap: any = { data: undefined, isLoading: false, error: null };
let scanState: any = { mutate: vi.fn(), isPending: false, error: null, data: undefined };
let armState: any = { mutate: vi.fn(), isPending: false, error: null, data: undefined };
let snapshotArgs: unknown[] = [];

vi.mock('../../../../hooks/useGammaMove', () => ({
  useGammaMoveSnapshot: (...args: unknown[]) => { snapshotArgs = args; return snap; },
  useGammaMoveScan: () => scanState,
  useArmGammaMove: () => armState,
}));

const CONFIG = {
  enabled: true, execution_mode: 'paper', level_proximity_pct: 1.0,
  min_oi_drop_pct: 3.0, volume_spike_mult: 2.5, min_price_gain_pct: 2.0,
  volume_lookback: 20, level_timeframe: 'day',
};

const STRATEGY = {
  id: 'gamma_move', name: 'Gamma Move', live_ready: false,
  headline_finding: 'Only the level filter is proven.',
  what_to_do: 'Trust the distance badge on the row.',
  evidence: 'Reaching +30% within two sessions: 46.2% [31.6, 61.4] within 1% of a level.',
  calibration: {}, calibrated_fields: [],
};

function row(over: any = {}) {
  return {
    id: 'RELIANCE26SEP1300CE@resistance:1300', state: 'armed', at_ms: 1789009200000,
    underlying: 'RELIANCE', regime: 'up', reason: null, exit_reason: null, entry_day: null,
    instrument: { instrument_id: '1', tradingsymbol: 'RELIANCE26SEP1300CE',
                  option_type: 'CE', strike: 1300, expiry: '2026-09-29',
                  lot_size: 500, tick_size: 0.05, exchange: 'NFO' },
    level: { price: 1300, kind: 'resistance', touches: 3, distance_pct: 0.15 },
    oi: 6000000, days_to_expiry: 9, spot: 1298,
    metrics: { oi_drop_pct: 4, volume_ratio: 5, price_gain_pct: 6, unwinding: true,
               abnormal: true, rising: true, bars_confirmed: 1, bars_required: 1,
               triggered: true },
    levels: { ltp: 53, entry: 53, stop: 45, trail: null, target: null, exit: null },
    sizing: { lots: 1, quantity: 500, at_risk_inr: 4000, deployed_inr: 26500 },
    ...over,
  };
}

function data(over: any = {}) {
  return {
    strategy: STRATEGY, config: CONFIG, scan: {}, session: null, simulation: null,
    candidates: [], positions: [], orphan_positions: [], blockers: [],
    record: { trades: 0, wins: 0, losses: 0, win_rate: null, consecutive_losses: 0,
              consecutive_wins: 0, realised_inr: 0, day_realised_inr: 0, day: '',
              verdict: 'no realised trades yet' },
    ...over,
  };
}

beforeEach(() => {
  snap = { data: data(), isLoading: false, error: null };
  scanState = { mutate: vi.fn(), isPending: false, error: null, data: undefined };
  armState = { mutate: vi.fn(), isPending: false, error: null, data: undefined };
});

describe('GammaMoveBoard', () => {
  it('leads with what the finding means and what to do about it', () => {
    render(<GammaMoveBoard nowMs={Date.now()} />);
    expect(screen.getByText('NOT VALIDATED')).toBeTruthy();
    expect(screen.getByText(/Only the level filter is proven/)).toBeTruthy();
    expect(screen.getByText(/Trust the distance badge/)).toBeTruthy();
  });

  it('keeps the statistics off the banner and on a hover', () => {
    /* This was a line of confidence intervals across the top of a trading
       screen. The evidence is still one gesture away, but it is no longer the
       first thing shouted at someone deciding whether to click Buy. */
    render(<GammaMoveBoard nowMs={Date.now()} />);
    expect(screen.queryByText(/46\.2%/)).toBeNull();
    const banner = screen.getByText('NOT VALIDATED').closest('[title]');
    expect(banner).toBeTruthy();
    expect(banner!.getAttribute('title')).toContain('46.2%');
  });

  it('offers a scan and runs it', () => {
    render(<GammaMoveBoard nowMs={Date.now()} />);
    fireEvent.click(screen.getByRole('button', { name: /scan now/i }));
    expect(scanState.mutate).toHaveBeenCalled();
  });

  it('disables live scan while replay is driving the board', () => {
    snap.data = data({ simulation: { mode: 'replay' },
                       blockers: ['replay has no 15-minute option open-interest tape — the trigger cannot fire'] });
    render(<GammaMoveBoard nowMs={Date.now()} />);
    expect(screen.getByRole('button', { name: /scan now/i })).toBeDisabled();
    expect(screen.getByText(/open-interest tape/)).toBeTruthy();
    expect(document.body.textContent).toContain('replay');
  });

  it('does not treat a leftover contract-sim blob as market replay', () => {
    snap.data = data({ simulation: { simulation: true, status: 'done' } });
    render(<GammaMoveBoard nowMs={Date.now()} />);
    expect(screen.getByRole('button', { name: /scan now/i })).not.toBeDisabled();
  });

  it('reads out every blocker rather than sitting silent', () => {
    snap.data = data({ blockers: ['strategy disabled', 'paper mode — no live orders'] });
    render(<GammaMoveBoard nowMs={Date.now()} />);
    expect(screen.getByText('strategy disabled')).toBeTruthy();
    expect(screen.getByText(/paper mode/)).toBeTruthy();
  });

  it('shows the funnel cost stage by stage', () => {
    snap.data = data({
      scan: { stage_a: { scanned: 150, near_level: 19, seconds: 40 },
              stage_b: { candidates: 14, seconds: 3 },
              stage_c: { watched: 14, armed: 1, historical_requests: 14, seconds: 5 },
              total_seconds: 48 },
    });
    render(<GammaMoveBoard nowMs={Date.now()} />);
    expect(screen.getByText('150')).toBeTruthy();
    expect(screen.getByText(/14 history calls/)).toBeTruthy();
  });

  it('does not poll while nothing is open', () => {
    render(<GammaMoveBoard nowMs={Date.now()} />);
    expect(snapshotArgs[1]).toBe(0);
  });

  it('renders an armed row', () => {
    snap.data = data({ candidates: [row()] });
    render(<GammaMoveBoard nowMs={Date.now()} />);
    expect(screen.getByText(/1 armed/)).toBeTruthy();
    // The shared board renders the underlying and the contract in its own
    // layout; assert the row exists rather than pinning its internal markup.
    expect(document.body.textContent).toContain('RELIANCE');
    expect(document.body.textContent).toContain('1300');
  });

  it('surfaces a refused entry instead of failing quietly', () => {
    armState.data = { ok: false, message: 'daily trade limit of 2 reached' };
    render(<GammaMoveBoard nowMs={Date.now()} />);
    expect(screen.getByText(/daily trade limit of 2 reached/)).toBeTruthy();
  });

  it('warns while size is de-scaled', () => {
    snap.data = data({
      record: { trades: 5, wins: 1, losses: 4, win_rate: 20, consecutive_losses: 3,
                consecutive_wins: 0, realised_inr: -8000, day_realised_inr: -8000,
                day: '2026-09-20', verdict: '1/5 winners' },
    });
    render(<GammaMoveBoard nowMs={Date.now()} />);
    expect(screen.getByText(/3 consecutive losses/)).toBeTruthy();
  });

  it('reports an error rather than an empty board', () => {
    snap = { data: undefined, isLoading: false, error: new Error('kite is down') };
    render(<GammaMoveBoard nowMs={Date.now()} />);
    expect(screen.getByText(/kite is down/)).toBeTruthy();
  });
});
