/**
 * The intraday pack — three 5-minute strategies behind one config and one scan.
 *
 * Field names are identical to the Python dataclass, so a setting added on one
 * side is readable on the other without a translation table to keep in sync.
 * Defaults, enums and the eligible universe are READ from the server rather
 * than typed here: the recurring bug in this codebase is a UI that claims
 * backend behaviour the backend does not honour.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../utils/api';
import { useReplayActive as useSimActive } from './useReplayStore';

const KEY = ['intraday-config'];
const SNAPSHOT_KEY = ['intraday-snapshot'];
const POSITIONS_KEY = ['intraday-positions'];
const BASE = '/api/v1/config/intraday';

export type IntradayStrategyId = 'pivot_break' | 'ma_ribbon' | 'vwap_supertrend';
export type Timeframe = '1m' | '3m' | '5m' | '10m' | '15m' | '30m';
export type PivotType = 'fibonacci' | 'classic';
export type PivotPeriod = 'day' | 'week';
export type TrailMode = 'none' | 'breakeven' | 'atr' | 'structure';
export type StopSource = 'vwap' | 'supertrend' | 'wider';
export type SizingMode = 'RISK_PCT' | 'LOTS';
export type StopMode = 'broker' | 'monitor' | 'both';
/** `ended` is a replayed row: the same rules on the same bars, not traded. */
export type SignalState = 'armed' | 'watching' | 'ended';

export interface IntradayConfig {
  enabled: boolean;
  auto_execute: boolean;
  timeframe: Timeframe;
  scan_indices: string[];
  scan_stocks: string[];
  scan_all_stocks: boolean;
  warmup_bars: number;
  session_start: string;
  no_entry_after: string;
  session_end: string;
  cooldown_bars: number;
  max_signals_per_symbol_per_day: number;
  expiry_series_indices: string[];
  expiry_series_stocks: string[];
  moneyness: string;
  min_option_oi: number;
  min_option_volume: number;
  min_option_premium: number;
  max_spread_pct: number;

  sizing_mode: SizingMode;
  risk_per_trade_pct: number;
  capital_inr: number;
  lots: number;
  max_lots: number;
  allow_min_lot_over_risk: boolean;

  stop_mode: StopMode;
  premium_stop_pct: number;
  premium_trail_pct: number;
  trail_activate_r: number;
  close_at_session_end: boolean;

  max_concurrent_positions: number;
  max_new_trades_per_day: number;
  daily_loss_limit_inr: number;
  descale_after_losses: number;
  scan_interval_seconds: number;

  dynamic_stops: boolean;
  stop_atr_floor_mult: number;
  stop_atr_cap_mult: number;
  dynamic_targets: boolean;
  target_atr_mult: number;

  pb_enabled: boolean;
  pb_ema_length: number;
  pb_pivot_type: PivotType;
  pb_pivot_period: PivotPeriod;
  pb_min_body_pct: number;
  pb_min_body_atr: number;
  pb_require_fresh_break: boolean;
  pb_break_buffer_atr: number;
  pb_atr_length: number;
  pb_target_r: number;
  pb_target2_r: number;
  pb_trail_mode: TrailMode;
  pb_breakeven_at_r: number;
  pb_trail_atr_mult: number;
  pb_max_stop_pct: number;

  rb_enabled: boolean;
  rb_ema_fast: number;
  rb_ema_1: number;
  rb_ema_2: number;
  rb_ema_slow: number;
  rb_require_full_cross: boolean;
  rb_confirm_bars: number;
  rb_min_spread_pct: number;
  rb_exit_on_opposite_cross: boolean;
  rb_stop_atr_mult: number;
  rb_atr_length: number;
  rb_target_r: number;

  vs_enabled: boolean;
  vs_atr_length: number;
  vs_factor: number;
  vs_target_points: number;
  vs_stop_source: StopSource;
  vs_max_stop_points: number;
  vs_min_stop_points: number;
  vs_trail_after_points: number;
  vs_require_volume_vwap: boolean;
  vs_require_fresh_flip: boolean;
  vs_confirm_within_bars: number;
  vs_dynamic_target: boolean;
  vs_target_atr_mult: number;
}

/** What the walk-forward harness found for one strategy. */
export interface IntradayValidation {
  strategy: string;
  promoted: boolean;
  measured_at: string;
  oos_trades: number;
  oos_net: number;
  sharpe: number;
  deflated_sharpe: number;
  permutation_p: number | null;
  max_drawdown_pct: number;
  checks: Record<string, boolean>;
  reasons: string[];
  /** A promotion at 0.05% slippage says nothing about a book paying 0.5%. */
  slippage_pct: number | null;
  symbols: string[];
}

