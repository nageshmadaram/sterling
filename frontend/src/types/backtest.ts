export interface StrategyDescriptor {
  id: string;
  name: string;
  category: string;
  description: string;
  default_timeframe: string;
  default_stop_points: number;
  default_target_points: number;
  default_trail_points: number;
}

export interface BacktestPreset {
  name: string;
  strategy: string;
  symbol: string;
  timeframe: string;
  lookback_days: number;
  lot_size: number;
  num_lots: number;
  starting_capital: number;
  stop_points: number;
  target_points: number;
  trail_points: number;
  slippage_points: number;
}

export interface UnifiedBacktestRequest {
  strategy: string;
  symbol: string;
  instrument_scope?: 'single' | 'indices' | 'fno_all' | 'fno_selected';
  scan_indices?: string[];
  scan_stocks?: string[];
  scan_all_stocks?: boolean;
  contract_type?: 'futures' | 'options_atm' | 'options_itm' | 'options_otm' | 'spot';
  expiry_cycle?: 'weekly' | 'monthly';
  strike_moneyness?: string[];
  data_source?: 'kite' | 'truedata' | 'auto';
  dynamic_mode?: boolean;
  timeframe: string;
  lookback_days: number;
  starting_capital: number;
  lot_size?: number;
  num_lots: number;
  slippage_points: number;
  brokerage_per_order: number;
  stt_pct: number;
  stop_points?: number;
  target_points?: number;
  trail_points?: number;
  session_cutoff_hour: number;
  session_cutoff_min: number;
  strategy_params?: Record<string, any>;
}

export interface BacktestTrade {
  trade_id: number;
  entry_time: string;
  exit_time: string;
  symbol?: string;
  direction: 'LONG' | 'SHORT';
  entry_price: number;
  exit_price: number;
  qty: number;
  sl_points?: number;
  tp_points?: number;
  reward_to_risk?: number;
  gross_pnl: number;
  friction_cost: number;
  net_pnl: number;
  return_pct: number;
  mae_points: number;
  mfe_points: number;
  holding_bars: number;
  exit_reason: string;
}

export interface PerformanceMetrics {
  total_trades: number;
  winning_trades: number;
  losing_trades: number;
  win_rate_pct: number;
  profit_factor: number;
  net_pnl_inr: number;
  total_return_pct: number;
  cagr_pct: number;
  max_drawdown_inr: number;
  max_drawdown_pct: number;
  max_drawdown_duration_bars: number;
  sharpe_ratio: number;
  sortino_ratio: number;
  calmar_ratio: number;
  payoff_ratio: number;
  avg_win_inr: number;
  avg_loss_inr: number;
  expectancy_inr: number;
  max_consecutive_wins: number;
  max_consecutive_losses: number;
  total_friction_inr: number;
  friction_drag_pct: number;
}

export interface EquityPoint {
  timestamp: string;
  equity: number;
  drawdown_pct: number;
  high_water_mark: number;
}

export interface MonteCarloSummary {
  simulations: number;
  mean_return_pct: number;
  median_return_pct: number;
  p5_return_pct: number;
  p95_return_pct: number;
  p95_max_drawdown_pct: number;
  prob_profit_pct: number;
}

export interface UnifiedBacktestResult {
  strategy: string;
  symbol: string;
  timeframe: string;
  data_source: string;
  candles_evaluated: number;
  start_date: string;
  end_date: string;
  starting_capital: number;
  ending_capital: number;
  metrics: PerformanceMetrics;
  equity_curve: EquityPoint[];
  trades: BacktestTrade[];
  monte_carlo?: MonteCarloSummary;
  timestamp_ms: number;
}

export interface AdaptiveEdgeComparisonMetrics {
  net_pnl_delta_inr: number;
  total_return_delta_pct: number;
  win_rate_delta_pct: number;
  profit_factor_v1: number | null;
  profit_factor_v2: number | null;
  max_drawdown_reduction_pct: number;
  sharpe_delta: number;
  sortino_delta: number;
  trades_v1: number;
  trades_v2: number;
  toxic_trades_avoided: number;
  stagnation_exits: number;
  tranche_a_scaled: number;
  friction_saved_inr: number;
}

export interface AdaptiveEdgeComparisonResult {
  v1: UnifiedBacktestResult;
  v2: UnifiedBacktestResult;
  comparison: AdaptiveEdgeComparisonMetrics;
}

