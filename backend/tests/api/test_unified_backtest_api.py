"""
API endpoint tests for the Unified Multi-Strategy Backtesting route.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints.backtest import router


def get_client() -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    return TestClient(app)


def test_get_unified_strategies():
    client = get_client()
    res = client.get("/api/v1/backtest/unified/strategies")
    assert res.status_code == 200
    strategies = res.json()
    assert len(strategies) == 5
    ids = [s["id"] for s in strategies]
    assert "adaptive_edge" in ids
    assert "supertrend" in ids
    assert "navigator" in ids
    assert "directional" in ids
    assert "mean_reversion" in ids


def test_get_unified_presets():
    client = get_client()
    res = client.get("/api/v1/backtest/unified/presets")
    assert res.status_code == 200
    presets = res.json()
    assert len(presets) >= 4


def test_run_unified_backtest_endpoint_kite_source():
    client = get_client()
    payload = {
        "strategy": "supertrend",
        "symbol": "NIFTY 50",
        "data_source": "kite",
        "timeframe": "5m",
        "lookback_days": 7,
        "starting_capital": 100000.0,
        "num_lots": 1,
        "stop_points": 40.0,
        "target_points": 80.0,
    }
    res = client.post("/api/v1/backtest/unified/run", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["strategy"] == "supertrend"
    assert data["symbol"] == "NIFTY 50"
    assert "KITE" in data["data_source"]
    assert "metrics" in data
    assert "equity_curve" in data
    assert "trades" in data
    assert data["starting_capital"] == 100000.0


def test_run_unified_backtest_endpoint_truedata_source():
    client = get_client()
    payload = {
        "strategy": "adaptive_edge",
        "symbol": "NIFTY BANK",
        "data_source": "truedata",
        "timeframe": "5m",
        "lookback_days": 7,
        "starting_capital": 150000.0,
        "num_lots": 2,
        "stop_points": 80.0,
        "target_points": 160.0,
    }
    res = client.post("/api/v1/backtest/unified/run", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert data["strategy"] == "adaptive_edge"
    assert data["symbol"] == "NIFTY BANK"
    assert "TRUEDATA" in data["data_source"]
    assert "metrics" in data
    assert "equity_curve" in data
    assert "trades" in data
    assert data["starting_capital"] == 150000.0


def test_run_adaptive_edge_compare_endpoint():
    client = get_client()
    payload = {
        "strategy": "adaptive_edge",
        "symbol": "NIFTY 50",
        "data_source": "kite",
        "timeframe": "5m",
        "lookback_days": 10,
        "starting_capital": 150000.0,
        "num_lots": 2,
    }
    res = client.post("/api/v1/backtest/adaptive-edge/compare", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert "v1" in data
    assert "v2" in data
    assert "comparison" in data
    cmp = data["comparison"]
    for key in ("net_pnl_delta_inr", "win_rate_delta_pct", "profit_factor_v1", "profit_factor_v2", "max_drawdown_reduction_pct", "toxic_trades_avoided"):
        assert key in cmp

