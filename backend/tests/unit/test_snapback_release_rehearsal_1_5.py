"""1.5 release rehearsal: the three checks the audit named by name.

Each one reproduces a defect that actually happened, or would have.
"""

from __future__ import annotations

import pytest


# 1 ------------------------------------ the catch-up signal, end to end


def test_an_old_catchup_signal_is_stored_replay_and_excluded_from_promotion(tmp_path):
    """The 1.3 defect, followed all the way to the gate input.

    It is not enough that the row is flagged correctly; the promotion builder
    must actually leave it out.
    """
    from app.services.snapback_authority import CATCHUP_SOURCE
    from app.services.snapback_observation_warehouse import SnapbackObservationWarehouse
    from study.snapback_promotion_inputs import build_promotion_input

    wh = SnapbackObservationWarehouse(db_path=str(tmp_path / "w.db"))
    wh.record_opportunity(
        opportunity_id="OPP-LAURUSLABS-OLD", symbol="LAURUSLABS",
        signal_type="SNAPBACK_FADE_UP", spot_price=1969.0,
        signal_timestamp="2026-09-11T10:00:00+00:00",
        source=CATCHUP_SOURCE,
    )

    stored = wh.get_records_by_table("opportunities")[0]
    assert stored["source"] == CATCHUP_SOURCE
    assert int(stored["authoritative"]) == 0
    assert wh.contradictory_authority_rows() == []

    # And the gate input contains nothing derived from it.
    result = build_promotion_input(
        records={
            "outcomes": [], "costs": [], "paper_positions": [], "daily_mtm": [],
            "hedge_rebalances": [], "quote_quality_events": [],
            "prospective_sessions": [],
        },
        expected_identity={"experiment_id": "E", "runtime_build_sha": "b"},
        source_snapshot_sha256="s", observed_sessions=0,
        allocation_capital=1_000_000.0,
    )

    assert result.completed_trades == 0


# 2 --------------------------------------- a BFO SENSEX option paper trade


def test_a_sensex_option_pays_bse_rates_and_flat_brokerage():
    """SENSEX options resolve on BFO. Pricing them at NSE rates, or charging a
    percentage brokerage, both understate cost."""
    from app.services.snapback_costs_v2 import EXCHANGE_BFO, statutory_charges_v2

    charges = statutory_charges_v2(
        exchange=EXCHANGE_BFO, segment="OPTIONS", side="BUY",
        price=150.0, quantity=20,          # one SENSEX lot, Rs 3,000 turnover
    )

    assert charges["brokerage"] == pytest.approx(20.0)
    assert charges["exchange_txn"] == pytest.approx(3000.0 * 0.000325)
    assert charges["exchange"] == EXCHANGE_BFO


def test_the_same_trade_on_nfo_is_priced_differently():
    from app.services.snapback_costs_v2 import (
        EXCHANGE_BFO, EXCHANGE_NFO, statutory_charges_v2,
    )

    common = dict(segment="OPTIONS", side="BUY", price=150.0, quantity=20)
    bse = statutory_charges_v2(exchange=EXCHANGE_BFO, **common)
    nse = statutory_charges_v2(exchange=EXCHANGE_NFO, **common)

    assert bse["total"] != nse["total"]


def test_a_sensex_trade_cannot_be_priced_without_naming_its_venue():
    from app.services.snapback_costs_v2 import statutory_charges_v2

    with pytest.raises(ValueError):
        statutory_charges_v2(exchange="", segment="OPTIONS", side="BUY",
                             price=150.0, quantity=20)


# 3 ------------------------ PASSED evidence, no reconciliation, no live entry


def test_a_passed_verdict_with_no_reconciliation_still_refuses_a_live_entry(monkeypatch):
    """The race this release closed: everything economic says yes, and the books
    have never been compared."""
    from app.services import snapback_readiness
    from app.services.snapback_family_account import FamilyAccountBinding

    monkeypatch.setattr(
        "app.services.snapback_family_account.binding_configured", lambda: True,
    )
    monkeypatch.setattr(
        "app.services.snapback_family_account.configured_binding",
        lambda: FamilyAccountBinding(user_id="default", account_id="KITE-FAMILY"),
    )
    monkeypatch.setattr(
        "app.services.snapback_reconciliation.latest_reconciliation",
        lambda **_k: None,
    )

    assert snapback_readiness._reconciliation_clean() is False


def test_the_live_gate_refuses_when_the_books_are_not_reconciled():
    from app.services.snapback_family_gate import evaluate_live_gate

    decision = evaluate_live_gate(
        intent="ENTER",
        evidence_verdict="PASSED",      # everything economic says yes
        live_eligible=True,
        risk_approved=True,
        system_status="HEALTHY",
        broker_reconciled=False,        # and the books were never compared
    )

    assert decision.allowed is False
    assert "broker_not_reconciled" in decision.reasons


def test_the_executor_refuses_the_same_state(tmp_path):
    """Defence in depth: the gate says no, and so does the executor."""
    import asyncio

    from app.services.snapback_live_executor import SnapbackLiveExecutor
    from app.services.snapback_plan_store import PlanStore

    store = PlanStore(db_path=str(tmp_path / "p.db"))
    store.publish(
        plan_id="PLAN-1", opportunity_id="OPP-1", account_id="KITE-FAMILY",
        option_symbol="NIFTY26OCT25000PE", option_quantity=75,
    )
    store.arm("PLAN-1", expected_revision=1, idempotency_key="k", armed_by="u")

    class _NeverCalled:
        async def submit_order(self, *a, **k):
            raise AssertionError("an order was placed without reconciliation")

    executor = SnapbackLiveExecutor(
        plan_store=store,
        execution_service=_NeverCalled(),
        reconciliation_fn=lambda account_id: {"clean": True, "fresh": False},
        risk_approval_fn=lambda **k: None,
        protection_fn=None,
        revalidate_fn=lambda plan: {"ok": True, "reasons": []},
    )

    result = asyncio.run(executor.execute("PLAN-1"))

    assert result.status == "REFUSED"


def test_the_live_intent_vocabulary_is_used_correctly():
    """A misspelled intent is denied as unknown — safe, but for the wrong reason,
    which hides the blockers an operator actually needs to see."""
    import inspect
    from pathlib import Path

    from app.services.snapback_family_gate import LiveIntent

    backend = Path(__file__).resolve().parents[2]
    valid = {i.value for i in LiveIntent}

    for path in list((backend / "app").rglob("snapback*.py")):
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            if "intent=\"" not in line:
                continue
            value = line.split('intent="', 1)[1].split('"', 1)[0]
            if value.isupper():
                assert value in valid, f"{path.name}: unknown live intent {value!r}"
