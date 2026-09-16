/**
 * The Family Operations surface.
 *
 * Everything on this screen is server truth. The browser computes no eligibility,
 * no health and no risk: it renders what the backend already decided, because a
 * screen that can reason about whether trading is allowed is a screen that can be
 * wrong about it.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../utils/api';

const PATH = '/api/v1/snapback/family/operations';
const KEY = ['snapback-family-operations'];

export type FamilySystemStatus = 'HEALTHY' | 'DEGRADED' | 'HALTED' | 'RECOVERY_REQUIRED' | 'RECONCILING';
export type FamilyMode = 'PAPER' | 'LIVE' | 'HALTED';
export type FamilyEvidence = 'INCONCLUSIVE' | 'PASSED' | 'FAILED' | 'UNVALIDATED';

export interface SnapbackFamilyOperations {
  system_status: FamilySystemStatus;
  mode: FamilyMode;
  strategy: string;
  runtime_sha: string | null;
  build_sha?: string | null;
  strategy_manifest: string | null;
  evidence: FamilyEvidence;
  evidence_missing_requirements: string[];
  broker_connected: boolean;
  market_data_fresh: boolean;
  runner_alive: boolean;
  /** null means unknown — never assume a backup happened. */
  backup_ok: boolean | null;
  alert_transport_configured: boolean;
  last_report: string | null;
  allocated_capital: number | null;
  cumulative_net_pnl: number | null;
  mean_net_pnl_per_trade: number | null;
  current_exposure_inr: number | null;
  open_positions_count: number;
  exit_pending: number;
  drawdown_pct: number | null;
  observed_sessions?: number | null;
  completed_trades?: number | null;
  new_trades_halted: boolean;
  live_blocked: boolean;
  unresolved_errors: string[];
  generated_at: string;
}

export function useSnapbackFamilyOperations(pollMs = 15000) {
  return useQuery<SnapbackFamilyOperations>({
    queryKey: KEY,
    queryFn: () => api.get<SnapbackFamilyOperations>(PATH),
    refetchInterval: pollMs,
    // A failed poll must not leave a stale green screen on display.
    staleTime: 0,
    retry: false,
  });
}

export function useSnapbackFamilyControls() {
  const qc = useQueryClient();

  const invalidate = () => qc.invalidateQueries({ queryKey: KEY });

  const stop = useMutation({
    mutationFn: (reason: string) =>
      api.post(`/api/v1/snapback/family/stop-new-trades?reason=${encodeURIComponent(reason)}`),
    onSuccess: invalidate,
  });

  const resume = useMutation({
    mutationFn: (reason: string) =>
      api.post(`/api/v1/snapback/family/resume-new-trades?reason=${encodeURIComponent(reason)}`),
    onSuccess: invalidate,
  });

  return { stop, resume };
}
