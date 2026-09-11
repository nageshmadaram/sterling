"""The three intraday strategies, as pure functions over a bar series.

No broker, no store, no clock: everything each strategy needs arrives as bars
and a config, so the live scanner, the simulation and a test all run the exact
same code. That is the property the replay audit in this repo was written to
enforce, and the reason a "works in backtest, silent live" bug is possible at
all when it is broken.

Each strategy returns an :class:`Evaluation` — the signal when there is one,
and, when there is not, the ordered list of conditions that failed. A board row
that says "no signal" is nearly useless; one that says *which* leg is short is
the difference between waiting and moving on.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np

from app.engines.indicators import compute_atr, compute_ema, compute_supertrend

from .config import IntradayConfig
from .indicators import FibPivots, fib_pivots, session_vwap, vwap_is_volume_weighted
from .models import Bars, IntradaySignal


@dataclass(frozen=True)
class Evaluation:
    """What one strategy made of one symbol's tape, at the last closed bar."""

    strategy: str
    symbol: str
    signal: Optional[IntradaySignal]
    blockers: tuple[str, ...]
    metrics: dict

    @property
    def state(self) -> str:
        return "armed" if self.signal else "watching"

    def as_dict(self) -> dict:
        return {
            "strategy": self.strategy,
            "symbol": self.symbol,
            "state": self.state,
            "signal": self.signal.as_dict() if self.signal else None,
            "blockers": list(self.blockers),
            "metrics": {k: (round(v, 4) if isinstance(v, float) else v)
                        for k, v in self.metrics.items()},
        }


