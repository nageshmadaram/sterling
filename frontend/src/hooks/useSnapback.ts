/**
 * Snapback — the only engine here that reads DAILY bars.
 *
 * Field names are identical to the Python dataclass, so a setting added on one
 * side is readable on the other without a translation table. Defaults, enums
 * and the eligible universe are READ from the server rather than typed here:
 * the recurring bug in this codebase is a UI that claims backend behaviour the
 * backend does not honour.
 *
 * The refetch cadence is deliberately slow. Every rule in this engine is stated
 * on a daily CLOSE, so nothing it watches can change intraday, and polling it
 * like a tick-driven board would spend a rate limit to redraw the same rows.
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../utils/api';

const KEY = ['snapback-config'];
const SNAPSHOT_KEY = ['snapback-snapshot'];
const VALIDATION_KEY = ['snapback-validation'];
const HISTORY_KEY = ['snapback-history'];
const BASE = '/api/v1/config/snapback';

export type SnapbackSide = 'fade_up' | 'fade_down';
export type ExitMode = 'horizon' | 'mean_touch' | 'either';
export type SizingMode = 'PREMIUM_PCT' | 'LOTS';
export type StopMode = 'broker' | 'monitor' | 'both';
export type SignalState = 'armed' | 'watching' | 'running' | 'ended';

export interface SnapbackConfig {
  enabled: boolean;
  auto_execute: boolean;

  universe_mode: 'curated' | 'fno';
  max_universe: number;
  market_filter: 'off' | 'bearish' | 'bullish';
  market_ema: number;
  hedge_mode: 'none' | 'index_futures';

  scan_indices: string[];
  scan_stocks: string[];
  scan_stock_contracts: boolean;

  lookback_days: number;
  min_stretch_atr: number;
  allow_fade_down: boolean;
  cooldown_days: number;
  min_atr_bp: number;

  target_delta: number;
  min_dte: number;
  max_dte: number;
  expiry_series_indices: string[];
  expiry_series_stocks: string[];
  min_option_premium: number;
  max_spread_pct: number;
  min_option_oi: number;

  exit_mode: ExitMode;
  hold_days: number;
  premium_stop_pct: number;
  premium_trail_pct: number;
  runner_mult: number;
  runner_trail_pct: number;
  mean_touch_ema: number;

  sizing_mode: SizingMode;
  premium_pct_of_capital: number;
  capital_inr: number;
  lots: number;
  max_lots: number;
  max_open_positions: number;
  one_position_per_underlying: boolean;
  stop_mode: StopMode;

  rv_window: number;
  assumed_vrp: number;
  smile_slope: number;
  smile_itm_slope: number;
  short_leg_delta: number;
}

export interface SnapbackContract {
  symbol: string;
  strike: number;
  option_type: 'CE' | 'PE';
  expiry: string | null;
  dte: number | null;
  lot_size: number;
  token: number;
  exchange: string;
  delta?: number;
  moneyness?: string;
}

export interface SnapbackQuote {
  premium: number | null;
  bid: number | null;
  ask: number | null;
  ltp: number | null;
  oi: number | null;
  spread_pct: number | null;
  blockers: string[];
}

export interface SnapbackOutcome {
  /** Still inside its holding period — a position, not a record. */
  open: boolean;
  entry_premium: number;
  exit_premium: number;
  exit_day: string;
  exit_ms: number;
  reason: string;
  held_days: number;
  sessions_left: number;
  net: number;
  return_pct: number;
  spot_out: number;
  /** The trade's beta to the index, and what the hedge removed and charged.
   *  Null when the trade ran unhedged. */
  beta: number | null;
  market_pnl: number | null;
  hedge_cost: number | null;
  net_unhedged: number | null;
}

export interface SnapbackRow {
  signal_id: string;
  strategy: 'snapback';
  side: SnapbackSide;
  symbol: string;
  state: SignalState;
  reason: string | null;
  direction: 'BULLISH' | 'BEARISH';
  opt_type: 'CE' | 'PE';
  timestamp_ms: number;
  spot: number;
  mean_target: number;
  distance_pct: number;
  stretch: number;
  level: number;
  strength: 'STRONG' | 'MODERATE' | 'WATCHING';
  realized_vol_pct: number;
  assumed_iv_pct: number;
  assumed_vrp: number;
  hold_days: number;
  underlying_token: number;
  contract: SnapbackContract | null;
  premium: number | null;
  /** True when `premium` came out of Black-Scholes rather than off a book. */
  premium_is_modelled: boolean;
  modelled_premium: number | null;
  quote: SnapbackQuote | null;
  stop_premium: number | null;
  /** What the contract is worth if spot returns to the mean — the thesis' own
   *  objective, priced at the entry's vol. */
  target_premium: number | null;
  /** Where the premium ratchet sits. Null when no trail is configured. */
  trail_premium: number | null;
  /** Above this premium the trade is HELD past the horizon under a give-back
   *  ratchet instead of being closed. Null when the runner is off. */
  runner_premium: number | null;
  /** What the trade actually did. Present on replayed rows only. */
  outcome: SnapbackOutcome | null;
  lots: number;
  quantity: number;
  deployed_inr: number | null;
  /** What ONE lot costs, in premium. The number that decides affordability. */
  min_outlay_inr: number | null;
  /** True when this row was replayed from stored bars rather than scanned. */
  historical?: boolean;
  reasons: string[];
  metrics: Record<string, unknown>;
}

