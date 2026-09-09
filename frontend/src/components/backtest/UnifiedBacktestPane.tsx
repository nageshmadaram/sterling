import React, { useState, useMemo } from 'react';
import {
  useUnifiedStrategies,
  useUnifiedPresets,
  useRunUnifiedBacktest,
  useRunAdaptiveEdgeComparison,
} from '../../hooks/useUnifiedBacktest';
import type {
  AdaptiveEdgeComparisonResult,
  BacktestTrade,
  UnifiedBacktestRequest,
  UnifiedBacktestResult,
} from '../../types/backtest';
import { k } from '../../styles/kiteUI';

function fmt(n: number, dp = 2): string {
  if (!isFinite(n)) return '0.00';
  return n.toLocaleString('en-IN', { maximumFractionDigits: dp, minimumFractionDigits: dp });
}

function fmtCurr(n: number): string {
  if (!isFinite(n)) return '₹0.00';
  const prefix = n >= 0 ? '+₹' : '-₹';
  return `${prefix}${Math.abs(n).toLocaleString('en-IN', { maximumFractionDigits: 2, minimumFractionDigits: 2 })}`;
}

const AVAILABLE_INDICES = [
  { id: 'NIFTY 50', label: 'NIFTY 50' },
  { id: 'NIFTY BANK', label: 'BANKNIFTY' },
  { id: 'NIFTY FIN SERVICE', label: 'FINNIFTY' },
  { id: 'MIDCPNIFTY', label: 'MIDCPNIFTY' },
  { id: 'SENSEX', label: 'SENSEX' },
  { id: 'BANKEX', label: 'BANKEX' },
];

const CURATED_FNO_STOCKS = [
  { group: 'Very High Liquidity', stocks: ['HDFCBANK', 'ICICIBANK', 'SBIN', 'RELIANCE'] },
  { group: 'High Liquidity & Volatility', stocks: ['INFY', 'TCS', 'BAJFINANCE', 'BHARTIARTL', 'TATAMOTORS', 'AXISBANK', 'KOTAKBANK', 'ADANIENT'] },
  { group: 'Metals & Energy', stocks: ['JSWSTEEL', 'TATASTEEL', 'HINDALCO', 'ONGC', 'COALINDIA'] },
  { group: 'Auto & Consumer', stocks: ['MARUTI', 'TITAN', 'ITC', 'ASIANPAINT', 'NESTLEIND'] },
];

