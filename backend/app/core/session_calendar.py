"""Trading-session arithmetic for Overnight and Swing holding periods.

Overnight and Swing budgets are measured in *trading sessions*, never in
calendar days. Friday to Monday is one session transition, not three days, and
a 15-session Swing limit counted in calendar days would force-exit a live
position roughly three weeks early.

Everything here delegates the holiday list to
:mod:`app.services.kite_engine.market_hours`, which is the verified source and
carries its own policy version. A second holiday list would eventually
disagree with the first, and the disagreement would surface as a mystery
force-exit.

The one rule that matters: when the calendar cannot answer, this module
raises. It never guesses a session count. A caller that cannot compute a hard
exit must refuse new exposure with ``TIMELINE_CALENDAR_UNKNOWN``.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Final

from app.services.kite_engine.market_hours import (
    POLICY_VERSION,
    SUPPORTED_EXCHANGES,
    VERIFIED_YEARS,
    calendar_covers,
    is_trading_day,
)

#: Version stamped onto every horizon plan, so a plan computed under one
#: holiday list is never silently re-evaluated under another.
CALENDAR_VERSION: Final[str] = POLICY_VERSION

#: Refusal code every caller must surface unchanged.
TIMELINE_CALENDAR_UNKNOWN: Final[str] = "TIMELINE_CALENDAR_UNKNOWN"

#: Guard against an unbounded walk if the holiday list were ever pathological.
#: 40 calendar days comfortably contains 15 trading sessions plus holidays.
_MAX_WALK_DAYS: Final[int] = 400


class CalendarUnavailable(LookupError):
    """The trading calendar cannot answer, so no horizon can be computed.

    Carries :data:`TIMELINE_CALENDAR_UNKNOWN` as ``code`` so callers refuse
    with the declared reason instead of inventing their own wording.
    """

    code = TIMELINE_CALENDAR_UNKNOWN


def _check_exchange(exchange: str) -> str:
    name = (exchange or "").upper()
    if name not in SUPPORTED_EXCHANGES:
        raise CalendarUnavailable(
            f"{TIMELINE_CALENDAR_UNKNOWN}: unsupported exchange {exchange!r}; "
            f"known: {sorted(SUPPORTED_EXCHANGES)}"
        )
    return name


def _require_covered(day: date) -> None:
    if not calendar_covers(day):
        raise CalendarUnavailable(
            f"{TIMELINE_CALENDAR_UNKNOWN}: no verified holiday list for "
            f"{day.isoformat()} (verified years {sorted(VERIFIED_YEARS)})"
        )


def is_trading_session(day: date, exchange: str = "NFO") -> bool:
    """Is ``day`` a trading session on ``exchange``?"""
    name = _check_exchange(exchange)
    _require_covered(day)
    try:
        return is_trading_day(day, name)
    except LookupError as exc:  # pragma: no cover - guarded by _require_covered
        raise CalendarUnavailable(f"{TIMELINE_CALENDAR_UNKNOWN}: {exc}") from exc


def next_trading_session(day: date, exchange: str = "NFO") -> date:
    """The first trading session strictly after ``day``."""
    name = _check_exchange(exchange)
    cursor = day
    for _ in range(_MAX_WALK_DAYS):
        cursor += timedelta(days=1)
        # Checked per step: walking off the end of the verified list must
        # refuse, not wrap around to a guess.
        if is_trading_session(cursor, name):
            return cursor
    raise CalendarUnavailable(
        f"{TIMELINE_CALENDAR_UNKNOWN}: no trading session within "
        f"{_MAX_WALK_DAYS} days after {day.isoformat()}"
    )


def add_trading_sessions(day: date, sessions: int, exchange: str = "NFO") -> date:
    """``day`` advanced by ``sessions`` trading sessions.

    ``sessions=0`` returns ``day`` itself, and asserts it is a session. Counting
    forward from a holiday would otherwise produce a horizon anchored to a day
    the position could not have been opened on.
    """
    if sessions < 0:
        raise ValueError("sessions must not be negative")
    name = _check_exchange(exchange)
    if not is_trading_session(day, name):
        raise CalendarUnavailable(
            f"{TIMELINE_CALENDAR_UNKNOWN}: {day.isoformat()} is not a trading "
            f"session on {name}, so it cannot anchor a session count"
        )
    cursor = day
    for _ in range(sessions):
        cursor = next_trading_session(cursor, name)
    return cursor


def sessions_between(start: date, end: date, exchange: str = "NFO") -> int:
    """Trading sessions strictly after ``start`` up to and including ``end``.

    So a position entered and still open on its entry day has elapsed 0
    sessions, and one still open on the next trading day has elapsed 1. That
    matches how a holding period is read on a statement, and it makes
    ``add_trading_sessions``/``sessions_between`` exact inverses.
    """
    name = _check_exchange(exchange)
    if end < start:
        raise ValueError("end precedes start")
    _require_covered(start)
    _require_covered(end)
    count = 0
    cursor = start
    for _ in range(_MAX_WALK_DAYS):
        if cursor >= end:
            return count
        cursor += timedelta(days=1)
        if is_trading_session(cursor, name):
            count += 1
    raise CalendarUnavailable(
        f"{TIMELINE_CALENDAR_UNKNOWN}: range {start.isoformat()}..{end.isoformat()} "
        f"exceeds the {_MAX_WALK_DAYS}-day walk limit"
    )


def can_plan_horizon(entry_day: date, sessions: int, exchange: str = "NFO") -> bool:
    """Could a hard exit ``sessions`` sessions out be computed today?

    The origination gate for Overnight and Swing. A Swing entered in late
    December cannot reach its 15th session inside the verified calendar, and
    that must block the entry rather than produce a fabricated hard exit.
    """
    try:
        add_trading_sessions(entry_day, sessions, exchange)
    except (CalendarUnavailable, ValueError):
        return False
    return True
