export interface AdaptiveEdgeSettings {
  enabled: boolean;
  symbol: string;
  symbols: string[];
  scan_source: 'spot' | 'derivatives' | 'both' | 'confluence';
  scan_indices: string[];
  scan_stocks: string[];
  scan_all_stocks: boolean;
  scan_stock_contracts: boolean;
  strike_moneyness: string[];
  scan_expiries: Array<'weekly' | 'monthly'>;
  scan_expiries_indices: Array<'weekly' | 'monthly'>;
  /** Expiry window, shared vocabulary with every other engine. */
  expiry_dte_min?: number;
  expiry_dte_max?: number;
  avoid_expiry_day?: boolean;
  w_short: number;
  w_long: number;
  stop_points: number;
  trail_points: number;
  profit_lock_activation_points: number;
  profit_lock_offset_points: number;
  persistence_bars: number;
  scalp_favorable_points: number;
  extended_favorable_points: number;
  intraday_favorable_points: number;
  tick_size: number;
  ib_minutes: number;
  drawdown_circuit_breaker_enabled?: boolean;
  max_daily_drawdown_pct?: number;
}

export interface AdaptiveEdgeReadiness {
  name: string;
  ready: boolean;
  detail: string;
}

export interface AdaptiveEdgeSession {
  entries: number | null;
  exits: number | null;
  reentries: number | null;
  blocked_pyramid: number | null;
  last_mode: string | null;
  last_thesis: string | null;
  last_protection_stage: string | null;
  last_overlays: string[];
  last_operating_mode: string | null;
  last_horizon: string | null;
  last_poc: number | null;
  last_cvd: number | null;
  last_location: string | null;
  last_bar_delta: number | null;
  last_vwap: number | null;
  last_or_location: string | null;
  last_poc_migration: string | null;
  peak_pnl: number | null;
  current_pnl: number | null;
  profit_giveback: number | null;
  lifecycle_action: string | null;
  last_position_quantity: number | null;
  exit_fill_price: number | null;
  audit_stages: string[];
}

export interface AdaptiveEdgeLeg {
  session_date?: string | null;
  entry_time?: string | null;
  exit_time?: string | null;
  symbol?: string | null;
  side?: string | null;
  entry_price?: number | null;
  exit_price?: number | null;
  stop_price?: number | null;
  trail_price?: number | null;
  lock_price?: number | null;
  entry_score?: number | null;
  entry_mode?: string | null;
  exit_mode?: string | null;
  peak_mode?: string | null;
  horizon?: string | null;
  operating_mode?: string | null;
  thesis?: string | null;
  protection_stage?: string | null;
  overlays?: string[];
  quantity?: number | null;
  flattened?: boolean | null;
  entry_poc?: number | null;
  entry_vwap?: number | null;
  entry_cvd?: number | null;
  strategy_version?: string | null;
}

export type AdaptiveEdgeOrigin = 'adaptive_edge' | 'spot_scan';
export type AdaptiveEdgeMode = 'MICRO' | 'SCALP' | 'EXTENDED' | 'EXTENDED_SCALP' | 'INTRADAY';
export type AdaptiveEdgeHorizon = 'IMPULSE' | 'SESSION_TREND' | 'SWING';
export type AdaptiveEdgeOverlay = string;

export interface AdaptiveEdgeOptionLeg {
  moneyness: string;
  option_type: string;
  option_symbol: string;
  strike: number;
  expiry: string | null;
  lot_size: number | null;
  token: number | null;
  exchange: string;
  entry_premium: number | null;
  stop_premium: number | null;
  trail_premium: number | null;
  ltp: number | null;
  resolution_reason: string | null;
  strategy_version?: string | null;
}

export interface AdaptiveEdgeSignal {
  id: string;
  underlying: string;
  tape_symbol: string;
  side: string | null;
  option_type: string | null;
  spot_entry: number | null;
  spot_exit: number | null;
  spot_sl: number | null;
  spot_tsl: number | null;
  entry_time: string | null;
  exit_time: string | null;
  score: number | null;
  poc: number | null;
  vwap: number | null;
  cvd: number | null;
  scanned: boolean;
  skip_reason: string | null;
  scan_origin?: AdaptiveEdgeOrigin | string | null;
  strategy_version?: string | null;
  flattened: boolean;
  quantity: number | null;
  overlays: string[];
  thesis: string | null;
  entry_mode: string | null;
  current_mode?: string | null;
  peak_mode?: string | null;
  exit_mode?: string | null;
  horizon?: string | null;
  mode_upgraded?: boolean;
  mode_downgraded?: boolean;
  mode_path?: string | null;
  mode_history?: string[];
  legs: AdaptiveEdgeOptionLeg[];
}

export interface AdaptiveEdgeModeTransition {
  timestamp?: string | null;
  previous_mode?: string | null;
  new_mode?: string | null;
  trigger_reason?: string | null;
  favorable_points?: number | null;
  giveback_ratio?: number | null;
}

export interface AdaptiveEdgeFormulaRow {
  status: string;
  reason: string;
}

export interface AdaptiveEdgeSnapshot {
  label: string;
  software_complete: boolean;
  production_gate_authorized: boolean;
  meets_a197: boolean;
  registry_locked: boolean;
  live_trading: boolean;
  settings: AdaptiveEdgeSettings;
  readiness: AdaptiveEdgeReadiness[];
  session: AdaptiveEdgeSession;
  legs: AdaptiveEdgeLeg[];
  signals?: AdaptiveEdgeSignal[];
  daily: Array<Record<string, unknown>>;
  quality: Record<string, unknown> | null;
  holdout: Record<string, unknown> | null;
  coverage: Record<string, unknown> | null;
  walk_forward: Record<string, unknown> | null;
  mode_counts: Record<string, number>;
  mode_transitions: AdaptiveEdgeModeTransition[];
  formula_table: Record<string, AdaptiveEdgeFormulaRow>;
  incomplete_reasons: string[];
}
