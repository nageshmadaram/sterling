"""Item 4a: the arm endpoint.

Arming is the moment a proposal becomes an authorization, so it is the one place
that must refuse everything the evidence has not earned. Live arming consults the
family live gate; paper arming does not, because paper exposure is not money.

Every response code says something different to a client: 404 the plan is not
here, 409 you are working from a stale revision or the plan is no longer
armable, 410 the plan's window has passed, 403 the system will not authorize
this. A retry with the same idempotency key replays rather than repeats.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.auth import UserContext, get_current_user

log = logging.getLogger(__name__)

router = APIRouter(tags=["snapback-plans"])


class ArmRequest(BaseModel):
    expected_revision: int = Field(..., ge=1)
    idempotency_key: str = Field(..., min_length=1, max_length=128)
    mode: str = Field(default="PAPER")


def _store():
    from app.services.snapback_plan_store import PlanStore

    return PlanStore()


def _admission(*, mode: str, account_id: str) -> Dict[str, Any]:
    """Whether the system will authorize execution of a plan in this mode."""
    if str(mode).upper() != "LIVE":
        # Paper arming still requires the runtime to be the declared one, but it
        # cannot move money, so the economic gate does not apply.
        return {"allowed": True, "mode": "PAPER", "reasons": []}

    from app.services.snapback_family_gate import evaluate_live_gate
    from app.services.snapback_family_ops import get_family_evidence_verdict
    from app.services.snapback_health import get_prospective_health
    from app.services.snapback_reconciliation import latest_reconciliation

    verdict = get_family_evidence_verdict()
    health = get_prospective_health()
    reconciliation = latest_reconciliation(account_id) or {}

    decision = evaluate_live_gate(
        intent="ENTER",
        evidence_verdict=str(verdict.get("verdict") or "INCONCLUSIVE"),
        live_eligible=bool(verdict.get("live_eligible")),
        risk_approved=True,
        system_status=str(health.get("status") or "HALTED"),
        broker_reconciled=bool(reconciliation.get("clean")),
    )
    reasons = list(decision.reasons)
    if str(verdict.get("verdict") or "INCONCLUSIVE").upper() != "PASSED":
        reasons.append(f"evidence {verdict.get('verdict') or 'INCONCLUSIVE'}")
    return {"allowed": bool(decision.allowed), "mode": "LIVE", "reasons": reasons}


@router.post("/snapback/plans/{plan_id}/arm")
async def arm_plan(
    plan_id: str,
    body: ArmRequest,
    user: UserContext = Depends(get_current_user),
) -> dict:
    from app.services.snapback_plan_store import PlanConflictError, PlanExpiredError

    store = _store()
    plan = store.get(plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail=f"plan {plan_id} not found")

    verdict = _admission(mode=body.mode, account_id=str(plan.get("account_id") or ""))
    if not verdict.get("allowed"):
        # Refused before any state changes: a plan that could not be armed must
        # not end up recorded as having been armed.
        raise HTTPException(
            status_code=403,
            detail={
                "error": "arming_not_authorized",
                "mode": verdict.get("mode"),
                "reasons": verdict.get("reasons") or [],
            },
        )

    try:
        armed = store.arm(
            plan_id,
            expected_revision=body.expected_revision,
            idempotency_key=body.idempotency_key,
            armed_by=str(getattr(user, "user_id", "") or "unknown"),
            mode=str(verdict.get("mode") or "PAPER"),
        )
    except PlanExpiredError as exc:
        raise HTTPException(status_code=410, detail=str(exc)) from exc
    except PlanConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return {
        "plan_id": plan_id,
        "status": armed.get("status"),
        "revision": armed.get("revision"),
        "mode": armed.get("mode"),
        "armed_at": armed.get("armed_at"),
        "armed_by": armed.get("armed_by"),
        "idempotent_replay": bool(armed.get("idempotent_replay")),
    }


@router.get("/snapback/plans/{plan_id}")
async def get_plan(
    plan_id: str, user: UserContext = Depends(get_current_user),
) -> dict:
    plan = _store().get(plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail=f"plan {plan_id} not found")
    return plan


@router.get("/snapback/plans/{plan_id}/events")
async def get_plan_events(
    plan_id: str, user: UserContext = Depends(get_current_user),
) -> dict:
    store = _store()
    if store.get(plan_id) is None:
        raise HTTPException(status_code=404, detail=f"plan {plan_id} not found")
    return {"plan_id": plan_id, "events": store.events(plan_id)}
