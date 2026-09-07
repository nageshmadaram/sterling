import React from 'react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import type { OrbFeedEntry } from '../../../utils/niftyOrbSignalAdapter';

const state = vi.hoisted(() => ({
  rows: [] as OrbFeedEntry[],
  loading: false,
  error: null as unknown,
  enabled: true as boolean | undefined,
  configLoading: false,
  setEnabled: vi.fn(),
  pending: false,
  setError: null as unknown,
}));

vi.mock('../../../hooks/useOrbSignals', () => ({
  useOrbSignals: () => ({ signals: state.rows, isLoading: state.loading, error: state.error }),
}));
vi.mock('../../../hooks/useOrbConfig', () => ({
  useOrbConfig: () => ({ data: { config: { enabled: state.enabled } }, isLoading: state.configLoading }),
  useSetOrbEnabled: () => ({ mutate: state.setEnabled, isPending: state.pending, error: state.setError }),
}));
const engineState = vi.hoisted(() => ({ auto: false }));
vi.mock('../../../hooks/useSterlingKiteEngine', () => ({
  useEngineConfig: () => ({ data: { auto_execute: engineState.auto } }),
}));
const openSection = vi.hoisted(() => vi.fn());
vi.mock('../config/registry', () => ({ openSettingsSection: openSection }));
// The expansion pulls a live book and mounts the shared calculator; neither is
// under test here, and both would drag the whole Kite query surface in.
vi.mock('../../../hooks/useKite', () => ({ useKiteQuote: () => ({ data: {} }) }));
vi.mock('../AdaptiveEdgePositionCalculator', () => ({
  AdaptiveEdgePositionCalculator: (p: Record<string, unknown>) => (
    <div data-testid="sizing">{`sizing ${p.tradingsymbol} @ ${p.defaultEntryPrice} sl ${p.defaultSl} on ${p.exchange} lots ${p.defaultLots} hideTsl ${p.hideTsl}`}</div>
  ),
}));

import { NiftyOrbSignalsFeed } from '../NiftyOrbSignalsFeed';
import { useOrderWindowStore } from '../../../store/useOrderWindowStore';

const entry = (over: Partial<OrbFeedEntry> = {}): OrbFeedEntry => ({
  id: over.underlying ?? 'r1', strategy: 'ORB', underlying: 'NIFTY', direction: 'long', state: 'SIGNAL',
  spot: 24050, orbHigh: 24012, orbLow: 23988, vwap: 24000, atr: 8, volumeRatio: 1.8,
  optionSymbol: 'NIFTY26AUG24000CE', optionStrike: 24000, optionType: 'CE', optionExpiry: '2026-08-27',
  optionPremium: 18, stopPremium: 14, targetPremium: 26, quantity: 150, riskInr: 187.5,
  maxLossInr: 2700, dataSource: 'kite', quoteAgeS: 3.2, reason: 'ORB high break + VWAP + momentum',
  delta: 0.577, gamma: 0.00088, thetaPerDay: -10.0, vegaPerPoint: 16.9, exchange: 'NFO', lotSize: 75,
  underlyingEntry: 24050, underlyingStop: 24040,
  timestamp: '2026-08-21T10:30:00+05:30', deltaIsEstimated: true, deltaSource: 'implied', impliedVol: 0.224,
  vwapBasis: 'volume', volumeConfirmed: true,
  ticketFingerprint: 'LONG|2026-08-21T10:30:00+05:30|NIFTY26AUG24000CE|CE|24000|2026-08-27|150|14|26',
  autoBlock: null,
  ...over,
});

function show(over: Partial<typeof state> = {}) {
  Object.assign(state, { rows: [], loading: false, error: null, enabled: true, configLoading: false, pending: false, setError: null }, over);
  return render(<NiftyOrbSignalsFeed />);
}

beforeEach(() => { state.setEnabled.mockClear(); openSection.mockClear(); engineState.auto = false; });

describe('ORB feed — engine off', () => {
  it('says the engine is off rather than showing an empty list', () => {
    show({ enabled: false });
    expect(screen.getByText('ORB + VWAP is off')).toBeInTheDocument();
    expect(screen.getByText('NOT SCANNING')).toBeInTheDocument();
  });

  it('carries the switch, so the fix is one click away', () => {
    show({ enabled: false });
    fireEvent.click(screen.getByRole('button', { name: /Turn on ORB \+ VWAP/i }));
    expect(state.setEnabled).toHaveBeenCalledWith(true);
  });

  it('shows progress while the toggle is in flight', () => {
    show({ enabled: false, pending: true });
    expect(screen.getByRole('button', { name: /Turning on…/ })).toBeDisabled();
  });

  it('reports a failed enable beside the control', () => {
    show({ enabled: false, setError: new Error('database unavailable') });
    expect(screen.getByText(/Could not turn it on: database unavailable/)).toBeInTheDocument();
  });

  it('offers a route to the settings section', () => {
    show({ enabled: false });
    fireEvent.click(screen.getByRole('button', { name: /ORB settings/i }));
    expect(openSection).toHaveBeenCalledWith('orbOptions');
  });

  it('distinguishes "on but no underlyings" from "off"', () => {
    show({ enabled: true, rows: [] });
    expect(screen.getByText('ORB universe is off')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Choose underlyings/i })).toBeInTheDocument();
    // Nothing to turn on here — the engine is already running.
    expect(screen.queryByRole('button', { name: /Turn on/i })).not.toBeInTheDocument();
  });
});

