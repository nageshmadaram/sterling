"""The start and stop sequences, and the rules they must not bend.

What is being tested here is not that eleven functions run. It is that an
unanswerable question stops the sequence, that a failed start leaves the system
refusing exposure, and that a stop will not walk away from an open position.
"""
from __future__ import annotations

import pytest

from app.services import service_lifecycle as lifecycle
from app.services.service_lifecycle import (
    LifecycleReport, StepResult, render_report, start_sequence, stop_sequence,
)


def _step(n, name, ok, detail=""):
    return lambda: StepResult(n, name, ok, detail)


class TestTheSequenceStopsAtTheFirstNonPass:
    def test_a_failure_stops_the_run(self):
        report = start_sequence([
            _step(1, "first", True),
            _step(2, "second", False, "broker said no"),
            _step(3, "third", True),
        ])
        assert [s.step for s in report.steps] == [1, 2]
        assert report.completed is False
        assert report.exit_code == 1
        assert report.stopped_at.name == "second"

    def test_unknown_stops_the_run_and_is_not_a_pass(self):
        report = start_sequence([
            _step(1, "first", True),
            _step(2, "second", None, "could not read the clock"),
            _step(3, "third", True),
        ])
        assert report.completed is False
        # Exit code 2 is the contract's "could not be checked", distinct from a
        # failure, because an operator must be able to tell them apart.
        assert report.exit_code == 2
        assert "That is not a pass" in render_report(report)

    def test_all_eleven_passing_completes(self):
        report = start_sequence([_step(i, f"s{i}", True) for i in range(1, 12)])
        assert report.completed is True
        assert report.exit_code == 0
        assert report.stopped_at is None


class TestAFailedStartLeavesTheSystemRefusingExposure:
    def test_recovery_required_is_engaged_first_and_cleared_last(self):
        assert lifecycle.START_STEPS[0] is lifecycle._step_engage_recovery
        assert lifecycle.START_STEPS[-1] is lifecycle._step_clear_recovery

    def test_a_start_that_stops_early_never_reaches_the_clear(self, monkeypatch):
        states: list[str] = []
        monkeypatch.setattr(lifecycle, "_set_recovery",
                            lambda state, code, reason: states.append(state))
        report = start_sequence([
            lifecycle._step_engage_recovery,
            _step(2, "doctor", False, "disk full"),
            lifecycle._step_clear_recovery,
        ])
        assert states == ["RECOVERY_REQUIRED"]
        assert report.completed is False
        assert "remains in RECOVERY_REQUIRED" in render_report(report)


class TestTheStopWillNotAbandonExposure:
    def test_an_open_position_refuses_the_stop(self, monkeypatch):
        from app.services.kite_engine import order_journal, positions

        held = type("P", (), {"symbol": "NIFTY26JAN24000CE"})()
        monkeypatch.setattr(positions, "known_uids", lambda: ["family"])
        monkeypatch.setattr(positions, "open_positions", lambda uid: [held])
        monkeypatch.setattr(order_journal, "unresolved", lambda uid: [])

        result = lifecycle._step_no_abandoned_exposure()
        assert result.ok is False
        assert "NIFTY26JAN24000CE" in result.detail

    def test_an_unresolved_intent_refuses_the_stop(self, monkeypatch):
        from app.services.kite_engine import order_journal, positions

        intent = type("I", (), {"symbol": "BANKNIFTY26JAN52000PE"})()
        monkeypatch.setattr(positions, "known_uids", lambda: ["family"])
        monkeypatch.setattr(positions, "open_positions", lambda uid: [])
        monkeypatch.setattr(order_journal, "unresolved", lambda uid: [intent])

        result = lifecycle._step_no_abandoned_exposure()
        assert result.ok is False
        assert "unresolved order intent" in result.detail

    def test_an_unreadable_registry_is_unknown_not_empty(self, monkeypatch):
        from app.services.kite_engine import positions

        def _boom():
            raise RuntimeError("database is locked")

        monkeypatch.setattr(positions, "known_uids", _boom)
        result = lifecycle._step_no_abandoned_exposure()
        assert result.ok is None
        assert "database is locked" in result.detail

    def test_nothing_held_permits_the_stop(self, monkeypatch):
        from app.services.kite_engine import order_journal, positions

        monkeypatch.setattr(positions, "known_uids", lambda: ["family"])
        monkeypatch.setattr(positions, "open_positions", lambda uid: [])
        monkeypatch.setattr(order_journal, "unresolved", lambda uid: [])
        assert lifecycle._step_no_abandoned_exposure().ok is True

    def test_force_continues_past_a_refusal_without_hiding_it(self):
        report = stop_sequence([
            _step(1, "halt", True),
            _step(2, "reconcile", False, "one mismatch"),
            _step(3, "no exposure", True),
        ], force=True)
        assert [s.step for s in report.steps] == [1, 2, 3]
        # The refusal is still in the report and still in the exit code; force
        # only allows the remaining steps to run.
        assert report.exit_code == 1
        assert "FAILED" in render_report(report)


