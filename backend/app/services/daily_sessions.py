"""One daily observation per exchange session, available only after its close."""
from datetime import datetime, time

from app.engines.snapback.models import IST, to_bars
from app.services.navigator.calendar import COVERED_YEARS, is_trading_day


def is_session_day(day) -> bool:
    """Known exchange closures; older stored tapes retain weekday coverage."""
    if day.year not in COVERED_YEARS:
        return False
    return is_trading_day(day)


def closed_daily_candles(candles, asof: datetime) -> list[dict]:
    """Accept broker candle shapes; normalize midnight/open stamps to the close.

    Daily rows are snapshots, not intraday fragments: duplicate stamps for one
    day replace one another rather than doubling volume or indicator windows.
    """
    bars = to_bars(candles)
    sessions = {}
    for i in range(len(bars)):
        day = datetime.fromtimestamp(float(bars.time[i]), IST).date()
        close = datetime.combine(day, time(15, 30), tzinfo=IST)
        if close > asof or not is_session_day(day):
            continue
        sessions[day] = {
            "time": close.timestamp(), "open": float(bars.open[i]),
            "high": float(bars.high[i]), "low": float(bars.low[i]),
            "close": float(bars.close[i]), "volume": float(bars.volume[i]),
        }
    return [sessions[day] for day in sorted(sessions)]


def completed_intraday_candle(candles, resolution: str, asof: datetime):
    """Aggregate only a full regular session with every expected opening stamp."""
    minutes = {'1m': 1, '3m': 3, '5m': 5, '10m': 10, '15m': 15,
               '30m': 30, '60m': 60}.get(resolution)
    bars = to_bars(candles)
    if minutes is None or not len(bars):
        return None
    day = datetime.fromtimestamp(float(bars.time[0]), IST).date()
    close = datetime.combine(day, time(15, 30), tzinfo=IST)
    opening = datetime.combine(day, time(9, 15), tzinfo=IST).timestamp()
    if close > asof or not is_session_day(day):
        return None
    expected = set(range(int(opening), int(close.timestamp()), minutes * 60))
    if set(bars.time) != expected:
        return None
    return dict(time=close.timestamp(), open=float(bars.open[0]), high=float(max(bars.high)),
                low=float(min(bars.low)), close=float(bars.close[-1]), volume=float(sum(bars.volume)))
