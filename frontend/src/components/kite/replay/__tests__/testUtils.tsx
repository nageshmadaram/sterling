import React from 'react';
import { vi } from 'vitest';
import {
  DEFAULT_STATUS,
  ReplayCapabilities,
  ReplaySignal,
  ReplayStatus,
  ReplayTrade,
  useReplayStore,
} from '../../../../hooks/useReplayStore';

export const FULL_CAPS: ReplayCapabilities = {
  friction: true,
  contract_on_signal: true,
  absolute_seek: true,
  stream: false,
  delta_status: true,
  multi_day: false,
  resolutions: ['1m', '5m', '15m'],
};

export function makeSignal(over: Partial<ReplaySignal> = {}): ReplaySignal {
  return {
    time_iso: '10:47:05',
    timestamp_ms: 1,
    strategy: 'supertrend',
    instrument: 'NIFTY',
    direction: 'BULLISH',
    strength: 'STRONG',
    entry: 24500,
    stop: 24400,
    target: 24700,
    ...over,
  };
}

export function makeTrade(over: Partial<ReplayTrade> = {}): ReplayTrade {
  return {
    trade_id: 'TRD-1001',
    entry_time_iso: '10:47:05',
    exit_time_iso: '11:07:05',
    timestamp_ms: 1,
    strategy: 'supertrend',
    symbol: 'NIFTY26AUG24500CE',
    underlying: 'NIFTY',
    direction: 'BUY',
    opt_type: 'CE',
    strike: 24500,
    lots: 2,
    quantity: 50,
    entry_price: 100,
    exit_price: 120,
    stop_loss: 75,
    target_price: 150,
    status: 'WIN',
    pnl_usd: 1000,
    pnl_pct: 20,
    duration_mins: 20,
    ...over,
  };
}

export function makeStatus(over: Partial<ReplayStatus> = {}): ReplayStatus {
  return {
    ...DEFAULT_STATUS,
    ...over,
    stats: { ...DEFAULT_STATUS.stats, ...(over.stats ?? {}) },
    capabilities: over.capabilities ?? FULL_CAPS,
  };
}

/** Reset the store to a known, dock-open baseline between tests. */
export function primeStore(over: Partial<ReturnType<typeof useReplayStore.getState>> = {}) {
  useReplayStore.setState({
    open: true,
    mode: 'docked',
    prevMode: 'docked',
    height: 320,
    tab: 'trades',
    shortcutsOpen: false,
    summaryOpen: false,
    hostContentHidden: false,
    selectedSignalKey: null,
    error: null,
    status: makeStatus(),
    draft: {
      date: '2026-09-04',
      endDate: '2026-09-04',
      startTime: '09:00:00',
      endTime: '15:30:00',
      speed: 5,
      resolution: '5m',
      strategies: ['all'],
      moneyness: ['ATM'],
      lots: 1,
      frictionMode: 'realistic',
      indexSpreadPct: 0.5,
      stockSpreadPct: 1.5,
      slippagePct: 0.25,
      instruments: [],
    },
    ...over,
  });
}

/** A fetch stub that returns the given status for every replay call. */
export function stubFetch(status: ReplayStatus = makeStatus()) {
  const fn = vi.fn().mockImplementation((url: string, init?: RequestInit) => {
    if (String(url).includes('available-dates')) {
      return Promise.resolve({
        ok: true,
        json: () =>
          Promise.resolve({
            dates: ['2026-09-03', '2026-09-04'],
            instrument: 'NIFTY',
            resolution: '5m',
            source: 'store',
            earliest: '2026-01-01',
            latest: '2026-09-04',
            holidays_filtered: false,
          }),
      });
    }
    if (String(url).includes('/speed')) {
      let reqSpeed: number | undefined;
      try {
        if (init && typeof init.body === 'string') {
          reqSpeed = JSON.parse(init.body).speed;
        }
      } catch {}
      const updatedStatus = {
        ...status,
        config: status.config
          ? { ...status.config, speed: reqSpeed ?? status.config.speed }
          : undefined,
      };
      return Promise.resolve({ ok: true, json: () => Promise.resolve(updatedStatus) });
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve(status) });
  });
  globalThis.fetch = fn as unknown as typeof fetch;
  return fn;
}

/** ResizeObserver is not implemented in jsdom. */
export function stubResizeObserver() {
  (globalThis as any).ResizeObserver = class {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
}

/**
 * Prime the store and the network with the same status.
 *
 * `useReplayStream` fetches `/status` on mount, so priming the store alone is
 * not enough: the boot response overwrites the fixture a moment later. Tests
 * that set a status must set it in both places, and this is the only way to do
 * that without the two drifting apart.
 */
export function setupDock(over: Partial<ReturnType<typeof useReplayStore.getState>> = {}) {
  const status = (over.status as ReplayStatus) ?? makeStatus();
  primeStore({ ...over, status });
  const fetchSpy = stubFetch(status);
  return { status, fetchSpy };
}
