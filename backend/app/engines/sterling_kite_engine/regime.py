"""Pure Sterling Kite Engine regime core — no I/O, no broker types.

Configured candle basis -> three SuperTrends -> per-bar bull/bear/flat regime,
fresh full-alignment entry transitions, and the trail line/trend selected by the
configured ``trail_target``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Tuple

import numpy as np
from numpy.typing import NDArray

from app.engines.indicators.atr import compute_atr
from app.engines.indicators.heikin_ashi import compute_heikin_ashi
from app.engines.indicators.supertrend import compute_supertrend
from app.engines.sterling_kite_engine.config import TrailTarget, SterlingKiteEngineConfig


@dataclass
class RegimeSeries:
    bull: NDArray[np.bool_]
    bear: NDArray[np.bool_]
    t_fast: NDArray[np.int64]
    t_mid: NDArray[np.int64]
    t_slow: NDArray[np.int64]
    l_fast: NDArray[np.float64]
    l_mid: NDArray[np.float64]
    l_slow: NDArray[np.float64]
    warmup: int
    # Basis prices drive indicators; only raw prices prove executable stop touches.
    raw_high: NDArray[np.float64] = field(default_factory=lambda: np.empty(0))
    raw_low: NDArray[np.float64] = field(default_factory=lambda: np.empty(0))
    basis_open: NDArray[np.float64] = field(default_factory=lambda: np.empty(0))
    basis_high: NDArray[np.float64] = field(default_factory=lambda: np.empty(0))
    basis_low: NDArray[np.float64] = field(default_factory=lambda: np.empty(0))
    basis_close: NDArray[np.float64] = field(default_factory=lambda: np.empty(0))
    atr: NDArray[np.float64] = field(default_factory=lambda: np.empty(0))

    def line(self, target: TrailTarget) -> NDArray[np.float64]:
        return {"fast": self.l_fast, "mid": self.l_mid, "slow": self.l_slow}[target]

    def trend(self, target: TrailTarget) -> NDArray[np.int64]:
        return {"fast": self.t_fast, "mid": self.t_mid, "slow": self.t_slow}[target]

    def red_line_count(self, direction: str, i: int) -> int:
        """Count how many ST lines are red (against the position) at bar ``i``.

        For a long position, red = trend == -1.
        For a short position, red = trend == +1.
        """
        against = -1 if direction == "long" else 1
        against = -1 if str(direction).lower() == "long" else 1
        count = 0
        if int(self.t_fast[i]) == against:
            count += 1
        if int(self.t_mid[i]) == against:
            count += 1
        if int(self.t_slow[i]) == against:
            count += 1
        return count

    def green_lines(self, direction: str, i: int) -> list:
        """Return the names of ST lines still aligned with the position at bar ``i``."""
        want = 1 if direction == "long" else -1
        want = 1 if str(direction).lower() == "long" else -1
        lines = []
        if int(self.t_slow[i]) == want:
            lines.append("slow")
        if int(self.t_mid[i]) == want:
            lines.append("mid")
        if int(self.t_fast[i]) == want:
            lines.append("fast")
        return lines

    def trail_value_for_threshold(self, i: int, threshold: int) -> float:
        """Return the ST line whose flip is the ``threshold``-th red."""
        name = {1: "fast", 2: "mid", 3: "slow"}.get(int(threshold), "fast")
        return float(self.line(name)[i])

    def best_trail_line_value(self, direction: str, i: int) -> float:
        """Return the tightest still-aligned ST line value at bar ``i``."""
        green = self.green_lines(direction, i)
        if not green:
            return 0.0
        return float(self.line(green[-1])[i])


def compute_regime(opens, highs, lows, closes, cfg: SterlingKiteEngineConfig) -> RegimeSeries:
    o = np.asarray(opens, dtype=float)
    h = np.asarray(highs, dtype=float)
    l = np.asarray(lows, dtype=float)
    c = np.asarray(closes, dtype=float)

    # Zerodha applies indicators to the candle series currently displayed. The
    # production scanner defaults to Heikin-Ashi. Raw OHLC remains distinct for
    # executable stop touches; synthetic HA extrema never prove a market fill.
    if cfg.candle_basis == "heikin_ashi":
        _, basis_h, basis_l, basis_c = compute_heikin_ashi(o, h, l, c)
        basis_o, basis_h, basis_l, basis_c = compute_heikin_ashi(o, h, l, c)
    else:
        basis_h, basis_l, basis_c = h, l, c
        basis_o, basis_h, basis_l, basis_c = o, h, l, c

    l_fast, t_fast = compute_supertrend(basis_h, basis_l, basis_c, cfg.fast[0], cfg.fast[1])
    l_mid, t_mid = compute_supertrend(basis_h, basis_l, basis_c, cfg.mid[0], cfg.mid[1])
    l_slow, t_slow = compute_supertrend(basis_h, basis_l, basis_c, cfg.slow[0], cfg.slow[1])

    valid = np.zeros(len(c), dtype=bool)
    valid[cfg.warmup:] = True

    bull = valid & (t_fast == 1) & (t_mid == 1) & (t_slow == 1)
    bear = valid & (t_fast == -1) & (t_mid == -1) & (t_slow == -1)

    atr_series = compute_atr(np.asarray(basis_h, dtype=float), np.asarray(basis_l, dtype=float), np.asarray(basis_c, dtype=float), cfg.fast[0])

    return RegimeSeries(
        bull=bull, bear=bear,
        t_fast=t_fast, t_mid=t_mid, t_slow=t_slow,
        l_fast=l_fast, l_mid=l_mid, l_slow=l_slow,
        warmup=cfg.warmup,
        raw_high=np.asarray(highs, dtype=float),
        raw_low=np.asarray(lows, dtype=float),
        basis_open=np.asarray(basis_o, dtype=float),
        basis_high=np.asarray(basis_h, dtype=float),
        basis_low=np.asarray(basis_l, dtype=float),
        basis_close=np.asarray(basis_c, dtype=float),
        atr=atr_series,
    )


def entry_transitions(r: RegimeSeries) -> Tuple[NDArray[np.bool_], NDArray[np.bool_]]:
    """Masks of bars that freshly enter full alignment (not aligned at i-1)."""
    prev_bull = np.concatenate([[False], r.bull[:-1]])
    prev_bear = np.concatenate([[False], r.bear[:-1]])
    longs = r.bull & ~prev_bull
    shorts = r.bear & ~prev_bear
    longs[: r.warmup + 1] = False
    shorts[: r.warmup + 1] = False
    return longs, shorts
