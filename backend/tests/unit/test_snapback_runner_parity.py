"""Runner semantics must match the validated rule exactly.

Two drifts were found: the peak could ratchet on an intraday tick (the backtest
advances it on completed daily closes), and the expiry exit waited until DTE <= 1
(the backtest leaves roughly a six-day buffer).
"""

from __future__ import annotations

import pytest

from app.engines.snapback.policy import (
    RUNNER_EXPIRY_BUFFER_CALENDAR_DAYS,
    RUNNER_PEAK_SOURCE,
    runner_should_exit_for_expiry,
    update_runner_peak,
)


def test_expiry_buffer_is_canonical_and_six_days():
    assert RUNNER_EXPIRY_BUFFER_CALENDAR_DAYS == 6


def test_runner_exits_while_the_buffer_remains():
    assert runner_should_exit_for_expiry(remaining_calendar_days=6) is True
    assert runner_should_exit_for_expiry(remaining_calendar_days=5) is True


def test_runner_holds_outside_the_buffer():
    assert runner_should_exit_for_expiry(remaining_calendar_days=7) is False
    assert runner_should_exit_for_expiry(remaining_calendar_days=20) is False


def test_peak_source_is_the_end_of_day_close():
    assert RUNNER_PEAK_SOURCE == "EOD_CLOSE"


def test_eod_mark_raises_the_peak():
    assert update_runner_peak(peak=160.0, observed_bid=200.0, source="EOD_CLOSE") == pytest.approx(200.0)


def test_intraday_tick_cannot_raise_the_peak():
    # An intraday spike must not ratchet the trail against the position.
    assert update_runner_peak(peak=160.0, observed_bid=250.0, source="INTRADAY") == pytest.approx(160.0)


def test_intraday_tick_below_the_peak_leaves_it_alone():
    assert update_runner_peak(peak=160.0, observed_bid=100.0, source="INTRADAY") == pytest.approx(160.0)


def test_runtime_uses_the_canonical_constants():
    import inspect

    from app.services import snapback as sb
    from app.services import snapback_prospective_collector as collector

    runtime = inspect.getsource(sb.process_prospective_daily_mtm_and_exits)
    lifecycle = inspect.getsource(collector.SnapbackProspectiveCollector.rebalance_and_mtm)

    # No hand-rolled duplicate of the expiry buffer or the peak rule.
    assert "rem_dte <= 1" not in runtime
    assert "RUNNER_EXPIRY_BUFFER_CALENDAR_DAYS" in runtime
    assert "update_runner_peak" in lifecycle