describe('ORB feed — tradable setups', () => {
  it('counts tradable setups against everything scanned', () => {
    show({ rows: [entry({ underlying: 'NIFTY' }), entry({ underlying: 'SBIN', state: 'WATCHING', reason: 'regime is RANGE' })] });
    expect(screen.getByText(/tradable · 2 scanned/)).toBeInTheDocument();
  });

  it('carries the whole ticket on the row', () => {
    show({ rows: [entry()] });
    // A standalone row (no group) still names its contract in full.
    expect(document.querySelector('.sb-row')?.textContent).toMatch(/24000/);
    expect(screen.getByText('CE · LONG')).toBeInTheDocument();
    expect(screen.getByText('NFO')).toBeInTheDocument();
    ['LTP', 'Entry ₹', 'SL', 'Target', 'Qty', 'At risk'].forEach((label) => {
      expect(screen.getByText(label), label).toBeInTheDocument();
    });
    expect(screen.queryByText('Entry (Δpts)')).not.toBeInTheDocument();
    expect(screen.queryByText('TSL')).not.toBeInTheDocument();
    expect(screen.queryByText('Leg (Δ)')).not.toBeInTheDocument();
  });

  it('marks a stale quote on the row without a SuperTrend time column', () => {
    show({ rows: [entry({ quoteAgeS: 42 })] });
    expect(screen.getByText('STALE')).toBeInTheDocument();
  });

  it('keeps candidates that did not fire out of the way', () => {
    // The board is a call to action; reasons are available but not in the path.
    show({ rows: [entry({ underlying: 'NIFTY' }), entry({ underlying: 'SBIN', state: 'WATCHING', reason: 'regime is RANGE' })] });
    expect(screen.queryByText('regime is RANGE')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /1 not signalling/ }));
    expect(screen.getByText('regime is RANGE')).toBeInTheDocument();
  });

  it('says so plainly when nothing is tradable', () => {
    show({ rows: [entry({ state: 'WATCHING', reason: 'outside entry window' })] });
    expect(screen.getByText('Waiting for entry window')).toBeInTheDocument();
    expect(screen.getByText(/09:30–12:00 IST/)).toBeInTheDocument();
    expect(screen.queryByText(/No tradable ORB setup right now/)).not.toBeInTheDocument();
  });

  it('promotes an errored underlying to the board instead of burying it', () => {
    // An underlying that could not be evaluated is a call to action; only rows
    // that were evaluated and simply had no setup belong in the disclosure.
    show({ rows: [entry({ underlying: 'NIFTY', state: 'WATCHING', reason: 'regime is RANGE' }), entry({ underlying: 'SBIN', state: 'ERROR', reason: 'no instrument' })] });
    expect(screen.getByRole('button', { name: /1 not signalling/ })).toBeInTheDocument();
    // The count sits in its own <b>, so match on the assembled line.
    expect(screen.getByText((_t, el) => /^0 tradable · 1 blocked · 2 scanned$/
      .test((el?.textContent ?? '').replace(/\s+/g, ' ').trim()))).toBeTruthy();
  });

  it('does not dump every underlying as a table when they share one gate', () => {
    // Auto-expanded identical "outside entry window" rows looked like a broken
    // SuperTrend table. The card states the gate; names stay collapsed.
    show({
      rows: [
        entry({ underlying: 'NIFTY', state: 'WATCHING', reason: 'outside entry window' }),
        entry({ underlying: 'SBIN', id: 'SBIN', state: 'WATCHING', reason: 'outside entry window' }),
      ],
    });
    expect(screen.getByText('Waiting for entry window')).toBeInTheDocument();
    expect(screen.queryByText('NIFTY')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /outside entry window/ }));
    expect(screen.getByText('NIFTY')).toBeInTheDocument();
    expect(screen.getByText('SBIN')).toBeInTheDocument();
  });

  it('calls out a scan that failed for everything', () => {
    show({ rows: [entry({ state: 'ERROR', reason: "'str' object has no attribute 'zerodha_token'" })] });
    expect(screen.getByText(/Scan failed for all 1 underlyings/)).toBeInTheDocument();
  });
});

