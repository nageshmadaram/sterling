"""Daily spot as-of must not leak that session's close into earlier 15m bars."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.engines.gamma_move import Candle
from app.engines.gamma_move.replay import _spot_asof

IST = timezone(timedelta(hours=5, minutes=30))


def _ms(y: int, m: int, d: int, hh: int, mm: int) -> int:
    return int(datetime(y, m, d, hh, mm, tzinfo=IST).timestamp() * 1000)


def _daily(y: int, m: int, d: int, close: float, *, hh: int = 0, mm: int = 0) -> Candle:
    ts = _ms(y, m, d, hh, mm)
    return Candle(ts_ms=ts, open=close, high=close, low=close, close=close)


SPOT = [
    _daily(2026, 8, 25, 1290.0),
    _daily(2026, 8, 26, 1300.0),  # Kite daily: stamped at midnight
    _daily(2026, 8, 27, 1310.0),
]


def test_a_morning_bar_sees_yesterdays_close_not_todays():
    assert _spot_asof(SPOT, _ms(2026, 8, 26, 10, 0)) == 1290.0
    assert _spot_asof(SPOT, _ms(2026, 8, 27, 10, 0)) == 1300.0


def test_todays_close_is_visible_only_after_the_session_ends():
    assert _spot_asof(SPOT, _ms(2026, 8, 26, 15, 29)) == 1290.0
    assert _spot_asof(SPOT, _ms(2026, 8, 26, 15, 30)) == 1300.0


def test_a_daily_bar_stamped_at_session_open_is_still_an_eod_close():
    """Kite often stamps daily candles 09:15. That stamp is not when the close is known."""
    stamped = [
        _daily(2026, 8, 25, 1290.0, hh=9, mm=15),
        _daily(2026, 8, 26, 1300.0, hh=9, mm=15),
    ]
    assert _spot_asof(stamped, _ms(2026, 8, 26, 10, 0)) == 1290.0
    assert _spot_asof(stamped, _ms(2026, 8, 26, 15, 30)) == 1300.0
