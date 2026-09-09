"""
Unit tests for the Unified Institutional Multi-Strategy Backtesting Engine.
"""

import math
import pytest
import pandas as pd
import numpy as np

from app.schemas.backtest import UnifiedBacktestRequest, UnifiedBacktestResult
from app.engines.backtest.unified_engine import (
    run_unified_backtest,
    generate_strategy_signals,
    calculate_atr,
    calculate_supertrend,
    calculate_rsi,
    calculate_bollinger_bands,
)


@pytest.fixture
def sample_real_candles():
    """Generates 150 realistic 5-minute OHLCV candles."""
    np.random.seed(42)
    n = 150
    timestamps = pd.date_range("2026-05-01 09:15:00", periods=n, freq="5min")
    price = 24500.0
    candles = []
    for i in range(n):
        ret = np.random.normal(0.0002, 0.0015)
        price = price * (1.0 + ret)
        o = price + np.random.uniform(-5, 5)
        h = max(o, price) + abs(np.random.normal(0, 10))
        l = min(o, price) - abs(np.random.normal(0, 10))
        c = price
        v = float(np.random.randint(1000, 10000))
        candles.append({
            "timestamp": timestamps[i].isoformat(),
            "open": round(o, 2),
            "high": round(h, 2),
            "low": round(l, 2),
            "close": round(c, 2),
            "volume": v,
        })
    return candles


def test_indicator_calculations(sample_real_candles):
    df = pd.DataFrame(sample_real_candles)
    df["open"] = pd.to_numeric(df["open"])
    df["high"] = pd.to_numeric(df["high"])
    df["low"] = pd.to_numeric(df["low"])
    df["close"] = pd.to_numeric(df["close"])

    atr = calculate_atr(df, 14)
    assert len(atr) == len(df)
    assert not atr.iloc[-1] == 0.0

    st_trend, st_line = calculate_supertrend(df, 10, 3.0)
    assert len(st_trend) == len(df)

    rsi = calculate_rsi(df["close"], 14)
    assert len(rsi) == len(df)
    assert 0.0 <= rsi.dropna().iloc[-1] <= 100.0

    upper, mid, lower = calculate_bollinger_bands(df["close"], 20, 2.0)
    assert len(upper) == len(df)
    assert (upper.dropna() >= lower.dropna()).all()


def test_generate_strategy_signals_all_strategies(sample_real_candles):
    df = pd.DataFrame(sample_real_candles)
    strategies = ["adaptive_edge", "supertrend", "navigator", "directional", "mean_reversion"]
    for strat in strategies:
        long_sigs, short_sigs = generate_strategy_signals(df, strat)
        assert len(long_sigs) == len(df)
        assert len(short_sigs) == len(df)
        assert isinstance(long_sigs.iloc[0], (bool, np.bool_))
        assert isinstance(short_sigs.iloc[0], (bool, np.bool_))


def test_unified_backtest_execution_and_frictions(sample_real_candles):
    req = UnifiedBacktestRequest(
        strategy="adaptive_edge",
        symbol="NIFTY 50",
        timeframe="5m",
        lookback_days=10,
        starting_capital=100000.0,
        num_lots=2,
        slippage_points=0.5,
        brokerage_per_order=20.0,
        stt_pct=0.00125,
        stop_points=40.0,
        target_points=80.0,
        trail_points=25.0,
    )

    result = run_unified_backtest(sample_real_candles, req)
    assert isinstance(result, UnifiedBacktestResult)
    assert result.strategy == "adaptive_edge"
    assert result.symbol == "NIFTY 50"
    assert result.candles_evaluated == len(sample_real_candles)
    assert len(result.equity_curve) == len(sample_real_candles)
    assert result.starting_capital == 100000.0

    # Friction is strictly deducted
    metrics = result.metrics
    assert metrics.total_friction_inr >= 0.0
    if len(result.trades) > 0:
        t = result.trades[0]
        assert t.gross_pnl != 0.0
        assert t.friction_cost > 0.0
        assert t.net_pnl == round(t.gross_pnl - t.friction_cost, 2)
        assert t.exit_reason in ["TARGET", "STOP_LOSS", "TRAILING_STOP", "SESSION_CUTOFF", "SIGNAL_REVERSAL", "MANUAL_EXIT"]


def test_unified_backtest_dynamic_mode(sample_real_candles):
    req = UnifiedBacktestRequest(
        strategy="supertrend",
        symbol="NIFTY 50",
        dynamic_mode=True,
        timeframe="5m",
        lookback_days=10,
        starting_capital=100000.0,
        num_lots=2,
    )
    result = run_unified_backtest(sample_real_candles, req)
    assert isinstance(result, UnifiedBacktestResult)
    if len(result.trades) > 0:
        t = result.trades[0]
        assert t.sl_points is not None
        assert t.sl_points > 0
        assert t.tp_points is not None
        assert t.tp_points > t.sl_points


def test_unified_backtest_insufficient_candles():
    req = UnifiedBacktestRequest(strategy="supertrend", symbol="NIFTY 50")
    with pytest.raises(ValueError, match="Insufficient candle history"):
        run_unified_backtest([], req)


def test_monte_carlo_resampling_on_sufficient_trades(sample_real_candles):
    req = UnifiedBacktestRequest(
        strategy="mean_reversion",
        symbol="NIFTY 50",
        timeframe="5m",
        lookback_days=10,
        starting_capital=100000.0,
        stop_points=10.0,
        target_points=15.0,
    )
    result = run_unified_backtest(sample_real_candles, req)
    if len(result.trades) >= 5:
        assert result.monte_carlo is not None
        assert result.monte_carlo.simulations == 500
        assert isinstance(result.monte_carlo.mean_return_pct, float)
        assert 0.0 <= result.monte_carlo.prob_profit_pct <= 100.0


def test_adaptive_edge_v1_vs_v2_signals():
    # Build series with 09:15 candle and high wick
    dts = pd.date_range("2026-05-01 09:15:00", periods=30, freq="5min")
    df = pd.DataFrame({
        "open": [24500.0] * 30,
        "high": [24550.0] * 30,
        "low": [24490.0] * 30,
        "close": [24510.0] * 30, # lower close, long upper wick
        "volume": [50000.0] * 30,
        "dt": dts,
    })
    # V1 generates signals indiscriminately
    long_v1, _ = generate_strategy_signals(df, "adaptive_edge", {"strategy_version": "v1_baseline"})
    long_v2, _ = generate_strategy_signals(df, "adaptive_edge", {"strategy_version": "v2_hardened"})
    # First candle at 09:15 should be locked out in V2
    assert not bool(long_v2.iloc[0])


def test_adaptive_edge_comparison_run(sample_real_candles):
    from app.engines.backtest.unified_engine import run_adaptive_edge_comparison
    req = UnifiedBacktestRequest(
        strategy="adaptive_edge",
        symbol="NIFTY 50",
        timeframe="5m",
        starting_capital=150000.0,
        num_lots=2,
    )
    res = run_adaptive_edge_comparison(sample_real_candles, req, data_source_label="TEST")
    assert "v1" in res
    assert "v2" in res
    assert "comparison" in res
    cmp = res["comparison"]
    assert "net_pnl_delta_inr" in cmp
    assert "toxic_trades_avoided" in cmp
    assert "stagnation_exits" in cmp

