"""Read-only surface over account continuity, egress and release certification.

Everything here reports. None of it activates a binding, attests a gate or
enables capital: those are deliberate operator actions taken at the command
line, where they are recorded with a name attached and cannot be triggered by a
stray request from a browser tab that was left open.

The one write in this file is the continuity checklist answer, and it is
write-only-forward metadata — a question getting an answer — which the store
itself screens for anything that looks like a credential.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException

from app.core.auth import UserContext, get_current_user
from app.core.broker_account_binding import SecretInBindingError

router = APIRouter(tags=["continuity"])


@router.get("/continuity/status")
async def continuity_status(_user: UserContext = Depends(get_current_user)) -> dict:
    """Static egress, the active account binding, and the handoff checklist."""
    from app.services.continuity_doctor import continuity_report

    return continuity_report().as_dict()


@router.get("/continuity/bindings")
async def list_bindings(_user: UserContext = Depends(get_current_user)) -> dict:
    """Every declared broker account binding, active and retired.

    Retired bindings are listed on purpose: "which account did that fill come
    from?" has to stay answerable after a migration.
    """
    from app.core.broker_account_binding import BindingError
    from app.services.account_binding_service import AccountBindingStore, binding_health

    try:
        bindings = [b.as_row() for b in AccountBindingStore().all()]
    except BindingError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"bindings": bindings, "health": binding_health()}


@router.get("/continuity/migration")
async def migration_procedure(_user: UserContext = Depends(get_current_user)) -> dict:
    """The account migration steps, and which of them a human must perform."""
    from app.services.account_binding_service import MIGRATION_PROCEDURE

    return {
        "steps": [
            {
                "order": step.order,
                "name": step.name,
                "description": step.description,
                "automated": step.automated,
            }
            for step in MIGRATION_PROCEDURE
        ],
        "invariant": (
            "The strategy identity survives a migration; the execution-evidence "
            "segment does not. Old fills stay with the old account."
        ),
    }


@router.post("/continuity/checklist/{key}")
async def answer_checklist(
    key: str,
    payload: dict = Body(...),
    _user: UserContext = Depends(get_current_user),
) -> dict:
    """Record one continuity answer. Refuses anything resembling a secret."""
    from app.services.continuity_doctor import record_answer

    answer = str(payload.get("answer") or "")
    owner = str(payload.get("owner") or "operator")
    try:
        item = record_answer(key, answer, owner=owner)
    except SecretInBindingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return item.as_dict()


@router.get("/continuity/certification")
async def certification(_user: UserContext = Depends(get_current_user)) -> dict:
    """The §5 release gate table for the running build."""
    from app.services.release_certification import certification_report

    return certification_report().as_dict()


@router.get("/continuity/shadow")
async def shadow_status(
    session_date: str | None = None,
    _user: UserContext = Depends(get_current_user),
) -> dict:
    """Per-lane shadow execution metrics; the whole store when no date is given."""
    from app.services.shadow_execution import ShadowStore

    store = ShadowStore()
    dates = [session_date] if session_date else store.sessions()
    rows: list[dict] = []
    for date in dates:
        rows.extend(store.read(date))
    return {"sessions": dates, "record_count": len(rows), "records": rows}


@router.get("/continuity/capital-permission")
async def capital_permission(_user: UserContext = Depends(get_current_user)) -> dict:
    """Why each lane may not send a real entry. Expected to be "everything"."""
    from app.services.capital_permission_report import all_lane_permissions

    return {"lanes": all_lane_permissions()}