export interface IntradayStrategyMeta {
  id: IntradayStrategyId;
  name: string;
  tag: string;
  tagline: string;
  how_it_works: string;
  /** A MEASUREMENT, read from the harness record — never hardcoded. */
  validated?: boolean;
  validation?: IntradayValidation | null;
}

export interface IntradayDescriptor {
  id: string;
  name: string;
  contract_version: string;
  tagline: string;
  strategies: IntradayStrategyMeta[];
  provenance: string;
  /** False for this pack, and the UI must say so rather than imply a result. */
  validated: boolean;
  calibration: Record<string, string>;
  /** Empty here. Every number is a judgement call, and the settings say so. */
  calibrated_fields: string[];
  enabled?: boolean;
}

export interface IntradaySignalRow {
  strategy: IntradayStrategyId;
  symbol: string;
  direction: 'BULLISH' | 'BEARISH';
  opt_type: 'CE' | 'PE';
  timestamp_ms: number;
  entry: number | null;
  stop: number | null;
  target: number | null;
  target2: number | null;
  risk: number | null;
  rr: number | null;
  strength: string;
  origin: string;
  reasons: string[];
  metrics: Record<string, unknown>;
}

export interface IntradayContract {
  symbol: string;
  strike: number;
  option_type: 'CE' | 'PE';
  /** Null on a replayed row: the strike is determined, the expiry is not. */
  expiry: string | null;
  dte: number | null;
  lot_size: number;
  token: number;
  exchange: string;
  /**
   * The strike was computed arithmetically (spot rounded to the instrument's
   * published step) rather than resolved against the broker's instrument list.
   * True only on replayed history, and the row says so.
   */
  estimated?: boolean;
  moneyness?: string | null;
}

/** What a historical signal went on to do. The replay already knows, and
 *  "a signal we would have taken" is much less useful than "and here is where
 *  it came out". */
export interface IntradayOutcome {
  exit: number;
  reason: string;
  points: number;
  r: number;
  bars_held: number;
  exit_ms: number;
}

export interface IntradayRow {
  /** The id the SCAN minted. Arming takes this, because arming something the
   *  scan did not produce is exactly what the route refuses. */
  signal_id: string | null;
  strategy: IntradayStrategyId;
  strategy_name: string;
  symbol: string;
  state: SignalState;
  blockers: string[];
  spot: number | null;
  timeframe: string;
  contract: IntradayContract | null;
  generated_at_ms: number;
  signal: IntradaySignalRow | null;
  metrics: Record<string, unknown>;
  /** Replayed from stored bars rather than scanned live. */
  historical?: boolean;
  outcome?: IntradayOutcome | null;
  underlying_token?: number;
  quote?: { premium: number; spread_pct: number | null; blockers: string[] } | null;
}

export interface IntradayContractRef {
  tradingsymbol: string;
  exchange: string;
  token: number;
  option_type: 'CE' | 'PE';
  strike: number;
  expiry: string;
  lot_size: number;
  tick_size: number;
}

/**
 * A live position, with BOTH ladders.
 *
 * `spot_*` is the strategy's thesis — what the rule actually said. `entry`,
 * `stop` and `target` are the money. They are separate because the premium can
 * round-trip while the spot rule is still perfectly intact, and that gap is
 * where an open drawdown builds.
 *
 * `side` is always "long": every one of these three BUYS an option, whichever
 * way the thesis points.
 */
export interface IntradayPosition {
  strategy: string;
  signal_id: string;
  underlying: string;
  contract: IntradayContractRef;
  thesis: 'BULLISH' | 'BEARISH';
  side: 'long';
  spot_entry: number;
  spot_stop: number;
  spot_target: number;
  spot_target2: number | null;
  spot_risk: number;
  entry: number;
  stop: number;
  initial_stop: number;
  target: number;
  /** The runner's objective. 0 on the two strategies with one target. */
  target2: number;
  quantity: number;
  lots: number;
  fill_price: number;
  effective_entry: number;
  peak: number;
  breakeven_done: boolean;
  /** The first target was reached and the runner leg is what is left. */
  target1_done: boolean;
  scaled_qty: number;
  scaled_price: number;
  banked_inr?: number;
  exiting: boolean;
  order_id: string;
  gtt_id: number;
  /** Whether the stop is actually resting at Zerodha, or only in this process. */
  broker_stop: boolean;
  stop_mode: string;
  status: 'pending' | 'open' | 'closed' | 'rejected';
  is_open: boolean;
  entered_ms: number;
  entry_day: string;
  exit_price: number;
  exit_reason: string;
  realised_inr: number;
}

