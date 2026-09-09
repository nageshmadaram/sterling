import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../utils/api';

const KEY = ['gamma-move-config'];
const SNAPSHOT_KEY = ['gamma-move-snapshot'];
const BASE = '/api/v1/config/gamma-move';

export type LevelTimeframe = 'day' | '60minute' | '15minute';
export type TriggerTimeframe = '5minute' | '15minute' | '30minute';
export type ExitPolicy = 'TIME_STOP' | 'PERCENT_TARGET' | 'TRAILING_STOP';
export type StopBasis = 'POINTS' | 'PERCENT';
export type SizingMode = 'LOTS' | 'RISK_PCT';
export type StopMode = 'broker' | 'monitor' | 'both';
/** Shared with every other option engine — one vocabulary, one meaning. */
export type ExpirySelection = 'nearest' | 'weekly' | 'monthly' | 'any';
export type ExpirySeries = 'weekly' | 'monthly';
export type SignalState = 'watching' | 'armed' | 'running' | 'weakening' | 'ended' | 'error';
export type Regime = 'up' | 'down' | 'unknown';

/** Field names are identical to the Python dataclass, so a setting added on one
 *  side is readable on the other without a translation table to keep in sync. */
export interface GammaMoveConfig {
  enabled: boolean;
  auto_execute?: boolean;
  /** Same names, semantics and liquidity boundary as every other engine here. */
  scan_stocks: string[];
  scan_all_stocks: boolean;
  stock_contracts: boolean;
  scan_indices: string[];
  min_option_oi: number;
  min_option_volume: number;
  min_option_premium: number;
  max_spread_pct: number;
  level_timeframe: LevelTimeframe;
  level_lookback_days: number;
  pivot_lookback: number;
  level_cluster_pct: number;
  min_level_touches: number;
  level_proximity_pct: number;
  strike_window_pct: number;
  max_candidates: number;
  /** Source rule, not calibrated. Strike must be chain-max OI on that expiry+type. */
  require_chain_max_oi?: boolean;
  /** Source rule, not calibrated. Spot must have broken through (or sit at) the wall. */
  require_spot_through_strike?: boolean;
  /** Contract settings — same names as every other engine's. */
  expiry_selection: ExpirySelection;
  expiry_dte_min: number;
  expiry_dte_max: number;
  avoid_expiry_day: boolean;
  scan_expiries_indices: ExpirySeries[];
  scan_expiries_stocks: ExpirySeries[];
  /** Which listed contracts by RANK — the same storage the shared Option
   *  contracts picker writes, so it is the same control here as elsewhere. */
  scan_weekly_series_indices: number[];
  scan_monthly_series_indices: number[];
  scan_monthly_series_stocks: number[];
  trigger_timeframe: TriggerTimeframe;
  volume_lookback: number;
  min_oi_drop_pct: number;
  volume_spike_mult: number;
  min_price_gain_pct: number;
  confirm_bars: number;
  regime_enabled: boolean;
  regime_timeframe: LevelTimeframe;
  regime_period: number;
  regime_multiplier: number;
  stop_basis: StopBasis;
  swing_lookback: number;
  stop_percent: number;
  stop_points: number;
  exit_policy: ExitPolicy;
  max_hold_days: number;
  target_pct: number;
  trail_pct: number;
  trail_start_pct: number;
  close_at_session_end: boolean;
  stop_mode: StopMode;
  session_start: string;
  session_end: string;
  scan_interval_seconds: number;
  sizing_mode: SizingMode;
  risk_per_trade_pct: number;
  capital_inr: number;
  lots: number;
  max_concurrent_positions: number;
  max_new_trades_per_day: number;
  max_premium_at_risk_inr: number;
  daily_loss_limit_inr: number;
  descale_after_losses: number;
  descale_factor: number;
  rescale_after_wins: number;
  data_source: 'kite';
}

/**
 * Paper/live and manual/auto, read from where they actually live.
 *
 * Not part of `GammaMoveConfig` on purpose. Paper/live for Kite is the
 * account's `is_paper` and manual/auto is the engine's `auto_execute`; both are
 * shared by every Kite strategy and set from the Trading Mode panel. A copy on
 * this config would be a claim about them rather than the thing itself, and
 * could disagree with the client that actually places the order.
 */
export interface GammaModeState {
  is_paper: boolean;
  auto_execute: boolean;
  note: string;
}

export interface GammaMoveStrategyInfo {
  id: string;
  name: string;
  contract_version: string;
  tagline: string;
  how_it_works: string;
  provenance: string;
  /** Whether the strategy has been validated. False — see `headline_finding`.
   *  Deliberately NOT a lock: it informs the operator, it does not overrule
   *  them, and paper/live has its own switch elsewhere. */
  validated: boolean;
  enabled: boolean;
  /** What each measured default cost to establish, keyed by field name. */
  calibration: Record<string, string>;
  /** Fields whose defaults came from measurement rather than judgement. */
  calibrated_fields: string[];
  /** What the calibration means for the next trade, in one sentence. */
  headline_finding: string;
  /** The action it implies. Kept apart from the claim so the board can lead
   *  with what to do rather than with a statistic. */
  what_to_do?: string;
  /** The measurement behind the claim — intervals, sample, where to check it.
   *  Deliberately not the first thing shown: it belongs on a hover, not across
   *  the top of a trading screen. */
  evidence?: string;
  /** Snapshot occupancy for the two source-rule gates. Not a measured edge. */
  source_gates?: Record<string, string>;
}