/** What the walk-forward harness found. Carries the checks that PASSED too. */
export interface SnapbackValidation {
  promoted: boolean;
  measured_at: string;
  span: string;
  universe: string[];
  oos_trades: number;
  oos_entry_days: number;
  oos_mean_day_return_pct: number;
  oos_ci_pct: number[];
  sharpe: number;
  deflated_sharpe: number;
  permutation_p: number | null;
  permutation_p_full_sample: number | null;
  breakeven_vrp: number | null;
  max_drawdown_pct: number;
  allocation_pct: number;
  total_return_pct: number;
  per_year_pct: Record<string, number>;
  checks: Record<string, boolean>;
  reasons: string[];
  slippage_pct: number | null;
  passed: number;
  total_checks: number;
}

export interface SnapbackSideDescriptor {
  id: SnapbackSide;
  name: string;
  tag: string;
  option_type: 'CE' | 'PE';
  tagline: string;
  how_it_works: string;
  evidence: string;
}

export interface SnapbackDescriptor {
  id: string;
  name: string;
  contract_version: string;
  tagline: string;
  sides: SnapbackSideDescriptor[];
  provenance: string;
  validated: boolean;
  validation: SnapbackValidation | null;
  calibration: Record<string, string>;
  calibrated_fields: string[];
  vrp_band: number[];
  enabled?: boolean;
}

export interface SnapbackSnapshot {
  strategy: SnapbackDescriptor;
  config: SnapbackConfig;
  contract_version: string;
  rows: SnapbackRow[];
  armed: number;
  scanned: number;
  scanning: boolean;
  last_scan_ms: number;
  last_error: string | null;
  failures: string[];
  warnings: string[];
  auto_execution_blocker: string | null;
  catchup_sessions: number;
}

export interface SnapbackConfigResponse {
  strategy: SnapbackDescriptor;
  config: SnapbackConfig;
  defaults: SnapbackConfig;
  vocabularies: Record<string, string[]>;
  vrp_band: number[];
  warnings: string[];
}

export function useSnapbackConfig() {
  return useQuery<SnapbackConfigResponse>({
    queryKey: KEY,
    queryFn: () => api.get(BASE),
    staleTime: 60_000,
  });
}

export function useSnapbackSnapshot() {
  return useQuery<SnapbackSnapshot>({
    queryKey: SNAPSHOT_KEY,
    queryFn: () => api.get(`${BASE}/snapshot`),
    // Five minutes, not five seconds. A daily rule cannot change intraday, and
    // a board that polls faster than its own data moves burns a rate limit to
    // redraw identical rows.
    refetchInterval: 300_000,
    staleTime: 120_000,
  });
}

export function useSnapbackValidation() {
  return useQuery<{ record: SnapbackValidation | Record<string, never>;
                    auto_execution_blocker: string | null;
                    how_to_measure: string }>({
    queryKey: VALIDATION_KEY,
    queryFn: () => api.get(`${BASE}/validation`),
    staleTime: 600_000,
  });
}

/**
 * What the engine fired over the last few weeks, from stored bars.
 *
 * The board needs this because the live scan only looks at the last three
 * closed sessions and these rules fire on ONE. Without it the board is blank
 * almost always — and a blank board reads as a broken engine.
 */
export function useSnapbackHistory(sessions = 30) {
  return useQuery<{ sessions: number; signals: SnapbackRow[]; count: number }>({
    queryKey: [...HISTORY_KEY, sessions],
    queryFn: () => api.get(`${BASE}/history?sessions=${sessions}`),
    staleTime: 600_000,
  });
}

export function useUpdateSnapback() {
  const qc = useQueryClient();
  return useMutation({
    // Only what CHANGED is sent. A `{...cfg, ...values}` full-object write
    // silently reverts whatever moved since the cache was fetched, which is a
    // bug this repo has already shipped and had to remove everywhere.
    mutationFn: (body: Partial<SnapbackConfig>) => api.put(BASE, body),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: KEY });
      qc.invalidateQueries({ queryKey: SNAPSHOT_KEY });
    },
  });
}

export function useRunSnapbackScan() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<SnapbackSnapshot>(`${BASE}/scan`),
    onSuccess: (data) => {
      qc.setQueryData(SNAPSHOT_KEY, data);
      qc.invalidateQueries({ queryKey: KEY });
    },
  });
}
