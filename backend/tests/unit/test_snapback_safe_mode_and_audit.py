"""The safety switch and the daily report.

Two properties carry the weight here. Safe mode must survive a restart, because
restarting is the first thing anyone does when something looks wrong. And the
auditor must classify evidence without knowing whether a trade won, because an
audit that can see P&L eventually becomes a filter that keeps winners.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from app.services.safe_mode import (
    NORMAL,
    SAFE_MODE,
    SafeModeError,
    SafeModeService,
    SafeModeTrigger,
)
from study.snapback_evidence_audit import audit_date, render_text


@pytest.fixture
def service(tmp_path):
    return SafeModeService(tmp_path / "safe_mode.json", runtime_sha="abc123")


# ─── safe mode ───────────────────────────────────────────────────────────────

def test_a_fresh_system_is_normal(service):
    state = service.read()

    assert state.state == NORMAL
    assert state.may_open_new_exposure is True


def test_engaging_blocks_new_exposure_only(service):
    service.engage(trigger=SafeModeTrigger.PROTECTION_MISSING, reason="GTT missing")

    state = service.read()
    assert state.active is True
    assert state.may_open_new_exposure is False
    # The whole point: exiting must never be blocked.
    assert state.may_manage_existing_exposure is True


def test_it_survives_a_restart(service, tmp_path):
    service.engage(trigger=SafeModeTrigger.UNKNOWN_BROKER_EXPOSURE, reason="stray position")

    # A brand new service object, as a restarted process would create.
    restarted = SafeModeService(tmp_path / "safe_mode.json")

    assert restarted.read().active is True
    assert "UNKNOWN_BROKER_EXPOSURE" in restarted.read().triggers


def test_an_unreadable_state_file_blocks_rather_than_assuming_normal(service, tmp_path):
    (tmp_path / "safe_mode.json").write_text("{ this is not json")

    state = service.read()

    assert state.state == SAFE_MODE
    assert "unreadable" in state.reason


def test_an_unrecognised_state_blocks(service, tmp_path):
    (tmp_path / "safe_mode.json").write_text(json.dumps({"state": "PROBABLY_FINE"}))

    assert service.read().state == SAFE_MODE


def test_release_is_refused_while_a_real_condition_stands(service):
    service.engage(trigger=SafeModeTrigger.RECONCILIATION_FAILURE)

    with pytest.raises(SafeModeError) as excinfo:
        service.release()

    assert "RECONCILIATION_FAILURE" in str(excinfo.value)
    assert service.read().active is True


def test_release_needs_an_explicit_operator_acknowledgement(service):
    service.engage(trigger=SafeModeTrigger.PROTECTION_FAILURE)

    service.release(operator_ack=True)

    assert service.read().state == NORMAL


def test_an_operator_engaged_safe_mode_needs_no_investigation_to_clear(service):
    service.engage(trigger=SafeModeTrigger.OPERATOR, reason="going out")

    service.release()

    assert service.read().state == NORMAL


def test_a_second_trigger_does_not_reset_how_long_it_has_been_blocked(service):
    first = service.engage(trigger=SafeModeTrigger.PROTECTION_MISSING)
    second = service.engage(trigger=SafeModeTrigger.UNRESOLVED_ORDER)

    assert second.entered_at == first.entered_at
    assert set(second.triggers) == {"PROTECTION_MISSING", "UNRESOLVED_ORDER"}


def test_an_unknown_trigger_is_refused(service):
    with pytest.raises(SafeModeError):
        service.engage(trigger="FEELS_WRONG")


def test_releasing_when_not_engaged_is_harmless(service):
    assert service.release().state == NORMAL


def test_the_state_file_is_written_atomically(service, tmp_path):
    service.engage(trigger=SafeModeTrigger.OPERATOR)

    assert (tmp_path / "safe_mode.json").exists()
    assert not list(tmp_path.glob("*.staging"))


def test_the_file_is_readable_by_a_person(service, tmp_path):
    """An operator must be able to cat it when the app will not start."""
    service.engage(trigger=SafeModeTrigger.PROTECTION_MISSING, reason="no stop on OPP-1")

    blob = json.loads((tmp_path / "safe_mode.json").read_text())

    assert blob["state"] == "SAFE_MODE"
    assert blob["reason"] == "no stop on OPP-1"


# ─── the auditor ─────────────────────────────────────────────────────────────

def _seed(root, rows):
    from app.services.snapback_evidence_store import SnapbackEvidenceStore

    store = SnapbackEvidenceStore(root, session_date=date(2026, 9, 17))
    for kind, payload in rows:
        store._write(kind, payload)
    return store


def _lifecycle(opportunity_id, state, sequence=0):
    return ("lifecycle", {
        "opportunity_id": opportunity_id, "sequence": sequence, "state": state,
        "event_type": "STATE_TRANSITION", "outcome": "", "previous_state": None,
        "detail": None, "occurred_at": "", "received_at": "", "payload_hash": "",
        "runtime_sha": "abc", "schema_version": "1", "event_id": f"{opportunity_id}-{sequence}",
    })


def test_an_empty_day_reports_zeroes_not_an_error(tmp_path):
    report = audit_date(tmp_path, date(2026, 9, 17))

    assert report["opportunities"] == 0
    assert report["economic_authoritative"] == 0


def test_the_denominator_is_reported(tmp_path):
    _seed(tmp_path, [
        _lifecycle("A", "RECONCILED"),
        _lifecycle("B", "TERMINAL_NOT_LISTED"),
        _lifecycle("C", "TERMINAL_SELECTION_UNKNOWN"),
        _lifecycle("D", "OPEN"),
        ("selections", {"opportunity_id": "A", "listed_status": "LISTED"}),
        ("selections", {"opportunity_id": "B", "listed_status": "NOT_LISTED"}),
        ("selections", {"opportunity_id": "C", "listed_status": "UNKNOWN"}),
        ("hedge_selections", {"opportunity_id": "A", "hedge_required": True,
                              "instrument_token": 5001}),
    ])

    report = audit_date(tmp_path, date(2026, 9, 17))

    assert report["opportunities"] == 4
    assert report["selection"] == {"LISTED": 1, "NOT_LISTED": 1, "UNKNOWN": 1}
    assert report["blocked"] == 2
    assert report["economic_authoritative"] == 1
    assert report["inconclusive"] == 1


def test_a_reconciled_trade_without_a_listed_selection_is_not_authoritative(tmp_path):
    _seed(tmp_path, [
        _lifecycle("A", "RECONCILED"),
        ("selections", {"opportunity_id": "A", "listed_status": "UNKNOWN"}),
        ("hedge_selections", {"opportunity_id": "A", "hedge_required": True,
                              "instrument_token": 5001}),
    ])

    report = audit_date(tmp_path, date(2026, 9, 17))

    # Reaching RECONCILED is necessary, not sufficient.
    assert report["economic_authoritative"] == 0
    assert report["inconclusive"] == 1


def test_a_reconciled_trade_with_an_unknown_hedge_is_not_authoritative(tmp_path):
    _seed(tmp_path, [
        _lifecycle("A", "RECONCILED"),
        ("selections", {"opportunity_id": "A", "listed_status": "LISTED"}),
        ("hedge_selections", {"opportunity_id": "A", "hedge_required": True,
                              "instrument_token": None}),
    ])

    assert audit_date(tmp_path, date(2026, 9, 17))["economic_authoritative"] == 0


def test_a_valid_waiver_still_counts(tmp_path):
    _seed(tmp_path, [
        _lifecycle("A", "RECONCILED"),
        ("selections", {"opportunity_id": "A", "listed_status": "LISTED"}),
        ("hedge_selections", {"opportunity_id": "A", "hedge_required": False,
                              "instrument_token": None}),
    ])

    assert audit_date(tmp_path, date(2026, 9, 17))["economic_authoritative"] == 1


def test_the_auditor_never_reads_profit(tmp_path):
    """An audit that can see P&L becomes a filter that keeps winners."""
    import study.snapback_evidence_audit as mod

    source = open(mod.__file__, encoding="utf-8").read()
    code = "\n".join(
        line for line in source.splitlines()
        if not line.strip().startswith("#")
    )

    for forbidden in ("pnl", "profit", "net_pnl", "expectancy", "winner"):
        assert forbidden not in code.lower().split('"""')[-1], f"auditor must not consider {forbidden}"


def test_the_verdict_is_never_computed_from_one_day(tmp_path):
    report = audit_date(tmp_path, date(2026, 9, 17))

    assert "INCONCLUSIVE" in report["economic_verdict"]


def test_the_text_report_renders(tmp_path):
    _seed(tmp_path, [_lifecycle("A", "RECONCILED")])

    text = render_text(audit_date(tmp_path, date(2026, 9, 17)))

    assert "SNAPBACK EVIDENCE AUDIT" in text
    assert "Economic-authoritative trades" in text