describe('ORB feed — Manual / Auto is one ticket', () => {
  it('labels MANUAL and says the user Buys the ticket Auto would place', () => {
    engineState.auto = false;
    show({ rows: [entry()] });
    expect(screen.getByText('MANUAL')).toBeInTheDocument();
    expect(screen.getByText(/Same ticket Auto would place/)).toBeInTheDocument();
  });

  it('labels AUTO as placing the same ticket, not a second strategy', () => {
    engineState.auto = true;
    show({ rows: [entry()] });
    expect(screen.getByText('AUTO')).toBeInTheDocument();
    expect(screen.getByText(/same ticket shown below/i)).toBeInTheDocument();
  });

  it('surfaces an Auto refusal on a Manual row', () => {
    show({ rows: [entry({ autoBlock: 'daily trade limit reached', reason: 'daily trade limit reached' })] });
    expect(screen.getByText('AUTO BLOCK')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /NIFTY CE Armed/ }));
    expect(screen.getByText('daily trade limit reached')).toBeInTheDocument();
  });
});

describe('ORB feed — expanded setup', () => {
  it('keeps the detail collapsed until asked', () => {
    show({ rows: [entry()] });
    expect(screen.queryByTestId('sizing')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: /NIFTY CE Armed/ })).toHaveAttribute('aria-expanded', 'false');
  });

  it('hands the plan to the shared position calculator, which owns the Buy path', () => {
    show({ rows: [entry()] });
    fireEvent.click(screen.getByRole('button', { name: /NIFTY CE Armed/ }));
    expect(screen.getByTestId('sizing')).toHaveTextContent('sizing NIFTY26AUG24000CE @ 18 sl 14 on NFO lots 2 hideTsl true');
  });

  it('row Buy opens the scan ticket, not a blank 1-lot window', () => {
    useOrderWindowStore.setState({ isOpen: false, options: null });
    show({ rows: [entry()] });
    fireEvent.click(screen.getByTitle('Buy'));
    const opts = useOrderWindowStore.getState().options;
    expect(opts?.symbol).toBe('NIFTY26AUG24000CE');
    expect(opts?.initialSide).toBe('BUY');
    expect(opts?.initialQty).toBe(150);
    expect(opts?.tag).toBe('ORB');
    expect(opts?.initialSlPct).toBeCloseTo(((14 - 18) / 18) * 100, 1);
    expect(opts?.initialTgtPct).toBeCloseTo(((26 - 18) / 18) * 100, 1);
  });

  it('shows a Buy ticket without a Sell-to-open', () => {
    show({ rows: [entry()] });
    expect(screen.getByTitle('Buy')).toBeInTheDocument();
    expect(screen.queryByTitle('Sell')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /NIFTY CE Armed/ }));
    expect(screen.getByRole('button', { name: 'BUY' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'SELL' })).not.toBeInTheDocument();
  });

  it('keeps ORB’s own evidence alongside the shared ticket', () => {
    show({ rows: [entry()] });
    fireEvent.click(screen.getByRole('button', { name: /NIFTY CE Armed/ }));
    expect(screen.getByText(/opening range & vwap/i)).toBeInTheDocument();
    expect(screen.getByText('ORB high')).toBeInTheDocument();
  });

  it('opens one setup at a time', () => {
    show({ rows: [entry({ underlying: 'NIFTY' }), entry({ underlying: 'SBIN' })] });
    fireEvent.click(screen.getByRole('button', { name: /NIFTY CE Armed/ }));
    fireEvent.click(screen.getByRole('button', { name: /SBIN CE Armed/ }));
    expect(screen.getByRole('button', { name: /NIFTY CE Armed/ })).toHaveAttribute('aria-expanded', 'false');
    expect(screen.getByRole('button', { name: /SBIN CE Armed/ })).toHaveAttribute('aria-expanded', 'true');
  });

  it('is keyboard operable', () => {
    show({ rows: [entry()] });
    const row = screen.getByRole('button', { name: /NIFTY CE Armed/ });
    fireEvent.keyDown(row, { key: 'Enter' });
    expect(row).toHaveAttribute('aria-expanded', 'true');
  });

  it('marks an assumed delta where the Greeks are shown', () => {
    show({ rows: [entry({ deltaSource: 'assumed', delta: 0.5, impliedVol: null })] });
    fireEvent.click(screen.getByRole('button', { name: /NIFTY CE Armed/ }));
    expect(screen.getByText('0.500 assumed')).toBeInTheDocument();
  });

  it('shows solved Greeks without a caveat', () => {
    show({ rows: [entry()] });
    fireEvent.click(screen.getByRole('button', { name: /NIFTY CE Armed/ }));
    expect(screen.getByText('0.577')).toBeInTheDocument();
    expect(screen.getByText('22.4%')).toBeInTheDocument();
    expect(screen.queryByText(/assumed/)).not.toBeInTheDocument();
  });
});
