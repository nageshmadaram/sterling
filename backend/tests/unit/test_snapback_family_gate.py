"""Family live gate: no Snapback exposure-increasing live order without PASSED
authoritative evidence, LIVE_ELIGIBLE state, RiskEngine approval, HEALTHY system and
a reconciled broker. Protective actions stay permitted while halted.
"""

from __future__ import annotations

import pytest

from app.services.snapback_family_gate import (
    LiveIntent,
    evaluate_live_gate,
    is_exposure_increasing,
)


def _ok(**over):
    base = dict(
        intent=LiveIntent.ENTER,
        evidence_verdict="PASSED",
        live_eligible=True,
        risk_approved=True,
        system_status="HEALTHY",
        broker_reconciled=True,
        operator_override=False,
    )
    base.update(over)
    return base


def test_all_conditions_met_allows_entry():
    decision = evaluate_live_gate(**_ok())

    assert decision.allowed is True
    assert decision.reasons == []


def test_today_state_blocks_live_entry():
    # Actual state tonight: evidence INCONCLUSIVE, not live eligible.
    decision = evaluate_live_gate(
        **_ok(evidence_verdict="INCONCLUSIVE", live_eligible=False)
    )

    assert decision.allowed is False
    assert "evidence_not_passed" in decision.reasons
    assert "not_live_eligible" in decision.reasons


def test_failed_evidence_blocks():
    decision = evaluate_live_gate(**_ok(evidence_verdict="FAILED"))

    assert decision.allowed is False
    assert "evidence_not_passed" in decision.reasons


def test_risk_engine_denial_blocks():
    decision = evaluate_live_gate(**_ok(risk_approved=False))

    assert decision.allowed is False
    assert "risk_engine_denied" in decision.reasons


def test_degraded_system_blocks():
    decision = evaluate_live_gate(**_ok(system_status="DEGRADED"))

    assert decision.allowed is False
    assert "system_not_healthy" in decision.reasons


def test_unreconciled_broker_blocks():
    decision = evaluate_live_gate(**_ok(broker_reconciled=False))

    assert decision.allowed is False
    assert "broker_not_reconciled" in decision.reasons


def test_manual_override_cannot_bypass_the_gate():
    decision = evaluate_live_gate(
        **_ok(
            evidence_verdict="INCONCLUSIVE",
            live_eligible=False,
            operator_override=True,
        )
    )

    assert decision.allowed is False
    assert "evidence_not_passed" in decision.reasons


@pytest.mark.parametrize("intent", [LiveIntent.EXIT, LiveIntent.FLATTEN, LiveIntent.RECONCILE])
def test_protective_actions_remain_permitted_while_halted(intent):
    decision = evaluate_live_gate(
        intent=intent,
        evidence_verdict="INCONCLUSIVE",
        live_eligible=False,
        risk_approved=False,
        system_status="HALTED",
        broker_reconciled=False,
        operator_override=False,
    )

    assert decision.allowed is True
    assert decision.protective is True


def test_exposure_classification():
    assert is_exposure_increasing(LiveIntent.ENTER) is True
    assert is_exposure_increasing(LiveIntent.ADD) is True
    assert is_exposure_increasing(LiveIntent.EXIT) is False
    assert is_exposure_increasing(LiveIntent.FLATTEN) is False
    assert is_exposure_increasing(LiveIntent.RECONCILE) is False


def test_unknown_intent_fails_closed():
    decision = evaluate_live_gate(**_ok(intent="something_new"))

    assert decision.allowed is False
    assert "unknown_intent" in decision.reasons


def test_stop_switch_blocks_entries_but_not_exits(tmp_path, monkeypatch):
    """The runner must skip the entry phase while halted, and still run risk/EOD."""
    import app.services.snapback_family_ops as fam

    monkeypatch.setenv("STERLING_NEW_TRADES_HALT_PATH", str(tmp_path / "halt.json"))

    assert fam.new_trades_halted() is False
    fam.set_new_trades_halted(True, reason="drill")
    assert fam.new_trades_halted() is True

    import inspect

    from app.services import snapback_runner

    source = inspect.getsource(snapback_runner.tick)

    # Entries are gated; the risk monitor and EOD phases are not.
    assert "entries_halted" in source
    assert "process_prospective_intraday_risk" in source
    assert "process_prospective_daily_mtm_and_exits" in source

    fam.set_new_trades_halted(False, reason="drill end")
    assert fam.new_trades_halted() is False


def test_unreadable_halt_state_fails_closed(tmp_path, monkeypatch):
    import app.services.snapback_family_ops as fam

    bad = tmp_path / "halt.json"
    bad.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("STERLING_NEW_TRADES_HALT_PATH", str(bad))

    assert fam.new_trades_halted() is True
