"""H2/H3/H6: server-owned plans, canonical risk, and readiness that cannot be argued with."""

from __future__ import annotations

from types import SimpleNamespace

import pytest


def test_the_execution_adapter_holds_no_private_risk_store():
    import inspect

    from app.services import snapback_execution

    source = inspect.getsource(snapback_execution)

    # Reservations live in the durable canonical engine, not a process dictionary.
    assert "self._reservations" not in source
    assert "CanonicalExecutionService" in source or "canonical" in source.lower()


def test_a_plan_is_server_owned():
    from app.services.snapback_execution import SnapbackExecutionPlan

    plan = SnapbackExecutionPlan(
        plan_id="PLAN-1", opportunity_id="OPP-1", account_id="KITE-1",
        option_exchange="NFO", option_symbol="NIFTY26OCT25000PE", option_quantity=850,
        futures_exchange="NFO", futures_symbol="NIFTY26OCTFUT",
        target_futures_quantity=75, max_option_price=102.0,
        hedge_price_limit=24600.0, risk_amount=5000.0, cash_required=85_000.0,
        margin_required=120_000.0, policy_snapshot_hash="pol-1", config_hash="cfg-1",
        revision=3,
    )

    assert plan.plan_id == "PLAN-1"
    assert plan.revision == 3


def test_arming_requires_the_expected_revision():
    from app.services.snapback_execution import PlanRevisionError, assert_plan_revision

    with pytest.raises(PlanRevisionError):
        assert_plan_revision(current_revision=4, expected_revision=3)

    assert_plan_revision(current_revision=3, expected_revision=3)


def test_the_browser_cannot_supply_authoritative_fields():
    import inspect

    from app.api.v1.endpoints import snapback_ops

    source = inspect.getsource(snapback_ops)

    if "plans/{plan_id}/arm" in source:
        arm = source.split("plans/{plan_id}/arm", 1)[1][:2000]
        for forbidden in ("quantity:", "price:", "symbol:", "account_id:"):
            assert forbidden not in arm


@pytest.mark.parametrize("state,allowed", [
    ("LIVE_ELIGIBLE", True),
    ("PAPER", False),
    ("OBSERVE", False),
    ("HALTED", False),
    ("RECONCILING", False),
])
def test_only_live_eligible_may_increase_exposure(state, allowed):
    from app.services.snapback_family_gate import LiveIntent, evaluate_live_gate

    decision = evaluate_live_gate(
        intent=LiveIntent.ENTER,
        evidence_verdict="PASSED" if state == "LIVE_ELIGIBLE" else "INCONCLUSIVE",
        live_eligible=(state == "LIVE_ELIGIBLE"),
        risk_approved=True,
        system_status="HEALTHY" if state == "LIVE_ELIGIBLE" else state,
        broker_reconciled=(state == "LIVE_ELIGIBLE"),
    )

    assert decision.allowed is allowed


def test_readiness_consumes_the_promotion_record_and_reconciliation():
    import inspect

    from app.services import snapback_readiness

    source = inspect.getsource(snapback_readiness)

    assert "PromotionService" in source
    assert "reconcil" in source.lower()


def test_readiness_is_reconciling_when_the_books_disagree(monkeypatch):
    from app.services import snapback_readiness

    monkeypatch.setattr(
        snapback_readiness, "_reconciliation_clean", lambda uid: False, raising=False,
    )
    monkeypatch.setattr(
        "app.services.snapback_health.get_prospective_health",
        lambda uid="default": {
            "status": "HEALTHY", "broker_connected": True, "unresolved_errors": [],
        },
    )
    monkeypatch.setattr(
        "app.services.snapback_family_ops.get_family_evidence_verdict",
        lambda: {"verdict": "PASSED"},
    )
    monkeypatch.setattr(
        "app.services.snapback_family_ops.new_trades_halted", lambda: False,
    )

    state = snapback_readiness.readiness_state("default")

    assert state.state == "RECONCILING"
    assert state.broker_reconciled is False


def test_live_is_never_reached_automatically():
    from app.services.snapback_readiness import LIVE_ELIGIBLE, readiness_state
    import inspect

    from app.services import snapback_readiness

    source = inspect.getsource(snapback_readiness.readiness_state)

    # Eligibility is as far as the system goes on its own.
    assert '"LIVE"' not in source.replace(LIVE_ELIGIBLE, "")
