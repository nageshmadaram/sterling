"""Trading-session arithmetic and the immutable per-trade horizon plan.

The rules pinned here are the ones whose absence produces a silently wrong
holding period: a weekend counted as three days, a holiday counted as a
session, a hard exit recomputed from an edited config, and an unknown calendar
treated as permission.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.core.horizon import (
    MODE_TIMELINES,
    HorizonMode,
    TimelineState,
    timeline_for,
)
from app.core.session_calendar import (
    TIMELINE_CALENDAR_UNKNOWN,
    CalendarUnavailable,
    add_trading_sessions,
    can_plan_horizon,
    is_trading_session,
    next_trading_session,
    sessions_between,
)
from app.core.trade_horizon import (
    HorizonUnavailable,
    TradeHorizonPlan,
    build_horizon_plan,
)

IST = ZoneInfo("Asia/Kolkata")

# Real 2026 NSE calendar landmarks (see market_hours._HOLIDAYS).
FRI = date(2026, 9, 18)          # trading Friday
MON = date(2026, 9, 21)          # the next session after FRI
HOLIDAY_MON = date(2026, 9, 14)  # declared holiday, a Monday
PRE_HOLIDAY_FRI = date(2026, 9, 11)
SAT = date(2026, 9, 19)


def ist(day: date, hh: int, mm: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=IST)


# ── calendar ──────────────────────────────────────────────────────────────


def test_weekday_is_a_session_and_weekend_is_not():
    assert is_trading_session(FRI) is True
    assert is_trading_session(SAT) is False


def test_declared_holiday_is_not_a_session():
    assert is_trading_session(HOLIDAY_MON) is False


def test_friday_to_monday_is_one_session_not_three_days():
    """The rule the whole module exists for."""
    assert next_trading_session(FRI) == MON
    assert sessions_between(FRI, MON) == 1
    assert (MON - FRI).days == 3


def test_a_holiday_monday_is_skipped():
    assert next_trading_session(PRE_HOLIDAY_FRI) == date(2026, 9, 15)


def test_sessions_between_counts_zero_on_the_entry_day():
    assert sessions_between(FRI, FRI) == 0


def test_add_and_between_are_exact_inverses():
    for n in range(0, 12):
        landed = add_trading_sessions(FRI, n)
        assert sessions_between(FRI, landed) == n


def test_add_trading_sessions_crosses_a_month_boundary():
    assert add_trading_sessions(date(2026, 9, 30), 1) == date(2026, 10, 1)
    # 2026-10-02 is a holiday, so two sessions out lands on the Monday.
    assert add_trading_sessions(date(2026, 9, 30), 2) == date(2026, 10, 5)


def test_counting_from_a_non_session_is_refused():
    """A horizon anchored to a holiday describes a trade that never opened."""
    with pytest.raises(CalendarUnavailable):
        add_trading_sessions(SAT, 3)


def test_unverified_year_fails_closed():
    with pytest.raises(CalendarUnavailable) as exc:
        is_trading_session(date(2027, 1, 4))
    assert TIMELINE_CALENDAR_UNKNOWN in str(exc.value)


def test_unsupported_exchange_fails_closed():
    with pytest.raises(CalendarUnavailable):
        is_trading_session(FRI, "MCX")


def test_a_swing_horizon_that_leaves_the_verified_calendar_cannot_be_planned():
    """Late December + 15 sessions runs past the verified holiday list."""
    assert can_plan_horizon(FRI, 15) is True
    assert can_plan_horizon(date(2026, 12, 28), 15) is False


# ── clock-based plans ─────────────────────────────────────────────────────


def _plan(mode: HorizonMode, at: datetime, **kw) -> TradeHorizonPlan:
    return build_horizon_plan(
        strategy_id="snapback", mode=mode, entry_at=at, **kw
    )


def test_ultra_scalping_hard_exit_is_twenty_minutes():
    entry = ist(FRI, 10, 4)
    plan = _plan(HorizonMode.ULTRA_SCALPING, entry)
    assert plan.hard_exit_at == entry + timedelta(minutes=20)
    assert plan.expected_window_start == entry + timedelta(minutes=1)
    assert plan.expected_window_end == entry + timedelta(minutes=10)
    assert plan.hard_exit_session is None


def test_scalping_hard_exit_is_ninety_minutes():
    entry = ist(FRI, 10, 0)
    plan = _plan(HorizonMode.SCALPING, entry)
    assert plan.hard_exit_at == entry + timedelta(minutes=90)


def test_intraday_hard_exit_is_clamped_to_the_session_square_off():
    """15:20 IST is a ceiling, and the mode's own 6h budget cannot lift it."""
    entry = ist(FRI, 13, 0)
    plan = _plan(HorizonMode.INTRADAY, entry)
    assert plan.hard_exit_at == ist(FRI, 15, 20)
    # The raw budget would have run past the close.
    assert entry + timedelta(seconds=timeline_for("intraday").hard_hold_limit) > ist(
        FRI, 15, 20
    )


