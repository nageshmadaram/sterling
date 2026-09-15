"""Causal indicator feature calculations for Snapback intraday and scalping mode.

Enforces strict prefix causality:
- Features evaluated at completed bar boundary t use only observations up to t.
- 30-bar contiguous same-session warmup required before producing valid setup features.
- Baseline calculation computes Bollinger Bands (20, 2); EMA, ADX, ATR are computed
  only when requested by configuration experiment flags.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

from app.engines.snapback.config import SnapbackConfig
from app.engines.snapback.intraday_models import FeatureSnapshot


def calculate_bollinger_bands(
    closes: List[float], period: int = 20, num_std: float = 2.0
) -> Tuple[float, float, float, float]:
    """Compute Bollinger mean, upper, lower, and sample std dev from a sequence of closes.

    Requires len(closes) >= period.
    """
    if len(closes) < period:
        return math.nan, math.nan, math.nan, math.nan

    window = closes[-period:]
    mean = sum(window) / float(period)
    variance = sum((x - mean) ** 2 for x in window) / float(period - 1 if period > 1 else 1)
    std_dev = math.sqrt(variance)

    upper = mean + num_std * std_dev
    lower = mean - num_std * std_dev

    return mean, upper, lower, std_dev


def calculate_ema(closes: List[float], period: int = 9) -> Optional[float]:
    """Compute EMA for the given closes sequence."""
    if len(closes) < period:
        return None
    multiplier = 2.0 / (period + 1)
    ema = sum(closes[:period]) / float(period)
    for price in closes[period:]:
        ema = (price - ema) * multiplier + ema
    return ema


def calculate_tr(high: float, low: float, prev_close: float) -> float:
    """Calculate True Range."""
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


def calculate_atr(
    highs: List[float], lows: List[float], closes: List[float], period: int = 14
) -> Optional[float]:
    """Compute Average True Range (ATR) over period bars."""
    if len(closes) < period + 1:
        return None
    tr_values: List[float] = []
    for i in range(1, len(closes)):
        tr_values.append(calculate_tr(highs[i], lows[i], closes[i - 1]))
    if len(tr_values) < period:
        return None
    atr = sum(tr_values[:period]) / float(period)
    for tr in tr_values[period:]:
        atr = (atr * (period - 1) + tr) / float(period)
    return atr


def calculate_adx(
    highs: List[float], lows: List[float], closes: List[float], period: int = 14
) -> Optional[float]:
    """Compute Average Directional Index (ADX)."""
    if len(closes) < 2 * period + 1:
        return None

    tr_list: List[float] = []
    dm_pos_list: List[float] = []
    dm_neg_list: List[float] = []

    for i in range(1, len(closes)):
        up_move = highs[i] - highs[i - 1]
        down_move = lows[i - 1] - lows[i]

        dm_pos = up_move if (up_move > down_move and up_move > 0) else 0.0
        dm_neg = down_move if (down_move > up_move and down_move > 0) else 0.0

        tr_list.append(calculate_tr(highs[i], lows[i], closes[i - 1]))
        dm_pos_list.append(dm_pos)
        dm_neg_list.append(dm_neg)

    if len(tr_list) < period:
        return None

    # Initial Wilder smoothing
    tr_smooth = sum(tr_list[:period])
    dm_pos_smooth = sum(dm_pos_list[:period])
    dm_neg_smooth = sum(dm_neg_list[:period])

    dx_list: List[float] = []

    for i in range(period, len(tr_list)):
        tr_smooth = tr_smooth - (tr_smooth / period) + tr_list[i]
        dm_pos_smooth = dm_pos_smooth - (dm_pos_smooth / period) + dm_pos_list[i]
        dm_neg_smooth = dm_neg_smooth - (dm_neg_smooth / period) + dm_neg_list[i]

        di_pos = (dm_pos_smooth / tr_smooth * 100.0) if tr_smooth > 0 else 0.0
        di_neg = (dm_neg_smooth / tr_smooth * 100.0) if tr_smooth > 0 else 0.0

        di_diff = abs(di_pos - di_neg)
        di_sum = di_pos + di_neg
        dx = (di_diff / di_sum * 100.0) if di_sum > 0 else 0.0
        dx_list.append(dx)

    if len(dx_list) < period:
        return None

    adx = sum(dx_list[:period]) / float(period)
    for dx in dx_list[period:]:
        adx = (adx * (period - 1) + dx) / float(period)

    return adx


def build_feature_snapshot(
    completed_bars: List[Dict[str, Any]],
    cfg: SnapbackConfig,
    *,
    min_warmup_bars: int = 30,
    period: int = 20,
) -> Optional[FeatureSnapshot]:
    """Extract causal features from a sequence of completed bars.

    Returns None if fewer than min_warmup_bars contiguous bars are provided.
    """
    if len(completed_bars) < min_warmup_bars:
        return None

    closes = [float(b["close"]) for b in completed_bars]
    highs = [float(b["high"]) for b in completed_bars]
    lows = [float(b["low"]) for b in completed_bars]
    last_bar = completed_bars[-1]
    ts_ms = int(last_bar.get("time", 0) * 1000 if last_bar.get("time", 0) < 1e11 else last_bar.get("time", 0))

    mean, upper, lower, std_dev = calculate_bollinger_bands(closes, period=period)
    if not math.isfinite(mean) or not math.isfinite(upper) or not math.isfinite(lower):
        return None

    available: List[str] = ["bollinger"]

    ema9 = calculate_ema(closes, period=9) if cfg.use_ema_confirmation else None
    if ema9 is not None:
        available.append("ema9")

    adx14 = calculate_adx(highs, lows, closes, period=14) if cfg.use_adx_filter else None
    if adx14 is not None:
        available.append("adx14")

    atr14 = calculate_atr(highs, lows, closes, period=14)

    return FeatureSnapshot(
        completed_bar_id=f"{ts_ms}",
        timestamp_ms=ts_ms,
        close=closes[-1],
        mean=mean,
        upper_band=upper,
        lower_band=lower,
        std_dev=std_dev,
        ema9=ema9,
        adx14=adx14,
        atr14=atr14,
        warmup_bars=len(completed_bars),
        available_features=available,
    )