export interface IntradayRecord {
  day: string;
  trades: number;
  wins: number;
  realised_inr: number;
  consecutive_losses: number;
  win_rate: number | null;
}

export interface IntradaySnapshot {
  strategy: IntradayDescriptor;
  config: IntradayConfig;
  warnings: string[];
  enabled_strategies: IntradayStrategyId[];
  rows: IntradayRow[];
  /** The last few sessions, replayed. Keeps the board readable when the live
   *  answer is "nothing on this bar", which it usually is. */
  history?: IntradayRow[];
  history_sessions?: number;
  armed: number;
  scanned: number;
  scanning: boolean;
  failures: string[];
  last_scan_ms: number;
  last_error: string | null;
  generated_at_ms: number;
  source?: string;
  positions?: IntradayPosition[];
  open_positions?: number;
  record?: IntradayRecord;
  /** Read from the ACCOUNT and the shared engine, never this page's own copy. */
  mode?: { is_paper?: boolean; auto_execute?: boolean };
  notes?: { kind: string; message: string; at_ms: number }[];
  /** Why the next entry would be refused, answered before the click. */
  entry_blocker?: string | null;
}

export interface IntradayConfigResponse {
  strategy: IntradayDescriptor;
  config: IntradayConfig;
  defaults: IntradayConfig;
  enabled_strategies: IntradayStrategyId[];
  vocabularies: Record<string, string[]>;
  warnings: string[];
}

export function useIntradayConfig() {
  return useQuery<IntradayConfigResponse>({
    queryKey: KEY,
    queryFn: () => api.get(BASE),
    staleTime: 30000,
  });
}

export function useIntradaySnapshot(enabled = true, refetchInterval = 0) {
  const isSimActive = useSimActive();
  return useQuery<IntradaySnapshot>({
    queryKey: SNAPSHOT_KEY,
    queryFn: () => api.get(`${BASE}/snapshot`),
    enabled,
    staleTime: isSimActive ? 0 : 2000,
    refetchInterval: enabled ? (isSimActive ? 300 : (refetchInterval || 5000)) : false,
  });
}

export function useUpdateIntraday() {
  const qc = useQueryClient();
  return useMutation({
    // Refetch rather than patch the cache: only the server knows what
    // validation did to the value that was sent.
    mutationFn: (body: Partial<IntradayConfig>) => api.put(BASE, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: KEY });
      qc.invalidateQueries({ queryKey: SNAPSHOT_KEY });
    },
  });
}

export function useIntradayScan() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<IntradaySnapshot>(`${BASE}/scan`),
    onSuccess: () => qc.invalidateQueries({ queryKey: SNAPSHOT_KEY }),
  });
}


export function useIntradayArm() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (signalId: string) =>
      api.post<{ ok: boolean; message?: string; symbol?: string; quantity?: number;
                 entry?: number; stop?: number; target?: number; gtt_id?: number;
                 paper?: boolean; sizing?: string }>(
        `${BASE}/arm`, { signal_id: signalId }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: SNAPSHOT_KEY });
      qc.invalidateQueries({ queryKey: POSITIONS_KEY });
    },
  });
}

export function useIntradayExit() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (symbol: string) =>
      api.post<{ ok: boolean; message?: string }>(`${BASE}/exit`, { symbol }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: SNAPSHOT_KEY });
      qc.invalidateQueries({ queryKey: POSITIONS_KEY });
    },
  });
}

export function useIntradaySquareOff() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<{ ok: boolean; closed: string[] }>(`${BASE}/square-off`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: SNAPSHOT_KEY });
      qc.invalidateQueries({ queryKey: POSITIONS_KEY });
    },
  });
}

export function useIntradayReconcile() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<{ restored: number; vanished: number; gone: string[];
                                 reprotected: number; error?: string }>(
      `${BASE}/reconcile`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: SNAPSHOT_KEY });
      qc.invalidateQueries({ queryKey: POSITIONS_KEY });
    },
  });
}

export function useIntradayPositions(enabled = true, refetchInterval = 0) {
  return useQuery<{ positions: IntradayPosition[]; realised_pnl_today: number;
                    record: IntradayRecord }>({
    queryKey: POSITIONS_KEY,
    queryFn: () => api.get(`${BASE}/positions`),
    enabled,
    refetchInterval: enabled ? (refetchInterval || 5000) : false,
  });
}