export function UnifiedBacktestPane() {
  const { data: strategies = [] } = useUnifiedStrategies();
  const { data: presets = [] } = useUnifiedPresets();
  const runMutation = useRunUnifiedBacktest();
  const compareMutation = useRunAdaptiveEdgeComparison();

  // Strategy & Mode
  const [strategy, setStrategy] = useState('adaptive_edge');
  const [strategyVersion, setStrategyVersion] = useState<'v2_hardened' | 'v1_baseline'>('v2_hardened');
  const [comparisonResult, setComparisonResult] = useState<AdaptiveEdgeComparisonResult | null>(null);
  const [dataSource, setDataSource] = useState<'kite' | 'truedata'>('kite');
  const [dynamicMode, setDynamicMode] = useState(true);

  // Instruments & Universe Scope
  const [instrumentScope, setInstrumentScope] = useState<'single' | 'indices' | 'fno_all' | 'fno_selected'>('single');
  const [singleSymbol, setSingleSymbol] = useState('NIFTY 50');
  const [scanIndices, setScanIndices] = useState<string[]>(['NIFTY 50', 'NIFTY BANK']);
  const [scanStocks, setScanStocks] = useState<string[]>(['RELIANCE', 'HDFCBANK', 'INFY', 'ICICIBANK', 'SBIN', 'TCS']);
  const [scanAllStocks, setScanAllStocks] = useState(false);

  // Contracts & Derivatives
  const [contractType, setContractType] = useState<'futures' | 'options_atm' | 'options_itm' | 'options_otm' | 'spot'>('futures');
  const [expiryCycle, setExpiryCycle] = useState<'weekly' | 'monthly'>('weekly');

  // Execution & Timeframe
  const [timeframe, setTimeframe] = useState('5m');
  const [lookbackDays, setLookbackDays] = useState(30);
  const [startingCapital, setStartingCapital] = useState(100000);
  const [numLots, setNumLots] = useState(2);
  const [stopPoints, setStopPoints] = useState<number | undefined>(40);
  const [targetPoints, setTargetPoints] = useState<number | undefined>(80);
  const [trailPoints, setTrailPoints] = useState<number | undefined>(25);
  const [slippagePoints, setSlippagePoints] = useState(0.5);
  const [brokerage, setBrokerage] = useState(20.0);
  const [sttPct, setSttPct] = useState(0.0002);

  // View state
  const [activeTab, setActiveTab] = useState<'equity' | 'drawdown' | 'trades' | 'monte_carlo'>('equity');
  const [tradeFilter, setTradeFilter] = useState<'ALL' | 'WIN' | 'LOSS'>('ALL');
  const [result, setResult] = useState<UnifiedBacktestResult | null>(null);

  const toggleIndex = (idx: string) => {
    setScanIndices((prev) =>
      prev.includes(idx) ? (prev.length > 1 ? prev.filter((x) => x !== idx) : prev) : [...prev, idx]
    );
  };

  const toggleStock = (stk: string) => {
    setScanStocks((prev) =>
      prev.includes(stk) ? (prev.length > 1 ? prev.filter((x) => x !== stk) : prev) : [...prev, stk]
    );
  };

  const handleApplyPreset = (p: typeof presets[0]) => {
    setStrategy(p.strategy);
    setSingleSymbol(p.symbol);
    setInstrumentScope('single');
    setTimeframe(p.timeframe);
    setLookbackDays(p.lookback_days);
    setStartingCapital(p.starting_capital);
    setNumLots(p.num_lots);
    setStopPoints(p.stop_points);
    setTargetPoints(p.target_points);
    setTrailPoints(p.trail_points);
    setSlippagePoints(p.slippage_points);
  };

  const handleRunBacktest = () => {
    const activeSymbol =
      instrumentScope === 'single'
        ? singleSymbol
        : instrumentScope === 'indices'
        ? `INDICES (${scanIndices.join(', ')})`
        : instrumentScope === 'fno_all'
        ? 'ALL_FNO_STOCKS (~180+)'
        : `FNO_PORTFOLIO (${scanStocks.slice(0, 4).join(', ')}${scanStocks.length > 4 ? ` +${scanStocks.length - 4}` : ''})`;

    const payload: UnifiedBacktestRequest = {
      strategy,
      symbol: activeSymbol,
      instrument_scope: instrumentScope,
      scan_indices: scanIndices,
      scan_stocks: scanStocks,
      scan_all_stocks: instrumentScope === 'fno_all' || scanAllStocks,
      contract_type: contractType,
      expiry_cycle: expiryCycle,
      data_source: dataSource,
      dynamic_mode: dynamicMode,
      timeframe,
      lookback_days: lookbackDays,
      starting_capital: startingCapital,
      num_lots: numLots,
      stop_points: dynamicMode ? undefined : stopPoints,
      target_points: dynamicMode ? undefined : targetPoints,
      trail_points: dynamicMode ? undefined : trailPoints,
      slippage_points: slippagePoints,
      brokerage_per_order: brokerage,
      stt_pct: sttPct,
      session_cutoff_hour: 15,
      session_cutoff_min: 15,
      strategy_params: strategy === 'adaptive_edge' ? { strategy_version: strategyVersion } : undefined,
    };

    runMutation.mutate(payload, {
      onSuccess: (res) => {
        setResult(res);
        setComparisonResult(null);
      },
    });
  };

  const handleRunComparison = () => {
    const activeSymbol =
      instrumentScope === 'single'
        ? singleSymbol
        : instrumentScope === 'indices'
        ? `INDICES (${scanIndices.join(', ')})`
        : instrumentScope === 'fno_all'
        ? 'ALL_FNO_STOCKS (~180+)'
        : `FNO_PORTFOLIO (${scanStocks.slice(0, 4).join(', ')}${scanStocks.length > 4 ? ` +${scanStocks.length - 4}` : ''})`;

    const payload: UnifiedBacktestRequest = {
      strategy: 'adaptive_edge',
      symbol: activeSymbol,
      instrument_scope: instrumentScope,
      scan_indices: scanIndices,
      scan_stocks: scanStocks,
      scan_all_stocks: instrumentScope === 'fno_all' || scanAllStocks,
      contract_type: contractType,
      expiry_cycle: expiryCycle,
      data_source: dataSource,
      dynamic_mode: dynamicMode,
      timeframe,
      lookback_days: lookbackDays,
      starting_capital: startingCapital,
      num_lots: numLots,
      stop_points: dynamicMode ? undefined : stopPoints,
      target_points: dynamicMode ? undefined : targetPoints,
      trail_points: dynamicMode ? undefined : trailPoints,
      slippage_points: slippagePoints,
      brokerage_per_order: brokerage,
      stt_pct: sttPct,
      session_cutoff_hour: 15,
      session_cutoff_min: 15,
    };

    compareMutation.mutate(payload, {
      onSuccess: (compRes) => {
        setComparisonResult(compRes);
        setResult(compRes.v2);
      },
    });
  };

  const filteredTrades = useMemo(() => {
    if (!result?.trades) return [];
    if (tradeFilter === 'WIN') return result.trades.filter((t) => t.net_pnl > 0);
    if (tradeFilter === 'LOSS') return result.trades.filter((t) => t.net_pnl <= 0);
    return result.trades;
  }, [result, tradeFilter]);

  const handleExportCSV = () => {
    if (!result?.trades || result.trades.length === 0) return;
    const headers = [
      'Trade ID',
      'Entry Time',
      'Exit Time',
      'Symbol',
      'Direction',
      'Contract',
      'Entry Price',
      'Exit Price',
      'Qty',
      'Dynamic SL (pts)',
      'Dynamic TP (pts)',
      'R:R Achieved',
      'Gross PnL (INR)',
      'Friction (INR)',
      'Net PnL (INR)',
      'Return (%)',
      'MAE (pts)',
      'MFE (pts)',
      'Exit Reason',
    ];
    const rows = result.trades.map((t) => [
      t.trade_id,
      t.entry_time,
      t.exit_time,
      t.symbol || result.symbol,
      t.direction,
      contractType.toUpperCase(),
      t.entry_price,
      t.exit_price,
      t.qty,
      t.sl_points ?? '',
      t.tp_points ?? '',
      t.reward_to_risk ?? '',
      t.gross_pnl,
      t.friction_cost,
      t.net_pnl,
      t.return_pct,
      t.mae_points,
      t.mfe_points,
      t.exit_reason,
    ]);
    const csvContent = 'data:text/csv;charset=utf-8,' + [headers.join(','), ...rows.map((e) => e.join(','))].join('\n');
    const encodedUri = encodeURI(csvContent);
    const link = document.createElement('a');
    link.setAttribute('href', encodedUri);
    link.setAttribute('download', `backtest_${result.strategy}_${contractType}_${result.timeframe}.csv`);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', width: '100%', background: 'var(--k-surface-4)', overflow: 'hidden' }}>
      {/* ── Top Bar: Presets & Real Data Status ── */}
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '10px 20px', background: 'var(--k-bg)', borderBottom: `1px solid ${k.border}`, flexShrink: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
          <span style={{ fontSize: 12, fontWeight: 700, color: 'var(--k-ink-4)', textTransform: 'uppercase', letterSpacing: 0.5, marginRight: 4 }}>
            Presets:
          </span>
          {presets.map((p) => (
            <button
              key={p.name}
              onClick={() => handleApplyPreset(p)}
              style={{
                fontSize: 11.5, fontWeight: 500, padding: '4px 10px', borderRadius: 14,
                border: '1px solid var(--k-border-strong-3)', background: 'var(--k-surface-2)', color: 'var(--k-text)', cursor: 'pointer',
                transition: 'all 0.15s ease',
              }}
              onMouseEnter={(e) => { e.currentTarget.style.borderColor = 'var(--k-brand)'; e.currentTarget.style.color = 'var(--k-brand)'; }}
              onMouseLeave={(e) => { e.currentTarget.style.borderColor = 'var(--k-border-strong-3)'; e.currentTarget.style.color = 'var(--k-text)'; }}
            >
              {p.name}
            </button>
          ))}
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11.5, fontWeight: 600, color: 'var(--k-green-deep)', background: 'rgba(46,125,50,0.08)', padding: '4px 10px', borderRadius: 4 }}>
          <span style={{ width: 7, height: 7, borderRadius: '50%', background: 'var(--k-green-deep)' }} />
          REAL DATA: {result?.data_source ?? (dataSource === 'kite' ? 'ZERODHA KITE ENGINE' : 'TRUEDATA V2.6 ENGINE')}
        </div>
      </div>

      {/* ── Main Content Area ── */}
      <div style={{ display: 'flex', flex: 1, minHeight: 0 }}>
        {/* ── Left Sidebar: Strategy & Parameters Config ── */}
        <div style={{ width: 360, background: 'var(--k-bg)', borderRight: `1px solid ${k.border}`, padding: '18px 20px', overflowY: 'auto', flexShrink: 0 }}>
          <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--k-ink-1)', marginBottom: 14, display: 'flex', alignItems: 'center', gap: 6 }}>
            ⚙️ Strategy & Universe Config
          </div>

          {/* Execution Mode: Dynamic vs Manual */}
          <div style={{ marginBottom: 14 }}>
            <label style={{ display: 'block', fontSize: 11, fontWeight: 700, color: 'var(--k-ink-5)', textTransform: 'uppercase', marginBottom: 6 }}>
              Execution & Risk Mode
            </label>
            <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr', gap: 6 }}>
              <button
                type="button"
                onClick={() => setDynamicMode(true)}
                style={{
                  padding: '7px 8px',
                  borderRadius: 4,
                  fontSize: 11.5,
                  fontWeight: 700,
                  border: dynamicMode ? '2px solid var(--k-brand)' : '1px solid var(--k-border-strong-3)',
                  background: dynamicMode ? 'rgba(240,100,40,0.08)' : 'var(--k-surface-2)',
                  color: dynamicMode ? 'var(--k-brand)' : 'var(--k-ink-4)',
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  gap: 4,
                }}
              >
                <span>⚡</span> Dynamic (Live)
              </button>
              <button
                type="button"
                onClick={() => setDynamicMode(false)}
                style={{
                  padding: '7px 8px',
                  borderRadius: 4,
                  fontSize: 11.5,
                  fontWeight: 700,
                  border: !dynamicMode ? '2px solid var(--k-ink-1)' : '1px solid var(--k-border-strong-3)',
                  background: !dynamicMode ? 'var(--k-hairline-3)' : 'var(--k-surface-2)',
                  color: !dynamicMode ? '#111' : 'var(--k-ink-4)',
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  gap: 4,
                }}
              >
                <span>⚙️</span> Manual Override
              </button>
            </div>
          </div>

          {/* Data Source Picker */}
          <div style={{ marginBottom: 14 }}>
            <label style={{ display: 'block', fontSize: 11, fontWeight: 700, color: 'var(--k-ink-5)', textTransform: 'uppercase', marginBottom: 6 }}>
              Historical Data Provider
            </label>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6 }}>
              <button
                type="button"
                onClick={() => setDataSource('kite')}
                style={{
                  padding: '7px 8px',
                  borderRadius: 4,
                  fontSize: 12,
                  fontWeight: 700,
                  border: dataSource === 'kite' ? '2px solid var(--k-brand)' : '1px solid var(--k-border-strong-3)',
                  background: dataSource === 'kite' ? 'rgba(240,100,40,0.08)' : 'var(--k-surface-2)',
                  color: dataSource === 'kite' ? 'var(--k-brand)' : 'var(--k-ink-4)',
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  gap: 5,
                }}
              >
                <span>🪁</span> Zerodha Kite
              </button>
              <button
                type="button"
                onClick={() => setDataSource('truedata')}
                style={{
                  padding: '7px 8px',
                  borderRadius: 4,
                  fontSize: 12,
                  fontWeight: 700,
                  border: dataSource === 'truedata' ? '2px solid #1976d2' : '1px solid var(--k-border-strong-3)',
                  background: dataSource === 'truedata' ? 'rgba(25,118,210,0.08)' : 'var(--k-surface-2)',
                  color: dataSource === 'truedata' ? '#1976d2' : 'var(--k-ink-4)',
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  gap: 5,
                }}
              >
                <span>📊</span> TrueData
              </button>
            </div>
          </div>

          {/* Strategy Picker */}
          <div style={{ marginBottom: 14 }}>
            <label style={{ display: 'block', fontSize: 11, fontWeight: 700, color: 'var(--k-ink-5)', textTransform: 'uppercase', marginBottom: 4 }}>
              Strategy
            </label>
            <select
              value={strategy}
              onChange={(e) => setStrategy(e.target.value)}
              style={{ width: '100%', padding: '7px 9px', fontSize: 13, borderRadius: 4, border: `1px solid ${k.border}`, background: 'var(--k-bg)' }}
            >
              {strategies.map((s) => (
                <option key={s.id} value={s.id}>{s.name} ({s.category})</option>
              ))}
            </select>

            {strategy === 'adaptive_edge' && (
              <div style={{ marginTop: 10, padding: '10px 12px', background: 'var(--k-surface-2)', borderRadius: 6, border: `1px solid ${k.border}` }}>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 6 }}>
                  <label style={{ fontSize: 11, fontWeight: 700, color: 'var(--k-ink-5)', textTransform: 'uppercase' }}>
                    Engine Version
                  </label>
                  <span style={{
                    fontSize: 10,
                    fontWeight: 700,
                    padding: '2px 7px',
                    borderRadius: 10,
                    background: strategyVersion === 'v2_hardened' ? 'rgba(46,125,50,0.15)' : 'rgba(211,47,47,0.15)',
                    color: strategyVersion === 'v2_hardened' ? '#2e7d32' : '#d32f2f'
                  }}>
                    {strategyVersion === 'v2_hardened' ? 'V2 PRODUCTION' : 'V1 LEGACY'}
                  </span>
                </div>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6 }}>
                  <button
                    type="button"
                    onClick={() => setStrategyVersion('v2_hardened')}
                    style={{
                      padding: '6px 4px',
                      fontSize: 11,
                      fontWeight: 700,
                      borderRadius: 4,
                      border: strategyVersion === 'v2_hardened' ? '1.5px solid #2e7d32' : '1px solid var(--k-border-strong-3)',
                      background: strategyVersion === 'v2_hardened' ? 'rgba(46,125,50,0.12)' : 'var(--k-bg)',
                      color: strategyVersion === 'v2_hardened' ? '#2e7d32' : 'var(--k-ink-4)',
                      cursor: 'pointer',
                    }}
                  >
                    🛡️ V2 Hardened
                  </button>
                  <button
                    type="button"
                    onClick={() => setStrategyVersion('v1_baseline')}
                    style={{
                      padding: '6px 4px',
                      fontSize: 11,
                      fontWeight: 700,
                      borderRadius: 4,
                      border: strategyVersion === 'v1_baseline' ? '1.5px solid #d32f2f' : '1px solid var(--k-border-strong-3)',
                      background: strategyVersion === 'v1_baseline' ? 'rgba(211,47,47,0.12)' : 'var(--k-bg)',
                      color: strategyVersion === 'v1_baseline' ? '#d32f2f' : 'var(--k-ink-4)',
                      cursor: 'pointer',
                    }}
                  >
                    🕰️ V1 Legacy
                  </button>
                </div>
                <div style={{ fontSize: 10.5, color: 'var(--k-ink-6)', marginTop: 6, lineHeight: 1.35 }}>
                  {strategyVersion === 'v2_hardened'
                    ? 'Active: 09:28 lockout, candle-body wick filter, 1.5R 50/50 partial scaling, 4-bar decay stop.'
                    : 'Active: Unrestricted 09:15 open bar entries, all-or-nothing 100% stop, baseline execution.'}
                </div>
              </div>
            )}
          </div>

          {/* ── Instruments & Universe Scope Section ── */}
          <div style={{ borderTop: `1px solid ${k.border}`, paddingTop: 12, marginBottom: 14 }}>
            <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--k-text)', marginBottom: 8, display: 'flex', alignItems: 'center', gap: 5 }}>
              🏛️ Instruments & Universe Scope
            </div>

            {/* Scope Tabs */}
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 4, marginBottom: 10 }}>
              {[
                { id: 'single' as const, label: 'Single' },
                { id: 'indices' as const, label: 'Indices' },
                { id: 'fno_selected' as const, label: 'Selected F&O' },
                { id: 'fno_all' as const, label: 'All F&O' },
              ].map((tab) => (
                <button
                  key={tab.id}
                  type="button"
                  onClick={() => setInstrumentScope(tab.id)}
                  style={{
                    padding: '5px 2px',
                    fontSize: 10.5,
                    fontWeight: 700,
                    borderRadius: 3,
                    border: instrumentScope === tab.id ? '1.5px solid var(--k-brand)' : '1px solid var(--k-border-strong-3)',
                    background: instrumentScope === tab.id ? 'rgba(240,100,40,0.1)' : 'var(--k-surface-2)',
                    color: instrumentScope === tab.id ? 'var(--k-brand)' : 'var(--k-ink-4)',
                    cursor: 'pointer',
                  }}
                >
                  {tab.label}
                </button>
              ))}
            </div>

            {/* Single Symbol View */}
            {instrumentScope === 'single' && (
              <div style={{ marginBottom: 8 }}>
                <label style={{ display: 'block', fontSize: 10.5, fontWeight: 700, color: 'var(--k-ink-6)', marginBottom: 3 }}>
                  Target Instrument
                </label>
                <select
                  value={singleSymbol}
                  onChange={(e) => setSingleSymbol(e.target.value)}
                  style={{ width: '100%', padding: '6px 8px', fontSize: 12.5, borderRadius: 4, border: `1px solid ${k.border}`, background: 'var(--k-bg)' }}
                >
                  <option value="NIFTY 50">NIFTY 50 (Lot: 25)</option>
                  <option value="NIFTY BANK">BANKNIFTY (Lot: 15)</option>
                  <option value="NIFTY FIN SERVICE">FINNIFTY (Lot: 25)</option>
                  <option value="SENSEX">SENSEX (Lot: 10)</option>
                  <option value="RELIANCE">RELIANCE (Lot: 250)</option>
                  <option value="HDFCBANK">HDFCBANK (Lot: 550)</option>
                  <option value="ICICIBANK">ICICIBANK (Lot: 700)</option>
                  <option value="SBIN">SBIN (Lot: 750)</option>
                  <option value="INFY">INFY (Lot: 400)</option>
                  <option value="TCS">TCS (Lot: 175)</option>
                  <option value="TATAMOTORS">TATAMOTORS (Lot: 575)</option>
                  <option value="BAJFINANCE">BAJFINANCE (Lot: 125)</option>
                </select>
              </div>
            )}

            {/* Indices Multi-Select */}
            {instrumentScope === 'indices' && (
              <div style={{ marginBottom: 8 }}>
                <label style={{ display: 'block', fontSize: 10.5, fontWeight: 700, color: 'var(--k-ink-6)', marginBottom: 4 }}>
                  Scan Indices ({scanIndices.length} active)
                </label>
                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6 }}>
                  {AVAILABLE_INDICES.map((idx) => {
                    const checked = scanIndices.includes(idx.id);
                    return (
                      <label
                        key={idx.id}
                        style={{
                          display: 'flex', alignItems: 'center', gap: 6, padding: '5px 7px', borderRadius: 4,
                          border: `1px solid ${checked ? 'var(--k-brand)' : 'var(--k-border)'}`,
                          background: checked ? 'rgba(240,100,40,0.06)' : 'var(--k-surface-2)',
                          fontSize: 11, fontWeight: checked ? 700 : 500, color: checked ? 'var(--k-brand)' : 'var(--k-ink-3)',
                          cursor: 'pointer',
                        }}
                      >
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={() => toggleIndex(idx.id)}
                          style={{ accentColor: 'var(--k-brand)', margin: 0 }}
                        />
                        {idx.label}
                      </label>
                    );
                  })}
                </div>
              </div>
            )}

            {/* Selected F&O Stocks */}
            {instrumentScope === 'fno_selected' && (
              <div style={{ marginBottom: 8 }}>
                <label style={{ display: 'block', fontSize: 10.5, fontWeight: 700, color: 'var(--k-ink-6)', marginBottom: 4 }}>
                  Selected F&O Stocks ({scanStocks.length} selected)
                </label>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8, maxHeight: 160, overflowY: 'auto', paddingRight: 4 }}>
                  {CURATED_FNO_STOCKS.map((group) => (
                    <div key={group.group}>
                      <div style={{ fontSize: 9.5, fontWeight: 700, color: 'var(--k-dim-2)', textTransform: 'uppercase', marginBottom: 3 }}>
                        {group.group}
                      </div>
                      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 4 }}>
                        {group.stocks.map((stk) => {
                          const checked = scanStocks.includes(stk);
                          return (
                            <label
                              key={stk}
                              style={{
                                display: 'flex', alignItems: 'center', gap: 5, padding: '4px 6px', borderRadius: 3,
                                border: `1px solid ${checked ? 'var(--k-brand)' : '#e5e5e5'}`,
                                background: checked ? 'rgba(240,100,40,0.06)' : 'var(--k-bg)',
                                fontSize: 10.5, fontWeight: checked ? 700 : 500, color: checked ? 'var(--k-brand)' : 'var(--k-ink-3)',
                                cursor: 'pointer',
                              }}
                            >
                              <input
                                type="checkbox"
                                checked={checked}
                                onChange={() => toggleStock(stk)}
                                style={{ accentColor: 'var(--k-brand)', margin: 0 }}
                              />
                              {stk}
                            </label>
                          );
                        })}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* All F&O Universe */}
            {instrumentScope === 'fno_all' && (
              <div style={{ background: 'rgba(25,118,210,0.06)', border: '1px solid rgba(25,118,210,0.2)', borderRadius: 4, padding: '8px 10px', fontSize: 11, color: 'var(--k-ink-1)', lineHeight: 1.4 }}>
                <div style={{ fontWeight: 700, color: '#1976d2', marginBottom: 2 }}>🌐 Full NSE F&O Universe (~180+ Stocks)</div>
                Simulates multi-asset portfolio scanning across all eligible liquid F&O stocks with lot sizes and margin scaling.
              </div>
            )}
          </div>

          {/* ── Contracts & Product Specification Section ── */}
          <div style={{ borderTop: `1px solid ${k.border}`, paddingTop: 12, marginBottom: 14 }}>
            <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--k-text)', marginBottom: 8, display: 'flex', alignItems: 'center', gap: 5 }}>
              📜 Contracts & Expiry Specs
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8, marginBottom: 8 }}>
              <div>
                <label style={{ display: 'block', fontSize: 10.5, fontWeight: 700, color: 'var(--k-ink-6)', marginBottom: 3 }}>
                  Contract Type
                </label>
                <select
                  value={contractType}
                  onChange={(e) => setContractType(e.target.value as any)}
                  style={{ width: '100%', padding: '6px 8px', fontSize: 11.5, borderRadius: 4, border: `1px solid ${k.border}`, background: 'var(--k-bg)' }}
                >
                  <option value="futures">📈 Futures (Delta 1.0)</option>
                  <option value="options_atm">⚡ Options ATM (Δ 0.50)</option>
                  <option value="options_itm">🎯 Options ITM (Δ 0.70)</option>
                  <option value="options_otm">🏹 Options OTM (Δ 0.30)</option>
                  <option value="spot">📊 Spot Index</option>
                </select>
              </div>

              <div>
                <label style={{ display: 'block', fontSize: 10.5, fontWeight: 700, color: 'var(--k-ink-6)', marginBottom: 3 }}>
                  Expiry Cycle
                </label>
                <select
                  value={expiryCycle}
                  onChange={(e) => setExpiryCycle(e.target.value as any)}
                  style={{ width: '100%', padding: '6px 8px', fontSize: 11.5, borderRadius: 4, border: `1px solid ${k.border}`, background: 'var(--k-bg)' }}
                >
                  <option value="weekly">Weekly Cycle</option>
                  <option value="monthly">Monthly Cycle</option>
                </select>
              </div>
            </div>

            <div style={{ fontSize: 10.5, color: 'var(--k-ink-6)', lineHeight: 1.35 }}>
              {contractType.startsWith('options')
                ? 'Simulates dynamic delta price capture & theta decay per bar held.'
                : 'Simulates full tick point movement with exchange lot sizes.'}
            </div>
          </div>

          {/* Timeframe & Lookback */}
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginBottom: 14 }}>
            <div>
              <label style={{ display: 'block', fontSize: 11, fontWeight: 700, color: 'var(--k-ink-5)', textTransform: 'uppercase', marginBottom: 4 }}>
                Timeframe
              </label>
              <select
                value={timeframe}
                onChange={(e) => setTimeframe(e.target.value)}
                style={{ width: '100%', padding: '7px 9px', fontSize: 12.5, borderRadius: 4, border: `1px solid ${k.border}`, background: 'var(--k-bg)' }}
              >
                <option value="5m">5 Minute (Scalp)</option>
                <option value="15m">15 Minute (Intraday)</option>
                <option value="3m">3 Minute</option>
                <option value="1m">1 Minute</option>
                <option value="30m">30 Minute</option>
                <option value="1h">1 Hour (Trend)</option>
                <option value="day">1 Day (Positional)</option>
              </select>
            </div>

            <div>
              <label style={{ display: 'block', fontSize: 11, fontWeight: 700, color: 'var(--k-ink-5)', textTransform: 'uppercase', marginBottom: 4 }}>
                Lookback (Days)
              </label>
              <input
                type="number"
                min={3}
                max={365}
                value={lookbackDays}
                onChange={(e) => setLookbackDays(Number(e.target.value))}
                style={{ width: '100%', padding: '7px 9px', fontSize: 13, borderRadius: 4, border: `1px solid ${k.border}`, background: 'var(--k-bg)', boxSizing: 'border-box' }}
              />
            </div>
          </div>

          {/* Capital & Lots */}
          <div style={{ display: 'grid', gridTemplateColumns: '1.4fr 1fr', gap: 10, marginBottom: 14 }}>
            <div>
              <label style={{ display: 'block', fontSize: 11, fontWeight: 700, color: 'var(--k-ink-5)', textTransform: 'uppercase', marginBottom: 4 }}>
                Starting Capital (₹)
              </label>
              <input
                type="number"
                step={10000}
                value={startingCapital}
                onChange={(e) => setStartingCapital(Number(e.target.value))}
                style={{ width: '100%', padding: '7px 9px', fontSize: 13, borderRadius: 4, border: `1px solid ${k.border}`, background: 'var(--k-bg)', boxSizing: 'border-box' }}
              />
            </div>
            <div>
              <label style={{ display: 'block', fontSize: 11, fontWeight: 700, color: 'var(--k-ink-5)', textTransform: 'uppercase', marginBottom: 4 }}>
                Lots
              </label>
              <input
                type="number"
                min={1}
                max={100}
                value={numLots}
                onChange={(e) => setNumLots(Number(e.target.value))}
                style={{ width: '100%', padding: '7px 9px', fontSize: 13, borderRadius: 4, border: `1px solid ${k.border}`, background: 'var(--k-bg)', boxSizing: 'border-box' }}
              />
            </div>
          </div>

          {/* Stop, Target & Trailing */}
          <div style={{ borderTop: `1px solid ${k.border}`, paddingTop: 12, marginBottom: 14 }}>
            <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--k-text)', marginBottom: 8 }}>
              🛡️ Risk & Exit Boundaries
            </div>

            {dynamicMode ? (
              <div style={{ background: 'rgba(240,100,40,0.06)', border: '1px solid rgba(240,100,40,0.2)', borderRadius: 4, padding: '8px 10px', fontSize: 11, color: 'var(--k-text)', lineHeight: 1.45 }}>
                <div style={{ fontWeight: 700, color: 'var(--k-brand)', marginBottom: 3 }}>⚡ Dynamic Risk Engine Active</div>
                <div>• <strong>Stop Loss (SL)</strong>: Auto-calculated per bar via Dynamic ATR & Swing Pivots.</div>
                <div>• <strong>Profit Target (TP)</strong>: Auto-expanded to 1:2.2+ R:R.</div>
                <div>• <strong>Trailing SL (TSL)</strong>: Auto-locks Break-Even at 1.0R and trails at 0.8×ATR for 1:3–1:6+ runners.</div>
              </div>
            ) : (
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr 1fr', gap: 8 }}>
                <div>
                  <label style={{ display: 'block', fontSize: 10, fontWeight: 700, color: 'var(--k-ink-6)', marginBottom: 2 }}>Stop (SL)</label>
                  <input
                    type="number"
                    value={stopPoints ?? ''}
                    placeholder="Points"
                    onChange={(e) => setStopPoints(e.target.value ? Number(e.target.value) : undefined)}
                    style={{ width: '100%', padding: '6px 6px', fontSize: 12, borderRadius: 4, border: `1px solid ${k.border}`, boxSizing: 'border-box' }}
                  />
                </div>
                <div>
                  <label style={{ display: 'block', fontSize: 10, fontWeight: 700, color: 'var(--k-ink-6)', marginBottom: 2 }}>Target (TP)</label>
                  <input
                    type="number"
                    value={targetPoints ?? ''}
                    placeholder="Points"
                    onChange={(e) => setTargetPoints(e.target.value ? Number(e.target.value) : undefined)}
                    style={{ width: '100%', padding: '6px 6px', fontSize: 12, borderRadius: 4, border: `1px solid ${k.border}`, boxSizing: 'border-box' }}
                  />
                </div>
                <div>
                  <label style={{ display: 'block', fontSize: 10, fontWeight: 700, color: 'var(--k-ink-6)', marginBottom: 2 }}>Trail (TSL)</label>
                  <input
                    type="number"
                    value={trailPoints ?? ''}
                    placeholder="Points"
                    onChange={(e) => setTrailPoints(e.target.value ? Number(e.target.value) : undefined)}
                    style={{ width: '100%', padding: '6px 6px', fontSize: 12, borderRadius: 4, border: `1px solid ${k.border}`, boxSizing: 'border-box' }}
                  />
                </div>
              </div>
            )}
          </div>

          {/* Friction & Costs */}
          <div style={{ borderTop: `1px solid ${k.border}`, paddingTop: 12, marginBottom: 18 }}>
            <div style={{ fontSize: 12, fontWeight: 700, color: 'var(--k-text)', marginBottom: 10 }}>
              💸 Indian F&O Friction Engine
            </div>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginBottom: 8 }}>
              <div>
                <label style={{ display: 'block', fontSize: 10, fontWeight: 700, color: 'var(--k-ink-6)', marginBottom: 2 }}>Slippage (pts)</label>
                <input
                  type="number"
                  step={0.1}
                  value={slippagePoints}
                  onChange={(e) => setSlippagePoints(Number(e.target.value))}
                  style={{ width: '100%', padding: '6px 8px', fontSize: 12, borderRadius: 4, border: `1px solid ${k.border}`, boxSizing: 'border-box' }}
                />
              </div>
              <div>
                <label style={{ display: 'block', fontSize: 10, fontWeight: 700, color: 'var(--k-ink-6)', marginBottom: 2 }}>Brokerage (₹/ord)</label>
                <input
                  type="number"
                  value={brokerage}
                  onChange={(e) => setBrokerage(Number(e.target.value))}
                  style={{ width: '100%', padding: '6px 8px', fontSize: 12, borderRadius: 4, border: `1px solid ${k.border}`, boxSizing: 'border-box' }}
                />
              </div>
            </div>
            <div style={{ fontSize: 11, color: 'var(--k-ink-6)', lineHeight: 1.4 }}>
              Includes STT (0.125%), Exchange Turnover (0.05%), and 18% GST on brokerage.
            </div>
          </div>

          {/* Run Button */}
          <button
            onClick={handleRunBacktest}
            disabled={runMutation.isPending}
            style={{
              width: '100%', padding: '12px', background: runMutation.isPending ? 'var(--k-faint-5)' : 'var(--k-brand)',
              color: 'var(--k-bg)', border: 'none', borderRadius: 4, fontSize: 14, fontWeight: 700,
              cursor: runMutation.isPending ? 'not-allowed' : 'pointer', transition: 'background 0.15s ease',
            }}
          >
            {runMutation.isPending ? 'Replaying Real Market Bars…' : '⚡ Run Backtest'}
          </button>
          {/* Run Button & A/B Comparison */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            <button
              onClick={handleRunBacktest}
              disabled={runMutation.isPending || compareMutation.isPending}
              style={{
                width: '100%', padding: '12px', background: (runMutation.isPending || compareMutation.isPending) ? 'var(--k-faint-5)' : 'var(--k-brand)',
                color: 'var(--k-bg)', border: 'none', borderRadius: 4, fontSize: 14, fontWeight: 700,
                cursor: (runMutation.isPending || compareMutation.isPending) ? 'not-allowed' : 'pointer', transition: 'background 0.15s ease',
              }}
            >
              {runMutation.isPending ? 'Replaying Real Market Bars…' : '⚡ Run Backtest'}
            </button>

            {strategy === 'adaptive_edge' && (
              <button
                type="button"
                onClick={handleRunComparison}
                disabled={runMutation.isPending || compareMutation.isPending}
                style={{
                  width: '100%',
                  padding: '9px 12px',
                  background: compareMutation.isPending ? 'var(--k-surface-2)' : 'linear-gradient(135deg, rgba(46,125,50,0.08) 0%, rgba(25,118,210,0.08) 100%)',
                  color: compareMutation.isPending ? 'var(--k-ink-6)' : 'var(--k-brand-green, #2e7d32)',
                  border: '1.5px dashed rgba(46,125,50,0.4)',
                  borderRadius: 4,
                  fontSize: 12,
                  fontWeight: 700,
                  cursor: (runMutation.isPending || compareMutation.isPending) ? 'not-allowed' : 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  gap: 6,
                }}
              >
                <span>⚖️</span>
                {compareMutation.isPending ? 'Simulating V1 vs V2 Attribution…' : 'Simulate Results: Old vs New (A/B)'}
              </button>
            )}
          </div>
        </div>

        {/* ── Right Main Surface: Metrics, Charts, Trades, Monte Carlo ── */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflowY: 'auto', padding: 20 }}>
          {!result && !runMutation.isPending && !compareMutation.isPending && (
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100%', color: 'var(--k-ink-6)' }}>
              <div style={{ fontSize: 36, marginBottom: 12 }}>📊</div>
              <div style={{ fontSize: 16, fontWeight: 700, color: 'var(--k-text)', marginBottom: 4 }}>
                Ready to Run Real-Data Backtest
              </div>
              <div style={{ fontSize: 13, maxWidth: 460, textAlign: 'center', lineHeight: 1.5 }}>
                Select your <strong>Instruments</strong> (Indices / F&O Stocks), <strong>Contracts</strong> (Futures / Options ATM), and Strategy on the left, then click <strong>Run Backtest</strong> to simulate with dynamic ATR risk and friction.
              </div>
            </div>
          )}

          {runMutation.isPending && (
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100%', color: 'var(--k-ink-6)' }}>
              <div style={{ fontSize: 32, marginBottom: 12, animation: 'spin 1s infinite linear' }}>⏳</div>
              <div style={{ fontSize: 15, fontWeight: 600, color: 'var(--k-ink-1)', marginBottom: 4 }}>
                Fetching Historical Candles & Simulating Executions…
              </div>
              <div style={{ fontSize: 12 }}>Calculating dynamic ATR risk boundaries, options delta scaling, fee ledgers, and MAE/MFE diagnostics</div>
            </div>
          )}

          {compareMutation.isPending && (
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', height: '100%', color: 'var(--k-ink-6)' }}>
              <div style={{ fontSize: 32, marginBottom: 12, animation: 'spin 1s infinite linear' }}>⚖️</div>
              <div style={{ fontSize: 15, fontWeight: 600, color: 'var(--k-ink-1)', marginBottom: 4 }}>
                Simulating V1 Legacy vs V2 Production-Hardened in Parallel…
              </div>
              <div style={{ fontSize: 12 }}>Evaluating 09:28 lockout, candle-body filter, 1.5R 50/50 partial scaling, and stagnation decay delta</div>
            </div>
          )}

          {result && !runMutation.isPending && !compareMutation.isPending && (
            <>
              {/* ── Adaptive Edge A/B Comparison Card ── */}
              {comparisonResult && (
                <div style={{
                  marginBottom: 20,
                  background: 'var(--k-bg)',
                  border: '1.5px solid rgba(46, 125, 50, 0.4)',
                  borderRadius: 8,
                  padding: 16,
                  boxShadow: '0 2px 8px rgba(0, 0, 0, 0.05)',
                }}>
                  {/* Header & View Switcher */}
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: 10, marginBottom: 14, paddingBottom: 12, borderBottom: `1px solid ${k.border}` }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <span style={{ fontSize: 18 }}>⚖️</span>
                      <div>
                        <div style={{ fontSize: 14, fontWeight: 800, color: 'var(--k-text)' }}>
                          Adaptive Edge A/B Simulation: V1 Legacy vs V2 Production-Hardened
                        </div>
                        <div style={{ fontSize: 11, color: 'var(--k-ink-5)' }}>
                          Identical historical market bars • Quantifying the empirical attribution delta of production edge protections
                        </div>
                      </div>
                    </div>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--k-ink-5)' }}>Viewing in detail:</div>
                      <div style={{ display: 'inline-flex', borderRadius: 4, overflow: 'hidden', border: `1px solid ${k.border}` }}>
                        <button
                          type="button"
                          onClick={() => setResult(comparisonResult.v2)}
                          style={{
                            padding: '4px 10px',
                            fontSize: 11,
                            fontWeight: 700,
                            border: 'none',
                            background: result === comparisonResult.v2 ? 'rgba(46,125,50,0.18)' : 'var(--k-surface-2)',
                            color: result === comparisonResult.v2 ? '#2e7d32' : 'var(--k-ink-4)',
                            cursor: 'pointer',
                          }}
                        >
                          🛡️ V2 Hardened
                        </button>
                        <button
                          type="button"
                          onClick={() => setResult(comparisonResult.v1)}
                          style={{
                            padding: '4px 10px',
                            fontSize: 11,
                            fontWeight: 700,
                            border: 'none',
                            borderLeft: `1px solid ${k.border}`,
                            background: result === comparisonResult.v1 ? 'rgba(211,47,47,0.18)' : 'var(--k-surface-2)',
                            color: result === comparisonResult.v1 ? '#d32f2f' : 'var(--k-ink-4)',
                            cursor: 'pointer',
                          }}
                        >
                          🕰️ V1 Legacy
                        </button>
                      </div>
                      <button
                        type="button"
                        onClick={() => setComparisonResult(null)}
                        style={{
                          background: 'transparent',
                          border: 'none',
                          color: 'var(--k-ink-6)',
                          cursor: 'pointer',
                          fontSize: 14,
                          padding: '2px 6px',
                        }}
                        title="Dismiss comparison"
                      >
                        ✕
                      </button>
                    </div>
                  </div>

                  {/* Highlight KPI Attribution Delta Chips */}
                  <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))', gap: 8, marginBottom: 16 }}>
                    <div style={{ padding: '8px 10px', borderRadius: 6, background: comparisonResult.comparison.net_pnl_delta_inr >= 0 ? 'rgba(46,125,50,0.08)' : 'rgba(211,47,47,0.08)', border: `1px solid ${comparisonResult.comparison.net_pnl_delta_inr >= 0 ? 'rgba(46,125,50,0.2)' : 'rgba(211,47,47,0.2)'}` }}>
                      <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--k-ink-5)', textTransform: 'uppercase' }}>PnL Delta</div>
                      <div style={{ fontSize: 14, fontWeight: 800, color: comparisonResult.comparison.net_pnl_delta_inr >= 0 ? '#2e7d32' : '#d32f2f', marginTop: 2 }}>
                        {fmtCurr(comparisonResult.comparison.net_pnl_delta_inr)}
                      </div>
                    </div>
                    <div style={{ padding: '8px 10px', borderRadius: 6, background: 'rgba(46,125,50,0.08)', border: '1px solid rgba(46,125,50,0.2)' }}>
                      <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--k-ink-5)', textTransform: 'uppercase' }}>Drawdown Reduction</div>
                      <div style={{ fontSize: 14, fontWeight: 800, color: '#2e7d32', marginTop: 2 }}>
                        +{comparisonResult.comparison.max_drawdown_reduction_pct}%
                      </div>
                    </div>
                    <div style={{ padding: '8px 10px', borderRadius: 6, background: 'rgba(25,118,210,0.08)', border: '1px solid rgba(25,118,210,0.2)' }}>
                      <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--k-ink-5)', textTransform: 'uppercase' }}>Win Rate Delta</div>
                      <div style={{ fontSize: 14, fontWeight: 800, color: '#1976d2', marginTop: 2 }}>
                        {comparisonResult.comparison.win_rate_delta_pct >= 0 ? '+' : ''}{comparisonResult.comparison.win_rate_delta_pct}%
                      </div>
                    </div>
                    <div style={{ padding: '8px 10px', borderRadius: 6, background: 'rgba(245,124,0,0.08)', border: '1px solid rgba(245,124,0,0.2)' }}>
                      <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--k-ink-5)', textTransform: 'uppercase' }}>Toxic Trades Avoided</div>
                      <div style={{ fontSize: 14, fontWeight: 800, color: '#e65100', marginTop: 2 }}>
                        {comparisonResult.comparison.toxic_trades_avoided} Avoided
                      </div>
                    </div>
                    <div style={{ padding: '8px 10px', borderRadius: 6, background: 'rgba(123,31,162,0.08)', border: '1px solid rgba(123,31,162,0.2)' }}>
                      <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--k-ink-5)', textTransform: 'uppercase' }}>Stagnation Decays</div>
                      <div style={{ fontSize: 14, fontWeight: 800, color: '#7b1fa2', marginTop: 2 }}>
                        {comparisonResult.comparison.stagnation_exits} Scratched
                      </div>
                    </div>
                    <div style={{ padding: '8px 10px', borderRadius: 6, background: 'rgba(0,121,107,0.08)', border: '1px solid rgba(0,121,107,0.2)' }}>
                      <div style={{ fontSize: 10, fontWeight: 700, color: 'var(--k-ink-5)', textTransform: 'uppercase' }}>1.5R Partial Scales</div>
                      <div style={{ fontSize: 14, fontWeight: 800, color: '#00796b', marginTop: 2 }}>
                        {comparisonResult.comparison.tranche_a_scaled} Locked
                      </div>
                    </div>
                  </div>

                  {/* Side-by-Side Attribution Table */}
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                    <thead>
                      <tr style={{ background: 'var(--k-surface-2)', borderBottom: `1px solid ${k.border}` }}>
                        <th style={{ textAlign: 'left', padding: '8px 10px', fontWeight: 700, color: 'var(--k-ink-4)' }}>Performance Metric</th>
                        <th style={{ textAlign: 'right', padding: '8px 10px', fontWeight: 700, color: '#d32f2f' }}>🕰️ V1 Legacy Baseline</th>
                        <th style={{ textAlign: 'right', padding: '8px 10px', fontWeight: 700, color: '#2e7d32' }}>🛡️ V2 Production-Hardened</th>
                        <th style={{ textAlign: 'right', padding: '8px 10px', fontWeight: 700, color: 'var(--k-ink-4)' }}>Delta Attribution</th>
                      </tr>
                    </thead>
                    <tbody>
                      <tr style={{ borderBottom: `1px solid ${k.border}` }}>
                        <td style={{ padding: '8px 10px', fontWeight: 600, color: 'var(--k-text)' }}>Net Realized P&L</td>
                        <td style={{ padding: '8px 10px', textAlign: 'right', fontWeight: 700, color: comparisonResult.v1.metrics.net_pnl_inr >= 0 ? '#2e7d32' : '#d32f2f' }}>
                          {fmtCurr(comparisonResult.v1.metrics.net_pnl_inr)} ({comparisonResult.v1.metrics.total_return_pct}%)
                        </td>
                        <td style={{ padding: '8px 10px', textAlign: 'right', fontWeight: 700, color: comparisonResult.v2.metrics.net_pnl_inr >= 0 ? '#2e7d32' : '#d32f2f' }}>
                          {fmtCurr(comparisonResult.v2.metrics.net_pnl_inr)} ({comparisonResult.v2.metrics.total_return_pct}%)
                        </td>
                        <td style={{ padding: '8px 10px', textAlign: 'right', fontWeight: 800, color: comparisonResult.comparison.net_pnl_delta_inr >= 0 ? '#2e7d32' : '#d32f2f' }}>
                          {fmtCurr(comparisonResult.comparison.net_pnl_delta_inr)}
                        </td>
                      </tr>
                      <tr style={{ borderBottom: `1px solid ${k.border}` }}>
                        <td style={{ padding: '8px 10px', fontWeight: 600, color: 'var(--k-text)' }}>Win Rate</td>
                        <td style={{ padding: '8px 10px', textAlign: 'right', fontWeight: 600, color: 'var(--k-ink-2)' }}>
                          {comparisonResult.v1.metrics.win_rate_pct}% ({comparisonResult.v1.metrics.winning_trades}/{comparisonResult.v1.metrics.total_trades})
                        </td>
                        <td style={{ padding: '8px 10px', textAlign: 'right', fontWeight: 600, color: 'var(--k-ink-2)' }}>
                          {comparisonResult.v2.metrics.win_rate_pct}% ({comparisonResult.v2.metrics.winning_trades}/{comparisonResult.v2.metrics.total_trades})
                        </td>
                        <td style={{ padding: '8px 10px', textAlign: 'right', fontWeight: 700, color: comparisonResult.comparison.win_rate_delta_pct >= 0 ? '#2e7d32' : '#d32f2f' }}>
                          {comparisonResult.comparison.win_rate_delta_pct >= 0 ? '+' : ''}{comparisonResult.comparison.win_rate_delta_pct}%
                        </td>
                      </tr>
                      <tr style={{ borderBottom: `1px solid ${k.border}` }}>
                        <td style={{ padding: '8px 10px', fontWeight: 600, color: 'var(--k-text)' }}>Max Drawdown</td>
                        <td style={{ padding: '8px 10px', textAlign: 'right', fontWeight: 600, color: '#d32f2f' }}>
                          {comparisonResult.v1.metrics.max_drawdown_pct}% ({fmtCurr(comparisonResult.v1.metrics.max_drawdown_inr)})
                        </td>
                        <td style={{ padding: '8px 10px', textAlign: 'right', fontWeight: 600, color: '#2e7d32' }}>
                          {comparisonResult.v2.metrics.max_drawdown_pct}% ({fmtCurr(comparisonResult.v2.metrics.max_drawdown_inr)})
                        </td>
                        <td style={{ padding: '8px 10px', textAlign: 'right', fontWeight: 700, color: '#2e7d32' }}>
                          -{comparisonResult.comparison.max_drawdown_reduction_pct}%
                        </td>
                      </tr>
                      <tr style={{ borderBottom: `1px solid ${k.border}` }}>
                        <td style={{ padding: '8px 10px', fontWeight: 600, color: 'var(--k-text)' }}>Profit Factor</td>
                        <td style={{ padding: '8px 10px', textAlign: 'right', fontWeight: 600, color: 'var(--k-ink-2)' }}>
                          {comparisonResult.comparison.profit_factor_v1 !== null ? fmt(comparisonResult.comparison.profit_factor_v1) : 'N/A'}
                        </td>
                        <td style={{ padding: '8px 10px', textAlign: 'right', fontWeight: 600, color: 'var(--k-ink-2)' }}>
                          {comparisonResult.comparison.profit_factor_v2 !== null ? fmt(comparisonResult.comparison.profit_factor_v2) : 'N/A'}
                        </td>
                        <td style={{ padding: '8px 10px', textAlign: 'right', fontWeight: 700, color: '#2e7d32' }}>
                          {comparisonResult.comparison.profit_factor_v2 !== null && comparisonResult.comparison.profit_factor_v1 !== null
                            ? `${(comparisonResult.comparison.profit_factor_v2 - comparisonResult.comparison.profit_factor_v1) >= 0 ? '+' : ''}${fmt(comparisonResult.comparison.profit_factor_v2 - comparisonResult.comparison.profit_factor_v1)}`
                            : '—'}
                        </td>
                      </tr>
                      <tr style={{ borderBottom: `1px solid ${k.border}` }}>
                        <td style={{ padding: '8px 10px', fontWeight: 600, color: 'var(--k-text)' }}>Sharpe Ratio</td>
                        <td style={{ padding: '8px 10px', textAlign: 'right', fontWeight: 600, color: 'var(--k-ink-2)' }}>
                          {fmt(comparisonResult.v1.metrics.sharpe_ratio)}
                        </td>
                        <td style={{ padding: '8px 10px', textAlign: 'right', fontWeight: 600, color: 'var(--k-ink-2)' }}>
                          {fmt(comparisonResult.v2.metrics.sharpe_ratio)}
                        </td>
                        <td style={{ padding: '8px 10px', textAlign: 'right', fontWeight: 700, color: comparisonResult.comparison.sharpe_delta >= 0 ? '#2e7d32' : '#d32f2f' }}>
                          {comparisonResult.comparison.sharpe_delta >= 0 ? '+' : ''}{fmt(comparisonResult.comparison.sharpe_delta)}
                        </td>
                      </tr>
                      <tr>
                        <td style={{ padding: '8px 10px', fontWeight: 600, color: 'var(--k-text)' }}>Active Protections</td>
                        <td style={{ padding: '8px 10px', textAlign: 'right', color: 'var(--k-ink-5)', fontSize: 11 }}>
                          09:15 open entries • 100% full SL • Full slippage
                        </td>
                        <td style={{ padding: '8px 10px', textAlign: 'right', color: '#2e7d32', fontSize: 11, fontWeight: 600 }}>
                          09:28 lockout • Body filter • 1.5R 50/50 scale • 4-bar decay stop
                        </td>
                        <td style={{ padding: '8px 10px', textAlign: 'right', color: '#2e7d32', fontSize: 11, fontWeight: 700 }}>
                          {comparisonResult.comparison.toxic_trades_avoided} toxic avoided
                        </td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              )}

              {/* ── KPI Metric Scorecard ── */}
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(130px, 1fr))', gap: 12, marginBottom: 18 }}>
                <div style={{ background: 'var(--k-bg)', padding: '12px 14px', borderRadius: 6, border: `1px solid ${k.border}` }}>
                  <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--k-ink-6)', textTransform: 'uppercase' }}>Net P&L</div>
                  <div style={{ fontSize: 18, fontWeight: 800, color: result.metrics.net_pnl_inr >= 0 ? 'var(--k-green-deep)' : 'var(--k-red-crimson)', marginTop: 2 }}>
                    {fmtCurr(result.metrics.net_pnl_inr)}
                  </div>
                  <div style={{ fontSize: 11, color: result.metrics.total_return_pct >= 0 ? 'var(--k-green-deep)' : 'var(--k-red-crimson)', fontWeight: 600 }}>
                    {result.metrics.total_return_pct >= 0 ? '+' : ''}{result.metrics.total_return_pct}%
                  </div>
                </div>

                <div style={{ background: 'var(--k-bg)', padding: '12px 14px', borderRadius: 6, border: `1px solid ${k.border}` }}>
                  <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--k-ink-6)', textTransform: 'uppercase' }}>Sharpe Ratio</div>
                  <div style={{ fontSize: 18, fontWeight: 800, color: result.metrics.sharpe_ratio >= 1.5 ? 'var(--k-green-deep)' : 'var(--k-ink-1)', marginTop: 2 }}>
                    {fmt(result.metrics.sharpe_ratio)}
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--k-ink-6)' }}>Sortino: {fmt(result.metrics.sortino_ratio)}</div>
                </div>

                <div style={{ background: 'var(--k-bg)', padding: '12px 14px', borderRadius: 6, border: `1px solid ${k.border}` }}>
                  <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--k-ink-6)', textTransform: 'uppercase' }}>Win Rate</div>
                  <div style={{ fontSize: 18, fontWeight: 800, color: result.metrics.win_rate_pct >= 50 ? 'var(--k-green-deep)' : 'var(--k-red-crimson)', marginTop: 2 }}>
                    {result.metrics.win_rate_pct}%
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--k-ink-6)' }}>{result.metrics.winning_trades}W / {result.metrics.losing_trades}L</div>
                </div>

                <div style={{ background: 'var(--k-bg)', padding: '12px 14px', borderRadius: 6, border: `1px solid ${k.border}` }}>
                  <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--k-ink-6)', textTransform: 'uppercase' }}>Profit Factor</div>
                  <div style={{ fontSize: 18, fontWeight: 800, color: result.metrics.profit_factor >= 1.5 ? 'var(--k-green-deep)' : 'var(--k-ink-1)', marginTop: 2 }}>
                    {fmt(result.metrics.profit_factor)}
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--k-ink-6)' }}>Payoff: {fmt(result.metrics.payoff_ratio)}x</div>
                </div>

                <div style={{ background: 'var(--k-bg)', padding: '12px 14px', borderRadius: 6, border: `1px solid ${k.border}` }}>
                  <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--k-ink-6)', textTransform: 'uppercase' }}>Max Drawdown</div>
                  <div style={{ fontSize: 18, fontWeight: 800, color: result.metrics.max_drawdown_pct <= 5 ? 'var(--k-green-deep)' : 'var(--k-red-crimson)', marginTop: 2 }}>
                    -{result.metrics.max_drawdown_pct}%
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--k-ink-6)' }}>{fmtCurr(-result.metrics.max_drawdown_inr)}</div>
                </div>

                <div style={{ background: 'var(--k-bg)', padding: '12px 14px', borderRadius: 6, border: `1px solid ${k.border}` }}>
                  <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--k-ink-6)', textTransform: 'uppercase' }}>Total Trades</div>
                  <div style={{ fontSize: 18, fontWeight: 800, color: 'var(--k-ink-1)', marginTop: 2 }}>
                    {result.metrics.total_trades}
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--k-ink-6)' }}>Exp: {fmtCurr(result.metrics.expectancy_inr)}</div>
                </div>

                <div style={{ background: 'var(--k-bg)', padding: '12px 14px', borderRadius: 6, border: `1px solid ${k.border}` }}>
                  <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--k-ink-6)', textTransform: 'uppercase' }}>Friction / STT</div>
                  <div style={{ fontSize: 18, fontWeight: 800, color: 'var(--k-ink-6)', marginTop: 2 }}>
                    ₹{fmt(result.metrics.total_friction_inr, 0)}
                  </div>
                  <div style={{ fontSize: 11, color: 'var(--k-ink-6)' }}>Drag: {result.metrics.friction_drag_pct}%</div>
                </div>
              </div>

              {/* ── Visualization Navigation Tabs ── */}
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', borderBottom: `1px solid ${k.border}`, background: 'var(--k-bg)', borderRadius: '6px 6px 0 0', padding: '0 12px' }}>
                <div style={{ display: 'flex', gap: 6 }}>
                  {[
                    { id: 'equity' as const, label: '📈 Equity Curve' },
                    { id: 'drawdown' as const, label: '🌊 Underwater Drawdown' },
                    { id: 'trades' as const, label: `📑 Trade Ledger (${result.trades.length})` },
                    { id: 'monte_carlo' as const, label: '🎲 Monte Carlo (500 Paths)' },
                  ].map((t) => {
                    const active = activeTab === t.id;
                    return (
                      <button
                        key={t.id}
                        onClick={() => setActiveTab(t.id)}
                        style={{
                          padding: '12px 16px', border: 'none', background: 'transparent', cursor: 'pointer',
                          fontSize: 13, fontWeight: active ? 700 : 500,
                          color: active ? 'var(--k-brand)' : 'var(--k-ink-4)',
                          borderBottom: active ? '2px solid var(--k-brand)' : '2px solid transparent',
                          marginBottom: -1,
                        }}
                      >
                        {t.label}
                      </button>
                    );
                  })}
                </div>

                {activeTab === 'trades' && (
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                    <div style={{ display: 'flex', gap: 4, background: 'var(--k-hairline-3)', padding: 2, borderRadius: 4 }}>
                      {(['ALL', 'WIN', 'LOSS'] as const).map((mode) => (
                        <button
                          key={mode}
                          onClick={() => setTradeFilter(mode)}
                          style={{
                            border: 'none', padding: '3px 8px', fontSize: 11, fontWeight: 700, borderRadius: 3,
                            background: tradeFilter === mode ? 'var(--k-bg)' : 'transparent',
                            color: tradeFilter === mode ? 'var(--k-ink-1)' : 'var(--k-ink-5)', cursor: 'pointer',
                          }}
                        >
                          {mode}
                        </button>
                      ))}
                    </div>
                    <button
                      onClick={handleExportCSV}
                      style={{
                        padding: '5px 12px', background: 'var(--k-surface-4)', border: `1px solid ${k.border}`,
                        borderRadius: 4, fontSize: 12, fontWeight: 600, color: 'var(--k-ink-1)', cursor: 'pointer',
                      }}
                    >
                      📥 Export CSV
                    </button>
                  </div>
                )}
              </div>

              {/* ── Tab Views ── */}
              <div style={{ background: 'var(--k-bg)', border: `1px solid ${k.border}`, borderTop: 'none', borderRadius: '0 0 6px 6px', padding: 20, flex: 1, minHeight: 380, overflowY: 'auto' }}>
                {activeTab === 'equity' && (
                  <div>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
                      <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--k-text)' }}>
                        Portfolio Capital Growth vs. High-Water Mark (INR) • {contractType.toUpperCase()}
                      </span>
                      <span style={{ fontSize: 12, color: 'var(--k-ink-6)' }}>
                        Evaluated {result.candles_evaluated} real candles ({result.start_date.substring(0, 10)} → {result.end_date.substring(0, 10)})
                      </span>
                    </div>

                    {/* SVG Equity Chart */}
                    <div style={{ width: '100%', height: 320, background: 'var(--k-surface-2)', borderRadius: 4, border: '1px solid var(--k-hairline-3)', padding: 10, position: 'relative' }}>
                      <svg width="100%" height="100%" viewBox="0 0 800 280" preserveAspectRatio="none">
                        {(() => {
                          const pts = result.equity_curve;
                          if (pts.length < 2) return null;
                          const values = pts.map((p) => p.equity);
                          const min = Math.min(...values) * 0.98;
                          const max = Math.max(...values, result.starting_capital) * 1.02;
                          const span = max - min || 1;

                          const coords = pts.map((p, i) => {
                            const x = (i / (pts.length - 1)) * 780 + 10;
                            const y = 260 - ((p.equity - min) / span) * 240;
                            return `${x.toFixed(1)},${y.toFixed(1)}`;
                          });

                          const hwmCoords = pts.map((p, i) => {
                            const x = (i / (pts.length - 1)) * 780 + 10;
                            const y = 260 - ((p.high_water_mark - min) / span) * 240;
                            return `${x.toFixed(1)},${y.toFixed(1)}`;
                          });

                          const polyline = coords.join(' ');
                          const area = `10,260 ${polyline} 790,260`;
                          const isUp = values[values.length - 1] >= values[0];

                          return (
                            <>
                              {/* Baseline Capital Line */}
                              <line
                                x1="10"
                                y1={260 - ((result.starting_capital - min) / span) * 240}
                                x2="790"
                                y2={260 - ((result.starting_capital - min) / span) * 240}
                                stroke="var(--k-faint-5)"
                                strokeDasharray="4 4"
                                strokeWidth="1"
                              />
                              {/* Area fill */}
                              <polygon points={area} fill={isUp ? 'rgba(46,125,50,0.08)' : 'rgba(198,40,40,0.08)'} />
                              {/* HWM Line */}
                              <polyline points={hwmCoords.join(' ')} fill="none" stroke="#90caf9" strokeDasharray="3 3" strokeWidth="1.5" />
                              {/* Equity Line */}
                              <polyline points={polyline} fill="none" stroke={isUp ? 'var(--k-green-deep)' : 'var(--k-red-crimson)'} strokeWidth="2.5" />
                            </>
                          );
                        })()}
                      </svg>
                    </div>
                  </div>
                )}

                {activeTab === 'drawdown' && (
                  <div>
                    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
                      <span style={{ fontSize: 13, fontWeight: 700, color: 'var(--k-text)' }}>
                        Underwater Drawdown Profile (%)
                      </span>
                      <span style={{ fontSize: 12, color: 'var(--k-red-crimson)', fontWeight: 600 }}>
                        Maximum Drawdown: -{result.metrics.max_drawdown_pct}%
                      </span>
                    </div>

                    <div style={{ width: '100%', height: 320, background: 'var(--k-surface-2)', borderRadius: 4, border: '1px solid var(--k-hairline-3)', padding: 10 }}>
                      <svg width="100%" height="100%" viewBox="0 0 800 280" preserveAspectRatio="none">
                        {(() => {
                          const pts = result.equity_curve;
                          if (pts.length < 2) return null;
                          const maxDd = Math.max(...pts.map((p) => p.drawdown_pct), 5);

                          const coords = pts.map((p, i) => {
                            const x = (i / (pts.length - 1)) * 780 + 10;
                            const y = 20 + (p.drawdown_pct / maxDd) * 240;
                            return `${x.toFixed(1)},${y.toFixed(1)}`;
                          });

                          const area = `10,20 ${coords.join(' ')} 790,20`;
                          return (
                            <>
                              <line x1="10" y1="20" x2="790" y2="20" stroke="var(--k-faint)" strokeWidth="1.5" />
                              <polygon points={area} fill="rgba(229,57,53,0.2)" />
                              <polyline points={coords.join(' ')} fill="none" stroke="var(--k-red-strong)" strokeWidth="2" />
                            </>
                          );
                        })()}
                      </svg>
                    </div>
                  </div>
                )}

                {activeTab === 'trades' && (
                  <div style={{ overflowX: 'auto' }}>
                    <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12, textAlign: 'left' }}>
                      <thead>
                        <tr style={{ background: '#f8f9fa', borderBottom: `2px solid ${k.border}`, color: 'var(--k-ink-4)' }}>
                          <th style={{ padding: '8px 10px' }}>#</th>
                          <th style={{ padding: '8px 10px' }}>Entry Time</th>
                          <th style={{ padding: '8px 10px' }}>Exit Time</th>
                          <th style={{ padding: '8px 10px' }}>Symbol</th>
                          <th style={{ padding: '8px 10px' }}>Side</th>
                          <th style={{ padding: '8px 10px' }}>Entry</th>
                          <th style={{ padding: '8px 10px' }}>Exit</th>
                          <th style={{ padding: '8px 10px' }}>Dynamic SL / TP</th>
                          <th style={{ padding: '8px 10px' }}>R:R</th>
                          <th style={{ padding: '8px 10px' }}>Net P&L</th>
                          <th style={{ padding: '8px 10px' }}>MAE / MFE</th>
                          <th style={{ padding: '8px 10px' }}>Exit Reason</th>
                        </tr>
                      </thead>
                      <tbody>
                        {filteredTrades.map((t) => {
                          const isWin = t.net_pnl > 0;
                          return (
                            <tr key={t.trade_id} style={{ borderBottom: '1px solid var(--k-hairline-3)' }}>
                              <td style={{ padding: '8px 10px', color: 'var(--k-ink-6)' }}>{t.trade_id}</td>
                              <td style={{ padding: '8px 10px' }}>{t.entry_time.replace('T', ' ').substring(5, 16)}</td>
                              <td style={{ padding: '8px 10px' }}>{t.exit_time.replace('T', ' ').substring(5, 16)}</td>
                              <td style={{ padding: '8px 10px', fontWeight: 600, color: 'var(--k-text)' }}>{t.symbol || result.symbol}</td>
                              <td style={{ padding: '8px 10px', fontWeight: 700, color: t.direction === 'LONG' ? 'var(--k-green-deep)' : 'var(--k-red-crimson)' }}>
                                {t.direction}
                              </td>
                              <td style={{ padding: '8px 10px' }}>₹{t.entry_price}</td>
                              <td style={{ padding: '8px 10px' }}>₹{t.exit_price}</td>
                              <td style={{ padding: '8px 10px', fontSize: 11.5, color: 'var(--k-ink-3)' }}>
                                {t.sl_points ? `${t.sl_points} / ${t.tp_points ?? '-'}` : '-'}
                              </td>
                              <td style={{ padding: '8px 10px', fontWeight: 700, color: (t.reward_to_risk ?? 0) >= 1.5 ? 'var(--k-green-deep)' : ((t.reward_to_risk ?? 0) <= 0 ? 'var(--k-red-crimson)' : 'var(--k-ink-1)') }}>
                                {t.reward_to_risk !== undefined ? `1:${t.reward_to_risk}R` : '-'}
                              </td>
                              <td style={{ padding: '8px 10px', fontWeight: 700, color: isWin ? 'var(--k-green-deep)' : 'var(--k-red-crimson)' }}>
                                {fmtCurr(t.net_pnl)} ({t.return_pct}%)
                              </td>
                              <td style={{ padding: '8px 10px', color: 'var(--k-ink-4)', fontSize: 11 }}>
                                -{t.mae_points} / +{t.mfe_points} pts
                              </td>
                              <td style={{ padding: '8px 10px' }}>
                                <span style={{
                                  fontSize: 10.5, fontWeight: 700, padding: '2px 6px', borderRadius: 3,
                                  background: t.exit_reason === 'TARGET' ? 'rgba(46,125,50,0.12)' : (t.exit_reason === 'TRAILING_STOP' ? 'rgba(25,118,210,0.12)' : (t.exit_reason === 'STOP_LOSS' ? 'rgba(198,40,40,0.12)' : 'var(--k-hairline-3)')),
                                  color: t.exit_reason === 'TARGET' ? 'var(--k-green-deep)' : (t.exit_reason === 'TRAILING_STOP' ? '#1976d2' : (t.exit_reason === 'STOP_LOSS' ? 'var(--k-red-crimson)' : 'var(--k-ink-3)')),
                                }}>
                                  {t.exit_reason}
                                </span>
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                )}

                {activeTab === 'monte_carlo' && (
                  <div>
                    <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--k-ink-1)', marginBottom: 6 }}>
                      🎲 500-Path Monte Carlo Resampling Simulation
                    </div>
                    <div style={{ fontSize: 12, color: 'var(--k-ink-5)', marginBottom: 16 }}>
                      Randomizes trade sequencing 500 times to compute statistical confidence bounds and evaluate drawdowns under unfavorable streak orderings.
                    </div>

                    {result.monte_carlo ? (
                      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(180px, 1fr))', gap: 14 }}>
                        <div style={{ background: 'var(--k-surface-2)', padding: 14, borderRadius: 6, border: '1px solid var(--k-hairline-3)' }}>
                          <div style={{ fontSize: 11, color: 'var(--k-ink-6)', textTransform: 'uppercase', fontWeight: 700 }}>Mean Expected Return</div>
                          <div style={{ fontSize: 20, fontWeight: 800, color: result.monte_carlo.mean_return_pct >= 0 ? 'var(--k-green-deep)' : 'var(--k-red-crimson)', marginTop: 4 }}>
                            {result.monte_carlo.mean_return_pct}%
                          </div>
                        </div>

                        <div style={{ background: 'var(--k-surface-2)', padding: 14, borderRadius: 6, border: '1px solid var(--k-hairline-3)' }}>
                          <div style={{ fontSize: 11, color: 'var(--k-ink-6)', textTransform: 'uppercase', fontWeight: 700 }}>5th Percentile (Worst 5%)</div>
                          <div style={{ fontSize: 20, fontWeight: 800, color: 'var(--k-red-crimson)', marginTop: 4 }}>
                            {result.monte_carlo.p5_return_pct}%
                          </div>
                        </div>

                        <div style={{ background: 'var(--k-surface-2)', padding: 14, borderRadius: 6, border: '1px solid var(--k-hairline-3)' }}>
                          <div style={{ fontSize: 11, color: 'var(--k-ink-6)', textTransform: 'uppercase', fontWeight: 700 }}>95th Percentile (Best 5%)</div>
                          <div style={{ fontSize: 20, fontWeight: 800, color: 'var(--k-green-deep)', marginTop: 4 }}>
                            +{result.monte_carlo.p95_return_pct}%
                          </div>
                        </div>

                        <div style={{ background: 'var(--k-surface-2)', padding: 14, borderRadius: 6, border: '1px solid var(--k-hairline-3)' }}>
                          <div style={{ fontSize: 11, color: 'var(--k-ink-6)', textTransform: 'uppercase', fontWeight: 700 }}>95% Max Drawdown Risk</div>
                          <div style={{ fontSize: 20, fontWeight: 800, color: 'var(--k-red-crimson)', marginTop: 4 }}>
                            -{result.monte_carlo.p95_max_drawdown_pct}%
                          </div>
                        </div>

                        <div style={{ background: 'var(--k-surface-2)', padding: 14, borderRadius: 6, border: '1px solid var(--k-hairline-3)' }}>
                          <div style={{ fontSize: 11, color: 'var(--k-ink-6)', textTransform: 'uppercase', fontWeight: 700 }}>Probability of Profit</div>
                          <div style={{ fontSize: 20, fontWeight: 800, color: result.monte_carlo.prob_profit_pct >= 70 ? 'var(--k-green-deep)' : 'var(--k-brand)', marginTop: 4 }}>
                            {result.monte_carlo.prob_profit_pct}%
                          </div>
                        </div>
                      </div>
                    ) : (
                      <div style={{ color: 'var(--k-ink-6)', fontSize: 13 }}>
                        Requires at least 5 completed trades to run Monte Carlo permutation analysis.
                      </div>
                    )}
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

export default UnifiedBacktestPane;
