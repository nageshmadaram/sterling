import { useMutation, useQuery } from '@tanstack/react-query';
import { api } from '../utils/api';
import type {
  AdaptiveEdgeComparisonResult,
  BacktestPreset,
  StrategyDescriptor,
  UnifiedBacktestRequest,
  UnifiedBacktestResult,
} from '../types/backtest';

const ROOT = '/api/v1/backtest/unified';

export function useUnifiedStrategies() {
  return useQuery<StrategyDescriptor[]>({
    queryKey: ['unified-backtest-strategies'],
    queryFn: () => api.get<StrategyDescriptor[]>(`${ROOT}/strategies`),
    staleTime: 60000,
  });
}

export function useUnifiedPresets() {
  return useQuery<BacktestPreset[]>({
    queryKey: ['unified-backtest-presets'],
    queryFn: () => api.get<BacktestPreset[]>(`${ROOT}/presets`),
    staleTime: 60000,
  });
}

export function useRunUnifiedBacktest() {
  return useMutation<UnifiedBacktestResult, Error, UnifiedBacktestRequest>({
    mutationFn: (req: UnifiedBacktestRequest) =>
      api.post<UnifiedBacktestResult>(`${ROOT}/run`, req),
  });
}

export function useRunAdaptiveEdgeComparison() {
  return useMutation<AdaptiveEdgeComparisonResult, Error, UnifiedBacktestRequest>({
    mutationFn: (req: UnifiedBacktestRequest) =>
      api.post<AdaptiveEdgeComparisonResult>('/api/v1/backtest/adaptive-edge/compare', req),
  });
}

