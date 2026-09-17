"""An alert must be actionable, and one root cause must be one incident."""
from __future__ import annotations

import pytest

from app.core.operator_alerts import (
    ACTIONS,
    OperatorAlert,
    Severity,
    collapse_to_root_causes,
    from_operational,
    render_alert,
    render_incident,
    should_enter_safe_mode,
)
from app.services.snapback_alerts import derive_operational_alerts


def _alert(**overrides) -> OperatorAlert:
    row = {
        "code": "database_unavailable",
        "severity": Severity.CRITICAL,
        "what_happened": "The evidence database cannot be read.",
        "trading_blocked": True,
        "next_action": "Run `sterlingctl safe on`.",
    }
    row.update(overrides)
    return OperatorAlert(**row)


class TestConstructorGuards:
    def test_an_alert_without_a_next_action_is_refused(self):
        with pytest.raises(ValueError, match="no next action"):
            _alert(next_action="   ")

    def test_an_alert_that_does_not_say_what_happened_is_refused(self):
        with pytest.raises(ValueError, match="does not say what happened"):
            _alert(what_happened="")

    def test_a_critical_that_does_not_block_trading_is_refused(self):
        # Either it is mis-severitied or it is a safety hole. Both are bugs.
        with pytest.raises(ValueError, match="CRITICAL but does not block"):
            _alert(trading_blocked=False)

    def test_a_warning_may_leave_trading_allowed(self):
        row = _alert(severity=Severity.WARNING, trading_blocked=False)
        assert row.severity is Severity.WARNING


class TestRendering:
    def test_every_required_element_is_present(self):
        out = render_alert(_alert(affected=("NIFTY26SEP25000CE",)))

        assert "[CRITICAL]" in out
        assert "cannot be read" in out
        assert "New trading: BLOCKED" in out
        assert "NIFTY26SEP25000CE" in out
        assert "Do this:" in out

    def test_no_affected_positions_says_so_rather_than_nothing(self):
        assert "Affected: none identified" in render_alert(_alert())


class TestTranslation:
    def test_a_known_code_gets_its_written_action(self):
        operational = next(
            a
            for a in derive_operational_alerts(
                health={"database_ok": False}, backup_ok=True
            )
            if a.code == "database_unavailable"
        )

        row = from_operational(operational)

        assert row.severity is Severity.CRITICAL
        assert row.trading_blocked
        assert "restore-check" in row.next_action

    def test_an_unknown_code_is_not_given_an_invented_action(self):
        class Unknown:
            code = "something_new"
            severity = "CRITICAL"
            message = "Nobody has seen this before."

        row = from_operational(Unknown())

        assert row.trading_blocked
        assert "No action is written down" in row.next_action
        assert "escalate" in row.next_action

    def test_every_derived_code_has_an_action_written_down(self):
        # Any code the derivation can emit and this table does not know about
        # reaches an operator with no instruction. That is the gap this asserts.
        emitted = {
            a.code
            for a in derive_operational_alerts(
                health={
                    "database_ok": False,
                    "manifest_ok": False,
                    "runner_alive": False,
                    "broker_connected": False,
                    "market_data_fresh": False,
                    "market_open": True,
                    "calendar_ok": False,
                    "stop_monitor_gap": True,
                    "status": "HALTED",
                },
                backup_ok=False,
                report_ok=False,
                session_evidence_incomplete=True,
                daily_loss_breached=True,
                drawdown_breached=True,
                reconciliation_mismatch=True,
                exit_pending_stale=True,
                processing_entry_stale=True,
            )
        }

        assert emitted, "the derivation produced nothing to check"
        assert emitted - set(ACTIONS) == set()


class TestRootCauses:
    def test_a_dead_database_owns_its_downstream_failures(self):
        alerts = [
            _alert(),
            _alert(
                code="backup_failed",
                what_happened="Backup did not complete.",
                next_action="Run backup.",
            ),
            _alert(
                code="evidence_report_failed",
                what_happened="Report failed.",
                next_action="Run report.",
            ),
        ]

        roots, effects = collapse_to_root_causes(alerts)

        assert [r.code for r in roots] == ["database_unavailable"]
        assert {e.code for e in effects} == {"backup_failed", "evidence_report_failed"}
        assert all(e.caused_by == "database_unavailable" for e in effects)

    def test_nothing_is_discarded(self):
        alerts = [
            _alert(),
            _alert(
                code="backup_failed",
                what_happened="Backup did not complete.",
                next_action="Run backup.",
            ),
        ]
        roots, effects = collapse_to_root_causes(alerts)
        assert len(roots) + len(effects) == len(alerts)

    def test_an_effect_without_its_cause_stands_on_its_own(self):
        # A failed backup with a healthy database is its own problem.
        alerts = [
            _alert(
                code="backup_failed",
                what_happened="Backup did not complete.",
                next_action="Run backup.",
            )
        ]
        roots, effects = collapse_to_root_causes(alerts)

        assert [r.code for r in roots] == ["backup_failed"]
        assert effects == ()

    def test_a_halt_is_filed_under_the_breaker_that_caused_it(self):
        alerts = [
            _alert(
                code="system_halted",
                what_happened="Sterling is HALTED.",
                next_action="Clear the cause.",
            ),
            _alert(
                code="drawdown_breaker",
                what_happened="Drawdown limit breached.",
                next_action="Leave the block in place.",
            ),
        ]

        roots, effects = collapse_to_root_causes(alerts)

        assert [r.code for r in roots] == ["drawdown_breaker"]
        assert [e.code for e in effects] == ["system_halted"]


class TestSafeMode:
    def test_any_critical_blocks_new_exposure(self):
        assert should_enter_safe_mode([_alert()])

    def test_warnings_alone_do_not(self):
        assert not should_enter_safe_mode(
            [_alert(severity=Severity.WARNING, trading_blocked=True)]
        )

    def test_no_alerts_does_not(self):
        assert not should_enter_safe_mode([])


def test_the_incident_reads_as_one_problem():
    out = render_incident(
        [
            _alert(),
            _alert(
                code="backup_failed",
                what_happened="Backup did not complete.",
                next_action="Run backup.",
            ),
        ]
    )

    assert "database_unavailable" in out
    assert "Downstream of the above" in out
    assert "SAFE_MODE" in out


def test_no_alerts_renders_as_no_alerts():
    assert render_incident([]) == "No alerts."
