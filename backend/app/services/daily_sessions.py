"""One daily observation per exchange session, available only after its close."""
from datetime import datetime, time

from app.engines.snapback.models import IST, to_bars
from app.services.navigator.calendar import COVERED_YEARS, is_trading_day


def is_session_day(day) -> bool:
    """Known exchange closures; older stored tapes retain weekday coverage."""
    return is_trading_day(day) if day.year in COVERED_YEARS else day.weekday() < 5


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
