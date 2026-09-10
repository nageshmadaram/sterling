// Types for the Kite-exclusive Sterling Kite Engine options engine.
// Mirrors backend app/engines/sterling_kite_engine/schemas.py.

import type { NavigatorDecision } from './navigator';

export type TrailTarget = 'fast' | 'mid' | 'slow';
export type Moneyness = 'ATM' | 'ITM1' | 'ITM2' | 'ITM3' | 'ITM4' | 'ITM5' | 'ITM10' | 'ITM15' | 'ITM20' | 'OTM1' | 'OTM2' | 'OTM3' | 'OTM4' | 'OTM5';
export type ScanSource = 'spot' | 'derivatives' | 'both' | 'confluence';
export type ScanExpiry = 'weekly' | 'monthly';
export type Vehicle = 'otm_options' | 'deep_itm_options' | 'futures';
export type DeepItmMoneyness = 'ITM5' | 'ITM10' | 'ITM15' | 'ITM20';
export type ExitMode = 'one_red' | 'two_red' | 'three_red' | 'three_red_signal';

export interface AlignmentChip {
  fast: number; // +1 / -1 / 0
  mid: number;
  slow: number;
}

export interface OptionLeg {
  moneyness: string; // ATM / ITM1 / ITM2 / OTM1 / OTM2
  option_type: string; // CE / PE
  option_symbol: string;
  strike: number;
  expiry: string;
  lot_size: number | null;
  premium_spot?: number;  // entry premium (Entry column)
  premium_sl?: number;    // live ratcheting trail stop (TSL column)
  entry_sl?: number;      // initial hard stop at the entry bar (SL column)
  // Premium level of the row's `target`. Navigator-originated rows only —
  // SuperTrend rows are trend-following and quote no fixed target.
  premium_target?: number | null;
  token?: number;
  is_active?: boolean; // this contract's SuperTrend still aligned on the latest bar
  signal_timestamp_ms?: number | null;
  entry_timestamp_ms?: number | null;
  alignment?: AlignmentChip | null;
  exit_state?: string | null;
  resolution_note?: string | null;
}

export interface SignalChartData {
  timestamp_ms: number;
  direction: string;
  regime: string;
  source?: 'spot' | 'derivatives' | 'confluence';
  premium_signal_ms?: number | null;
  marker_basis?: 'underlying' | 'premium' | 'external';
}

export interface EngineSignalRow {
  underlying: string;
  token: number;
  exchange: string; // option exchange (NFO / BFO)
  regime: 'BULL' | 'BEAR';
  alignment: AlignmentChip;
  direction: 'long' | 'short';
  option_type: 'CE' | 'PE';
  legs: OptionLeg[];
  spot: number;
  stop_loss: number;       // live ratcheting trail stop (TSL column)
  entry_sl?: number;       // initial hard stop at the entry bar (SL column)
  exit_state?: string;     // red-counter progress "<reds>/<threshold> red" (Exit column)
  // Why this entry ended, when it has. The red counter and the trailing stop are
  // independent rules and either can end a trade, so exit_state alone cannot explain
  // an ended row — it only reports the counter.
  exit_reason?: string | null;
  // Profit objective, same units as entry_sl. Always null for SuperTrend rows (their
  // exit is the trail + red counter); set for Navigator-originated rows from its
  // AVWAP stop/target proposal.
  target?: number | null;
  score: number;
  timestamp_ms: number;
  // "navigator" = Navigator Signal Origination — no SuperTrend trigger at all,
  // surfaced purely from Navigator's own AVWAP+volatility evidence.
  source?: 'spot' | 'derivatives' | 'confluence' | 'navigator';
  is_active?: boolean; // SuperTrend still aligned on the latest bar (trade running)
  is_fresh?: boolean;  // entered on the latest closed bar (the live "ready now" trigger)
  adx?: number | null;      // ADX at signal time (trend strength, 0–100)
  atr_pct?: number | null;  // ATR percentile at signal time (volatility rank)
  // Sterling Value-Flow Navigator (optional, off by default). Never changes
  // score/source/is_active/is_fresh above — those stay exactly as the base
  // engine computed them.
  navigator?: NavigatorDecision | null;
  resolution_reason?: string | null;
}

