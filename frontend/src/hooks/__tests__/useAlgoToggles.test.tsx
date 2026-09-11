import { describe, expect, it, vi } from 'vitest';
import { renderHook } from '@testing-library/react';
import { useAlgoToggles } from '../useAlgoToggles';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import React from 'react';

vi.mock('../useSterlingKiteEngine', () => ({
  useEngineConfig: () => ({ data: { engine_enabled: true, auto_execute: true } }),
  usePatchEngineConfig: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock('../useNavigator', () => ({
  useNavigatorConfig: () => ({
    data: { record: { config: { enabled: true, auto_execute_originated: false }, revision: 1 } },
  }),
  useSetNavigatorConfig: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock('../useGammaMove', () => ({
  useGammaMoveConfig: () => ({ data: { config: { enabled: true, auto_execute: false } } }),
  useUpdateGammaMove: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock('../useAdaptiveEdge', () => ({
  useAdaptiveEdgeEngineConfig: () => ({ data: { config: { enabled: true, auto_execute: true } } }),
  useSetAdaptiveEdgeEngineConfig: () => ({ mutate: vi.fn(), isPending: false }),
}));

describe('useAlgoToggles', () => {
  it('returns all surviving strategies with their respective auto_execute states', () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const wrapper = ({ children }: { children: React.ReactNode }) => (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );

    const { result } = renderHook(() => useAlgoToggles(), { wrapper });
    expect(result.current).toHaveLength(4);

    const ids = result.current.map((t) => t.id);
    expect(ids).toEqual([
      'supertrend',
      'navigator',
      'gamma_move',
      'adaptive_edge',
    ]);

    expect(result.current.find((t) => t.id === 'supertrend')?.enabled).toBe(true);
    expect(result.current.find((t) => t.id === 'navigator')?.enabled).toBe(false);
    expect(result.current.find((t) => t.id === 'gamma_move')?.enabled).toBe(false);
    expect(result.current.find((t) => t.id === 'adaptive_edge')?.enabled).toBe(true);
  });
});
