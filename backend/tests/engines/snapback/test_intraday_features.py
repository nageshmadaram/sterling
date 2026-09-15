"""Unit tests for causal indicator feature snapshot calculations."""

import math
import pytest
from app.engines.snapback.config import SnapbackConfig
from app.engines.snapback.intraday_features import (
    calculate_bollinger_bands,
    calculate_ema,
    calculate_atr,
    calculate_adx,
    build_feature_snapshot,
)


def test_bollinger_bands_calculation():
    closes = [float(100 + i) for i in range(25)]
    mean, upper, lower, std_dev = calculate_bollinger_bands(closes, period=20)

    assert math.isfinite(mean)
    assert math.isfinite(upper)
    assert math.isfinite(lower)
    assert upper > mean > lower
    assert std_dev > 0.0


def test_bollinger_bands_requires_period_window():
    closes = [100.0] * 10
    mean, upper, lower, std_dev = calculate_bollinger_bands(closes, period=20)
    assert math.isnan(mean)


def test_feature_snapshot_warmup_requirement():
    cfg = SnapbackConfig(trading_mode="scalp")
    bars = [{"time": 1700000000 + i * 60, "open": 100, "high": 105, "low": 95, "close": 100, "volume": 1000} for i in range(20)]

    # 20 bars is below 30-bar warmup minimum -> returns None
    snap = build_feature_snapshot(bars, cfg, min_warmup_bars=30)
    assert snap is None

    # 35 bars satisfies warmup minimum
    bars_35 = [{"time": 1700000000 + i * 60, "open": 100, "high": 105, "low": 95, "close": 100, "volume": 1000} for i in range(35)]
    snap_35 = build_feature_snapshot(bars_35, cfg, min_warmup_bars=30)
    assert snap_35 is not None
    assert snap_35.is_finite()
    assert "bollinger" in snap_35.available_features
    assert snap_35.ema9 is None  # disabled in default baseline config


def test_feature_snapshot_experiment_toggles():
    cfg = SnapbackConfig(trading_mode="scalp", use_ema_confirmation=True, use_adx_filter=True)
    bars = [{"time": 1700000000 + i * 60, "open": 100 + i, "high": 105 + i, "low": 95 + i, "close": 100 + i, "volume": 1000} for i in range(40)]

    snap = build_feature_snapshot(bars, cfg, min_warmup_bars=30)
    assert snap is not None
    assert "ema9" in snap.available_features
    assert "adx14" in snap.available_features
    assert snap.ema9 is not None
    assert snap.adx14 is not None
