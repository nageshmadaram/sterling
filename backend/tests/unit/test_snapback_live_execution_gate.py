"""The family gate is enforced at the one broker boundary.

The gate existed with tests but had no production call site. It belongs inside
CanonicalExecutionService.submit_order() for strategy_id == "snapback", after the
request-bound RiskApproval is validated and before journal reservation or broker
submission. The server derives evidence, readiness, health and reconciliation itself:
a caller-supplied boolean is not trusted.
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.execution_service import (
    CanonicalExecutionService,
    ExecutionRequest,
    ExposureEffect,
    RiskApproval,
)


def _request(**over):
    base = dict(
        uid="default",
        account_id="ACC-1",
        symbol="LAURUSLABS26OCT2100PE",
        side="BUY",
        quantity=850,
        strategy_id="snapback",
        signal_id="SIG-1",
        generation_id="GEN-1",
        exchange="NFO",
        exposure_effect=ExposureEffect.INCREASE_EXPOSURE,
        payload={},
    )
    base.update(over)
    return ExecutionRequest(**base)


def _approval(request, **over):
    base = dict(
        approval_id="RA-1",
        approved=True,
        uid=request.uid,
        account_id=request.account_id,
        symbol=request.symbol,
        side=request.side,
        quantity=request.quantity,
        generation_id=request.generation_id,
        strategy_id=request.strategy_id,
        signal_id=request.signal_id,
        timestamp_ms=int(time.time() * 1000),
        available_capital=1_000_000.0,
        capital_required=100.0,
    )
    base.update(over)
    return RiskApproval(**base)


class SpyBroker:
    """Records every write so a paper/observe path can be proven silent."""

    def __init__(self):
        self.place_order = AsyncMock(return_value={"order_id": "X1"})
        self.modify_order = AsyncMock()
        self.cancel_order = AsyncMock()

    async def get_margins(self):
        return {"equity": {"available": {"live_balance": 1_000_000.0}}}

    @property
    def writes(self) -> int:
        return (
            self.place_order.await_count
            + self.modify_order.await_count
            + self.cancel_order.await_count
        )


@pytest.fixture
def service():
    return CanonicalExecutionService()


@pytest.mark.asyncio
async def test_inconclusive_snapback_cannot_reach_place_order(service, monkeypatch):
    broker = SpyBroker()
    request = _request()

    monkeypatch.setattr(
        "app.services.execution_service.snapback_live_gate_decision",
        lambda **kw: SimpleNamespace(allowed=False, protective=False,
                                     reasons=["evidence_not_passed", "not_live_eligible"]),
    )

    result = await service.submit_order(
        request=request, broker_client=broker, risk_approval=_approval(request)
    )

    assert result.success is False
    assert "evidence_not_passed" in (result.error or "")
    assert broker.writes == 0


@pytest.mark.asyncio
async def test_perfect_risk_approval_cannot_bypass_the_economic_gate(service, monkeypatch):
    broker = SpyBroker()
    request = _request()

    monkeypatch.setattr(
        "app.services.execution_service.snapback_live_gate_decision",
        lambda **kw: SimpleNamespace(allowed=False, protective=False,
                                     reasons=["evidence_not_passed"]),
    )

    # A flawless, request-bound, unexpired approval with real capital evidence.
    result = await service.submit_order(
        request=request, broker_client=broker, risk_approval=_approval(request)
    )

    assert result.success is False
    assert broker.writes == 0


@pytest.mark.asyncio
async def test_caller_supplied_override_is_ignored(service, monkeypatch):
    broker = SpyBroker()
    request = _request(payload={"live_eligible": True, "evidence_verdict": "PASSED",
                                "operator_override": True})

    seen = {}

    def gate(**kw):
        seen.update(kw)
        return SimpleNamespace(allowed=False, protective=False, reasons=["evidence_not_passed"])

    monkeypatch.setattr("app.services.execution_service.snapback_live_gate_decision", gate)

    result = await service.submit_order(
        request=request, broker_client=broker, risk_approval=_approval(request)
    )

    assert result.success is False
    # The gate is called by the server; the payload's claims are not passed as truth.
    assert seen.get("operator_override") is not True
    assert broker.writes == 0


@pytest.mark.asyncio
async def test_protective_exit_is_allowed_while_halted(service, monkeypatch):
    broker = SpyBroker()
    request = _request(side="SELL", exposure_effect=ExposureEffect.CLOSE_POSITION)

    called = {"n": 0}

    def gate(**kw):
        called["n"] += 1
        return SimpleNamespace(allowed=True, protective=True, reasons=[])

    monkeypatch.setattr("app.services.execution_service.snapback_live_gate_decision", gate)

    result = await service.submit_order(request=request, broker_client=broker)

    # A protective action is not blocked by the economic gate.
    assert result.status != "REJECTED_BY_FAMILY_GATE"


@pytest.mark.asyncio
async def test_non_snapback_strategies_are_unaffected(service, monkeypatch):
    broker = SpyBroker()
    request = _request(strategy_id="orb")

    def gate(**kw):
        raise AssertionError("the Snapback family gate must not run for other strategies")

    monkeypatch.setattr("app.services.execution_service.snapback_live_gate_decision", gate)

    result = await service.submit_order(
        request=request, broker_client=broker, risk_approval=_approval(request)
    )

    # It may still be rejected for other reasons, but never by the Snapback gate.
    assert result.status != "REJECTED_BY_FAMILY_GATE"


@pytest.mark.asyncio
async def test_gate_failure_is_fail_closed(service, monkeypatch):
    broker = SpyBroker()
    request = _request()

    def broken(**kw):
        raise RuntimeError("evidence store unavailable")

    monkeypatch.setattr("app.services.execution_service.snapback_live_gate_decision", broken)

    result = await service.submit_order(
        request=request, broker_client=broker, risk_approval=_approval(request)
    )

    assert result.success is False
    assert broker.writes == 0
