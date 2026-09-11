"""Indicators the three intraday strategies need that the shared pack lacks.

Everything else (EMA, ATR, SuperTrend) comes from ``app.engines.indicators`` —
one implementation per indicator, so a SuperTrend here is the same SuperTrend
the Kite engine trades.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np
from numpy.typing import NDArray


@dataclass(frozen=True)
class FibPivots:
    """Standard pivot set, Fibonacci variant, from one prior period's H/L/C.

    TradingView's "Fibonacci" pivot type: the pivot is the same classic
    ``(H+L+C)/3``; only the bands differ, sitting at 0.382 / 0.618 / 1.000 of
    the prior range. R3/S3 are a full range away, which is why price reaching
    them intraday is rare enough to be worth a flag rather than an entry.
    """

    p: float
    r1: float
    r2: float
    r3: float
    s1: float
    s2: float
    s3: float

    @property
    def resistances(self) -> tuple[tuple[str, float], ...]:
        return (("P", self.p), ("R1", self.r1), ("R2", self.r2), ("R3", self.r3))

    @property
    def supports(self) -> tuple[tuple[str, float], ...]:
        return (("P", self.p), ("S1", self.s1), ("S2", self.s2), ("S3", self.s3))

    def nearest_below(self, price: float) -> tuple[str, float] | tuple[None, None]:
        """Highest pivot level at or below ``price`` — the one just broken up through."""
        under = [(k, v) for k, v in self.all_levels() if v <= price]
        return max(under, key=lambda kv: kv[1]) if under else (None, None)

    def nearest_above(self, price: float) -> tuple[str, float] | tuple[None, None]:
        over = [(k, v) for k, v in self.all_levels() if v >= price]
        return min(over, key=lambda kv: kv[1]) if over else (None, None)

    def all_levels(self) -> tuple[tuple[str, float], ...]:
        return (("S3", self.s3), ("S2", self.s2), ("S1", self.s1), ("P", self.p),
                ("R1", self.r1), ("R2", self.r2), ("R3", self.r3))

    def as_dict(self) -> dict[str, float]:
        return {k: round(v, 2) for k, v in self.all_levels()}


def fib_pivots(high: float, low: float, close: float) -> Optional[FibPivots]:
    """Fibonacci pivots from the PRIOR period's H/L/C. ``None`` if the range is nil.

    A zero range is not a degenerate pivot set to be rendered as seven identical
    numbers — it means the prior period had no trading (a holiday, a halted
    instrument, a synthetic bar), and every "break" of such a level is an
    artefact. Refusing is the only honest answer.
    """
    rng = float(high) - float(low)
    if not np.isfinite(rng) or rng <= 0:
        return None
    p = (float(high) + float(low) + float(close)) / 3.0
    return FibPivots(
        p=p,
        r1=p + 0.382 * rng, r2=p + 0.618 * rng, r3=p + 1.000 * rng,
        s1=p - 0.382 * rng, s2=p - 0.618 * rng, s3=p - 1.000 * rng,
    )


def session_vwap(
    highs: NDArray[np.float64],
    lows: NDArray[np.float64],
    closes: NDArray[np.float64],
    volumes: NDArray[np.float64],
    session_starts: Sequence[bool],
) -> NDArray[np.float64]:
    """Anchored VWAP, reset at each session start.

    Typical price (HLC/3) weighted by volume, the TradingView default — not the
    close, which is what a naive implementation uses and which drifts visibly on
    wide bars.

    ``volume`` of zero for a whole session is the index case: Kite reports no
    volume on NIFTY/BANKNIFTY spot, and a volume-weighted average of zero weights
    is 0/0. That silently pinned VWAP onto the close in an earlier engine here
    and made every "close below VWAP" test false forever, so this falls back to a
    running average of typical price and the caller is told by
    :func:`vwap_is_volume_weighted` which of the two it got.
    """
    n = len(closes)
    out = np.zeros(n, dtype=np.float64)
    if n == 0:
        return out
    tp = (highs + lows + closes) / 3.0
    vol = np.where(np.isfinite(volumes), volumes, 0.0)

    # Session-anchored cumulative sums, vectorised. The loop this replaced ran
    # once per evaluation, and an evaluation happens on every bar of every
    # window of every fold in a walk-forward run — it was the single slowest
    # thing in this strategy's replay after SuperTrend's own recurrence.
    starts = np.asarray(session_starts, dtype=bool)
    # Index of the bar each session began on, carried forward.
    begin = np.maximum.accumulate(np.where(starts, np.arange(n), 0))
    begin[0] = 0

    def _session_cumsum(values: NDArray[np.float64]) -> NDArray[np.float64]:
        """Cumulative sum that resets at each session start."""
        total = np.cumsum(values)
        # What had accumulated BEFORE this session began.
        prior = np.where(begin > 0, total[begin - 1], 0.0)
        # A bar that IS a session start has nothing before it within its own
        # session, so its prior is everything up to the previous bar.
        prior = np.where(starts, np.concatenate(([0.0], total[:-1])), prior)
        return total - prior

    cum_pv = _session_cumsum(tp * vol)
    cum_v = _session_cumsum(vol)
    cum_tp = _session_cumsum(tp)
    cum_n = _session_cumsum(np.ones(n))

    weighted = cum_pv / np.where(cum_v > 0, cum_v, 1.0)
    # The index case: no volume at all, so a volume-weighted average is 0/0.
    # A running mean of typical price is the honest fallback, and
    # `vwap_is_volume_weighted` tells the caller which of the two it got.
    unweighted = cum_tp / np.where(cum_n > 0, cum_n, 1.0)
    out = np.where(cum_v > 0, weighted, unweighted)
    return out


def vwap_is_volume_weighted(volumes: NDArray[np.float64],
                            session_starts: Sequence[bool]) -> bool:
    """Whether the last session carried any volume at all.

    A VWAP computed without volume is a session mean, not a VWAP. Strategies
    that stake a stop on it need to know which one they are holding.
    """
    last = 0
    for i, s in enumerate(session_starts):
        if s:
            last = i
    tail = volumes[last:]
    return bool(len(tail)) and float(np.nansum(tail)) > 0.0