def test_square_off_never_extends_a_shorter_budget():
    entry = ist(FRI, 9, 30)
    plan = _plan(HorizonMode.SCALPING, entry)
    assert plan.hard_exit_at == entry + timedelta(minutes=90)
    assert plan.hard_exit_at < ist(FRI, 15, 20)


@pytest.mark.parametrize(
    "mode", [HorizonMode.ULTRA_SCALPING, HorizonMode.SCALPING, HorizonMode.INTRADAY]
)
def test_session_bound_modes_never_plan_past_the_square_off(mode):
    plan = _plan(mode, ist(FRI, 9, 30))
    assert plan.hard_exit_at is not None
    assert plan.hard_exit_at <= ist(FRI, 15, 20)
    assert plan.hard_exit_at.date() == FRI


def test_entry_after_the_square_off_is_refused_not_carried_overnight():
    with pytest.raises(HorizonUnavailable):
        _plan(HorizonMode.SCALPING, ist(FRI, 15, 30))


def test_entry_with_too_little_session_left_is_refused():
    """An intraday entry at 15:00 could never run its 45-minute minimum."""
    with pytest.raises(HorizonUnavailable):
        _plan(HorizonMode.INTRADAY, ist(FRI, 15, 0))


def test_utc_entry_converts_exactly_to_ist():
    """04:34 UTC is 10:04 IST; the horizon must not drift by the offset."""
    utc_entry = datetime(2026, 9, 18, 4, 34, tzinfo=timezone.utc)
    plan = _plan(HorizonMode.ULTRA_SCALPING, utc_entry)
    assert plan.entry_at == ist(FRI, 10, 4)
    assert plan.hard_exit_at == ist(FRI, 10, 24)


def test_naive_entry_is_refused():
    with pytest.raises(ValueError):
        _plan(HorizonMode.SCALPING, datetime(2026, 9, 18, 10, 0))


# ── session-based plans ───────────────────────────────────────────────────


def test_overnight_hard_maximum_is_five_sessions():
    plan = _plan(HorizonMode.OVERNIGHT, ist(FRI, 11, 15))
    assert plan.hard_max_sessions == 5
    assert plan.hard_exit_session == add_trading_sessions(FRI, 5)
    assert plan.hard_exit_at is None


def test_swing_hard_maximum_is_fifteen_sessions():
    plan = _plan(HorizonMode.SWING, ist(FRI, 11, 15))
    assert plan.hard_max_sessions == 15
    assert plan.hard_exit_session == add_trading_sessions(FRI, 15)


def test_friday_entry_counts_the_weekend_as_one_session():
    plan = _plan(HorizonMode.OVERNIGHT, ist(FRI, 11, 15))
    assert plan.entry_session_date == FRI
    assert plan.sessions_elapsed(ist(MON, 10, 0)) == 1
    # Not three, which a calendar-day count would have produced.
    assert (MON - FRI).days == 3