def _minute_of_day_ist(epoch_s: float) -> int:
    return int(((float(epoch_s) + 19800) % 86400) // 60)


def _hhmm_minutes(hhmm: str) -> int:
    hh, mm = str(hhmm).split(":")
    return int(hh) * 60 + int(mm)


def _in_entry_window(bars: Bars, i: int, cfg: IntradayConfig) -> Optional[str]:
    """``None`` when entries are allowed at bar ``i``, else why not."""
    m = _minute_of_day_ist(bars.time[i])
    if m < _hhmm_minutes(cfg.session_start):
        return f"before {cfg.session_start} — opening auction, not a trend"
    if m >= _hhmm_minutes(cfg.no_entry_after):
        return f"after {cfg.no_entry_after} — no new intraday entries"
    return None


def _too_short(bars: Bars, cfg: IntradayConfig) -> Optional[str]:
    if len(bars) < cfg.warmup_bars:
        return f"only {len(bars)} bars, warmup needs {cfg.warmup_bars}"
    return None


def apply_dynamic_levels(entry: float, stop: float, targets: list[float],
                        atr: float, bullish: bool, cfg: IntradayConfig,
                        metrics: dict) -> tuple[float, list[float]]:
    """Widen a stop that volatility has made meaningless, and a target it has overtaken.

    Both adjustments move levels AWAY from entry and never towards it, which is
    the property that makes this safe to apply to every strategy:

    * A structural stop — a candle's low, the slow EMA, VWAP — can land two
      ticks from the close on a quiet bar. That is not a stop, it is a fee.
      ``stop_atr_floor_mult`` pushes it out to a distance the instrument
      actually moves in a bar.
    * A fixed target can be inside one bar's range on a fast day, which turns a
      trend strategy into a scalp without anyone deciding to. ``target_atr_mult``
      lifts it to the ATR distance when that is further.

    TIGHTENING either one would be trading a different strategy than the one on
    the board, so it is not done — a stop that is too WIDE is refused upstream
    by each strategy's own cap, where the operator can see it.
    """
    if not (atr > 0 and np.isfinite(atr)):
        return stop, targets
    sign = 1.0 if bullish else -1.0
    out_stop = stop
    if cfg.dynamic_stops and cfg.stop_atr_floor_mult > 0:
        floor = cfg.stop_atr_floor_mult * atr
        if abs(entry - stop) < floor:
            out_stop = entry - sign * floor
            metrics["stop_widened_to_atr_floor"] = True
    if cfg.stop_atr_cap_mult:
        metrics["stop_atr_multiple"] = abs(entry - out_stop) / atr
        metrics["stop_beyond_atr_cap"] = (
            metrics["stop_atr_multiple"] > cfg.stop_atr_cap_mult)
    out_targets = list(targets)
    if cfg.dynamic_targets and cfg.target_atr_mult > 0:
        reach = cfg.target_atr_mult * atr
        for i, t in enumerate(out_targets):
            if t is None:
                continue
            if abs(t - entry) < reach:
                out_targets[i] = entry + sign * reach
                metrics["target_extended_to_atr"] = True
    return out_stop, out_targets


# --------------------------------------------------------------- 1. pivot_break

def prior_period_hlc(bars: Bars, period: str = "day") -> Optional[tuple[float, float, float]]:
    """High/low/close of the session before the one the last bar belongs to.

    Pivots are a property of the PREVIOUS period. Computing them from the
    session in progress makes the level move under the trade, which reads as a
    break that un-breaks itself.
    """
    days = bars.session_day
    if not days:
        return None
    today = days[-1]
    idx = [i for i, d in enumerate(days) if d != today]
    if not idx:
        return None
    prev_day = days[idx[-1]]
    if period == "week":
        # Same rule, a week wide: every bar strictly before the current session
        # that falls in the seven days preceding it.
        sel = [i for i, d in enumerate(days) if d != today and d >= _shift_days(today, -7)]
    else:
        sel = [i for i, d in enumerate(days) if d == prev_day]
    if not sel:
        return None
    hi = float(np.max(bars.high[sel]))
    lo = float(np.min(bars.low[sel]))
    close = float(bars.close[sel[-1]])
    return hi, lo, close


def _shift_days(day: str, delta: int) -> str:
    from datetime import date, timedelta
    y, m, d = (int(x) for x in day.split("-"))
    return (date(y, m, d) + timedelta(days=delta)).isoformat()


def pivots_for(bars: Bars, cfg: IntradayConfig) -> Optional[FibPivots]:
    hlc = prior_period_hlc(bars, cfg.pb_pivot_period)
    if hlc is None:
        return None
    hi, lo, close = hlc
    piv = fib_pivots(hi, lo, close)
    if piv is None or cfg.pb_pivot_type == "fibonacci":
        return piv
    # Classic: same pivot, conventional bands. Kept so the type knob is real
    # rather than a label over one implementation.
    rng = hi - lo
    p = piv.p
    return FibPivots(p=p, r1=2 * p - lo, r2=p + rng, r3=hi + 2 * (p - lo),
                     s1=2 * p - hi, s2=p - rng, s3=lo - 2 * (hi - p))


def evaluate_pivot_break(bars: Bars, cfg: IntradayConfig, symbol: str) -> Evaluation:
    """A strong candle closing through BOTH the EMA and a Fibonacci pivot.

    Long and short are symmetric, and the option side follows the direction:
    a break up buys the CE, a break down buys the PE.
    """
    strat = "pivot_break"
    blockers: list[str] = []
    metrics: dict = {}
    short = _too_short(bars, cfg)
    if short:
        return Evaluation(strat, symbol, None, (short,), metrics)

    i = len(bars) - 1
    window = _in_entry_window(bars, i, cfg)
    if window:
        blockers.append(window)

    piv = pivots_for(bars, cfg)
    if piv is None:
        return Evaluation(strat, symbol, None,
                          (*blockers, "no prior session range — pivots undefined"), metrics)
    metrics["pivots"] = piv.as_dict()

    ema = compute_ema(bars.close, cfg.pb_ema_length)
    atr = compute_atr(bars.high, bars.low, bars.close, cfg.pb_atr_length)
    o, h, l, c = (float(bars.open[i]), float(bars.high[i]),
                  float(bars.low[i]), float(bars.close[i]))
    prev_c = float(bars.close[i - 1])
    a = float(atr[i])
    if not np.isfinite(a) or a <= 0:
        return Evaluation(strat, symbol, None, (*blockers, "ATR undefined"), metrics)

    rng = h - l
    body = abs(c - o)
    body_pct = (body / rng * 100.0) if rng > 0 else 0.0
    body_atr = body / a
    metrics.update({"ema": float(ema[i]), "atr": a, "close": c,
                    "body_pct": body_pct, "body_atr": body_atr})

    bullish = c > o
    bearish = c < o
    if not (bullish or bearish):
        blockers.append("doji — no direction to break in")
    if body_pct < cfg.pb_min_body_pct:
        blockers.append(f"body {body_pct:.0f}% of range < {cfg.pb_min_body_pct:.0f}%")
    if body_atr < cfg.pb_min_body_atr:
        blockers.append(f"body {body_atr:.2f}×ATR < {cfg.pb_min_body_atr:.2f}×")

    buf = cfg.pb_break_buffer_atr * a
    direction = "BULLISH" if bullish else "BEARISH"
    if bullish:
        if c <= float(ema[i]) + buf:
            blockers.append(f"close {c:.2f} not clear of EMA{cfg.pb_ema_length} "
                            f"{float(ema[i]):.2f} by {cfg.pb_break_buffer_atr:.2f}×ATR")
        crossed = [(k, v) for k, v in piv.all_levels()
                   if prev_c <= v < c - buf] if cfg.pb_require_fresh_break else \
                  [(k, v) for k, v in piv.all_levels() if v < c - buf]
        level = max(crossed, key=lambda kv: kv[1]) if crossed else (None, None)
    else:
        if c >= float(ema[i]) - buf:
            blockers.append(f"close {c:.2f} not clear of EMA{cfg.pb_ema_length} "
                            f"{float(ema[i]):.2f} by {cfg.pb_break_buffer_atr:.2f}×ATR")
        crossed = [(k, v) for k, v in piv.all_levels()
                   if c + buf < v <= prev_c] if cfg.pb_require_fresh_break else \
                  [(k, v) for k, v in piv.all_levels() if v > c + buf]
        level = min(crossed, key=lambda kv: kv[1]) if crossed else (None, None)

    if level[0] is None:
        blockers.append("no pivot level broken on this bar"
                        if cfg.pb_require_fresh_break else "price is not through any pivot")
    else:
        metrics.update({"level_kind": level[0], "level_price": float(level[1])})

    stop = l if bullish else h
    risk = abs(c - stop)
    metrics["risk"] = risk
    if risk <= 0:
        blockers.append("signal candle has no range — stop would equal entry")
    elif (risk / c * 100.0) > cfg.pb_max_stop_pct:
        blockers.append(f"stop {risk / c * 100.0:.2f}% of price > "
                        f"{cfg.pb_max_stop_pct:.2f}% cap — candle too wide to risk")

    if blockers:
        return Evaluation(strat, symbol, None, tuple(blockers), metrics)

    sign = 1.0 if bullish else -1.0
    stop, (t1, t2) = apply_dynamic_levels(
        c, stop, [c + sign * cfg.pb_target_r * risk, c + sign * cfg.pb_target2_r * risk],
        a, bullish, cfg, metrics)
    risk = abs(c - stop)
    metrics["risk"] = risk
    sig = IntradaySignal(
        strategy=strat, symbol=symbol, direction=direction,
        option_type="CE" if bullish else "PE",
        timestamp_ms=int(bars.time[i] * 1000),
        entry=c, stop=stop,
        target=t1, target2=t2,
        risk=risk, strength="STRONG",
        origin=f"{level[0]} + EMA{cfg.pb_ema_length}",
        reasons=(f"strong {'green' if bullish else 'red'} candle "
                 f"(body {body_pct:.0f}% of range, {body_atr:.2f}×ATR)",
                 f"closed through pivot {level[0]} at {float(level[1]):.2f}",
                 f"closed {'above' if bullish else 'below'} "
                 f"EMA{cfg.pb_ema_length} at {float(ema[i]):.2f}",
                 f"stop at the signal candle's {'low' if bullish else 'high'} "
                 f"{stop:.2f} ({risk:.2f} pts)"),
        metrics=metrics,
    )
    return Evaluation(strat, symbol, sig, (), metrics)


# ----------------------------------------------------------------- 2. ma_ribbon

def ribbon_lines(bars: Bars, cfg: IntradayConfig) -> dict[str, np.ndarray]:
    return {
        "fast": compute_ema(bars.close, cfg.rb_ema_fast),
        "e1": compute_ema(bars.close, cfg.rb_ema_1),
        "e2": compute_ema(bars.close, cfg.rb_ema_2),
        "slow": compute_ema(bars.close, cfg.rb_ema_slow),
    }


def _ribbon_side(lines: dict[str, np.ndarray], i: int) -> int:
    """+1 when the slow line is ABOVE every other line, -1 when below, else 0.

    Zero is the important value: it is the state where the slow line has crossed
    *some* of the ribbon. The strategy says explicitly not to trade that, so it
    gets its own value rather than being folded into one of the two sides.
    """
    others = (float(lines["fast"][i]), float(lines["e1"][i]), float(lines["e2"][i]))
    slow = float(lines["slow"][i])
    if not all(np.isfinite(x) and x > 0 for x in (*others, slow)):
        return 0
    if slow < min(others):
        return -1
    if slow > max(others):
        return 1
    return 0


def evaluate_ma_ribbon(bars: Bars, cfg: IntradayConfig, symbol: str) -> Evaluation:
    """The 55 crossing the WHOLE ribbon — never one line of it.

    Slow line below all three faster lines is the bullish state (buy CE); above
    all three is bearish (buy PE). The entry is the bar the state completes on,
    and the position is held until the opposite state completes.
    """
    strat = "ma_ribbon"
    blockers: list[str] = []
    metrics: dict = {}
    short = _too_short(bars, cfg)
    if short:
        return Evaluation(strat, symbol, None, (short,), metrics)

    i = len(bars) - 1
    window = _in_entry_window(bars, i, cfg)
    if window:
        blockers.append(window)

    lines = ribbon_lines(bars, cfg)
    atr = compute_atr(bars.high, bars.low, bars.close, cfg.rb_atr_length)
    c = float(bars.close[i])
    side = _ribbon_side(lines, i)
    metrics.update({k: float(v[i]) for k, v in lines.items()})
    metrics["side"] = side

    confirm = max(1, int(cfg.rb_confirm_bars))
    if i - confirm < 0:
        return Evaluation(strat, symbol, None, (*blockers, "not enough bars to confirm"), metrics)

    if side == 0:
        blockers.append("the 55 has crossed only part of the ribbon — "
                        "a partial cross is explicitly not a signal")
    held = all(_ribbon_side(lines, j) == side for j in range(i - confirm + 1, i + 1)) \
        if side else False
    before = _ribbon_side(lines, i - confirm)
    if side and not held:
        blockers.append(f"full cross has not held {confirm} bar(s)")
    if side and before == side:
        blockers.append("full cross is not fresh — this side was already in force")

    vals = [float(v[i]) for v in lines.values()]
    spread_pct = (max(vals) - min(vals)) / c * 100.0 if c > 0 else 0.0
    metrics["spread_pct"] = spread_pct
    if spread_pct < cfg.rb_min_spread_pct:
        blockers.append(f"ribbon spread {spread_pct:.2f}% < {cfg.rb_min_spread_pct:.2f}% — "
                        "the four lines are braided, not trending")

    if not cfg.rb_require_full_cross and side == 0:
        # The operator has explicitly turned the rule off. Fall back to the
        # slow line against the fastest one only, and say so on the row.
        side = -1 if float(lines["slow"][i]) < float(lines["fast"][i]) else 1
        blockers = [b for b in blockers if "partial cross" not in b]

    bullish = side == -1
    a = float(atr[i]) if np.isfinite(atr[i]) else 0.0
    slow = float(lines["slow"][i])
    stop = slow - cfg.rb_stop_atr_mult * a if bullish else slow + cfg.rb_stop_atr_mult * a
    risk = abs(c - stop)
    metrics["risk"] = risk
    if risk <= 0:
        blockers.append("price is already through the slow line — no room for a stop")

    if blockers:
        return Evaluation(strat, symbol, None, tuple(blockers), metrics)

    sign = 1.0 if bullish else -1.0
    stop, (t1,) = apply_dynamic_levels(
        c, stop, [c + sign * cfg.rb_target_r * risk], a, bullish, cfg, metrics)
    risk = abs(c - stop)
    metrics["risk"] = risk
    sig = IntradaySignal(
        strategy=strat, symbol=symbol,
        direction="BULLISH" if bullish else "BEARISH",
        option_type="CE" if bullish else "PE",
        timestamp_ms=int(bars.time[i] * 1000),
        entry=c, stop=stop,
        target=t1, target2=None,
        risk=risk, strength="STRONG",
        origin=f"EMA{cfg.rb_ema_slow} "
               f"{'below' if bullish else 'above'} the whole ribbon",
        reasons=(f"EMA{cfg.rb_ema_slow} crossed {'below' if bullish else 'above'} "
                 f"all of EMA{cfg.rb_ema_fast}/{cfg.rb_ema_1}/{cfg.rb_ema_2}",
                 f"held for {confirm} bar(s)",
                 f"ribbon spread {spread_pct:.2f}% of price",
                 f"hold until EMA{cfg.rb_ema_slow} crosses "
                 f"{'above' if bullish else 'below'} the whole ribbon again"),
        metrics=metrics,
    )
    return Evaluation(strat, symbol, sig, (), metrics)


def ribbon_should_exit(bars: Bars, cfg: IntradayConfig, direction: str) -> tuple[bool, str]:
    """The stated exit: the opposite full cross. Evaluated at the last bar."""
    if len(bars) < cfg.warmup_bars:
        return False, "warming up"
    lines = ribbon_lines(bars, cfg)
    side = _ribbon_side(lines, len(bars) - 1)
    want_exit = (side == 1) if direction == "BULLISH" else (side == -1)
    if want_exit:
        return True, f"EMA{cfg.rb_ema_slow} crossed back through the whole ribbon"
    return False, "ribbon still on side"


# ----------------------------------------------------------- 3. vwap_supertrend

def evaluate_vwap_supertrend(bars: Bars, cfg: IntradayConfig, symbol: str) -> Evaluation:
    """A SuperTrend flip confirmed by which side of VWAP the candle closed.

    SuperTrend turning red with a close below VWAP is the short (buy PE); the
    mirror is the long (buy CE). VWAP is the stop and the objective is a fixed
    number of points.
    """
    strat = "vwap_supertrend"
    blockers: list[str] = []
    metrics: dict = {}
    short = _too_short(bars, cfg)
    if short:
        return Evaluation(strat, symbol, None, (short,), metrics)

    i = len(bars) - 1
    window = _in_entry_window(bars, i, cfg)
    if window:
        blockers.append(window)

    st_line, trend = compute_supertrend(bars.high, bars.low, bars.close,
                                        cfg.vs_atr_length, cfg.vs_factor)
    starts = bars.session_starts
    vwap = session_vwap(bars.high, bars.low, bars.close, bars.volume, starts)
    c = float(bars.close[i])
    v = float(vwap[i])
    metrics.update({"vwap": v, "supertrend": float(st_line[i]),
                    "trend": int(trend[i]), "close": c})

    volume_weighted = vwap_is_volume_weighted(bars.volume, starts)
    metrics["vwap_volume_weighted"] = volume_weighted
    metrics["vwap_basis"] = "volume" if volume_weighted else "session_mean"
    if cfg.vs_require_volume_vwap and not volume_weighted:
        # Actionable, because "no volume" is a property of the INSTRUMENT, not
        # of today. Kite reports none on index spot and always will, so a row
        # that only states the fact leaves an operator watching a strategy that
        # can never fire and no way to know that is why.
        blockers.append(
            "no volume on this instrument — index spot reports none, so VWAP "
            "would be a session mean rather than volume-weighted. Scan stocks "
            "or futures for this strategy, or turn off 'require real volume' to "
            "trade the session mean instead")

    # Which bar the colour changed on, looking back only as far as a flip may
    # still be called fresh.
    look = max(0, int(cfg.vs_confirm_within_bars))
    flip_at: Optional[int] = None
    for j in range(i, max(0, i - look) - 1, -1):
        if j - 1 >= 0 and int(trend[j]) != 0 and int(trend[j]) != int(trend[j - 1]):
            flip_at = j
            break
    metrics["flip_bars_ago"] = (i - flip_at) if flip_at is not None else None
    if cfg.vs_require_fresh_flip and flip_at is None:
        blockers.append(f"no SuperTrend flip within {look + 1} bar(s)")

    now = int(trend[i])
    if now == 0:
        blockers.append("SuperTrend undefined — still inside its ATR warmup")
    bearish = now == -1
    bullish = now == 1

    if bearish and not (c < v):
        blockers.append(f"SuperTrend is red but the close {c:.2f} is not below "
                        f"VWAP {v:.2f}")
    if bullish and not (c > v):
        blockers.append(f"SuperTrend is green but the close {c:.2f} is not above "
                        f"VWAP {v:.2f}")

    if cfg.vs_stop_source == "vwap":
        stop = v
    elif cfg.vs_stop_source == "supertrend":
        stop = float(st_line[i])
    else:
        stop = max(v, float(st_line[i])) if bearish else min(v, float(st_line[i]))
    dist = abs(c - stop)
    metrics["stop_points"] = dist
    if dist < cfg.vs_min_stop_points:
        blockers.append(f"stop {dist:.2f} pts away < {cfg.vs_min_stop_points:.2f} — "
                        "inside the noise, the next tick takes it out")
    elif dist > cfg.vs_max_stop_points:
        blockers.append(f"stop {dist:.2f} pts away > {cfg.vs_max_stop_points:.2f} — "
                        f"a {cfg.vs_target_points:.0f}-point target does not pay for it")

    if blockers:
        return Evaluation(strat, symbol, None, tuple(blockers), metrics)

    sign = 1.0 if bullish else -1.0
    # The 20-point objective is the one fixed number in this pack. 20 points is
    # a whole move on one index and a rounding error on another, so it is lifted
    # to the ATR distance when that is further — never lowered.
    atr_now = float(abs(c - float(st_line[i])) / max(cfg.vs_factor, 1e-9))
    points = cfg.vs_target_points
    if cfg.vs_dynamic_target and cfg.vs_target_atr_mult > 0 and atr_now > 0:
        points = max(points, cfg.vs_target_atr_mult * atr_now)
        metrics["target_points_used"] = points
    stop, (t1,) = apply_dynamic_levels(
        c, stop, [c + sign * points], atr_now, bullish, cfg, metrics)
    dist = abs(c - stop)
    metrics["stop_points"] = dist
    sig = IntradaySignal(
        strategy=strat, symbol=symbol,
        direction="BULLISH" if bullish else "BEARISH",
        option_type="CE" if bullish else "PE",
        timestamp_ms=int(bars.time[i] * 1000),
        entry=c, stop=stop,
        target=t1, target2=None,
        risk=dist, strength="STRONG",
        origin=f"ST({cfg.vs_atr_length}, {cfg.vs_factor}) "
               f"{'green' if bullish else 'red'} + VWAP",
        reasons=(f"SuperTrend turned {'green' if bullish else 'red'}"
                 + (f" {i - flip_at} bar(s) ago" if flip_at is not None and i != flip_at
                    else " on this bar"),
                 f"closed {'above' if bullish else 'below'} VWAP {v:.2f}",
                 f"stop at VWAP, {dist:.2f} pts",
                 f"target {points:.0f} pts, trail after "
                 f"{cfg.vs_trail_after_points:.0f}"),
        metrics=metrics,
    )
    return Evaluation(strat, symbol, sig, (), metrics)


# ------------------------------------------------------------------ dispatch

EVALUATORS: dict[str, Callable[[Bars, IntradayConfig, str], Evaluation]] = {
    "pivot_break": evaluate_pivot_break,
    "ma_ribbon": evaluate_ma_ribbon,
    "vwap_supertrend": evaluate_vwap_supertrend,
}


def evaluate_all(bars: Bars, cfg: IntradayConfig, symbol: str,
                 strategies: Optional[tuple[str, ...]] = None) -> list[Evaluation]:
    """Run every enabled strategy over one symbol's tape.

    Order is stable so a board sorted by nothing else still renders the same way
    twice, and an evaluator that raises is reported as its own blocker rather
    than taking the whole scan down with it.
    """
    wanted = strategies if strategies is not None else cfg.enabled_strategies()
    out: list[Evaluation] = []
    for key in wanted:
        fn = EVALUATORS.get(key)
        if fn is None:
            continue
        try:
            out.append(fn(bars, cfg, symbol))
        except Exception as exc:  # one bad symbol must not end the scan
            out.append(Evaluation(key, symbol, None, (f"evaluation failed: {exc}",), {}))
    return out


# ------------------------------------------------------- is the thesis dead?

def thesis_broken(bars: Bars, cfg: IntradayConfig, strategy: str,
                  thesis: str) -> tuple[bool, str]:
    """Whether the REASON for holding has gone, in the strategy's own terms.

    Separate from the price stop on purpose. A stop answers "how much am I
    willing to lose"; this answers "is the idea still true" — and for two of
    these three the stated exit IS the idea failing, not a price:

    * ``ma_ribbon`` is explicitly held until the opposite full cross.
    * ``vwap_supertrend`` entered on a SuperTrend colour and a VWAP side; both
      reversing is the counter-signal.
    * ``pivot_break`` has a price stop as its stated exit, so nothing here
      duplicates it — returning a second opinion would let this close a trade
      the strategy says is still on.

    Evaluated at the last CLOSED bar, like every other rule in this pack.
    """
    if len(bars) < cfg.warmup_bars:
        return False, ""
    bullish = thesis == "BULLISH"
    if strategy == "ma_ribbon":
        if not cfg.rb_exit_on_opposite_cross:
            return False, ""
        return ribbon_should_exit(bars, cfg, thesis)
    if strategy == "vwap_supertrend":
        i = len(bars) - 1
        _, trend = compute_supertrend(bars.high, bars.low, bars.close,
                                      cfg.vs_atr_length, cfg.vs_factor)
        vwap = session_vwap(bars.high, bars.low, bars.close, bars.volume,
                            bars.session_starts)
        c, v, now = float(bars.close[i]), float(vwap[i]), int(trend[i])
        if now == 0:
            return False, ""
        # Both legs must reverse. One alone is the ordinary noise of a trend
        # that is still intact, and exiting on it turns a 20-point objective
        # into a scratch.
        flipped = (now == -1) if bullish else (now == 1)
        wrong_side = (c < v) if bullish else (c > v)
        if flipped and wrong_side:
            return True, "SuperTrend flipped back through VWAP"
    return False, ""