export interface SignalsResponse {
  generated_ms: number;
  scanning: boolean;
  scanning_label: string;
  rows: EngineSignalRow[];
  next_scan_ms: number;
  auto_scan: boolean;
  market_open: boolean;
}

export interface ActivityEvent {
  ts_ms: number;
  kind: string; // scan_start | scan_done | order_placed | order_blocked | order_failed | error | info
  message: string;
}

export interface ActivityResponse {
  events: ActivityEvent[];
  scanning: boolean;
  auto_scan: boolean;
  last_scan_ms: number;
  next_scan_ms: number;
  signal_count: number;
  scanning_label: string;
  market_open: boolean;
}

export interface SetupPoint {
  time: number; // epoch seconds
  open: number;
  high: number;
  low: number;
  close: number;
}

export interface SetupLine {
  time: number;
  value: number;
}

export interface SetupChart {
  underlying: string;
  candles: SetupPoint[];
  st_fast: SetupLine[];
  st_mid: SetupLine[];
  st_slow: SetupLine[];
  entry_index: number | null;
  trail_target: string;
  exit_mode?: string;
}

export interface DepthLevel {
  price: number;
  quantity: number;
  orders: number;
}

export interface OptionDetail {
  moneyness: string;
  option_type: string;
  option_symbol: string;
  strike: number;
  expiry: string;
  lot_size: number | null;
  dte: number;
  last_price: number;
  bid: number;
  ask: number;
  iv: number; // decimal
  delta: number;
  gamma: number;
  theta: number;
  vega: number;
  /** False when Black-Scholes could not be evaluated and `delta` is only the
   *  intrinsic sign (±1.00 / 0.00). Such a leg must never win a "best strike"
   *  badge — a fabricated 1.00 outranks every real delta. Optional so an older
   *  cached response defaults to trusting its greeks. */
  greeks_solved?: boolean;
  depth_buy: DepthLevel[];
  depth_sell: DepthLevel[];
  // The signal's own premium plan for this leg — the board's Entry / SL / TSL /
  // Target columns. null when the leg was never hydrated (option history empty and
  // the signal too old to honestly use today's LTP as its entry).
  entry_premium?: number | null;
  initial_stop_premium?: number | null;
  trail_stop_premium?: number | null;
  target_premium?: number | null;
  is_active?: boolean;
}

export interface EngineDetailResponse {
  underlying: string;
  token: number;
  exchange: string;
  direction: 'long' | 'short';
  regime: 'BULL' | 'BEAR';
  alignment: AlignmentChip;
  option_type: 'CE' | 'PE';
  triggered_ms: number;
  spot_at_trigger: number;
  spot_now: number;
  stop_loss: number;
  options: OptionDetail[];
  resolution_reason?: string | null;
  // Which engine owns this row. The dock opens from a board that mixes SuperTrend and
  // Navigator rows, so this decides what context and actions make sense to show.
  source?: 'spot' | 'derivatives' | 'confluence' | 'navigator';
  score?: number;
  entry_sl?: number | null;
  target?: number | null;
  exit_state?: string | null;
  exit_reason?: string | null;
  is_active?: boolean;
  is_fresh?: boolean;
  adx?: number | null;
  atr_pct?: number | null;
  navigator?: NavigatorDecision | null;
}