def test_session_plan_skips_a_holiday():
    plan = _plan(HorizonMode.OVERNIGHT, ist(PRE_HOLIDAY_FRI, 11, 0))
    assert plan.sessions_elapsed(ist(HOLIDAY_MON, 11, 0)) == 0
    assert plan.sessions_elapsed(ist(date(2026, 9, 15), 11, 0)) == 1


def test_session_entry_on_a_non_session_is_refused():
    with pytest.raises(HorizonUnavailable):
        _plan(HorizonMode.SWING, ist(SAT, 11, 0))


def test_swing_entry_that_outruns_the_verified_calendar_is_refused():
    with pytest.raises(HorizonUnavailable) as exc:
        _plan(HorizonMode.SWING, ist(date(2026, 12, 28), 11, 0))
    assert TIMELINE_CALENDAR_UNKNOWN in str(exc.value)


# ── timeline state ────────────────────────────────────────────────────────


def test_clock_timeline_states_in_order():
    entry = ist(FRI, 10, 4)
    plan = _plan(HorizonMode.ULTRA_SCALPING, entry)
    assert plan.state(entry + timedelta(seconds=30)) is TimelineState.EARLY
    assert plan.state(entry + timedelta(minutes=8)) is TimelineState.EXPECTED
    assert plan.state(entry + timedelta(minutes=15)) is TimelineState.EXTENDED
    assert plan.state(entry + timedelta(minutes=20)) is TimelineState.HARD_EXIT_DUE
    assert plan.state(entry + timedelta(minutes=99)) is TimelineState.HARD_EXIT_DUE
    assert plan.state(entry, closed=True) is TimelineState.CLOSED


def test_session_timeline_states_in_order():
    plan = _plan(HorizonMode.SWING, ist(FRI, 11, 15))

    def at(n):
        return ist(add_trading_sessions(FRI, n), 11, 0)

    assert plan.state(at(1)) is TimelineState.EARLY
    assert plan.state(at(4)) is TimelineState.EXPECTED
    assert plan.state(at(12)) is TimelineState.EXTENDED
    assert plan.state(at(15)) is TimelineState.HARD_EXIT_DUE


def test_uncomputable_state_is_unknown_and_forces_an_exit():
    """An UNKNOWN horizon is never a reason to keep carrying risk."""
    plan = _plan(HorizonMode.SWING, ist(FRI, 11, 15))
    beyond_calendar = datetime(2027, 3, 1, 11, 0, tzinfo=IST)
    assert plan.state(beyond_calendar) is TimelineState.UNKNOWN
    assert plan.hard_exit_due(beyond_calendar) is True


def test_hard_exit_due_is_false_inside_the_budget():
    entry = ist(FRI, 10, 4)
    plan = _plan(HorizonMode.ULTRA_SCALPING, entry)
    assert plan.hard_exit_due(entry + timedelta(minutes=5)) is False


# ── immutability ──────────────────────────────────────────────────────────


def test_plan_is_frozen():
    plan = _plan(HorizonMode.SCALPING, ist(FRI, 10, 0))
    with pytest.raises(Exception):
        plan.hard_exit_at = ist(FRI, 15, 0)  # type: ignore[misc]


def test_a_later_config_change_does_not_move_an_open_position(monkeypatch):
    """Edit a mode at 14:00 and every already-open position must keep its budget."""
    entry = ist(FRI, 10, 0)
    plan = _plan(HorizonMode.SCALPING, entry)
    original = (
        plan.expected_window_end,
        plan.hard_exit_at,
        plan.mode_version,
        plan.mode_config_hash,
    )

    widened = MODE_TIMELINES[HorizonMode.SCALPING].__class__(
        **{
            **{
                f: getattr(MODE_TIMELINES[HorizonMode.SCALPING], f)
                for f in MODE_TIMELINES[HorizonMode.SCALPING].__dataclass_fields__
            },
            "expected_hold_max": 4 * 3600,
            "hard_hold_limit": 5 * 3600,
            "version": "scalping_v2",
        }
    )
    monkeypatch.setitem(MODE_TIMELINES, HorizonMode.SCALPING, widened)

    assert (
        plan.expected_window_end,
        plan.hard_exit_at,
        plan.mode_version,
        plan.mode_config_hash,
    ) == original
    assert plan.state(entry + timedelta(minutes=95)) is TimelineState.HARD_EXIT_DUE

    # A position opened after the edit does get the new budget.
    fresh = _plan(HorizonMode.SCALPING, ist(FRI, 10, 30))
    assert fresh.mode_version == "scalping_v2"
    assert fresh.mode_config_hash != plan.mode_config_hash


