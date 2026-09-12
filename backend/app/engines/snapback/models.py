"""Shapes Snapback works in.

One signal type for both sides. ``fade_up`` and ``fade_down`` are mirrors of one
rule, not two strategies, and giving them separate types would put the mirror
symmetry somewhere a reader has to reconstruct.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional, Sequence

import numpy as np
from numpy.typing import NDArray

IST = timezone(timedelta(hours=5, minutes=30))

Side = Literal["fade_up", "fade_down"]
Direction = Literal["BULLISH", "BEARISH"]
OptionType = Literal["CE", "PE"]

STRATEGY_ID = "snapback"


@dataclass(frozen=True)
class Bars:
    """Daily candles as columns, built once per evaluation.

    Daily and not intraday, deliberately. The effect this engine trades is a
    multi-session reversion; measured on the same instruments at a 5-minute
    timeframe, this repo's own harness found the costs of trading it eat more
    than the whole gross edge.
    """

    time: NDArray[np.float64]          # epoch seconds of each session, ascending
    open: NDArray[np.float64]
    high: NDArray[np.float64]
    low: NDArray[np.float64]
    close: NDArray[np.float64]
    volume: NDArray[np.float64]

    def __len__(self) -> int:
        return int(len(self.close))

    def day(self, i: int) -> str:
        return ist_day(float(self.time[i]))


def ist_day(epoch_s: float) -> str:
    """IST calendar date of an epoch second. IST is a fixed +05:30, so this is
    arithmetic and needs no timezone database."""
    return datetime.fromtimestamp(float(epoch_s), tz=IST).strftime("%Y-%m-%d")


def to_bars(candles: Sequence[Any]) -> Bars:
    """Accepts dicts, objects with the usual attributes, or tuples.

    Sorted and de-duplicated by timestamp. A broker's historical endpoint can
    return the boundary bar of two adjacent windows twice, and a duplicated
    session shifts every rolling window by one without changing anything a
    reader would notice.
    """
    rows: list[tuple[float, float, float, float, float, float]] = []
    for c in candles:
        if isinstance(c, dict):
            t = c.get("time", c.get("timestamp",
                                    c.get("timestamp_ms", c.get("date"))))
            o, h, l, cl = c.get("open"), c.get("high"), c.get("low"), c.get("close")
            v = c.get("volume", 0.0)
        elif isinstance(c, (tuple, list)) and len(c) >= 5:
            t, o, h, l, cl = c[0], c[1], c[2], c[3], c[4]
            v = c[5] if len(c) > 5 else 0.0
        else:
            # ``timestamp_ms`` FIRST: that is the repo's own ``schemas.market
            # .Candle``, which is what every Kite client call returns. Looking
            # only for ``timestamp``/``time`` made every live bar unparseable,
            # so the scan built an empty tape for all 200 instruments and
            # reported "no signals" rather than "I could not read the candles".
            t = getattr(c, "timestamp_ms",
                        getattr(c, "timestamp", getattr(c, "time", None)))
            o, h, l, cl = (getattr(c, "open", None), getattr(c, "high", None),
                           getattr(c, "low", None), getattr(c, "close", None))
            v = getattr(c, "volume", 0.0)
        ts = _epoch_seconds(t)
        if ts is None or None in (o, h, l, cl):
            continue
        rows.append((ts, float(o), float(h), float(l), float(cl), float(v or 0.0)))
    rows.sort(key=lambda r: r[0])
    seen: dict[float, tuple] = {}
    for r in rows:
        seen[r[0]] = r
    rows = [seen[k] for k in sorted(seen)]
    if not rows:
        empty = np.array([], dtype=np.float64)
        return Bars(empty, empty, empty, empty, empty, empty)
    a = np.array(rows, dtype=np.float64)
    return Bars(a[:, 0], a[:, 1], a[:, 2], a[:, 3], a[:, 4], a[:, 5])


def _epoch_seconds(t: Any) -> Optional[float]:
    if t is None:
        return None
    if isinstance(t, (int, float)):
        v = float(t)
        # Milliseconds are common from the frontend and from some feeds; a
        # millisecond stamp read as seconds lands in the year 56000 and sorts
        # correctly, so it never looks wrong until a date is rendered.
        return v / 1000.0 if v > 1e11 else v
    if isinstance(t, datetime):
        return t.timestamp()
    try:
        from datetime import date as _date
        if isinstance(t, _date):
            return datetime(t.year, t.month, t.day, tzinfo=IST).timestamp()
    except Exception:                                              # noqa: BLE001
        pass
    try:
        return datetime.fromisoformat(str(t)).timestamp()
    except Exception:                                              # noqa: BLE001
        return None


@dataclass(frozen=True)
class SnapbackSignal:
    """One setup: an instrument that has run too far, and the option to buy.

    ``entry`` and ``mean_target`` are SPOT prices; the premium levels belong to
    the contract and are attached downstream by the picker, because the strike
    depends on a live quote when there is one and on the model when there is
    not — and a signal that carried a modelled premium into a live board would
    be presenting an assumption as a price.
    """

    symbol: str
    side: Side
    direction: Direction
    option_type: OptionType
    timestamp_ms: int
    #: Spot at the signal's close. The FILL is the next session's open.
    entry: float
    #: Where the thesis completes: the instrument's own 20-day mean.
    mean_target: float
    #: How far past that mean it currently sits, in ATR(14).
    stretch: float
    atr: float
    #: Annualised realised vol at entry, and the vol the premium is modelled at.
    realized_vol: float
    assumed_iv: float
    #: The breakout that qualified it: the high (or low) it closed through.
    level: float
    strength: Literal["STRONG", "MODERATE", "WATCHING"]
    reasons: tuple[str, ...] = ()
    metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def distance_pct(self) -> float:
        """How far spot has to travel for the thesis to complete, in percent."""
        if self.entry <= 0:
            return 0.0
        return abs(self.entry - self.mean_target) / self.entry * 100.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "strategy": STRATEGY_ID,
            "symbol": self.symbol,
            "side": self.side,
            "direction": self.direction,
            "opt_type": self.option_type,
            "timestamp_ms": self.timestamp_ms,
            "entry": round(self.entry, 2),
            "mean_target": round(self.mean_target, 2),
            "distance_pct": round(self.distance_pct, 2),
            "stretch": round(self.stretch, 2),
            "atr": round(self.atr, 2),
            "realized_vol": round(self.realized_vol, 4),
            "assumed_iv": round(self.assumed_iv, 4),
            "level": round(self.level, 2),
            "strength": self.strength,
            "reasons": list(self.reasons),
            "metrics": {k: (round(v, 4) if isinstance(v, float) else v)
                        for k, v in self.metrics.items()},
        }
