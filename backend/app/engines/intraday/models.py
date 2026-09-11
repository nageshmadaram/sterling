"""Shared shapes for the three intraday strategies.

One signal type for all three. The engines differ in what makes them fire, not
in what a trade is, and a board that had to hold three vocabularies for "stop"
is the bug this codebase has already paid for once.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional, Sequence

import numpy as np
from numpy.typing import NDArray

Direction = Literal["BULLISH", "BEARISH"]
OptionType = Literal["CE", "PE"]

#: Every strategy id this pack publishes. The ids are the strings the board, the
#: replay dock and ``SimSignalEvent.strategy`` all key on — a mismatch here does
#: not error, it silently filters everything out.
STRATEGY_IDS: tuple[str, ...] = ("pivot_break", "ma_ribbon", "vwap_supertrend")


@dataclass(frozen=True)
class Bars:
    """A column-oriented view of a candle list, built once per evaluation.

    The strategies each need three or four indicators over the same series;
    re-deriving numpy arrays inside every one of them was the obvious cost to
    remove, and it is the only reason this type exists.
    """

    time: NDArray[np.float64]          # epoch seconds, ascending
    open: NDArray[np.float64]
    high: NDArray[np.float64]
    low: NDArray[np.float64]
    close: NDArray[np.float64]
    volume: NDArray[np.float64]
    session_day: tuple[str, ...]       # IST calendar date per bar, "YYYY-MM-DD"

    def __len__(self) -> int:
        return int(len(self.close))

    @property
    def session_starts(self) -> tuple[bool, ...]:
        days = self.session_day
        return tuple(i == 0 or days[i] != days[i - 1] for i in range(len(days)))

    @property
    def last_session_start(self) -> int:
        starts = self.session_starts
        return max((i for i, s in enumerate(starts) if s), default=0)


@dataclass(frozen=True)
class IntradaySignal:
    """One actionable setup, in spot terms plus the option side to buy.

    ``stop`` and ``target`` are spot prices. The premium leg is chosen by the
    shared contract picker downstream — this engine's job is to say *which way*
    and *where the trade is wrong*, which is the part the three strategies
    actually disagree about.
    """

    strategy: str
    symbol: str
    direction: Direction
    option_type: OptionType
    timestamp_ms: int
    entry: float
    stop: float
    target: float
    #: The 1:3 extension, when the strategy runs a two-stage target. ``None``
    #: when a strategy has one fixed objective (VWAP/SuperTrend's 20 points).
    target2: Optional[float]
    risk: float                       # entry - stop, always positive
    strength: Literal["STRONG", "MODERATE", "WATCHING"]
    #: Which condition is carrying the signal, in the strategy's own words.
    origin: str
    reasons: tuple[str, ...] = ()
    metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def rr1(self) -> float:
        return abs(self.target - self.entry) / self.risk if self.risk > 0 else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "symbol": self.symbol,
            "direction": self.direction,
            "opt_type": self.option_type,
            "timestamp_ms": self.timestamp_ms,
            "entry": round(self.entry, 2),
            "stop": round(self.stop, 2),
            "target": round(self.target, 2),
            "target2": round(self.target2, 2) if self.target2 is not None else None,
            "risk": round(self.risk, 2),
            "rr": round(self.rr1, 2),
            "strength": self.strength,
            "origin": self.origin,
            "reasons": list(self.reasons),
            "metrics": {k: (round(v, 4) if isinstance(v, float) else v)
                        for k, v in self.metrics.items()},
        }


def _ist_day(epoch_s: float) -> str:
    """IST calendar date of an epoch second, without importing a tz database.

    IST is a fixed +05:30 with no daylight saving, so the offset is arithmetic.
    """
    return _EPOCH_DAY(epoch_s)


def _EPOCH_DAY(epoch_s: float) -> str:
    from datetime import datetime, timedelta, timezone
    ist = timezone(timedelta(hours=5, minutes=30))
    return datetime.fromtimestamp(float(epoch_s), tz=ist).strftime("%Y-%m-%d")


def _epoch_seconds(bar: Any) -> float:
    """Seconds from whichever of the four time spellings this bar uses.

    Candles reach this engine from four places — the Kite client, the replay
    store, the simulation's bar history and a test fixture — and they spell the
    timestamp four ways. Guessing wrong does not raise; it silently mis-dates
    every session boundary, which is why this is one function.
    """
    if isinstance(bar, dict):
        for key in ("time", "timestamp", "ts"):
            v = bar.get(key)
            if v is not None:
                v = float(v)
                return v / 1000.0 if v > 1e11 else v
        for key in ("timestamp_ms", "time_ms"):
            v = bar.get(key)
            if v is not None:
                return float(v) / 1000.0
        v = bar.get("date")
        if v is not None:
            from datetime import datetime
            if hasattr(v, "timestamp"):
                return float(v.timestamp())
            return float(datetime.fromisoformat(str(v)).timestamp())
        return 0.0
    for key in ("timestamp_ms", "time_ms"):
        v = getattr(bar, key, None)
        if v is not None:
            return float(v) / 1000.0
    for key in ("time", "timestamp", "ts"):
        v = getattr(bar, key, None)
        if v is not None:
            v = float(v)
            return v / 1000.0 if v > 1e11 else v
    v = getattr(bar, "date", None)
    if v is not None and hasattr(v, "timestamp"):
        return float(v.timestamp())
    return 0.0


def _field(bar: Any, name: str, default: float = 0.0) -> float:
    v = bar.get(name, default) if isinstance(bar, dict) else getattr(bar, name, default)
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if np.isfinite(f) else default


def to_bars(candles: Sequence[Any]) -> Bars:
    """Build the column view from any candle representation used in this repo."""
    rows = list(candles or [])
    n = len(rows)
    t = np.zeros(n); o = np.zeros(n); h = np.zeros(n)
    lo = np.zeros(n); c = np.zeros(n); v = np.zeros(n)
    for i, bar in enumerate(rows):
        t[i] = _epoch_seconds(bar)
        o[i] = _field(bar, "open")
        h[i] = _field(bar, "high")
        lo[i] = _field(bar, "low")
        c[i] = _field(bar, "close")
        v[i] = _field(bar, "volume")
    return Bars(time=t, open=o, high=h, low=lo, close=c, volume=v,
                session_day=tuple(_ist_day(x) for x in t))


def resample(candles: Sequence[Any], minutes: int) -> list[dict[str, float]]:
    """Aggregate finer bars up to ``minutes``, bucketed on the IST wall clock.

    The simulation replays 1-minute tape; these strategies are specified on
    5-minute candles. Aggregating on a fixed epoch modulus would place the
    bucket boundary at :00 UTC and put NSE's 09:15 open in the middle of a
    bucket, so the boundary is taken from minutes-since-midnight IST instead —
    the same grid the broker's own 5minute candles use.

    Bars already at or coarser than ``minutes`` are returned unchanged.
    """
    rows = list(candles or [])
    if minutes <= 1 or len(rows) < 2:
        return [_as_dict(b) for b in rows]
    step = _median_step_seconds(rows)
    if step >= minutes * 60:
        return [_as_dict(b) for b in rows]

    out: list[dict[str, float]] = []
    bucket_key: Optional[tuple[str, int]] = None
    for bar in rows:
        ts = _epoch_seconds(bar)
        day = _ist_day(ts)
        ist_minute = int(((ts + 19800) % 86400) // 60)
        key = (day, ist_minute // minutes)
        if key != bucket_key:
            out.append({"time": ts, "open": _field(bar, "open"),
                        "high": _field(bar, "high"), "low": _field(bar, "low"),
                        "close": _field(bar, "close"), "volume": _field(bar, "volume")})
            bucket_key = key
            continue
        agg = out[-1]
        agg["high"] = max(agg["high"], _field(bar, "high"))
        agg["low"] = min(agg["low"], _field(bar, "low"))
        agg["close"] = _field(bar, "close")
        agg["volume"] += _field(bar, "volume")
    return out


def _as_dict(bar: Any) -> dict[str, float]:
    return {"time": _epoch_seconds(bar), "open": _field(bar, "open"),
            "high": _field(bar, "high"), "low": _field(bar, "low"),
            "close": _field(bar, "close"), "volume": _field(bar, "volume")}


def _median_step_seconds(rows: Sequence[Any]) -> float:
    times = [_epoch_seconds(b) for b in rows[-40:]]
    gaps = [b - a for a, b in zip(times, times[1:]) if 0 < (b - a) < 86400]
    return float(np.median(gaps)) if gaps else 0.0