export interface EngineConfigModel {
  engine_enabled: boolean;
  trail_target: TrailTarget;
  exit_mode: ExitMode;
  // Opt-in: anchor the price stop to the exit_mode-th ST line (one_red→fast,
  // two_red→mid, three_red→slow) instead of always the tightest. Default off =
  // validated fast trail. Changes the computed stop → a scan-affecting setting.
  exit_aligned_trail?: boolean;
  // Enforce the trailing stop as a real exit (default on). Off = the old
  // red-counter-only rule, where a trade could sit indefinitely below its own stop.
  price_stop_exit?: boolean;
  strike_moneyness: Moneyness[];
  scan_source: ScanSource;
  scan_expiries: ScanExpiry[];
  scan_expiries_indices?: ScanExpiry[] | null;
  scan_expiries_stocks?: Array<'monthly'> | null;
  // The API persists zero-based series ranks for compatibility. The UI resolves
  // these private values to exact Kite-listed contract dates before displaying them.
  /** The expiry window, shared by every engine under these names. */
  expiry_dte_min?: number;
  expiry_dte_max?: number;
  avoid_expiry_day?: boolean;
  scan_weekly_series_indices?: number[];
  scan_monthly_series_indices?: number[];
  scan_monthly_series_stocks?: number[];
  scan_indices: string[];
  scan_stocks: string[];
  scan_all_stocks: boolean;
  /** Master switch above the stock list. False leaves single-stock underlyings
   *  out of the scan entirely — no stock contracts, no stock rows. */
  scan_stock_contracts?: boolean;
  auto_execute: boolean;
  // Per-trade risk sizing (workstream F)
  risk_sizing: boolean;
  risk_pct: number;
  max_lots: number;
  /** Take the smallest lot even when it breaks risk_pct. Off = skip the entry. */
  allow_min_lot_over_risk?: boolean;
  /** 1H bars a contract's own last bar may lag the underlying's and still auto-execute. */
  max_contract_staleness_bars?: number;
  // ── Exit / auto-exec guards (all default off / conservative) ───────────────
  // Square off an option this many calendar days before expiry (0 = off; options only).
  expiry_square_off_days?: number;
  // Square off a held position after this many 1H bars (0 = off; opt-in theta cap).
  time_stop_bars?: number;
  // Block NEW auto-exec entries in the last N minutes before the 15:30 close (0 = off).
  block_entry_minutes_before_close?: number;
  // Skip an auto-exec entry whose leg is too illiquid: spread wider than this % of mid
  // (null = off) or open interest below this floor (null = off).
  max_spread_pct?: number | null;
  min_oi?: number | null;
  // Halt NEW auto-exec entries once realized losses for the IST day reach this % of
  // F&O capital (null = off; never force-closes).
  max_daily_loss_pct?: number | null;
  // Protective stop mode (workstreams C/D)
  stop_mode: 'broker' | 'monitor' | 'both';
  /** Arm a hand-placed order the same way an auto-executed one is armed, using the
   *  board's own stop for that contract. Off means a manual entry is yours to manage
   *  and the order response says UNPROTECTED rather than implying a stop. */
  protect_manual_orders?: boolean;
  // ── Directional mode (additive, opt-in) ────────────────────────────────
  directional_mode: boolean;
  vehicle: Vehicle;
  enabled_vehicles: Vehicle[];
  itm_depth: DeepItmMoneyness | null;
  target_delta: number | null;
  futures_expiry: 'near' | 'next';
  adx_min: number | null;
  atr_pct_min: number | null;
  wire_risk_infra: boolean;
}

export interface ExpiryCalendarEntry {
  name: string;
  display_name: string;
  weekly: string[];
  monthly: string[];
}

export interface ExpiryCalendarResponse {
  as_of: string;
  source: 'kite_instruments';
  indices: ExpiryCalendarEntry[];
  stocks: ExpiryCalendarEntry[];
}

// ─── Options backtest (workstream H) ─────────────────────────────────────────
export type BacktestDataMode = 'synthetic' | 'real' | 'both';

