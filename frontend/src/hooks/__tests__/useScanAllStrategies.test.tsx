/**
 * One re-scan for every strategy that has one.
 *
 * Re-scan reached SuperTrend and Navigator — the two engines whose rows share
 * the pane the button sits on. Gamma Move, ORB and Adaptive Edge all have scan
 * endpoints and it never touched them, so pressing it refreshed part of the
 * platform and left the rest on its own background loop.
 */
import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useScanActivity } from '../../store/useScanActivity';

const calls: string[] = [];
const fail = new Set<string>();

// Overlap counting lives HERE, in the one runner every mock shares, so every
// engine is instrumented rather than one. The concurrency test used to install its
// own timing runner with `vi.doMock` after this module had been imported, which
// never reached the already-bound mutations: it measured maxActive = 0 against
// `toBeLessThanOrEqual(1)` and passed while observing nothing at all.
let active = 0;
let maxActive = 0;

const runner = (name: string) => async () => {
  active += 1;
  maxActive = Math.max(maxActive, active);
  calls.push(name);
  try {
    // A real suspension point. Without one, every call completes before the next
    // begins whatever the caller does, and overlap could not be detected even in
    // a fan-out that genuinely fired them together.
    await new Promise((r) => { setTimeout(r, 1); });
    if (fail.has(name)) throw new Error(`${name} refused`);
    return {};
  } finally {
    active -= 1;
  }
};

vi.mock('../useSterlingKiteEngine', () => ({
  useRunScan: () => ({ mutateAsync: runner('supertrend'), isPending: false }),
}));
vi.mock('../useNavigator', () => ({
  useRunNavigatorScan: () => ({ mutateAsync: runner('navigator'), isPending: false }),
}));
vi.mock('../useGammaMove', () => ({
  useGammaMoveScan: () => ({ mutateAsync: runner('gamma_move'), isPending: false }),
}));
vi.mock('../../utils/api', () => ({
  api: { post: (url: string) => runner(url.includes('adaptive-edge') ? 'adaptive_edge' : url)() },
}));

import { useScanAllStrategies, SCANNABLE_ENGINE_LABEL } from '../useScanAllStrategies';

function harness() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  );
  return renderHook(() => useScanAllStrategies(), { wrapper });
}

beforeEach(() => { calls.length = 0; fail.clear(); active = 0; maxActive = 0; });

describe('useScanAllStrategies', () => {
  it('runs every strategy given, in the order given', async () => {
    const { result } = harness();
    await act(async () => {
      await result.current.scanAll(['supertrend', 'navigator', 'gamma_move', 'adaptive_edge']);
    });
    expect(calls).toEqual(['supertrend', 'navigator', 'gamma_move', 'adaptive_edge']);
  });

  it('honours a lens-first order', async () => {
    const { result } = harness();
    await act(async () => {
      await result.current.scanAll(['navigator', 'supertrend']);
    });
    // The table being read must not wait behind the other engine's full scan.
    expect(calls).toEqual(['navigator', 'supertrend']);
  });

  it('runs them one at a time, never together', async () => {
    // They share the same Kite ~3 req/s historical budget, so firing them
    // together makes each slower rather than the set faster.
    const { result } = harness();
    await act(async () => {
      await result.current.scanAll(['supertrend', 'navigator', 'gamma_move']);
    });
    expect(calls).toEqual(['supertrend', 'navigator', 'gamma_move']);
    // Exactly one, not "at most one": at most one is also true of a run that
    // never happened, which is what this assertion used to be.
    expect(maxActive).toBe(1);
  });

  it('one engine failing does not stop the others', async () => {
    fail.add('navigator');
    const { result } = harness();
    let results: Awaited<ReturnType<typeof result.current.scanAll>> = [];
    await act(async () => {
      results = await result.current.scanAll(['supertrend', 'navigator', 'gamma_move']);
    });
    expect(calls).toEqual(['supertrend', 'navigator', 'gamma_move']);
    expect(results.find((r) => r.engine === 'navigator')?.ok).toBe(false);
    expect(results.filter((r) => r.ok).map((r) => r.engine)).toEqual(['supertrend', 'gamma_move']);
  });

  it('reports failures rather than swallowing them', async () => {
    // A button that claims to scan everything has to say which ones it could not.
    fail.add('gamma_move');
    const { result } = harness();
    let results: Awaited<ReturnType<typeof result.current.scanAll>> = [];
    await act(async () => { results = await result.current.scanAll(['gamma_move']); });
    expect(results[0].error).toContain('refused');
  });

  it('publishes which engine it is on, so the status line can name it', async () => {
    const seen: Array<string | null> = [];
    const unsubscribe = useScanActivity.subscribe((s) => seen.push(s.current));
    const { result } = harness();
    await act(async () => {
      await result.current.scanAll(['supertrend', 'gamma_move']);
    });
    unsubscribe();
    expect(seen).toEqual(['supertrend', 'gamma_move', null]);
  });

  it('clears it when the sweep ends', async () => {
    const { result } = harness();
    await act(async () => {
      await result.current.scanAll(['supertrend', 'gamma_move']);
    });
    expect(useScanActivity.getState().current).toBeNull();
  });

  it('clears it even when an engine throws', async () => {
    // In a `finally`. A stuck "scanning" is worse than no label at all: it hides
    // the next real one.
    fail.add('gamma_move');
    const { result } = harness();
    await act(async () => {
      await result.current.scanAll(['gamma_move', 'supertrend']);
    });
    expect(useScanActivity.getState().current).toBeNull();
  });

  it('contains expected scannable engines', () => {
    expect(Object.keys(SCANNABLE_ENGINE_LABEL)).toEqual(
      ['supertrend', 'navigator', 'gamma_move', 'adaptive_edge', 'intraday']);
  });
});
