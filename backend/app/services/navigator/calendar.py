"""IST trading-session calendar for NSE (spec §10.2 session verification,
§22 external fact #2, §12.1 expiry-close resolution).

**External fact — verify before this gates real capital.** Sourced
2026-07-27 from two independent secondary aggregators that agreed exactly:
  - https://cleartax.in/s/stock-market-holidays-2026
  - https://groww.in/p/nse-holidays
These are NOT the primary NSE circular. Reconcile against the exchange's
own official notice (nseindia.com > Markets > Market Timings & Holidays)
before promoting anything gated by this calendar out of shadow/advisory
mode. Muhurat trading (a bonus Sunday session, 2026-11-08) is NOT modeled
as a trading day here — it is a special extra session, not a regular one,
and Navigator does not scan it.

Coverage is intentionally narrow: `COVERED_YEARS` lists only years this
module has verified holiday data for. Any date outside that set raises
`CalendarUnknownError` — callers must surface `CALENDAR_UNKNOWN`, never
assume a plain weekday check is enough.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

CALENDAR_VERSION = "nse_calendar_v2025_2026a"
CALENDAR_SOURCE_URLS = (
    "https://nsearchives.nseindia.com/",
    "https://cleartax.in/s/stock-market-holidays-2026",
    "https://groww.in/p/nse-holidays",
)
CALENDAR_FETCHED_ON = "2026-07-27"

SESSION_OPEN_TIME = time(9, 15)
SESSION_CLOSE_TIME = time(15, 30)

_NSE_HOLIDAYS_2025: frozenset[date] = frozenset({
    date(2025, 2, 26),   # Mahashivratri
    date(2025, 3, 14),   # Holi
    date(2025, 3, 31),   # Id-Ul-Fitr
    date(2025, 4, 10),   # Shri Mahavir Jayanti
    date(2025, 4, 14),   # Dr. Baba Saheb Ambedkar Jayanti
    date(2025, 4, 18),   # Good Friday
    date(2025, 5, 1),    # Maharashtra Day
    date(2025, 8, 15),   # Independence Day
    date(2025, 8, 27),   # Ganesh Chaturthi
    date(2025, 10, 2),   # Mahatma Gandhi Jayanti / Dussehra
    date(2025, 10, 21),  # Diwali Laxmi Pujan
    date(2025, 10, 22),  # Balipratipada
    date(2025, 11, 5),   # Prakash Gurpurb Sri Guru Nanak Dev
    date(2025, 12, 25),  # Christmas
})

_NSE_HOLIDAYS_2026: frozenset[date] = frozenset({
    date(2026, 1, 15),   # Municipal Corporation Election - Maharashtra
    date(2026, 1, 26),   # Republic Day
    date(2026, 3, 3),    # Holi
    date(2026, 3, 26),   # Shri Ram Navami
    date(2026, 3, 31),   # Shri Mahavir Jayanti
    date(2026, 4, 3),    # Good Friday
    date(2026, 4, 14),   # Dr. Baba Saheb Ambedkar Jayanti
    date(2026, 5, 1),    # Maharashtra Day
    date(2026, 5, 28),   # Bakri Id
    date(2026, 6, 26),   # Muharram
    date(2026, 9, 14),   # Ganesh Chaturthi
    date(2026, 10, 2),   # Mahatma Gandhi Jayanti
    date(2026, 10, 20),  # Dussehra
    date(2026, 11, 10),  # Diwali - Balipratipada
    date(2026, 11, 24),  # Prakash Gurpurb Sri Guru Nanak Dev
    date(2026, 12, 25),  # Christmas
})

_HOLIDAYS_BY_YEAR: dict[int, frozenset[date]] = {
    2025: _NSE_HOLIDAYS_2025,
    2026: _NSE_HOLIDAYS_2026,
}
COVERED_YEARS: frozenset[int] = frozenset(_HOLIDAYS_BY_YEAR)


class CalendarUnknownError(RuntimeError):
    """Raised for any date outside `COVERED_YEARS`. Callers must surface
    this as reason code `CALENDAR_UNKNOWN` — never assume a trading day (or
    a holiday) for an unverified year."""


def _require_covered(d: date) -> None:
    if d.year not in COVERED_YEARS:
        raise CalendarUnknownError(
            f"no verified NSE calendar for year {d.year} (covered: {sorted(COVERED_YEARS)})"
        )


def is_trading_day(d: date) -> bool:
    _require_covered(d)
    return d.weekday() < 5 and d not in _HOLIDAYS_BY_YEAR[d.year]


def session_bounds_ist(d: date) -> tuple[datetime, datetime]:
    """Official (open, close) datetimes in IST for a trading day."""
    if not is_trading_day(d):
        raise ValueError(f"{d} is not a covered NSE trading day")
    open_dt = datetime.combine(d, SESSION_OPEN_TIME, tzinfo=IST)
    close_dt = datetime.combine(d, SESSION_CLOSE_TIME, tzinfo=IST)
    return open_dt, close_dt


def is_market_open_at(ts_ms: int) -> bool:
    """Whether the exact instant `ts_ms` (epoch ms) falls within an official
    NSE cash session — weekday AND time window AND not a verified holiday.
    Raises `CalendarUnknownError` outside covered years."""
    dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=IST)
    d = dt.date()
    if not is_trading_day(d):
        return False
    open_dt, close_dt = session_bounds_ist(d)
    return open_dt <= dt <= close_dt


def next_trading_day(d: date) -> date:
    """Next covered trading day strictly after `d`. Raises
    `CalendarUnknownError` if the search would run past a verified year."""
    cursor = d + timedelta(days=1)
    limit = date(max(COVERED_YEARS), 12, 31)
    while cursor <= limit:
        _require_covered(cursor)
        if is_trading_day(cursor):
            return cursor
        cursor += timedelta(days=1)
    raise CalendarUnknownError(f"no covered trading day found after {d}")


def entry_delay_cutoff_ist(d: date, delay_minutes: int) -> datetime:
    """Official session open + the configurable post-open delay (spec §6.1
    `entry_delay_after_open_minutes`)."""
    open_dt, _ = session_bounds_ist(d)
    return open_dt + timedelta(minutes=delay_minutes)


def expiry_close_ist(d: date) -> datetime:
    """Exact contract expiry close for a listed expiry date.

    Defaults to the standard 15:30 IST session close. No early-close special
    session has been verified for any covered date, so none is modeled here
    — this function will need an override table the day one is confirmed."""
    _, close_dt = session_bounds_ist(d)
    return close_dt
