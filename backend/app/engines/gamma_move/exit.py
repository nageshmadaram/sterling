"""Where a Gamma Move trade stops and where it ends.

Only one of these rules comes from the source. It says to stop at the swing low
of the option's own premium and to hold one day, two at most; it gives **no
exit rule at all** -- its 2x and 3x figures are outcomes of discretionary exits,
not a rule that produced them. So ``TIME_STOP`` is the only policy supported by
evidence, and it is the only one live mode will run.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional, Sequence

from .config import GammaMoveConfig
from .models import ExitEvent, OICandle, PositionState, align_to_tick, q2
from .trigger import session_day


def swing_low_stop(candles: Sequence[OICandle], cfg: GammaMoveConfig) -> Optional[float]:
    if len(candles) < 2:
        return None
    window = list(candles)[-cfg.swing_lookback:]
    lows = [c.low for c in window if c.low > 0]
    return q2(min(lows)) if lows else None


def initial_stop(entry: float, candles: Sequence[OICandle],
                 cfg: GammaMoveConfig) -> Optional[float]:
    if entry <= 0:
        return None
    swing = swing_low_stop(candles, cfg)
    floor = entry - cfg.stop_distance_inr(entry)
    if cfg.stop_basis == "PERCENT" and floor > 0:
        stop = max(swing, floor) if swing is not None else floor
    else:
        stop = swing if swing is not None else floor
    if stop is None or stop <= 0 or stop >= entry:
        return None
    return q2(stop)


def target_price(entry: float, cfg: GammaMoveConfig) -> Optional[float]:
    if cfg.exit_policy != "PERCENT_TARGET" or cfg.target_pct <= 0 or entry <= 0:
        return None
    return q2(entry * (1 + cfg.target_pct / 100.0))


def update_trail(pos: PositionState, ltp: float, cfg: GammaMoveConfig) -> Optional[float]:
    if cfg.exit_policy != "TRAILING_STOP" or cfg.trail_pct <= 0 or ltp <= 0:
        return pos.trail
    pos.high_water = max(pos.high_water, ltp)
    if cfg.trail_start_pct > 0 and \
            pos.high_water < pos.entry * (1 + cfg.trail_start_pct / 100.0):
        return pos.trail
    candidate = q2(pos.high_water * (1 - cfg.trail_pct / 100.0))
    pos.trail = candidate if pos.trail is None else max(pos.trail, candidate)
    return pos.trail


def weekday_sessions_held(entry_day: str, today: str) -> int:
    """Trading sessions elapsed after entry. Weekends do not count."""
    try:
        a = date.fromisoformat(str(entry_day)[:10])
        b = date.fromisoformat(str(today)[:10])
    except (TypeError, ValueError):
        return 0
    if b <= a:
        return 0
    n = 0
    d = a + timedelta(days=1)
    while d <= b:
        if d.weekday() < 5:
            n += 1
        d += timedelta(days=1)
    return n


def should_exit(pos: PositionState, ltp: float, now_ms: int, today: str,
                cfg: GammaMoveConfig, *, session_over: bool = False) -> Optional[str]:
    if ltp > 0 and pos.stop > 0 and ltp <= pos.stop:
        return "stop"
    if pos.trail is not None and ltp > 0 and ltp <= pos.trail:
        return "trail"
    if pos.target is not None and ltp > 0 and ltp >= pos.target:
        return "target"
    if today != pos.entry_day:
        held = weekday_sessions_held(pos.entry_day, today)
        pos.sessions_held = held
        if held >= cfg.max_hold_days:
            return "time_stop"
    if session_over and cfg.close_at_session_end:
        return "session_end"
    return None


def exit_order_price(ltp: float, tick: float) -> float:
    return align_to_tick(max(ltp, tick), tick, side="sell")


def build_exit_event(pos: PositionState, reason: str, price: float,
                     at_ms: int) -> ExitEvent:
    return ExitEvent(signal_id=pos.signal_id, reason=reason, price=q2(price), at_ms=at_ms)


def realised_inr(pos: PositionState, exit_price: float) -> float:
    basis = pos.effective_entry if getattr(pos, "effective_entry", 0) else pos.entry
    return q2((float(exit_price) - float(basis)) * pos.quantity)
