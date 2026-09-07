/**
 * A signal that fired but could not be filled is news, not noise.
 *
 * ORB's board splits rows into "tradable" (ACTIONABLE) and a collapsed "not
 * signalling" list. `SIGNAL_UNRESOLVED` — the strategy fired, then no contract
 * could be resolved — used to map to `watching`, which put it in the collapsed
 * list next to underlyings that simply had no setup. A live breakout blocked by
 * a misconfigured expiry window was therefore indistinguishable from a quiet
 * market, which is exactly how a dead board looks healthy.
 *
 * It maps to `error` instead: something needs attention, and the reason string
 * already carries what.
 */
import { describe, it, expect } from 'vitest';
import { orbToBoard } from '../orbAdapter';
import { toOrbFeedEntries, type OrbFeedEntry } from '../../../../utils/niftyOrbSignalAdapter';

const base: OrbFeedEntry = {
  id: 'ORB-1', strategy: 'ORB', underlying: 'RELIANCE', direction: 'long',
  state: 'WATCHING', spot: 1288.1, orbHigh: 1290, orbLow: 1280, vwap: 1285,
  atr: 3.2, volumeRatio: 1.8, optionSymbol: null, optionStrike: null,
  optionType: null, optionExpiry: null, optionPremium: null, stopPremium: null,
  targetPremium: null, quantity: null, riskInr: null, maxLossInr: null,
  deltaIsEstimated: false, deltaSource: null, delta: null, impliedVol: null,
  gamma: null, thetaPerDay: null, vegaPerPoint: null, exchange: null,
  lotSize: null, underlyingEntry: null, underlyingStop: null, dataSource: 'kite',
  quoteAgeS: null, reason: null, timestamp: '2026-08-27T10:30:00+05:30',
  vwapBasis: 'volume', volumeConfirmed: true,
};

describe('a blocked ORB signal', () => {
  it('surfaces as an error, not as a quiet watching row', () => {
    const blocked = orbToBoard({
      ...base,
      state: 'SIGNAL_UNRESOLVED',
      reason: 'No RELIANCE CE expiry within DTE 0-0 -- nearest listed expiry is 33 days out',
    });
    expect(blocked.status).toBe('error');
    expect(blocked.reason).toContain('33 days out');
  });

  it('still calls a genuinely quiet underlying watching', () => {
    expect(orbToBoard({ ...base, state: 'WATCHING' }).status).toBe('watching');
  });

  it('still calls a fillable signal armed', () => {
    expect(orbToBoard({ ...base, state: 'SIGNAL' }).status).toBe('armed');
  });

  it('maps a retained fire to ended, not to a live armed ticket', () => {
    const past = orbToBoard({ ...base, state: 'ENDED', optionSymbol: 'NIFTY26AUG25000CE', optionType: 'CE' });
    expect(past.status).toBe('ended');
    expect(past.flags.some((f) => f.label === 'PAST')).toBe(true);
  });
});

describe('the averaging basis', () => {
  it('labels an index line TWAP, because that is what it is', () => {
    const idx = orbToBoard({ ...base, underlying: 'NIFTY', vwapBasis: 'time', volumeConfirmed: false });
    const setup = idx.sections.find((s) => s.title.includes('Opening range'))!;
    expect(setup.title).toContain('TWAP');
    expect(setup.stats.find((s) => s.label === 'TWAP')?.value).toBe('1285.00');
    expect(setup.stats.some((s) => s.label === 'VWAP')).toBe(false);
  });

  it('labels a stock line VWAP', () => {
    const stock = orbToBoard(base);
    const setup = stock.sections.find((s) => s.title.includes('Opening range'))!;
    expect(setup.stats.find((s) => s.label === 'VWAP')?.value).toBe('1285.00');
  });

  it('says the volume gate was never evaluated rather than printing a fake ratio', () => {
    const idx = orbToBoard({ ...base, vwapBasis: 'time', volumeConfirmed: false, volumeRatio: 1.0 });
    const setup = idx.sections.find((s) => s.title.includes('Opening range'))!;
    expect(setup.stats.find((s) => s.label === 'Volume')?.value).toBe('no feed');
  });
});

describe('the payload adapter', () => {
  it('carries the basis through from the backend scan', () => {
    const [entry] = toOrbFeedEntries({
      signals: [{
        underlying: 'NIFTY', status: 'signal',
        signal: { direction: 'LONG', vwap: 24090, vwap_basis: 'time', volume_confirmed: false },
        trade: null,
      }],
    });
    expect(entry.vwapBasis).toBe('time');
    expect(entry.volumeConfirmed).toBe(false);
  });

  it('defaults to a volume basis when the backend omits the field', () => {
    const [entry] = toOrbFeedEntries({
      signals: [{ underlying: 'SBIN', status: 'watching', signal: { direction: 'NONE' }, trade: null }],
    });
    expect(entry.vwapBasis).toBe('volume');
    expect(entry.volumeConfirmed).toBe(true);
  });
});
