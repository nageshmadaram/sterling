"""1.5 P0-LIVE: ARMED plans become orders through one ordered, resumable path.

Before this, arming transitioned a durable plan to ARMED and nothing consumed it.
submit_plan() existed with no production caller, submitted only the option leg,
and passed no RiskApproval — so the canonical service would have refused it
anyway. "Live execution complete" was too strong a claim.

The ordering here is the whole safety argument:

    revalidate → reconcile → risk approve → option fill
    → hedge sized to the CONFIRMED fill → protection → OPEN

Every step is gated on the previous one having actually happened, not on its
acknowledgement. Nothing below places an order itself; the executor asks the
canonical execution service, which is the only thing that talks to a broker.
"""

from __future__ import annotations

import asyncio
from typing import Any, Dict, List

import pytest


# ------------------------------------------------------------ the broker spy


class BrokerSpy:
    """Records every order the executor asks for, in order."""

    def __init__(self, *, fills=None, reject=(), unknown=()):
        self.submitted: List[Dict[str, Any]] = []
        self.fills = fills or {}
        self.reject = set(reject)
        self.unknown = set(unknown)
        self.protection: List[Dict[str, Any]] = []

    async def submit_order(self, request, broker_client=None, risk_approval=None,
                           risk_approved=False):
        from app.services.execution_service import ExecutionResult

        self.submitted.append({
            "symbol": request.symbol,
            "side": request.side,
            "quantity": request.quantity,
            "exchange": request.exchange,
            "risk_approval": risk_approval,
            "exposure_effect": getattr(request, "exposure_effect", None),
        })

        if request.symbol in self.reject:
            return ExecutionResult(success=False, status="REJECTED",
                                   error="broker rejected")
        if request.symbol in self.unknown:
            return ExecutionResult(success=False, status="UNKNOWN")

        filled = self.fills.get(request.symbol, request.quantity)
        return ExecutionResult(
            success=True, status="FILLED", order_id=f"ORD-{len(self.submitted)}",
            filled_quantity=filled, filled_price=100.0,
        )

    async def place_protection(self, **kwargs):
        self.protection.append(kwargs)
        return {"state": "ACTIVE", "protection_id": "GTT-1"}


@pytest.fixture
def plan_store(tmp_path):
    from app.services.snapback_plan_store import PlanStore

    store = PlanStore(db_path=str(tmp_path / "plans.db"))
    store.publish(
        plan_id="PLAN-1", opportunity_id="OPP-1", account_id="KITE-FAMILY",
        option_symbol="NIFTY26OCT25000PE", option_quantity=150,
        futures_symbol="NIFTY26OCTFUT", target_futures_quantity=75,
        max_option_price=120.0, hedge_price_limit=25100.0,
        risk_amount=9000.0, cash_required=9000.0, margin_required=140000.0,
        policy_snapshot_hash="pol-1", runtime_build_sha="build-1",
        option_exchange="NFO", futures_exchange="NFO",
    )
    store.arm("PLAN-1", expected_revision=1, idempotency_key="k1", armed_by="u1")
    return store


def _executor(plan_store, broker, **over):
    from app.services.snapback_live_executor import SnapbackLiveExecutor

    defaults = dict(
        plan_store=plan_store,
        execution_service=broker,
        reconciliation_fn=lambda account_id: {"clean": True, "fresh": True},
        risk_approval_fn=lambda **k: _approval(**k),
        protection_fn=broker.place_protection,
        revalidate_fn=lambda plan: {"ok": True, "reasons": []},
    )
    defaults.update(over)
    return SnapbackLiveExecutor(**defaults)


def _approval(**kwargs):
    from app.services.execution_service import RiskApproval

    return RiskApproval(
        approval_id="RISK-1", uid=kwargs.get("uid", "default"),
        account_id=kwargs.get("account_id", "KITE-FAMILY"),
        symbol=kwargs.get("symbol", ""), side=kwargs.get("side", "BUY"),
        quantity=int(kwargs.get("quantity", 0)),
        generation_id=kwargs.get("generation_id", "gen-1"),
        approved=True,
    )


# --------------------------------------------------------------- happy path


def test_the_full_sequence_runs_in_order(plan_store):
    broker = BrokerSpy()
    result = asyncio.run(_executor(plan_store, broker).execute("PLAN-1"))

    assert result.status == "OPEN"
    symbols = [o["symbol"] for o in broker.submitted]
    assert symbols == ["NIFTY26OCT25000PE", "NIFTY26OCTFUT"]
    assert broker.protection, "protection was never established"


def test_every_order_carries_a_risk_approval(plan_store):
    """The canonical service refuses an exposure increase without one, so an
    executor that omits it fails closed — but silently, at the broker boundary."""
    broker = BrokerSpy()
    asyncio.run(_executor(plan_store, broker).execute("PLAN-1"))

    for order in broker.submitted:
        assert order["risk_approval"] is not None, order["symbol"]


def test_the_plan_is_consumed_exactly_once(plan_store):
    broker = BrokerSpy()
    executor = _executor(plan_store, broker)

    asyncio.run(executor.execute("PLAN-1"))
    second = asyncio.run(executor.execute("PLAN-1"))

    assert second.status in ("ALREADY_CONSUMED", "REFUSED")
    assert len([o for o in broker.submitted if o["side"] == "BUY"]) == 2


def test_only_an_armed_plan_executes(plan_store):
    broker = BrokerSpy()
    plan_store.cancel("PLAN-1", reason="superseded")

    result = asyncio.run(_executor(plan_store, broker).execute("PLAN-1"))

    assert result.status == "REFUSED"
    assert broker.submitted == []


# -------------------------------------------------------- gates before order


