import { useQuery } from '@tanstack/react-query';
import { api } from '../utils/api';
import { toOrbFeedEntries, type OrbFeedEntry } from '../utils/niftyOrbSignalAdapter';

import { useReplayActive as useSimActive } from './useReplayStore';

export function useOrbSignals(enabled = true) {
  const isSimActive = useSimActive();
  const query = useQuery({
    queryKey: ['nifty-orb-options-scan'],
    queryFn: async (): Promise<OrbFeedEntry[]> => {
      const payload = await api.post('/api/v1/config/nifty-orb-options/scan', {});
      return toOrbFeedEntries(payload);
    },
    enabled,
    refetchInterval: enabled ? (isSimActive ? 300 : 5000) : false,
    refetchIntervalInBackground: false,
    staleTime: isSimActive ? 0 : 5000,
    retry: 1,
  });

  return {
    ...query,
    signals: query.data ?? [],
    isRefreshing: query.isFetching,
  };
}
