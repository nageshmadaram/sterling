import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../utils/api';
import { notifyOrder } from '../store/useKiteNotifications';
import type {
  ActivityResponse, BacktestRequest, BacktestResponse, EngineConfigModel,
  EngineDetailResponse, EngineOrderRequest, EngineOrderResponse, ExpiryCalendarResponse, LiquidityGroup,
  OpenPositionsResponse, SetupChart, SignalsResponse,
} from '../types/kiteEngine';

const E = '/api/v1/kite/engine';

// ─── Options backtest (workstream H) ─────────────────────────────────────────
export function useEngineBacktest() {
  return useMutation<BacktestResponse, Error, BacktestRequest>({
    mutationFn: (req) => api.post<BacktestResponse>(`${E}/backtest`, req),
  });
}

// ─── Signals (polled) ────────────────────────────────────────────────────────
// The backend now flushes rows symbol-by-symbol while a scan is running (see
// scanner.py), so poll fast during a scan to show setups landing on the go;
// fall back to a slow idle poll once scanning finishes.
import { useReplayStore } from './useReplayStore';

export function useEngineSignals() {
  const isSimActive = useReplayStore((s) => s.status.state !== 'idle');
  return useQuery<SignalsResponse>({
    queryKey: ['kite-engine-signals'],
    queryFn: () => api.get<SignalsResponse>(`${E}/signals`),
    refetchInterval: (query) => {
      if (useReplayStore.getState().status.state !== 'idle') return 300;
      return query.state.data?.scanning ? 2_000 : 15_000;
    },
    staleTime: isSimActive ? 0 : 5_000,
  });
}

// ─── Stock registry (cached) ───────────────────────────────────────────────
export function useStockRegistry() {
  return useQuery<LiquidityGroup[]>({
    queryKey: ['kite-engine-stock-registry'],
    queryFn: () => api.get<LiquidityGroup[]>(`${E}/stock-registry`),
    staleTime: 300_000,
  });
}

// ─── Config ────────────────────────────────────────────────────────────────
export function useEngineConfig() {
  return useQuery<EngineConfigModel>({
    queryKey: ['kite-engine-config'],
    queryFn: () => api.get<EngineConfigModel>(`${E}/config`),
    staleTime: 30_000,
    // Signals, activity and open positions all poll; the config — the one thing
    // that decides what happens to real money — used to be the only query never
    // revalidated, so a change made in another tab or applied server-side stayed
    // invisible here indefinitely. Refetch on focus and on a slow timer.
    refetchInterval: 60_000,
    refetchOnWindowFocus: true,
  });
}

// Exact contract dates from Kite's NFO/BFO instrument dump. The backend cache is
// hourly; a shorter query window lets an expiry-day rollover refresh automatically.
export function useExpiryCalendar() {
  return useQuery<ExpiryCalendarResponse>({
    queryKey: ['kite-engine-expiry-calendar'],
    queryFn: () => api.get<ExpiryCalendarResponse>(`${E}/expiry-calendar`),
    staleTime: 15 * 60_000,
    refetchInterval: 30 * 60_000,
    retry: 1,
  });
}

export function useSetEngineConfig() {
  const qc = useQueryClient();
  return useMutation<EngineConfigModel, Error, EngineConfigModel>({
    mutationFn: (body) => api.post<EngineConfigModel>(`${E}/config`, body),
    onSuccess: (data) => qc.setQueryData(['kite-engine-config'], data),
  });
}

/**
 * Change only the named fields, leaving every other one exactly as the server
 * has it.
 *
 * `useSetEngineConfig` POSTs the whole model, so a caller has to spread its
 * cached copy — `{...cfg, ...values}` — and any field that moved since that
 * copy was fetched gets reverted. Two tabs, or two surfaces in one tab, could
 * silently undo each other's real-money settings. A PATCH that names one field
 * cannot revert the other 37.
 */
export function usePatchEngineConfig() {
  const qc = useQueryClient();
  return useMutation<EngineConfigModel, Error, Partial<EngineConfigModel>>({
    mutationFn: (body) => api.patch<EngineConfigModel>(`${E}/config`, body),
    onSuccess: (data) => qc.setQueryData(['kite-engine-config'], data),
  });
}

