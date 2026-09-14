import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../utils/api';
import { notifyOrder } from '../store/useKiteNotifications';
import type {
  TrueDataCredential,
  TrueDataCredentialCreate,
  TrueDataCredentialUpdate,
  TrueDataSettings,
  TrueDataStatus,
} from '../types/truedata';

const TD = '/api/v1/truedata';

export function useTrueDataSettings() {
  return useQuery<TrueDataSettings>({
    queryKey: ['truedata-settings'],
    queryFn: () => api.get<TrueDataSettings>(`${TD}/settings`),
    staleTime: 10_000,
  });
}

export function useUpdateTrueDataSettings() {
  const qc = useQueryClient();
  return useMutation<TrueDataSettings, Error, TrueDataSettings>({
    mutationFn: (body) => api.post<TrueDataSettings>(`${TD}/settings`, body),
    onSuccess: (_data, vars) => {
      qc.invalidateQueries({ queryKey: ['truedata-settings'] });
      qc.invalidateQueries({ queryKey: ['truedata-status'] });
      qc.invalidateQueries({ queryKey: ['adaptive-edge-snapshot'] });
      notifyOrder({
        kind: 'info',
        title: 'Market data source updated',
        message: `Primary data source set to ${vars.data_source === 'truedata' ? 'TrueData' : 'Zerodha Kite'}.`,
      });
    },
    onError: (err) => {
      notifyOrder({
        kind: 'error',
        title: 'Data source update failed',
        message: err.message || 'Could not update market data source preference.',
      });
    },
  });
}

export function useTrueDataCredentials() {
  return useQuery<TrueDataCredential[]>({
    queryKey: ['truedata-credentials'],
    queryFn: () => api.get<TrueDataCredential[]>(`${TD}/credentials`),
    staleTime: 15_000,
  });
}

export function useAddTrueDataCredential() {
  const qc = useQueryClient();
  return useMutation<TrueDataCredential, Error, TrueDataCredentialCreate>({
    mutationFn: (body) => api.post<TrueDataCredential>(`${TD}/credentials`, body),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ['truedata-credentials'] });
      qc.invalidateQueries({ queryKey: ['truedata-status'] });
      notifyOrder({
        kind: 'info',
        title: 'TrueData credential added',
        message: `Feed "${data.label}" saved and connected.`,
      });
    },
    onError: (err) => {
      notifyOrder({
        kind: 'error',
        title: 'Could not add TrueData feed',
        message: err.message || 'Failed to save TrueData credentials.',
      });
    },
  });
}

export function useUpdateTrueDataCredential() {
  const qc = useQueryClient();
  return useMutation<
    TrueDataCredential,
    Error,
    { id: string } & TrueDataCredentialUpdate
  >({
    mutationFn: ({ id, ...body }) => api.put<TrueDataCredential>(`${TD}/credentials/${id}`, body),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ['truedata-credentials'] });
      qc.invalidateQueries({ queryKey: ['truedata-status'] });
      notifyOrder({
        kind: 'info',
        title: 'TrueData credential saved',
        message: `Feed "${data.label}" updated.`,
      });
    },
    onError: (err) => {
      notifyOrder({
        kind: 'error',
        title: 'Update failed',
        message: err.message || 'Could not update TrueData credentials.',
      });
    },
  });
}

export function useDeleteTrueDataCredential() {
  const qc = useQueryClient();
  return useMutation<void, Error, string>({
    mutationFn: (id) => api.delete<void>(`${TD}/credentials/${id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['truedata-credentials'] });
      qc.invalidateQueries({ queryKey: ['truedata-status'] });
      notifyOrder({
        kind: 'info',
        title: 'TrueData credential removed',
        message: 'Feed deleted successfully.',
      });
    },
    onError: (err) => {
      notifyOrder({
        kind: 'error',
        title: 'Delete failed',
        message: err.message || 'Could not remove TrueData credential.',
      });
    },
  });
}

export function useTrueDataStatus() {
  return useQuery<TrueDataStatus>({
    queryKey: ['truedata-status'],
    queryFn: () => api.get<TrueDataStatus>(`${TD}/status`),
    refetchInterval: 30_000,
  });
}

export function useRunTrueDataDiagnostics() {
  const qc = useQueryClient();
  return useMutation<
    import('../types/truedata').DiagnosticSuiteResult,
    Error,
    { category_id?: string } | void
  >({
    mutationFn: (params) =>
      api.post<import('../types/truedata').DiagnosticSuiteResult>(
        `${TD}/diagnostics/run`,
        params || {}
      ),
    onSuccess: (data) => {
      qc.setQueryData(['truedata-diagnostics-latest'], data);
      qc.invalidateQueries({ queryKey: ['truedata-status'] });
    },
  });
}

export function useTrueDataDiagnosticsSummary() {
  return useQuery<{
    authenticated: boolean;
    username_hint?: string | null;
    is_active: boolean;
    realtime_port: number;
    has_credentials: boolean;
  }>({
    queryKey: ['truedata-diagnostics-summary'],
    queryFn: () => api.get(`${TD}/diagnostics/summary`),
    refetchInterval: 15_000,
  });
}

