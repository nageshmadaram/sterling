"""The entry rule: open interest falling while volume and premium rise."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional, Sequence

from .config import GammaMoveConfig
from .models import OICandle, TriggerMetrics

_IST = timezone(timedelta(hours=5, minutes=30))
_BAR_MS = {"5minute": 300_000, "15minute": 900_000, "30minute": 1_800_000}


def session_day(ts_ms: int) -> str:
    return datetime.fromtimestamp(ts_ms / 1000, _IST).strftime("%Y-%m-%d")


def slice_session(candles: Sequence[OICandle], day: Optional[str] = None) -> list[OICandle]:
    if not candles:
        return []
    target = day or session_day(candles[-1].ts_ms)
    return [c for c in candles if session_day(c.ts_ms) == target]


def volume_baseline(candles: Sequence[OICandle], upto: int, lookback: int) -> Optional[float]:
    if upto < lookback:
        return None
    window = candles[upto - lookback:upto]
    if len(window) < lookback:
        return None
    total = sum(c.volume for c in window)
    mean = total / lookback
    return mean if mean > 0 else None


def evaluate_bar(candles: Sequence[OICandle], index: int,
                 cfg: GammaMoveConfig) -> Optional[TriggerMetrics]:
    if index <= 0 or index >= len(candles):
        return None
    cur, prev = candles[index], candles[index - 1]
    if session_day(cur.ts_ms) != session_day(prev.ts_ms):
        return None
    if prev.oi <= 0 or prev.close <= 0 or cur.close <= 0:
        return None
    base = volume_baseline(candles, index, cfg.volume_lookback)
    if base is None:
        return None
    oi_drop = (prev.oi - cur.oi) / prev.oi * 100.0
    vol_ratio = cur.volume / base
    price_gain = (cur.close - prev.close) / prev.close * 100.0
    return TriggerMetrics(
        oi_drop_pct=oi_drop, volume_ratio=vol_ratio, price_gain_pct=price_gain,
        unwinding=oi_drop >= cfg.min_oi_drop_pct,
        abnormal=vol_ratio >= cfg.volume_spike_mult,
        rising=price_gain >= cfg.min_price_gain_pct,
        bars_confirmed=0, bars_required=cfg.confirm_bars,
    )


def closed_bars(candles: Sequence[OICandle], cfg: GammaMoveConfig,
                now_ms: Optional[int]) -> list[OICandle]:
    series = list(candles)
    if not series or now_ms is None:
        return series
    last = series[-1]
    width = _BAR_MS.get(cfg.trigger_timeframe, 900_000)
    if last.ts_ms < now_ms < last.ts_ms + width:
        return series[:-1]
    return series


def evaluate(candles: Sequence[OICandle], cfg: GammaMoveConfig,
             *, day: Optional[str] = None,
             now_ms: Optional[int] = None) -> Optional[TriggerMetrics]:
    if not candles:
        return None
    candles = closed_bars(candles, cfg, now_ms)
    today = slice_session(candles, day)
    if len(today) < 2:
        return None
    full = list(candles)
    last_ts = today[-1].ts_ms
    idx = next((i for i in range(len(full) - 1, -1, -1) if full[i].ts_ms == last_ts), None)
    if idx is None:
        return None
    latest = evaluate_bar(full, idx, cfg)
    if latest is None:
        return None
    confirmed = 0
    for back in range(cfg.confirm_bars):
        m = evaluate_bar(full, idx - back, cfg)
        if m is None or not (m.unwinding and m.abnormal and m.rising):
            break
        confirmed += 1
    return TriggerMetrics(
        oi_drop_pct=latest.oi_drop_pct, volume_ratio=latest.volume_ratio,
        price_gain_pct=latest.price_gain_pct, unwinding=latest.unwinding,
        abnormal=latest.abnormal, rising=latest.rising,
        bars_confirmed=confirmed, bars_required=cfg.confirm_bars,
    )