export function useResetEngineConfig() {
  const qc = useQueryClient();
  return useMutation<EngineConfigModel, Error, void>({
    mutationFn: () => api.post<EngineConfigModel>(`${E}/config/reset`),
    onSuccess: (data) => {
      qc.setQueryData(['kite-engine-config'], data);
      // A reset changes strike coverage, the trail and the exit rule all at
      // once, so every row on the board was computed under rules that no longer
      // apply. Drop the derived caches rather than leave the board, the setup
      // chart and the detail dock quoting the old configuration.
      qc.invalidateQueries({ queryKey: ['kite-engine-signals'] });
      qc.invalidateQueries({ queryKey: ['kite-engine-setup'] });
      qc.invalidateQueries({ queryKey: ['kite-engine-detail'] });
      notifyOrder({
        kind: 'info',
        title: 'Settings reset',
        message: 'Sterling Kite Engine configuration restored to defaults.',
      });
    },
  });
}

// ─── Scan trigger ────────────────────────────────────────────────────────────
export function useRunScan() {
  const qc = useQueryClient();
  return useMutation<SignalsResponse, Error, void>({
    mutationFn: () => api.post<SignalsResponse>(`${E}/scan`),
    // /scan blocks server-side until the whole scan finishes, so the mutation's
    // own promise can't show progress — that comes from the polling /signals
    // query instead. Flip it into "scanning" the instant the button is clicked
    // (rather than waiting for the query's own idle-interval timer to catch up,
    // which can leave the board on a stale/empty view for the entire scan) and
    // force an immediate refetch so real rows show up as soon as the backend
    // starts flushing them.
    //
    // Do NOT invalidate here: that refetch races the optimistic flag and can
    // land before the backend has flipped its own `scanning`, overwriting the
    // true we just set with a false — the board then looks idle for the whole
    // scan. The 2s poll that `scanning: true` itself switches on brings the
    // rows in, so the invalidate bought nothing and cost the flag.
    onMutate: () => {
      qc.setQueryData<SignalsResponse>(['kite-engine-signals'], (prev) =>
        prev ? { ...prev, scanning: true } : prev);
    },
    // A SuperTrend scan returns SuperTrend rows only. Replacing the cache
    // wholesale therefore wipes any Navigator-originated rows that
    // useRunNavigatorScan had merged in, until the next poll re-fetches them —
    // rows visibly vanish from the board. Merge on the same key instead.
    onSuccess: (data) => {
      qc.setQueryData<SignalsResponse>(['kite-engine-signals'], (prev) => {
        const navigatorRows = (prev?.rows ?? []).filter((row) => row.source === 'navigator');
        if (!navigatorRows.length) return data;
        const fresh = new Set(data.rows.map((row) => `${row.token}:${row.option_type}:${row.timestamp_ms}`));
        const kept = navigatorRows.filter((row) => !fresh.has(`${row.token}:${row.option_type}:${row.timestamp_ms}`));
        return { ...data, rows: [...data.rows, ...kept] };
      });
    },
    // Without this a failed /scan leaves `scanning: true` stuck in the cache
    // until a poll happens to return false — the board claims to be scanning
    // when nothing is running.
    onError: () => {
      qc.setQueryData<SignalsResponse>(['kite-engine-signals'], (prev) =>
        prev ? { ...prev, scanning: false } : prev);
      qc.invalidateQueries({ queryKey: ['kite-engine-signals'] });
    },
  });
}

// ─── Cancel scan (force-stop) ─────────────────────────────────────────────────
export function useCancelScan() {
  const qc = useQueryClient();
  return useMutation<SignalsResponse, Error, void>({
    mutationFn: () => api.post<SignalsResponse>(`${E}/scan/cancel`),
    onSuccess: (data) => qc.setQueryData(['kite-engine-signals'], data),
  });
}

// ─── Activity feed (terminal) ─────────────────────────────────────────────────
export function useEngineActivity() {
  return useQuery<ActivityResponse>({
    queryKey: ['kite-engine-activity'],
    queryFn: () => api.get<ActivityResponse>(`${E}/activity`),
    refetchInterval: 10_000,
  });
}