class TestTheFeedFreshnessRule:
    def test_a_connected_socket_with_no_ticks_is_unknown(self, monkeypatch):
        monkeypatch.setattr(lifecycle, "_ops_get",
                            lambda path, timeout=5.0: {"feed": {"connected": True, "last_tick_ms": 0}})
        result = lifecycle._step_fresh_feed()
        assert result.ok is None
        assert "no tick" in result.detail

    def test_a_stale_tick_fails_even_though_the_socket_is_connected(self, monkeypatch):
        import time

        stale = int(time.time() * 1000) - (lifecycle.MAX_TICK_AGE_MS + 5_000)
        monkeypatch.setattr(lifecycle, "_ops_get",
                            lambda path, timeout=5.0: {"feed": {"connected": True, "last_tick_ms": stale}})
        assert lifecycle._step_fresh_feed().ok is False

    def test_a_fresh_tick_passes(self, monkeypatch):
        import time

        fresh = int(time.time() * 1000) - 1_000
        monkeypatch.setattr(lifecycle, "_ops_get",
                            lambda path, timeout=5.0: {"feed": {"connected": True, "last_tick_ms": fresh}})
        assert lifecycle._step_fresh_feed().ok is True

    def test_an_unreachable_service_is_unknown(self, monkeypatch):
        def _boom(path, timeout=5.0):
            raise OSError("connection refused")

        monkeypatch.setattr(lifecycle, "_ops_get", _boom)
        assert lifecycle._step_fresh_feed().ok is None


class TestTheBrokerBindingRule:
    def test_an_authenticated_account_that_is_not_the_binding_fails(self, monkeypatch):
        monkeypatch.setattr(lifecycle, "_ops_get", lambda path, timeout=5.0: {
            "broker": {"connected": True, "account_id": "AB1234",
                       "bound_client_id": "ZZ9999", "binding_matches": False}})
        result = lifecycle._step_broker_connected()
        assert result.ok is False
        assert "AB1234" in result.detail and "ZZ9999" in result.detail

    def test_no_active_binding_is_unknown_not_permission(self, monkeypatch):
        monkeypatch.setattr(lifecycle, "_ops_get", lambda path, timeout=5.0: {
            "broker": {"connected": True, "account_id": "AB1234",
                       "bound_client_id": None, "binding_matches": None}})
        assert lifecycle._step_broker_connected().ok is None

    def test_a_matching_binding_passes(self, monkeypatch):
        monkeypatch.setattr(lifecycle, "_ops_get", lambda path, timeout=5.0: {
            "broker": {"connected": True, "account_id": "AB1234",
                       "bound_client_id": "AB1234", "binding_matches": True}})
        assert lifecycle._step_broker_connected().ok is True


class TestTheOperatorCanReadIt:
    def test_the_report_names_the_step_that_stopped_it(self):
        report = LifecycleReport(action="start", steps=[
            StepResult(1, "engage RECOVERY_REQUIRED", True),
            StepResult(2, "pre-start doctor", False, "disk 96% full"),
        ])
        text = render_report(report)
        assert "pre-start doctor" in text
        assert "disk 96% full" in text
        assert "STOPPED at step 2" in text

    def test_a_clean_run_says_so_without_qualification(self):
        report = LifecycleReport(action="start",
                                 steps=[StepResult(i, f"s{i}", True) for i in range(1, 12)])
        assert "completed; every step ran and passed" in render_report(report)


def test_the_documented_step_counts_match_the_spec():
    """Eleven start steps and five stop steps, as the runbook promises."""
    assert len(lifecycle.START_STEPS) == 11
    assert len(lifecycle.STOP_STEPS) == 5
    assert [s().step for s in ()] == []  # no accidental empty-sequence pass
    assert start_sequence([]).completed is False