export interface BacktestRequest {
  symbol: string;
  data_mode: BacktestDataMode;
  trail_target: TrailTarget;
  exit_mode: ExitMode;
  lookback_bars: number;
  starting_capital: number;
  qty: number;
  iv: number;
  dte_days: number;
  moneyness_offset_pct: number;
  slippage_pct?: number | null;
  brokerage_per_order?: number | null;
}

export interface BacktestTrade {
  entry_ms: number;
  exit_ms: number;
  direction: string;
  entry_premium: number;
  exit_premium: number;
  qty: number;
  gross_pnl: number;
  costs: number;
  net_pnl: number;
  bars_held: number;
  exit_reason: string;
}

export interface BacktestStats {
  trades: number;
  wins: number;
  losses: number;
  win_rate: number;
  gross_pnl: number;
  total_costs: number;
  net_pnl: number;
  profit_factor: number;
  expectancy: number;
  avg_win: number;
  avg_loss: number;
  max_drawdown: number;
  sharpe: number;
  return_pct: number;
  final_capital: number;
}

export interface BacktestRun {
  mode: string;
  caveat: string;
  trades: BacktestTrade[];
  equity_curve: number[];
  stats: BacktestStats;
}

export interface BacktestResponse {
  symbol: string;
  data_mode: string;
  generated_ms: number;
  runs: BacktestRun[];
  bs_vs_real_drift_pct?: number | null;
  notes: string[];
}

export interface EngineOrderRequest {
  option_symbol: string;
  exchange: string;
  side: 'BUY' | 'SELL';
  quantity: number;
  order_type?: string;
  product?: string;
}

export interface EngineOrderResponse {
  order_id: string;
  status: string;
  message: string;
  /** Whether anything will exit this position without you acting. A hand-placed
   *  order is armed from the board's own plan for that contract; when it cannot be
   *  (contract not on the board, no premium stop, protection switched off) the
   *  order still goes through and this is false. */
  protected?: boolean;
  /** Plain-language description of what was armed, or why nothing was. */
  protection?: string;
}

// ─── Stock registry ────────────────────────────────────────────────────────
export type LiquidityLevel = 'Very High' | 'High' | 'Good' | 'Moderate' | 'Moderate-Good';

export interface StockEntry {
  name: string;
  label: string;
  liquidity: LiquidityLevel;
  volatility: string;
  indices: string;
  why: string;
}

export interface LiquidityGroup {
  liquidity: string;
  stocks: StockEntry[];
}

// ─── Engine open positions ────────────────────────────────────────────────────
export type EngineVehicle = 'otm_options' | 'deep_itm_options' | 'futures';

export interface EngineOpenPosition {
  symbol: string;
  exchange: string;
  token: number;
  qty: number;
  lot_size: number;
  entry_premium: number;
  fill_price: number;
  stop_premium: number;
  status: string;
  direction: 'long' | 'short';
  vehicle: EngineVehicle;
  underlying: string;
  opened_ms: number;
  exit_reason: string;
  order_id: string;
  exit_mode?: string;  // the chosen exit counter at entry (one_red etc) — persisted per position
  current_red_count?: number;
  exit_threshold?: number;
  exit_pending?: boolean;
  pnl_reconciliation_required?: boolean;
}

export interface OpenPositionsResponse {
  positions: EngineOpenPosition[];
}

// ─── Live Readiness & Emergency Safeguards ───────────────────────────────────
export interface ReadinessCheckItem {
  name: string;
  status: 'ok' | 'warning' | 'blocked';
  detail: string;
  data?: Record<string, any>;
}

export interface ReadinessResponse {
  ready_for_live: boolean;
  is_live_account: boolean;
  account_label: string;
  checks: Record<string, ReadinessCheckItem>;
  blockers: string[];
  warnings: string[];
  timestamp_ms: number;
}

export interface EmergencyActionResponse {
  status: string;
  message: string;
  positions_count: number;
  squared_off: number;
  failed: number;
  details: Array<{ symbol: string; status: string; price?: number; detail?: string }>;
}