def test_a_dirty_book_stops_before_any_order(plan_store):
    broker = BrokerSpy()
    executor = _executor(
        plan_store, broker,
        reconciliation_fn=lambda account_id: {"clean": False, "fresh": True},
    )

    result = asyncio.run(executor.execute("PLAN-1"))

    assert result.status == "REFUSED"
    assert broker.submitted == []


def test_a_stale_reconciliation_stops_before_any_order(plan_store):
    broker = BrokerSpy()
    executor = _executor(
        plan_store, broker,
        reconciliation_fn=lambda account_id: {"clean": True, "fresh": False},
    )

    result = asyncio.run(executor.execute("PLAN-1"))

    assert result.status == "REFUSED"
    assert broker.submitted == []


def test_a_failed_revalidation_stops_before_any_order(plan_store):
    """A plan armed minutes ago may no longer be the right trade."""
    broker = BrokerSpy()
    executor = _executor(
        plan_store, broker,
        revalidate_fn=lambda plan: {"ok": False, "reasons": ["quote stale"]},
    )

    result = asyncio.run(executor.execute("PLAN-1"))

    assert result.status == "REFUSED"
    assert broker.submitted == []


def test_a_denied_risk_approval_stops_before_any_order(plan_store):
    broker = BrokerSpy()

    def denied(**kwargs):
        from dataclasses import replace

        return replace(_approval(**kwargs), approved=False, reason="margin")

    result = asyncio.run(_executor(plan_store, broker, risk_approval_fn=denied)
                         .execute("PLAN-1"))

    assert result.status == "REFUSED"
    assert broker.submitted == []


# ------------------------------------------------------------- partial fills


def test_the_hedge_is_sized_to_the_confirmed_option_fill(plan_store):
    """Half the option filled means half the hedge. Sizing to the intended
    quantity would leave a naked futures leg."""
    broker = BrokerSpy(fills={"NIFTY26OCT25000PE": 75})   # half of 150

    asyncio.run(_executor(plan_store, broker).execute("PLAN-1"))

    hedge = next(o for o in broker.submitted if o["symbol"] == "NIFTY26OCTFUT")
    assert hedge["quantity"] == 38   # ceil(75/150 * 75)


def test_a_zero_option_fill_places_no_hedge(plan_store):
    broker = BrokerSpy(fills={"NIFTY26OCT25000PE": 0})

    result = asyncio.run(_executor(plan_store, broker).execute("PLAN-1"))

    assert [o["symbol"] for o in broker.submitted] == ["NIFTY26OCT25000PE"]
    assert result.status != "OPEN"


def test_a_rejected_option_entry_places_no_hedge(plan_store):
    broker = BrokerSpy(reject={"NIFTY26OCT25000PE"})

    result = asyncio.run(_executor(plan_store, broker).execute("PLAN-1"))

    assert len(broker.submitted) == 1
    assert result.status == "REJECTED"


def test_an_unknown_option_submission_does_not_hedge_or_resubmit(plan_store):
    """UNKNOWN means the order may be working. Hedging against a position that
    may not exist, or resubmitting, are both worse than stopping."""
    broker = BrokerSpy(unknown={"NIFTY26OCT25000PE"})

    result = asyncio.run(_executor(plan_store, broker).execute("PLAN-1"))

    assert len(broker.submitted) == 1
    assert result.status == "RECONCILING"


# --------------------------------------------------------------- protection


def test_protection_covers_the_confirmed_fill(plan_store):
    broker = BrokerSpy(fills={"NIFTY26OCT25000PE": 75})

    asyncio.run(_executor(plan_store, broker).execute("PLAN-1"))

    assert broker.protection[0]["quantity"] == 75


def test_a_position_is_not_open_until_protection_is_acknowledged(plan_store):
    broker = BrokerSpy()

    async def never_acknowledges(**kwargs):
        return {"state": "SUBMITTING"}

    result = asyncio.run(
        _executor(plan_store, broker, protection_fn=never_acknowledges)
        .execute("PLAN-1")
    )

    assert result.status != "OPEN"
    assert result.protection_state == "SUBMITTING"


def test_a_failed_protection_triggers_the_deadline_policy(plan_store):
    broker = BrokerSpy()

    async def refuses(**kwargs):
        return {"state": "REQUIRED"}

    result = asyncio.run(
        _executor(plan_store, broker, protection_fn=refuses).execute("PLAN-1")
    )

    assert result.status == "PROTECTION_FAILED"
    assert result.emergency_exit_quantity == 150


def test_an_unknown_protection_response_reconciles_rather_than_selling(plan_store):
    broker = BrokerSpy()

    async def unknown(**kwargs):
        return {"state": "RECONCILING"}

    result = asyncio.run(
        _executor(plan_store, broker, protection_fn=unknown).execute("PLAN-1")
    )

    assert result.emergency_exit_quantity == 0
    assert result.reconcile_first is True


# ------------------------------------------------------------ wiring proof


def test_the_arm_endpoint_does_not_execute_inline():
    """Arming authorises; it must not place orders on the request thread."""
    import inspect

    from app.api.v1.endpoints import snapback_plans

    source = inspect.getsource(snapback_plans.arm_plan)

    assert "SnapbackLiveExecutor" not in source
    assert "submit_order" not in source


def test_the_executor_is_the_only_snapback_caller_of_submit_order():
    from pathlib import Path

    backend = Path(__file__).resolve().parents[2]
    offenders = []
    for path in (backend / "app" / "services").glob("snapback*.py"):
        text = path.read_text(encoding="utf-8")
        if "submit_order(" in text and path.name != "snapback_live_executor.py":
            offenders.append(path.name)

    assert offenders == [], f"Snapback places orders outside the executor: {offenders}"