def test_plan_id_is_stable_and_separates_modes():
    entry = ist(FRI, 10, 0)
    a = _plan(HorizonMode.SCALPING, entry)
    b = _plan(HorizonMode.SCALPING, entry)
    c = _plan(HorizonMode.ULTRA_SCALPING, entry)
    assert a.plan_id == b.plan_id
    assert a.plan_id != c.plan_id


def test_a_plan_must_have_exactly_one_hard_limit():
    entry = ist(FRI, 10, 0)
    common = dict(
        strategy_id="snapback",
        mode=HorizonMode.SCALPING,
        entry_at=entry,
        expected_window_start=entry,
        expected_window_end=entry + timedelta(minutes=45),
        review_interval_seconds=15,
        exchange="NFO",
        timezone="Asia/Kolkata",
        calendar_version="x",
        mode_version="scalping_v1",
    )
    with pytest.raises(ValueError):
        TradeHorizonPlan(**common, hard_exit_at=None, hard_exit_session=None)
    with pytest.raises(ValueError):
        TradeHorizonPlan(
            **common,
            hard_exit_at=entry + timedelta(minutes=90),
            hard_exit_session=MON,
        )


# ── presentation ──────────────────────────────────────────────────────────


def test_clock_row_carries_the_operator_fields():
    entry = ist(FRI, 10, 4)
    row = _plan(HorizonMode.ULTRA_SCALPING, entry).describe(
        entry + timedelta(minutes=8, seconds=31)
    )
    assert row["mode"] == "ultra_scalping"
    assert row["timeline_state"] == "expected"
    assert row["age_seconds"] == pytest.approx(511.0)
    assert row["hard_exit_at"] == ist(FRI, 10, 24).isoformat()
    assert row["calendar_version"]


def test_session_row_carries_sessions_not_days():
    plan = _plan(HorizonMode.SWING, ist(FRI, 11, 15))
    row = plan.describe(ist(add_trading_sessions(FRI, 4), 11, 0))
    assert row["sessions_elapsed"] == 4
    assert row["expected_sessions"] == [3, 10]
    assert row["hard_max_sessions"] == 15
    assert row["timeline_state"] == "expected"
    assert "age_seconds" not in row


# ── calendar sources must not drift apart ─────────────────────────────────


def test_the_two_holiday_lists_agree_on_2026():
    """Sterling carries two independently sourced NSE holiday lists.

    ``market_hours`` is built from the NSE circulars; ``navigator.calendar``
    from secondary aggregators, and it also covers 2025. They agree exactly on
    2026 today. Nothing enforces that, so a one-sided edit would let the
    horizon engine and Navigator disagree about whether a position is one
    session old — which surfaces as an unexplained force-exit, not as an error.

    This test fails on the edit rather than on the trade.
    """
    from app.services.kite_engine.market_hours import _HOLIDAYS, VERIFIED_YEARS
    from app.services.navigator.calendar import _NSE_HOLIDAYS_2026, COVERED_YEARS

    assert _HOLIDAYS == _NSE_HOLIDAYS_2026
    # market_hours is the narrower, circular-sourced list; the horizon engine
    # uses it deliberately. Widening it must be a conscious act.
    assert VERIFIED_YEARS == {2026}
    assert VERIFIED_YEARS <= COVERED_YEARS
