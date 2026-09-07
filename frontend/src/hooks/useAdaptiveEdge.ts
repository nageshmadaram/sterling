import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../utils/api';
import { notifyOrder } from '../store/useKiteNotifications';
import type { AdaptiveEdgeSettings, AdaptiveEdgeSnapshot } from '../types/adaptiveEdge';

const ROOT = '/api/v1/adaptive-edge';
/* The engine configuration the scanner and runner actually read. The legacy
   /adaptive-edge/settings surface above mirrors the fields it shares with this
   one; the rest of that surface reaches no engine and is listed in
   `inert_fields` so the UI can say so rather than pretending. */
const CONFIG_ROOT = '/api/v1/config/adaptive-edge';

import { useReplayActive as useSimActive } from './useReplayStore';

export function useAdaptiveEdgeSnapshot() {
  const isSimActive = useSimActive();
  return useQuery<AdaptiveEdgeSnapshot>({
    queryKey: ['adaptive-edge-snapshot'],
    queryFn: () => api.get<AdaptiveEdgeSnapshot>(`${ROOT}/snapshot`),
    refetchInterval: isSimActive ? 300 : 5000,
    staleTime: isSimActive ? 0 : 5000,
  });
}

export function useAdaptiveEdgeSettings() {
  return useQuery<{
    settings: AdaptiveEdgeSettings;
    live_trading: boolean;
    /* Controls this surface still accepts that reach no engine. Published so
       the UI can say so, rather than letting a save succeed silently. */
    inert_fields?: string[];
    engine_fields?: string[];
  }>({
    queryKey: ['adaptive-edge-settings'],
    queryFn: () => api.get(`${ROOT}/settings`),
  });
}

export function useSetAdaptiveEdgeSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (settings: AdaptiveEdgeSettings) => api.put(`${ROOT}/settings`, settings),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['adaptive-edge-settings'] });
      qc.invalidateQueries({ queryKey: ['adaptive-edge-snapshot'] });
      notifyOrder({ kind: 'info', title: 'Adaptive Edge settings saved', message: 'Research policy only. Live trading stays blocked.' });
    },
  });
}


export interface AdaptiveEdgeEngineConfig {
  strategy: {
    id: string;
    name: string;
    validated: boolean;
    calibrated_fields: string[];
    calibration: Record<string, string>;
    headline_finding: string;
    what_to_do: string;
  };
  config: Record<string, unknown>;
  defaults: Record<string, unknown>;
  vocabularies: Record<string, string[]>;
  warnings: string[];
}

export function useAdaptiveEdgeEngineConfig() {
  return useQuery<AdaptiveEdgeEngineConfig>({
    queryKey: ['adaptive-edge-engine-config'],
    queryFn: () => api.get<AdaptiveEdgeEngineConfig>(CONFIG_ROOT),
  });
}

export function useSetAdaptiveEdgeEngineConfig() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (values: Record<string, unknown>) => api.put(CONFIG_ROOT, values),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['adaptive-edge-engine-config'] });
      qc.invalidateQueries({ queryKey: ['adaptive-edge-snapshot'] });
      notifyOrder({
        kind: 'info',
        title: 'Adaptive Edge engine settings saved',
        message: 'Paper only — the strategy is not promoted for live execution.',
      });
    },
  });
}