export interface GammaMoveResponse {
  strategy: GammaMoveStrategyInfo;
  config: GammaMoveConfig;
  /** The engine's own defaults, published so the UI never mirrors a second copy. */
  defaults: GammaMoveConfig;
  vocabularies: Record<string, string[]>;
  /** Options live mode refuses. Greyed out rather than offered, so the operator
   *  never clicks a switch that validation will reject afterwards. */
  research_only: { exit_policy: string[] };
  /** Configured choices worth stating out loud. Computed by the engine, so the
   *  UI cannot invent its own list or let one go stale. */
  warnings: string[];
}

export interface TriggerMetrics {
  oi_drop_pct: number;
  volume_ratio: number;
  price_gain_pct: number;
  unwinding: boolean;
  abnormal: boolean;
  rising: boolean;
  bars_confirmed: number;
  bars_required: number;
  triggered: boolean;
}

export interface GammaSignalRow {
  id: string;
  state: SignalState;
  at_ms: number;
  underlying: string;
  regime: Regime;
  reason: string | null;
  exit_reason: string | null;
  entry_day: string | null;
  instrument: {
    instrument_id: string; tradingsymbol: string; option_type: 'CE' | 'PE';
    strike: number; expiry: string; lot_size: number; tick_size: number; exchange: string;
  };
  level: { price: number; kind: 'support' | 'resistance'; touches: number; distance_pct: number };
  oi: number;
  days_to_expiry: number;
  spot: number;
  metrics: TriggerMetrics | null;
  /** Every level is nullable: a missing number must render as "—", never as 0. */
  levels: {
    ltp: number | null; entry: number | null; stop: number | null;
    trail: number | null; target: number | null; exit: number | null;
  };
  sizing: {
    lots: number | null; quantity: number | null;
    at_risk_inr: number | null; deployed_inr: number | null;
  };
}

export interface GammaPositionRow {
  symbol: string; signal_id: string; entry: number; stop: number;
  trail: number | null; target: number | null; quantity: number; lots: number;
  entered_ms: number; entry_day: string; sessions_held: number;
  exiting: boolean; high_water: number;
  /** Broker reality. `entry` is what we intended; `fill_price` is what happened. */
  status: 'pending' | 'open' | 'closed' | 'rejected';
  order_id: string;
  fill_price: number;
  effective_entry: number;
  /** Broker-side GTT id. 0 means nothing at the broker — this process is the
   *  only thing watching the position. */
  gtt_id: number;
  stop_mode: StopMode;
}

export interface GammaScanState {
  last_run_ms?: number;
  stage_a?: { scanned: number; near_level: number; seconds: number };
  stage_b?: { candidates: number; seconds: number };
  stage_c?: { watched: number; armed: number; historical_requests: number; seconds: number };
  total_seconds?: number;
}

export interface GammaTradeRecord {
  trades: number; wins: number; losses: number;
  win_rate: number | null;
  consecutive_losses: number; consecutive_wins: number;
  realised_inr: number; day_realised_inr: number; day: string;
  verdict: string;
}

export interface GammaSessionStatus {
  day: string;
  phase: string;
  halt_reason: string;
  trades_today: number;
  candidates: GammaSignalRow[];
  positions: GammaPositionRow[];
  record: GammaTradeRecord;
  subscribed: number[];
  notes: { kind: string; message: string; at_ms: number }[];
}

export interface GammaMoveSnapshot {
  strategy: GammaMoveStrategyInfo;
  config: GammaMoveConfig;
  scan: GammaScanState;
  session: GammaSessionStatus | null;
  simulation: Record<string, unknown> | null;
  candidates: GammaSignalRow[];
  positions: GammaPositionRow[];
  record: GammaTradeRecord;
  orphan_positions: { symbol: string; quantity: number; entry_price: number }[];
  universe?: { underlyings: number; sample: string[] };
  mode?: GammaModeState;
  /** Configured risks, as sentences. Distinct from `blockers`, which are
   *  reasons nothing can happen at all. */
  warnings?: string[];
  /** Every reason nothing is armed. Rendered verbatim — a quiet engine that
   *  will not say why is what this list exists to prevent. */
  blockers: string[];
}

export function useGammaMoveConfig() {
  return useQuery<GammaMoveResponse>({
    queryKey: KEY,
    queryFn: () => api.get(BASE),
    staleTime: 30000,
  });
}

import { useReplayActive as useSimActive } from './useReplayStore';

export function useGammaMoveSnapshot(enabled = true, refetchInterval = 0) {
  const isSimActive = useSimActive();
  return useQuery<GammaMoveSnapshot>({
    queryKey: SNAPSHOT_KEY,
    queryFn: () => api.get(`${BASE}/snapshot`),
    enabled,
    staleTime: isSimActive ? 0 : 2000,
    refetchInterval: enabled ? (isSimActive ? 300 : (refetchInterval || 2000)) : false,
  });
}

export function useUpdateGammaMove() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: Partial<GammaMoveConfig>) => api.put(BASE, body),
    // Refetch rather than patch the cache: the server is the only thing that
    // knows what validation did to the value that was sent.
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: KEY });
      qc.invalidateQueries({ queryKey: SNAPSHOT_KEY });
    },
  });
}

export function useGammaMoveScan() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<{ scanned: number; armed: number }>(`${BASE}/scan`),
    onSuccess: () => qc.invalidateQueries({ queryKey: SNAPSHOT_KEY }),
  });
}

export function useArmGammaMove() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (signalId: string) =>
      api.post<{ ok: boolean; message?: string; symbol?: string; entry?: number; stop?: number }>(
        `${BASE}/arm`, { signal_id: signalId }),
    onSuccess: () => qc.invalidateQueries({ queryKey: SNAPSHOT_KEY }),
  });
}
