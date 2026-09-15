"""Unit and integration tests for causal intraday replay and ablation runner."""

import pytest
from app.engines.snapback.config import SnapbackConfig
from app.services.snapback_replay import run_intraday_causal_replay


def test_run_intraday_causal_replay_execution():
    cfg = SnapbackConfig(trading_mode="scalp", scalp_target_points=5.0, scalp_stop_points=4.0)

    # Generate 40 synthetic spot bars (with upper band stretch and re-entry around bar 32)
    spot_bars = []
    base_time = 1700000000
    for i in range(40):
        if i == 31:
            close_price = 120.0  # stretch above band
        elif i == 32:
            close_price = 100.0  # re-entry back inside band
        else:
            close_price = 100.0 + (i % 3) * 0.5

        spot_bars.append({
            "time": base_time + i * 60,
            "open": 100.0,
            "high": max(100.0, close_price + 1.0),
            "low": min(100.0, close_price - 1.0),
            "close": close_price,
            "volume": 1000,
        })

    option_quotes = [
        {"timestamp_ms": (base_time + i * 60) * 1000, "bid": 99.0, "ask": 100.0}
        for i in range(40)
    ]

    res = run_intraday_causal_replay(spot_bars, option_quotes, cfg, symbol="NIFTY", lot_size=50)

    assert "opportunities" in res
    assert "completed_trades" in res
    assert "total_net_pnl" in res
    assert isinstance(res["trades"], list)
