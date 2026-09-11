import { useEngineConfig, usePatchEngineConfig } from './useSterlingKiteEngine';
import { useNavigatorConfig, useSetNavigatorConfig } from './useNavigator';
import { useGammaMoveConfig, useUpdateGammaMove } from './useGammaMove';
import { useAdaptiveEdgeEngineConfig, useSetAdaptiveEdgeEngineConfig } from './useAdaptiveEdge';
import { useIntradayConfig, useUpdateIntraday } from './useIntraday';

export type EngineToggleId =
  | 'supertrend' | 'navigator' | 'gamma_move' | 'adaptive_edge' | 'intraday';

export interface EngineToggle {
  id: EngineToggleId;
  label: string;
  enabled: boolean;
  pending: boolean;
  toggle: (() => void) | null;
  description: string;
}

export function useEngineEnabled(): Record<EngineToggleId, boolean> {
  const st = useEngineConfig();
  const nav = useNavigatorConfig();
  const gm = useGammaMoveConfig();
  const ae = useAdaptiveEdgeEngineConfig();
  const id = useIntradayConfig();

  return {
    supertrend: st.data?.engine_enabled !== false,
    navigator: nav.data?.record ? !!nav.data.record.config.enabled : true,
    gamma_move: (gm.data?.config as { enabled?: boolean } | undefined)?.enabled !== false,
    adaptive_edge: (ae.data?.config as { enabled?: boolean } | undefined)?.enabled !== false,
    intraday: id.data?.config?.enabled !== false,
  };
}

export function useEngineToggles(): EngineToggle[] {
  const on = useEngineEnabled();
  const st = useEngineConfig();
  const stSet = usePatchEngineConfig();

  const nav = useNavigatorConfig();
  const navSet = useSetNavigatorConfig();

  const gm = useGammaMoveConfig();
  const gmSet = useUpdateGammaMove();

  const ae = useAdaptiveEdgeEngineConfig();
  const aeSet = useSetAdaptiveEdgeEngineConfig();

  const idCfg = useIntradayConfig();
  const idSet = useUpdateIntraday();

  const stOn = on.supertrend;
  const navRecord = nav.data?.record;
  const navOn = on.navigator;
  const gmOn = on.gamma_move;
  const aeOn = on.adaptive_edge;
  const idOn = on.intraday;

  return [
    {
      id: 'supertrend',
      label: 'SuperTrend engine',
      enabled: stOn,
      pending: stSet.isPending,
      toggle: st.data ? () => stSet.mutate({ engine_enabled: !stOn }) : null,
      description: stOn
        ? 'Scanning, producing signals, and eligible for automatic execution.'
        : 'Off — no SuperTrend scanning and no SuperTrend signals.',
    },
    {
      id: 'navigator',
      label: 'Value-Flow Navigator',
      enabled: navOn,
      pending: navSet.isPending,
      toggle: navRecord
        ? () => navSet.mutate({
            config: { ...navRecord.config, enabled: !navOn },
            expected_revision: navRecord.revision,
          })
        : null,
      description: navOn
        ? 'On. It can confirm SuperTrend setups and originate its own.'
        : 'Off — no Navigator evidence and no Navigator-originated setups.',
    },
    {
      id: 'gamma_move',
      label: 'Gamma Move',
      enabled: gmOn,
      pending: gmSet.isPending,
      toggle: gm.data ? () => gmSet.mutate({ enabled: !gmOn }) : null,
      description: gmOn
        ? 'Watching open-interest unwind around the levels.'
        : 'Off — no OI unwind scanning and no Gamma Move signals.',
    },
    {
      id: 'adaptive_edge',
      label: 'Adaptive Edge',
      enabled: aeOn,
      pending: aeSet.isPending,
      toggle: ae.data ? () => aeSet.mutate({ enabled: !aeOn }) : null,
      description: aeOn
        ? 'Order-flow scalping. Signals only — live execution stays gated.'
        : 'Off — no order-flow scanning and no Adaptive Edge candidates.',
    },
    {
      id: 'intraday',
      label: 'Intraday pack',
      enabled: idOn,
      pending: idSet.isPending,
      toggle: idCfg.data ? () => idSet.mutate({ enabled: !idOn }) : null,
      description: idOn
        ? 'Pivot Break, MA Ribbon and VWAP SuperTrend on 5-minute candles. Signals only.'
        : 'Off — none of the three intraday strategies scans or signals.',
    },
  ];
}

export default useEngineToggles;
