"""The safety switch and the daily broker-evidence report.

Safe mode must survive a restart. The auditor must classify evidence without
knowing whether a trade won, must fail closed on unreadable evidence, and must
not call a trade authoritative merely because its last lifecycle row says
RECONCILED.
"""

from __future__ import annotations

import json
from datetime import date

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


def test_a_missing_state_file_blocks_rather_than_reading_as_normal(service):
    """No file is an unknown, and an unknown must not admit new risk.

    A machine whose safety state was deleted looks exactly like one that never
    had it. Reading the absence as NORMAL made deleting the file a way to turn
    safe mode off.
    """
    state = service.read()
    assert state.state == SAFE_MODE
    assert state.may_open_new_exposure is False
    assert SafeModeTrigger.SAFETY_STATE_UNAVAILABLE in state.triggers
    # Managing what is already open is never blocked.
    assert state.may_manage_existing_exposure is True


def test_a_missing_state_file_stays_blocked_across_a_restart(service, tmp_path):
    assert service.read().active is True
    assert SafeModeService(tmp_path / "safe_mode.json").read().active is True


def test_initialising_is_the_only_way_a_missing_state_becomes_normal(service):
    with pytest.raises(SafeModeError):
        service.initialise()

    state = service.initialise(operator_ack=True, note="first boot")
    assert state.state == NORMAL
    assert service.read().may_open_new_exposure is True


def test_initialising_never_overwrites_an_engaged_state(service):
    service.engage(trigger=SafeModeTrigger.PROTECTION_MISSING, reason="GTT missing")
    state = service.initialise(operator_ack=True)
    assert state.active is True
    assert SafeModeTrigger.PROTECTION_MISSING in state.triggers


def test_engaging_blocks_new_exposure_only(service):
    service.engage(trigger=SafeModeTrigger.PROTECTION_MISSING, reason="GTT missing")
    state = service.read()
    assert state.active is True
    assert state.may_open_new_exposure is False
    assert state.may_manage_existing_exposure is True


def test_it_survives_a_restart(service, tmp_path):
    service.engage(trigger=SafeModeTrigger.UNKNOWN_BROKER_EXPOSURE, reason="stray position")
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
    service.initialise(operator_ack=True)
    assert service.release().state == NORMAL


def test_releasing_a_missing_state_still_needs_an_acknowledgement(service):
    """There is no quiet path from "no safety state" to "trading allowed"."""
    with pytest.raises(SafeModeError):
        service.release()


def test_the_state_file_is_written_atomically(service, tmp_path):
    service.engage(trigger=SafeModeTrigger.OPERATOR)
    assert (tmp_path / "safe_mode.json").exists()
    assert not list(tmp_path.glob("*.staging"))


def test_the_file_is_readable_by_a_person(service, tmp_path):
    service.engage(trigger=SafeModeTrigger.PROTECTION_MISSING, reason="no stop on OPP-1")
    blob = json.loads((tmp_path / "safe_mode.json").read_text())
    assert blob["state"] == "SAFE_MODE"
    assert blob["reason"] == "no stop on OPP-1"


# ─── the broker-evidence auditor ─────────────────────────────────────────────


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


def _complete_chain(opportunity_id="A", *, waived=False):
    from app.services.snapback_evidence_recorder import State
    hedge_state = State.HEDGE_WAIVED if waived else State.HEDGE_FILLED
    states = [
        State.OPPORTUNITY_CREATED,
        State.CANDIDATE_UNIVERSE_CAPTURED,
        State.SELECTION_RECORDED,
        State.LISTED_CONFIRMED,
        State.HEDGE_SELECTION_RECORDED,
        State.MARKET_SUBSCRIBED,
        State.MARKET_EVIDENCE_READY,
        State.ENTRY_INTENT_RECORDED,
        State.BROKER_SUBMITTED,
        State.BROKER_ACKNOWLEDGED,
        State.OPTION_FILL_PENDING,
        State.OPTION_FILLED,
        hedge_state,
        State.PROTECTION_PENDING,
        State.PROTECTION_ACTIVE,
        State.OPEN,
        State.EXITING,
        State.EXIT_FILLED,
        State.RECONCILED,
    ]
    rows = [_lifecycle(opportunity_id, state, i) for i, state in enumerate(states)]
    rows.append(("selections", {"opportunity_id": opportunity_id, "listed_status": "LISTED"}))
    rows.append(("hedge_selections", {
        "opportunity_id": opportunity_id,
        "hedge_required": not waived,
        "hedge_reason": "FROZEN_RULE_UNHEDGED" if waived else "SELECTED",
        "instrument_token": None if waived else 5001,
    }))
    return rows


def test_an_empty_day_reports_zeroes_not_an_error(tmp_path):
    report = audit_date(tmp_path, date(2026, 9, 17))
    assert report["audit_status"] == "PASS"
    assert report["opportunities"] == 0
    assert report["economic_authoritative"] == 0


