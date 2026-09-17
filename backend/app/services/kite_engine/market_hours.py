"""Versioned NSE session policy; auction phases never authorize strategy orders.

Sources verified 2026-09-05: NSE CMTR/71775, CMTR/72260 (holidays),
CMTR/74466 and FAOP/74467 (CAS), SEBI circular 99122 (pre-open).
Special sessions and unverified calendar years fail closed for new entries.
"""
from __future__ import annotations

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

_IST = ZoneInfo("Asia/Kolkata")
POLICY_VERSION = "nse-2026-09-07-v1"
CAS_START = date(2026, 8, 3)
PREOPEN_START = date(2026, 9, 7)
_HOLIDAYS = frozenset(date.fromisoformat(d) for d in (
    "2026-01-15", "2026-01-26", "2026-03-03", "2026-03-26", "2026-03-31",
    "2026-04-03", "2026-04-14", "2026-05-01", "2026-05-28", "2026-06-26",
    "2026-09-14", "2026-10-02", "2026-10-20", "2026-11-10", "2026-11-24",
    "2026-12-25",
))


#: Calendar years whose holiday list has been verified against the exchange
#: circulars cited above. A date outside these years is not "probably a working
#: day" — it is unknown, and every caller must fail closed on it.
VERIFIED_YEARS = frozenset({2026})

SUPPORTED_EXCHANGES = frozenset({"NSE", "NFO", "BSE", "BFO"})


def calendar_covers(day: date) -> bool:
    """Is ``day`` inside the verified holiday calendar?"""
    return day.year in VERIFIED_YEARS


def is_trading_day(day: date, exchange: str = "NFO") -> bool:
    """Day-level session question, with no time-of-day component.

    Raises when the calendar cannot answer, rather than returning ``False``:
    "not a trading day" and "I do not know" lead to opposite decisions, and
    collapsing them would let an unknown date silently shorten a holding
    period.
    """
    if exchange.upper() not in SUPPORTED_EXCHANGES:
        raise ValueError(f"unsupported session exchange: {exchange}")
    if not calendar_covers(day):
        raise LookupError(
            f"holiday calendar does not cover {day.isoformat()}; verified years "
            f"are {sorted(VERIFIED_YEARS)}"
        )
    return day.weekday() < 5 and day not in _HOLIDAYS


def _local(now: datetime | None = None) -> datetime:
    value = now if now is not None else datetime.now(_IST)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("session timestamps must be timezone-aware")
    return value.astimezone(_IST)


def continuous_close(day: date, exchange: str = "NFO", *, cas_eligible: bool = False) -> time:
    exchange = exchange.upper()
    if exchange not in {"NSE", "NFO", "BSE", "BFO"}:
        raise ValueError(f"unsupported session exchange: {exchange}")
    if day >= CAS_START:
        if exchange == "NFO":
            return time(15, 40)
        if exchange == "NSE" and cas_eligible:
            return time(15, 15)
    # BSE retains its legacy bound until its separate policy is verified.
    return time(15, 30)


def session_phase(now: datetime | None = None, *, exchange: str = "NFO",
                  cas_eligible: bool = False) -> str:
    t = _local(now)
    exchange = exchange.upper()
    if t.year not in VERIFIED_YEARS or exchange not in SUPPORTED_EXCHANGES:
        return "calendar_unknown"
    if t.weekday() >= 5 or t.date() in _HOLIDAYS:
        return "closed"
    clock = t.time()
    if time(9, 15) <= clock < continuous_close(t.date(), exchange, cas_eligible=cas_eligible):
        return "continuous"
    if exchange in {"NSE", "NFO"} and time(9) <= clock < time(9, 15):
        if t.date() < PREOPEN_START:
            return "preopen_legacy"
        if clock < time(9, 5):
            return "preopen_market_limit"
        if clock < time(9, 8):
            return "preopen_limit"
        if clock < time(9, 10):
            return "preopen_random_or_matching"
        return "preopen_matching" if clock < time(9, 12) else "preopen_buffer"
    if exchange == "NSE" and cas_eligible and t.date() >= CAS_START:
        for end, phase in ((time(15, 20), "cas_transition"),
                           (time(15, 25), "cas_market_limit"),
                           (time(15, 28), "cas_limit"),
                           (time(15, 30), "cas_random_or_matching"),
                           (time(15, 35), "cas_matching")):
            if time(15, 15) <= clock < end:
                return phase
    return "closed"


def is_market_open(now: datetime | None = None, *, exchange: str = "NFO",
                   cas_eligible: bool = False) -> bool:
    return session_phase(now, exchange=exchange, cas_eligible=cas_eligible) == "continuous"


def minutes_to_close(now: datetime | None = None, *, exchange: str = "NFO",
                     cas_eligible: bool = False) -> float | None:
    t = _local(now)
    if not is_market_open(t, exchange=exchange, cas_eligible=cas_eligible):
        return None
    close = datetime.combine(t.date(), continuous_close(t.date(), exchange,
                             cas_eligible=cas_eligible), tzinfo=_IST)
    return (close - t).total_seconds() / 60


def entry_block_reason(now: datetime | None = None, *, exchange: str = "NFO",
                       cash_signal: bool = False, buffer_minutes: int = 0) -> str:
    """Require continuous traded market and continuous signal source.

    Cash/index feeds can contain auction observations after 15:15. Stop
    cash-derived origination there; held derivatives retain stop monitoring.
    """
    t = _local(now)
    phase = session_phase(t, exchange=exchange)
    if phase != "continuous":
        return f"session_{phase}"
    if cash_signal and t.date() >= CAS_START and t.time() >= time(15, 15):
        return "cash_signal_auction"
    remaining = minutes_to_close(t, exchange=exchange)
    if cash_signal and t.date() >= CAS_START:
        cash_close = t.replace(hour=15, minute=15, second=0, microsecond=0)
        remaining = min(remaining, (cash_close - t).total_seconds() / 60)
    if remaining <= max(0, buffer_minutes):
        return "entry_close_buffer"
    return ""