// ─── Server logs (real backend logs, interleaved into the terminal) ──────────
export interface ServerLogLine {
  ts_ms: number;
  level: string;
  name: string;
  message: string;
}
export function useEngineServerLogs(enabled: boolean) {
  return useQuery<{ logs: ServerLogLine[] }>({
    queryKey: ['kite-engine-server-logs'],
    queryFn: () => api.get<{ logs: ServerLogLine[] }>(`${E}/server-logs?limit=400`),
    refetchInterval: enabled ? 10_000 : false,
    enabled,
  });
}

// ─── Setup chart (click-to-visualize) ─────────────────────────────────────────
export function useEngineSetup(token: number | null, underlying: string, enabled: boolean) {
  return useQuery<SetupChart>({
    queryKey: ['kite-engine-setup', token, underlying],
    queryFn: () => api.get<SetupChart>(
      `${E}/setup/${token}?underlying=${encodeURIComponent(underlying)}`),
    enabled: enabled && token != null,
    staleTime: 60_000,
  });
}

// ─── Place a BUY/SELL through the engine path (logged to the terminal) ────────
export function useEnginePlaceOrder() {
  const qc = useQueryClient();
  return useMutation<EngineOrderResponse, Error, EngineOrderRequest>({
    mutationFn: (body) => api.post<EngineOrderResponse>(`${E}/order`, body),
    onSuccess: (data, body) => {
      qc.invalidateQueries({ queryKey: ['kite-engine-activity'] });
      qc.invalidateQueries({ queryKey: ['kite-engine-open-positions'] });
      // Whether the position is guarded is the single most important thing to say
      // after a manual BUY — the board shows an SL/TSL beside it either way, so an
      // unarmed entry has to be called out here rather than left to look protected.
      const unprotected = data?.protected === false && body.side === 'BUY';
      notifyOrder({
        kind: data?.status === 'duplicate' ? 'info' : unprotected ? 'info' : 'placed',
        title: data?.status === 'duplicate' ? 'Already submitted'
          : unprotected ? 'Order placed — UNPROTECTED' : 'Order placed',
        message: `${body.side} ${body.quantity} ${body.option_symbol}.`
          + (data?.protection ? ` ${unprotected ? 'No automatic exit' : 'Protected'}: ${data.protection}.` : ''),
        orderId: data?.order_id,
      });
    },
    onError: (err, body) => {
      notifyOrder({ kind: 'rejected', title: 'Order rejected', message: `${body.side} ${body.option_symbol} — ${err.message}` });
    },
  });
}

// ─── Signal detail (trigger info + live price + greeks + depth) ───────────────
// `source` disambiguates the row: a Navigator origination and a SuperTrend row
// for the same instrument share a token, so without it the server can answer a
// Navigator click with the SuperTrend row's entry, stop and legs.
export function useEngineDetail(
  token: number | null, timestamp_ms: number | null, enabled: boolean, source?: string,
) {
  return useQuery<EngineDetailResponse>({
    queryKey: ['kite-engine-detail', token, timestamp_ms, source ?? ''],
    queryFn: () => api.get<EngineDetailResponse>(
      `${E}/detail/${token}?timestamp_ms=${timestamp_ms || 0}`
      + (source ? `&source=${encodeURIComponent(source)}` : ''),
    ),
    enabled: enabled && token != null,
    refetchInterval: 15_000,
  });
}

// ─── Engine open positions (vehicle + direction labels) ───────────────────────
export function useEngineOpenPositions() {
  return useQuery<OpenPositionsResponse>({
    queryKey: ['kite-engine-open-positions'],
    queryFn: () => api.get<OpenPositionsResponse>(`${E}/open-positions`),
    refetchInterval: 10_000,
  });
}

export function useCloseEnginePosition() {
  const qc = useQueryClient();
  return useMutation<OpenPositionsResponse, Error, string>({
    mutationFn: (symbol) => api.delete<OpenPositionsResponse>(`${E}/open-positions/${encodeURIComponent(symbol)}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['kite-engine-open-positions'] }),
  });
}