def test_the_denominator_is_reported(tmp_path):
    rows = _complete_chain("A") + [
        _lifecycle("B", "TERMINAL_NOT_LISTED", 0),
        _lifecycle("C", "TERMINAL_SELECTION_UNKNOWN", 0),
        _lifecycle("D", "OPEN", 0),
        ("selections", {"opportunity_id": "B", "listed_status": "NOT_LISTED"}),
        ("selections", {"opportunity_id": "C", "listed_status": "UNKNOWN"}),
    ]
    _seed(tmp_path, rows)
    report = audit_date(tmp_path, date(2026, 9, 17))
    assert report["opportunities"] == 4
    assert report["selection"] == {"LISTED": 1, "NOT_LISTED": 1, "UNKNOWN": 1}
    assert report["blocked"] == 2
    assert report["economic_authoritative"] == 1
    assert report["inconclusive"] == 1


def test_a_reconciled_label_without_the_path_is_not_authoritative(tmp_path):
    _seed(tmp_path, [
        _lifecycle("A", "RECONCILED", 0),
        ("selections", {"opportunity_id": "A", "listed_status": "LISTED"}),
        ("hedge_selections", {"opportunity_id": "A", "hedge_required": True,
                              "hedge_reason": "SELECTED", "instrument_token": 5001}),
    ])
    report = audit_date(tmp_path, date(2026, 9, 17))
    assert report["economic_authoritative"] == 0
    assert report["inconclusive"] == 1


def test_a_reconciled_trade_without_a_listed_selection_is_not_authoritative(tmp_path):
    rows = _complete_chain("A")
    rows = [r for r in rows if r[0] != "selections"]
    rows.append(("selections", {"opportunity_id": "A", "listed_status": "UNKNOWN"}))
    _seed(tmp_path, rows)
    report = audit_date(tmp_path, date(2026, 9, 17))
    assert report["economic_authoritative"] == 0
    assert report["inconclusive"] == 1


def test_a_reconciled_trade_with_an_unknown_hedge_is_not_authoritative(tmp_path):
    rows = _complete_chain("A")
    rows = [r for r in rows if r[0] != "hedge_selections"]
    rows.append(("hedge_selections", {"opportunity_id": "A", "hedge_required": True,
                                      "hedge_reason": "HEDGE_SELECTION_UNKNOWN",
                                      "instrument_token": None}))
    _seed(tmp_path, rows)
    assert audit_date(tmp_path, date(2026, 9, 17))["economic_authoritative"] == 0


def test_only_a_declared_waiver_counts(tmp_path):
    _seed(tmp_path, _complete_chain("A", waived=True))
    assert audit_date(tmp_path, date(2026, 9, 17))["economic_authoritative"] == 1

    bad = _complete_chain("B", waived=True)
    bad = [
        (kind, ({**payload, "hedge_reason": "BECAUSE_I_SAID_SO"}
                if kind == "hedge_selections" else payload))
        for kind, payload in bad
    ]
    _seed(tmp_path, bad)
    report = audit_date(tmp_path, date(2026, 9, 17))
    assert report["economic_authoritative"] == 1  # A only; B is refused
    assert report["hedge"]["unknown"] >= 1


def test_corrupt_evidence_is_an_audit_failure_not_an_empty_partition(tmp_path):
    path = tmp_path / "evidence" / "date=2026-09-17" / "lifecycle"
    path.mkdir(parents=True)
    (path / "part-bad.json").write_text("{not-json", encoding="utf-8")

    report = audit_date(tmp_path, date(2026, 9, 17))

    assert report["audit_status"] == "FAIL"
    assert report["load_errors"]
    assert report["economic_authoritative"] == 0


def test_non_contiguous_lifecycle_sequence_is_flagged(tmp_path):
    _seed(tmp_path, [_lifecycle("A", "OPPORTUNITY_CREATED", 0),
                     _lifecycle("A", "RECONCILED", 2)])
    report = audit_date(tmp_path, date(2026, 9, 17))
    assert report["audit_status"] == "FAIL"
    assert any("non-contiguous" in error for error in report["load_errors"])


def test_the_auditor_never_reads_profit(tmp_path):
    import study.snapback_evidence_audit as mod
    source = open(mod.__file__, encoding="utf-8").read()
    code = "\n".join(line for line in source.splitlines()
                     if not line.strip().startswith("#"))
    for forbidden in ("pnl", "profit", "net_pnl", "expectancy", "winner"):
        assert forbidden not in code.lower().split('"""')[-1], f"auditor must not consider {forbidden}"


def test_the_verdict_is_never_computed_from_one_day(tmp_path):
    report = audit_date(tmp_path, date(2026, 9, 17))
    assert "INCONCLUSIVE" in report["economic_verdict"]


def test_the_text_report_renders(tmp_path):
    _seed(tmp_path, _complete_chain("A"))
    text = render_text(audit_date(tmp_path, date(2026, 9, 17)))
    assert "SNAPBACK EVIDENCE AUDIT" in text
    assert "Audit status" in text
    assert "Economic-authoritative trades" in text
