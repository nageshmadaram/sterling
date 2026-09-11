import { useMutation, useQueryClient } from '@tanstack/react-query';
import { api } from '../utils/api';
import { useRunScan } from './useSterlingKiteEngine';
import { useRunNavigatorScan } from './useNavigator';
import { useGammaMoveScan } from './useGammaMove';
import { useScanActivity } from '../store/useScanActivity';

export type ScannableEngine =
  | 'supertrend' | 'navigator' | 'gamma_move' | 'adaptive_edge';

export const SCANNABLE_ENGINE_LABEL: Record<ScannableEngine, string> = {
  supertrend: 'SuperTrend',
  navigator: 'Navigator',
  gamma_move: 'Gamma Move',
  adaptive_edge: 'Adaptive Edge',
};

export interface EngineScanResult {
  engine: ScannableEngine;
  ok: boolean;
  /** Present when the scan was refused or failed. */
  error?: string;
}

/** `POST /config/adaptive-edge/scan`. Had no hook before this. */
export function useAdaptiveEdgeScan() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post('/api/v1/config/adaptive-edge/scan', {}),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['adaptive-edge-snapshot'] }),
  });
}

export function useScanAllStrategies() {
  const supertrend = useRunScan();
  const navigator = useRunNavigatorScan();
  const gammaMove = useGammaMoveScan();
  const adaptiveEdge = useAdaptiveEdgeScan();

  const runners: Record<ScannableEngine, () => Promise<unknown>> = {
    supertrend: () => supertrend.mutateAsync(),
    navigator: () => navigator.mutateAsync(),
    gamma_move: () => gammaMove.mutateAsync(),
    adaptive_edge: () => adaptiveEdge.mutateAsync(),
  };

  const scanAll = async (order: readonly ScannableEngine[]): Promise<EngineScanResult[]> => {
    const results: EngineScanResult[] = [];
    const setCurrent = useScanActivity.getState().setCurrent;
    try {
      for (const engine of order) {
        setCurrent(engine);
        try {
          await runners[engine]();
          results.push({ engine, ok: true });
        } catch (err) {
          results.push({
            engine,
            ok: false,
            error: err instanceof Error ? err.message : String(err),
          });
        }
      }
    } finally {
      setCurrent(null);
    }
    return results;
  };

  return {
    scanAll,
    isPending: supertrend.isPending || navigator.isPending
      || gammaMove.isPending || adaptiveEdge.isPending,
  };
}

export default useScanAllStrategies;
