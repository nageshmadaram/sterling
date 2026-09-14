/**
 * The account's daily-loss breaker.
 *
 * The thresholds used to be one process-wide pair that nothing in the app ever
 * set, so every account halted at the shipped -1500 whatever its size. They are
 * per-account and editable now; `is_account_override` is how the UI tells "this
 * account chose these" from "this account is on the default".
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../utils/api';
import { notifyOrder } from '../store/useKiteNotifications';

const PATH = '/api/v1/kite/risk/daily-loss';
const KEY = ['kite-daily-loss'];

export interface DailyLossLimit {
  enabled: boolean;
  /** Both are a realised INR loss, so both are negative. */
  soft_warn_inr: number;
  hard_halt_inr: number;
  uid: string;
  /** False means this account is reading the shipped default, not its own. */
  is_account_override: boolean;
  /** Today's realised P&L for this account, and where it sits. */
  pnl_inr: number;
  level: 'clear' | 'warning' | 'halt';
  default: { enabled: boolean; soft_warn_inr: number; hard_halt_inr: number };
}

export function useDailyLossLimit(enabled = true) {
  return useQuery<DailyLossLimit>({
    queryKey: KEY,
    queryFn: () => api.get<DailyLossLimit>(PATH),
    enabled,
    // The P&L half of this moves with the book, so it cannot be cached hard.
    refetchInterval: 30_000,
  });
}

export function useSetDailyLossLimit() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { enabled: boolean; soft_warn_inr: number; hard_halt_inr: number }) =>
      api.put<DailyLossLimit>(PATH, body),
    onSuccess: (data) => {
      qc.setQueryData(KEY, data);
      notifyOrder({
        kind: 'info',
        title: 'Daily loss limit saved',
        message: data.enabled
          ? `Armed: warn at ₹${Math.abs(data.soft_warn_inr).toLocaleString('en-IN')}, halt at ₹${Math.abs(data.hard_halt_inr).toLocaleString('en-IN')}.`
          : 'Daily loss limit disabled.',
      });
    },
    onError: (err: any) => {
      notifyOrder({
        kind: 'error',
        title: 'Save failed',
        message: err?.message || 'Could not update daily loss limit.',
      });
    },
  });
}

export function useClearDailyLossLimit() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.delete<DailyLossLimit>(PATH),
    onSuccess: (data) => {
      qc.setQueryData(KEY, data);
      notifyOrder({
        kind: 'info',
        title: 'Daily loss limit reset',
        message: 'Restored account defaults.',
      });
    },
    onError: (err: any) => {
      notifyOrder({
        kind: 'error',
        title: 'Reset failed',
        message: err?.message || 'Could not reset daily loss limit.',
      });
    },
  });
}
