"""The rule, and what nine years say about it.

**What it trades.** An instrument that closes through its own 20-session high
AND sits at least 1.5 ATR above its 20-session mean, while the market is below
its own 50-session mean, is bought a put and held ten sessions.

**What it is worth.** Against a day-matched unconditional put the signal has a
real excess of about +1.5 percentage points — the names it picks under-perform.
The BOOK still loses: -1.08% per entry day out of sample on 202 underlyings over
nine years, entry-timing permutation p = 0.37. A bought put is a large short
position in a rising market and 1.5 points does not pay for it. See
``docs/strategy/snapback/VALIDATION_REPORT.md``, which is the authority.

**Two claims an earlier version of this file made that the data refutes:**

* *"More stretch is a stronger signal."* It is the reverse, monotonically:
  1.5-2 ATR returns -0.97%, 3-4 ATR -5.88%, 4+ ATR -6.98% over 9,295 trades.
  The old claim rested on 134.
* *"Buying calls on the same breakout loses, so the underlying reverts."* True
  on the narrow sample and still true in excess terms (-4.87pp), but it does not
  make the put side profitable, which is what it was cited for.

**What survives.** The market gate, because it is mechanism-driven rather than
found by search: it gates the put's beta, and it roughly doubles the excess. And
the option wrapper itself — the loss is the premium, which is the only reason a
book that fades strength is survivable at all.

**Why the harness values the exit at the ENTRY's vol.** Spot and vol are
negatively correlated, so a put bought into strength gains vol when the fade
works. Crediting that would flatter the result, so the vega term is exactly zero
and the measurement is a floor.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional, Sequence

import numpy as np
from numpy.typing import NDArray

from app.engines.indicators.atr import compute_atr
from app.engines.indicators.ema import compute_ema

from .config import SnapbackConfig
from .models import Bars, SnapbackSignal, ist_day
from .pricing import implied_vol_proxy, realized_vol


@dataclass(frozen=True)
class Features:
    """Everything the rule reads, computed once over the whole tape.

    A dataclass rather than five loose arrays because every one of these is
    causal — index ``i`` uses bars up to and including ``i`` — and keeping them
    together is what makes that property checkable in one test instead of five.
    """

    ema: NDArray[np.float64]
    atr: NDArray[np.float64]
    stretch: NDArray[np.float64]
    prior_high: NDArray[np.float64]
    prior_low: NDArray[np.float64]
    rv: NDArray[np.float64]
    iv: NDArray[np.float64]


def features(bars: Bars, cfg: SnapbackConfig) -> Features:
    n = len(bars)
    e = compute_ema(bars.close, cfg.mean_touch_ema)
    a = compute_atr(bars.high, bars.low, bars.close, 14)
    # An ATR of zero is a halted or synthetic session, not a tight one. Dividing
    # by it yields an infinite stretch, which passes every threshold there is.
    safe_atr = np.where(a > 0, a, np.nan)
    stretch = (bars.close - e) / safe_atr
    stretch = np.where(np.isfinite(stretch), stretch, 0.0)
    # The EMA is seeded with an SMA and reads plausible while it is still wrong,
    # so the stretch it feeds is meaningless before the seed has washed out.
    stretch[:cfg.mean_touch_ema] = 0.0

    look = int(cfg.lookback_days)
    prior_high = np.full(n, np.nan)
    prior_low = np.full(n, np.nan)
    if n > look:
        # STRICTLY prior bars. Including bar i makes `close > prior_high` almost
        # never true (the bar's own high is >= its close), which reads as "the
        # signal is rare" rather than as "the window is wrong".
        #
        # A sliding window rather than a loop: this runs once per symbol per
        # replay, and the permutation test replays the whole book hundreds of
        # times.
        hw = np.lib.stride_tricks.sliding_window_view(bars.high[:n - 1], look)
        lw = np.lib.stride_tricks.sliding_window_view(bars.low[:n - 1], look)
        prior_high[look:] = hw.max(axis=1)
        prior_low[look:] = lw.min(axis=1)

    rv = realized_vol(bars.close, cfg.rv_window)
    return Features(ema=e, atr=a, stretch=stretch, prior_high=prior_high,
                    prior_low=prior_low, rv=rv,
                    iv=implied_vol_proxy(rv, cfg.assumed_vrp))


def fires(f: Features, i: int, side: str, cfg: SnapbackConfig,
          bars: Bars) -> bool:
    """Whether ``side`` triggers on the CLOSE of bar ``i``."""
    if i < cfg.warmup_bars() or i >= len(bars):
        return False
    if not np.isfinite(f.rv[i]) or f.rv[i] <= 0:
        return False
    if cfg.min_atr_bp > 0:
        px = float(bars.close[i])
        if px <= 0 or (f.atr[i] / px) * 10_000.0 < cfg.min_atr_bp:
            return False
    if side == "fade_up":
        if not np.isfinite(f.prior_high[i]):
            return False
        return (bars.close[i] > f.prior_high[i]
                and f.stretch[i] >= cfg.min_stretch_atr)
    if side == "fade_down":
        if not cfg.allow_fade_down or not np.isfinite(f.prior_low[i]):
            return False
        return (bars.close[i] < f.prior_low[i]
                and f.stretch[i] <= -cfg.min_stretch_atr)
    return False


def _strength(stretch: float, cfg: SnapbackConfig) -> str:
    """How far past the threshold this one is.

    Three bands rather than a score, because the measurement supports an
    ordering and does not support a number: at 3 ATR the pooled mean is roughly
    double the 1.5 ATR case on a sample a third the size. Ordering is what that
    licenses.
    """
    s = abs(stretch)
    if s >= max(cfg.min_stretch_atr * 2.0, 3.0):
        return "STRONG"
    if s >= cfg.min_stretch_atr:
        return "MODERATE"
    return "WATCHING"


def evaluate_at(bars: Bars, cfg: SnapbackConfig, i: int,
                f: Optional[Features] = None) -> list[SnapbackSignal]:
    """Every side that fires on bar ``i``. Reads nothing after ``i``."""
    f = f or features(bars, cfg)
    out: list[SnapbackSignal] = []
    for side in cfg.sides():
        if not fires(f, i, side, cfg, bars):
            continue
        up = side == "fade_up"
        stretch = float(f.stretch[i])
        out.append(SnapbackSignal(
            symbol="",                       # filled by the caller that knows it
            side=side,                       # type: ignore[arg-type]
            direction="BEARISH" if up else "BULLISH",
            option_type="PE" if up else "CE",
            timestamp_ms=int(bars.time[i] * 1000),
            entry=float(bars.close[i]),
            mean_target=float(f.ema[i]),
            stretch=stretch,
            atr=float(f.atr[i]),
            realized_vol=float(f.rv[i]),
            assumed_iv=float(f.iv[i]),
            level=float(f.prior_high[i] if up else f.prior_low[i]),
            strength=_strength(stretch, cfg),   # type: ignore[arg-type]
            reasons=_reasons(bars, f, i, up, cfg),
            metrics={
                "stretch_atr": round(stretch, 2),
                "lookback_days": cfg.lookback_days,
                "rv_pct": round(float(f.rv[i]) * 100, 1),
                "assumed_iv_pct": round(float(f.iv[i]) * 100, 1),
                "assumed_vrp": cfg.assumed_vrp,
                "hold_days": cfg.hold_days,
            },
        ))
    return out


def _reasons(bars: Bars, f: Features, i: int, up: bool,
             cfg: SnapbackConfig) -> tuple[str, ...]:
    level = f.prior_high[i] if up else f.prior_low[i]
    word = "high" if up else "low"
    through = "above" if up else "below"
    return (
        f"Closed {through} its {cfg.lookback_days}-session {word} "
        f"({level:,.2f}).",
        f"{abs(f.stretch[i]):.1f} ATR {'above' if up else 'below'} the "
        f"{cfg.mean_touch_ema}-session mean ({f.ema[i]:,.2f}).",
        f"Realised vol {f.rv[i] * 100:.1f}%; premium modelled at "
        f"{f.iv[i] * 100:.1f}% ({cfg.assumed_vrp:.2f}x).",
        f"Holding {cfg.hold_days} sessions — the horizon the edge was "
        f"measured over.",
    )


def evaluate(bars: Bars, cfg: SnapbackConfig, symbol: str, *,
             catchup_bars: int = 1,
             market_gate: Optional[Mapping[str, bool]] = None
             ) -> list[SnapbackSignal]:
    """Signals on the last ``catchup_bars`` CLOSED sessions.

    Looking back more than one session is not a convenience. This rule fires on
    a single bar, and any scan cycle that misses a day — a restart, a rate
    limit, a holiday the calendar disagreed about — never sees that signal
    again. The replay would find it and the live engine would not, which is the
    same divergence as a second implementation with none of the visibility.
    """
    n = len(bars)
    if n == 0:
        return []
    from .regime import allowed
    f = features(bars, cfg)
    out: list[SnapbackSignal] = []
    start = max(0, n - max(int(catchup_bars), 1))
    for i in range(start, n):
        if not allowed(market_gate, ist_day(float(bars.time[i]))):
            continue
        for sig in evaluate_at(bars, cfg, i, f):
            out.append(_with_symbol(sig, symbol))
    return out


def _with_symbol(sig: SnapbackSignal, symbol: str) -> SnapbackSignal:
    from dataclasses import replace
    return replace(sig, symbol=symbol)


def entry_indices(bars: Bars, cfg: SnapbackConfig, side: str,
                  market_gate: Optional[Mapping[str, bool]] = None
                  ) -> NDArray[np.intp]:
    """Every bar of the tape on which ``side`` fires, after the cooldown.

    The cooldown AND the market gate are applied HERE and not in the backtest,
    so the live scan and the replay cannot disagree about what the entry set is.
    A market grinding to new highs satisfies the raw condition on every session
    of the grind, and counting those as separate observations is what turns one
    event into twenty.

    ``market_gate`` is a day -> allowed map from :mod:`.regime`. It is the
    setting that decides whether this strategy makes money — ungated the book
    returns -1.28% per entry day over nine years and 202 underlyings, gated
    +2.13% — so it belongs in the function that DEFINES an entry rather than in
    one of the two callers, where the two could drift.
    """
    from .regime import allowed
    f = features(bars, cfg)
    out: list[int] = []
    last = -10 ** 9
    for i in range(len(bars)):
        if i - last < cfg.cooldown_days:
            continue
        if not allowed(market_gate, ist_day(float(bars.time[i]))):
            continue
        if fires(f, i, side, cfg, bars):
            out.append(i)
            last = i
    return np.array(out, dtype=np.intp)
