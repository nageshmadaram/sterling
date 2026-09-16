"""Snapback live execution adapter.

A thin layer over the canonical authorities. It deliberately owns no risk store of its
own: the previous in-memory reservation dictionary described itself as durable
while living in one process, so a restart forgot every reservation it had made.

Reservations, approvals, journalling and broker submission all belong to
CanonicalExecutionService and the canonical RiskEngine. The plan below is server-owned:
the browser receives a plan_id and a revision, never a quantity, price, contract or
account it can influence.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)


class PlanRevisionError(RuntimeError):
    """The plan changed since the operator last saw it."""


class PlanNotFoundError(RuntimeError):
    """No such server-owned plan."""


@dataclass(frozen=True)
class SnapbackExecutionPlan:
    plan_id: str
    opportunity_id: str
    account_id: str

    option_exchange: str
    option_symbol: str
    option_quantity: int

    futures_exchange: str
    futures_symbol: str
    target_futures_quantity: int

    max_option_price: float
    hedge_price_limit: float

    risk_amount: float
    cash_required: float
    margin_required: float

    policy_snapshot_hash: str
    config_hash: str

    revision: int = 1

    def as_client_view(self) -> Dict[str, Any]:
        """What a browser may see: identity and revision, never authority."""
        return {
            "plan_id": self.plan_id,
            "opportunity_id": self.opportunity_id,
            "revision": self.revision,
        }


def assert_plan_revision(*, current_revision: int, expected_revision: int) -> None:
    """Refuse to act on a plan the operator has not actually seen."""
    if int(current_revision) != int(expected_revision):
        raise PlanRevisionError(
            f"plan revision {expected_revision} is stale; current revision is "
            f"{current_revision}"
        )


async def submit_plan(
    *,
    plan: SnapbackExecutionPlan,
    expected_revision: int,
    idempotency_key: str,
    broker_client=None,
    uid: str = "default",
) -> Any:
    """Route one armed plan through the canonical execution authority.

    Every admission check — evidence, readiness, health, reconciliation, risk — is
    performed by the canonical service and the family gate it calls. This function
    adds no second opinion.
    """
    from app.services.execution_service import (
        CanonicalExecutionService, ExecutionRequest, ExposureEffect,
    )

    assert_plan_revision(
        current_revision=plan.revision, expected_revision=expected_revision,
    )

    request = ExecutionRequest(
        uid=uid,
        account_id=plan.account_id,
        strategy_id="snapback",
        generation_id=plan.config_hash,
        signal_id=plan.opportunity_id,
        exchange=plan.option_exchange,
        symbol=plan.option_symbol,
        side="BUY",
        quantity=plan.option_quantity,
        exposure_effect=ExposureEffect.INCREASE_EXPOSURE,
        order_type="LIMIT",
        price=plan.max_option_price,
        capital_required=plan.cash_required,
        payload={
            "product": "NRML",
            "idempotency_key": idempotency_key,
            "plan_id": plan.plan_id,
        },
    )

    return await CanonicalExecutionService().submit_order(
        request=request, broker_client=broker_client,
    )
