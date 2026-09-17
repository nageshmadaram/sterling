"""The doctor verdict and the operator dashboard.

The rule under test throughout: a check that could not be run is not a pass.
``sterlingctl doctor`` exiting zero must mean "I looked and it is fine".
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.core.health import ComponentHealth, SystemHealth, compose_health
from app.core.operator_report import (
    EXIT_COULD_NOT_CHECK,
    EXIT_OK,
    EXIT_UNSAFE,
    DoctorCheck,
    DoctorReport,
    doctor_from_preflight,
    operator_dashboard,
    render_dashboard,
    run_checks,
)


@dataclass
class _Preflight:
    code: str
    passed: bool
    details: object = ""


# ── doctor verdict ────────────────────────────────────────────────────────


def test_all_passing_checks_exit_zero():
    report = DoctorReport(checks=(DoctorCheck("clock", True),))
    assert report.safe is True
    assert report.exit_code == EXIT_OK


def test_a_failed_check_exits_unsafe():
    report = DoctorReport(
        checks=(DoctorCheck("clock", True), DoctorCheck("disk", False, "full"))
    )
    assert report.safe is False
    assert report.exit_code == EXIT_UNSAFE


def test_a_check_that_could_not_run_is_not_a_pass():
    report = DoctorReport(
        checks=(DoctorCheck("clock", True), DoctorCheck("broker", None, "no session"))
    )
    assert report.safe is False
    assert report.exit_code == EXIT_COULD_NOT_CHECK


def test_a_failure_outranks_an_unknown():
    report = DoctorReport(
        checks=(DoctorCheck("disk", False), DoctorCheck("broker", None))
    )
    assert report.exit_code == EXIT_UNSAFE


def test_running_nothing_at_all_is_the_worst_outcome():
    """Reporting success because nothing ran is the one unforgivable answer."""
    report = DoctorReport(checks=())
    assert report.safe is False
    assert report.exit_code == EXIT_COULD_NOT_CHECK


def test_an_exception_becomes_could_not_check_not_a_failure():
    def boom() -> tuple[bool, str]:
        raise ConnectionError("broker unreachable")

    checks = run_checks({"broker": boom, "clock": lambda: (True, "")})
    by_name = {c.name: c for c in checks}
    assert by_name["broker"].passed is None
    assert "ConnectionError" in by_name["broker"].detail
    assert by_name["clock"].passed is True


def test_every_check_runs_even_after_one_fails():
    """A doctor that stops at the first problem hides how much is wrong."""
    calls: list[str] = []

    def record(name: str, ok: bool):
        def check() -> tuple[bool, str]:
            calls.append(name)
            return ok, ""

        return check

    run_checks(
        {"a": record("a", False), "b": record("b", False), "c": record("c", True)}
    )
    assert sorted(calls) == ["a", "b", "c"]


def test_preflight_checks_convert_faithfully():
    report = doctor_from_preflight(
        [_Preflight("disk", False, {"free_bytes": 1}), _Preflight("clock", True)]
    )
    assert report.exit_code == EXIT_UNSAFE
    assert {c.name for c in report.failures} == {"disk"}


def test_the_doctor_can_carry_the_health_report():
    report = doctor_from_preflight(
        [_Preflight("clock", True)],
        components={
            name: ComponentHealth(name, SystemHealth.NORMAL)
            for name in ("broker", "market_data", "evidence", "reconciliation", "safe_mode")
        },
    )
    assert report.health is not None
    assert report.health.overall is SystemHealth.NORMAL
    assert report.as_dict()["health"]["overall"] == "normal"


# ── dashboard ─────────────────────────────────────────────────────────────


def test_the_dashboard_lists_all_ten_lanes():
    payload = operator_dashboard()
    assert len(payload["lanes"]) == 10


def test_every_blocked_lane_states_a_reason():
    payload = operator_dashboard()
    for lane_key, reason in payload["lanes_blocked"].items():
        assert reason, lane_key
    assert set(payload["lanes_originating"]) & set(payload["lanes_blocked"]) == set()


def test_no_supertrend_lane_may_originate_yet():
    payload = operator_dashboard()
    assert not [
        key for key in payload["lanes_originating"] if key.startswith("supertrend:")
    ]


def test_an_unreported_system_shows_as_recovery_required():
    payload = operator_dashboard()
    assert payload["system"]["overall"] == "recovery_required"
    assert payload["system"]["may_open_new_exposure"] is False


def test_rendering_names_the_state_and_the_reason():
    healthy = compose_health(
        {
            name: ComponentHealth(name, SystemHealth.NORMAL)
            for name in ("broker", "market_data", "evidence", "reconciliation", "safe_mode")
        }
    )
    text = render_dashboard(operator_dashboard(healthy))
    assert "STERLING STATUS" in text
    assert "NORMAL" in text
    assert "snapback" not in text or "SNAPBACK" in text
    assert "LANE_RULES_UNDEFINED" in text
    # Column padding must not run the name into its value.
    assert "reconciliationNORMAL" not in text


@pytest.mark.parametrize("lane", ["ultra_scalping", "overnight", "swing"])
def test_every_mode_appears_in_the_rendered_screen(lane):
    assert lane in render_dashboard(operator_dashboard())
