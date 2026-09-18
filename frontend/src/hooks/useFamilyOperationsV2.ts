/**
 * The operator dashboard's data source.
 *
 * `retry: false` and `staleTime: 0` are deliberate. This is a safety screen: an
 * unreachable backend must be visible immediately, not smoothed over by retries
 * while the page keeps showing the last green state. The caller is expected to
 * treat `isError` as authoritative and ignore cached data for the status
 * verdict, however recent that data looks.
 */
import { useQuery } from '@tanstack/react-query';
import { api } from '../utils/api';
import type { FamilyOperationsV2 } from '../types/familyOperations';

export const FAMILY_OPERATIONS_V2_PATH = '/api/v1/operations/family';
export const FAMILY_OPERATIONS_V2_KEY = ['family-operations-v2'];

/** Ten seconds: fast enough that a stopped system is noticed, slow enough to be free. */
export const FAMILY_OPERATIONS_POLL_MS = 10_000;

export function useFamilyOperationsV2() {
  return useQuery<FamilyOperationsV2>({
    queryKey: FAMILY_OPERATIONS_V2_KEY,
    queryFn: () => api.get<FamilyOperationsV2>(FAMILY_OPERATIONS_V2_PATH),
    refetchInterval: FAMILY_OPERATIONS_POLL_MS,
    staleTime: 0,
    // No retry: a safety screen that hides an outage behind three attempts is
    // showing a system state that may no longer exist.
    retry: false,
    refetchOnWindowFocus: true,
  });
}

/** Seconds since the payload was generated, or null when it cannot be computed. */
export function dataAgeSeconds(generatedAt: string | undefined, now: number = Date.now()): number | null {
  if (!generatedAt) return null;
  const parsed = Date.parse(generatedAt);
  if (Number.isNaN(parsed)) return null;
  return Math.max(0, Math.round((now - parsed) / 1000));
}
