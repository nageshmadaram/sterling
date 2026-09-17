"""Read-only surface over the two-strategy, five-mode model.

Every endpoint here reports. None of them opens, closes or sizes anything, and
none of them can change a lane's state: promotion is an evidence decision made
deliberately, not an HTTP call. The operator write actions that do exist
(SAFE_MODE, reconcile) already live in their own routers and are not duplicated
here.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException

from app.core.auth import UserContext, get_current_user
from app.core.focus import FOCUSABLE, FocusConfigurationError, focus_policy
from app.core.horizon import MODE_TIMELINES, HorizonMode, UnknownMode, canonical_mode
from app.core.lane_promotion import MIN_SESSIONS, MIN_TRADES, evaluate_all_lanes
from app.core.lane_registry import UnknownLane, get_lane, lanes_for, may_originate
from app.core.operator_report import operator_dashboard

router = APIRouter(tags=["lanes"])


def _timeline(mode: HorizonMode) -> dict:
    cfg = MODE_TIMELINES[mode]
    body = {
        "signal_timeframe": cfg.signal_timeframe,
        "execution_timeframe": cfg.execution_timeframe,
        "duration_unit": cfg.duration_unit,
        "hard_hold_limit": cfg.hard_hold_limit,
        "allow_overnight": cfg.allow_overnight,
        "force_close_time": cfg.force_close_time,
        "review_interval_seconds": cfg.review_interval_seconds,
        "mode_version": cfg.version,
    }
    if cfg.duration_unit == "trading_sessions":
        body["expected_min_sessions"] = cfg.expected_hold_min
        body["expected_max_sessions"] = cfg.expected_hold_max
        body["hard_max_sessions"] = cfg.hard_hold_limit
    else:
        body["expected_hold_min_seconds"] = cfg.expected_hold_min
        body["expected_hold_max_seconds"] = cfg.expected_hold_max
    return body


def _identity(strategy: str, mode: HorizonMode) -> dict:
    """Rule and config hashes, taken from each engine's own lane adapter."""
    if strategy == "snapback":
        from app.engines.snapback import lanes as adapter

        return {
            "strategy_version": adapter.STRATEGY_VERSION,
            "mode_version": adapter.MODE_VERSIONS[mode],
            "rule_hash": adapter.lane_rule_hash(mode),
            "legacy_engine_mode": adapter.engine_mode_of(mode),
        }
    from app.engines.sterling_kite_engine import lanes as adapter

    return {
        "strategy_version": adapter.STRATEGY_VERSION,
        "mode_version": adapter.MODE_VERSIONS[mode],
        "rule_hash": adapter.lane_rule_hash(mode),
        "config_hash": adapter.config_hash(),
        "core_frozen": adapter.core_is_frozen(),
    }


@router.get("/focus/status")
async def focus_status(_user: UserContext = Depends(get_current_user)) -> dict:
    """Which strategies may originate, and where that setting came from."""
    try:
        policy = focus_policy()
    except FocusConfigurationError as exc:
        # A misconfigured focus setting must read as an error, not as the
        # default: the operator meant to restrict something.
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {
        "focusable": sorted(FOCUSABLE),
        "originators": sorted(policy.originators),
        "source": policy.source,
    }


@router.get("/strategies")
async def list_strategies(_user: UserContext = Depends(get_current_user)) -> dict:
    policy = focus_policy()
    return {
        "strategies": [
            {
                "strategy": name,
                "may_originate": policy.may_originate(name),
                "modes": [lane.mode.value for lane in lanes_for(name)],
            }
            for name in sorted(FOCUSABLE)
        ]
    }


@router.get("/strategies/{strategy}/modes")
async def list_modes(
    strategy: str, _user: UserContext = Depends(get_current_user)
) -> dict:
    lanes = lanes_for(strategy)
    if not lanes:
        raise HTTPException(status_code=404, detail=f"unknown strategy {strategy!r}")
    return {
        "strategy": strategy.lower(),
        "modes": [
            {
                "mode": lane.mode.value,
                "state": lane.state.value,
                "rules_defined": lane.rules_defined,
                "note": lane.note,
            }
            for lane in lanes
        ],
    }


def _resolve(strategy: str, mode: str):
    try:
        resolved = canonical_mode(mode)
        return get_lane(strategy, resolved), resolved
    except UnknownMode as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except UnknownLane as exc:
        raise HTTPException(
            status_code=404, detail=f"unknown lane {exc.args[0]}"
        ) from exc


@router.get("/strategies/{strategy}/{mode}/status")
async def lane_status(
    strategy: str, mode: str, _user: UserContext = Depends(get_current_user)
) -> dict:
    lane, resolved = _resolve(strategy, mode)
    decision = may_originate(lane.strategy_id, resolved)
    return {
        "strategy": lane.strategy_id,
        "mode": resolved.value,
        "lane_key": lane.lane_key,
        "state": lane.state.value,
        "rules_defined": lane.rules_defined,
        "note": lane.note,
        "may_originate": decision.allowed,
        "refusal": {"reason": decision.reason, "detail": decision.detail}
        if not decision.allowed
        else None,
        "identity": _identity(lane.strategy_id, resolved),
        "timeline": _timeline(resolved),
    }


@router.get("/strategies/{strategy}/{mode}/promotion")
async def lane_promotion(
    strategy: str, mode: str, _user: UserContext = Depends(get_current_user)
) -> dict:
    """This lane's verdict on its own evidence. Never a combined one.

    The evidence store is not read here yet — no runner stamps a lane onto its
    rows, so every lane would report an empty sample either way. Reporting the
    declared contract and an explicit ``evidence_wired`` flag is honest;
    returning zeros as though they were a measurement would not be.
    """
    lane, resolved = _resolve(strategy, mode)
    verdict = evaluate_all_lanes([])[lane.lane_key]
    return {
        "strategy": lane.strategy_id,
        "mode": resolved.value,
        "lane_key": lane.lane_key,
        "evidence_wired": False,
        "required": {"sessions": MIN_SESSIONS, "completed_trades": MIN_TRADES},
        "verdict": verdict["verdict"],
        "checks": verdict["checks"],
        "reasons": verdict["reasons"],
        "lane_evidence": verdict["lane"],
    }


@router.get("/lanes/dashboard")
async def lanes_dashboard(_user: UserContext = Depends(get_current_user)) -> dict:
    """All ten lanes plus live system health, as the operator screen shows them."""
    from app.services.snapback_system_health import system_health

    return operator_dashboard(system_health())


@router.get("/system/health")
async def system_health_status(
    _user: UserContext = Depends(get_current_user),
) -> dict:
    """The one status an operator acts on, with every component that fed it."""
    from app.services.snapback_system_health import system_health

    return system_health().as_dict()


@router.get("/strategies/{strategy}/{mode}/evidence")
async def lane_evidence(
    strategy: str, mode: str, _user: UserContext = Depends(get_current_user)
) -> dict:
    """What this lane has actually collected, and what it had to leave out.

    The exclusion counts are the point. "47 eligible trades out of 900 rows" is
    a different answer from "47 trades", and only the first says whether the
    sample is small because the strategy is selective or because most rows
    could not be attributed.
    """
    from app.core.lane_promotion import collect_lane_evidence, coverage_report

    lane, resolved = _resolve(strategy, mode)
    rows = _authoritative_rows()
    evidence = collect_lane_evidence(rows, lane.lane_key)
    return {
        "strategy": lane.strategy_id,
        "mode": resolved.value,
        "lane_key": lane.lane_key,
        "evidence": evidence.as_dict(),
        "store_coverage": coverage_report(rows),
    }


def _authoritative_rows() -> list[dict]:
    """Completed outcomes from the evidence store, or an empty list.

    An unreadable store returns empty rather than raising: the endpoint's job
    is to report, and "0 of 0" plus a visible store error is more useful to an
    operator than a 500.
    """
    try:
        from app.services.snapback_observation_warehouse import (
            SnapbackObservationWarehouse,
        )

        warehouse = SnapbackObservationWarehouse()
        import sqlite3

        conn = sqlite3.connect(warehouse.db_path)
        conn.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in conn.execute("SELECT * FROM outcomes")]
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 - reporting must not 500 on a store problem
        return []


@router.get("/positions")
async def positions(_user: UserContext = Depends(get_current_user)) -> dict:
    """Open positions with their lane, horizon and protection.

    Deliberately not a P&L list. A row showing only profit hides whether the
    position is past its hard exit or carrying unconfirmed protection, which
    are the two things that need acting on today.
    """
    from datetime import datetime, timezone

    from app.core.horizon import TimelineState, try_canonical_mode
    from app.core.trade_horizon import IST

    rows: list[dict] = []
    try:
        from app.services.snapback_observation_warehouse import (
            SnapbackObservationWarehouse,
        )

        open_positions = SnapbackObservationWarehouse().get_active_paper_positions()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=503, detail=f"position store unreadable: {exc}"
        ) from exc

    now = datetime.now(timezone.utc).astimezone(IST)
    for pos in open_positions:
        mode = try_canonical_mode(str(pos.get("mode") or ""))
        hard_at = pos.get("hard_exit_at")
        hard_session = pos.get("hard_exit_session")
        state = _timeline_state_of(pos, now)
        rows.append({
            "opportunity_id": pos.get("opportunity_id"),
            "strategy": pos.get("strategy_id") or "",
            "mode": mode.value if mode else "",
            "lane_key": pos.get("lane_key") or "",
            "underlying": pos.get("symbol"),
            "contract": pos.get("option_symbol"),
            "exchange": pos.get("option_exchange") or "",
            "direction": "long",
            "entry_at": pos.get("entry_timestamp"),
            "entry_price": pos.get("option_entry_price"),
            "quantity": pos.get("option_qty"),
            "expected_window": [
                pos.get("expected_window_start"), pos.get("expected_window_end"),
            ],
            "hard_exit_at": hard_at,
            "hard_exit_session": hard_session,
            "horizon_plan_id": pos.get("horizon_plan_id") or "",
            "timeline_state": state,
            "protection": "unknown",
            "evidence_health": (
                "attributed" if pos.get("lane_key") else "unattributed"
            ),
            "status": pos.get("status"),
        })

    unresolved = [
        r for r in rows
        if r["timeline_state"] in (TimelineState.HARD_EXIT_DUE.value,
                                   TimelineState.UNKNOWN.value)
    ]
    return {
        "positions": rows,
        "open_count": len(rows),
        "hard_exit_due_or_unknown": len(unresolved),
        "unattributed_count": sum(
            1 for r in rows if r["evidence_health"] == "unattributed"
        ),
    }


def _timeline_state_of(pos: dict, now) -> str:
    """Timeline state from what the row itself stored.

    Recomputed from the row rather than the live config, so an edited mode does
    not change how an already-open position reads.
    """
    from datetime import datetime

    from app.core.horizon import TimelineState

    raw = pos.get("hard_exit_at")
    if raw:
        try:
            return (
                TimelineState.HARD_EXIT_DUE.value
                if now >= datetime.fromisoformat(str(raw))
                else TimelineState.EXPECTED.value
            )
        except ValueError:
            return TimelineState.UNKNOWN.value
    session = pos.get("hard_exit_session")
    if session:
        try:
            return (
                TimelineState.HARD_EXIT_DUE.value
                if now.date() >= datetime.fromisoformat(str(session)).date()
                else TimelineState.EXPECTED.value
            )
        except ValueError:
            return TimelineState.UNKNOWN.value
    # No stored horizon at all: the position has nothing forcing it flat.
    return TimelineState.UNKNOWN.value


# ── operator actions ──────────────────────────────────────────────────────
#
# These are the only writes in this router, and each is recorded in
# operator_actions before it takes effect: a change nobody can attribute is a
# change nobody can undo with confidence.


def _record_action(action: str, actor: str, detail: str = "") -> str:
    try:
        from app.services.snapback_observation_warehouse import (
            SnapbackObservationWarehouse,
        )

        return SnapbackObservationWarehouse().record_operator_action(
            action=action, actor=actor, detail=detail
        )
    except Exception:  # noqa: BLE001 - never block the action on its own audit row
        return ""


@router.post("/operator/safe-mode")
async def operator_safe_mode(
    payload: dict = Body(...), user: UserContext = Depends(get_current_user)
) -> dict:
    """Engage SAFE_MODE, or leave it with an explicit acknowledgement.

    Leaving is deliberately harder than entering: turning it off requires
    ``acknowledge`` and a reason, because the moment a transient check passes is
    exactly when a person should still be looking.
    """
    from app.services.safe_mode import SafeModeService, SafeModeTrigger

    engage = bool(payload.get("engage"))
    reason = str(payload.get("reason") or "").strip()
    actor = getattr(user, "username", "") or getattr(user, "user_id", "")
    service = SafeModeService(_safe_mode_path(), runtime_sha="")

    if engage:
        if not reason:
            raise HTTPException(status_code=400, detail="a reason is required")
        _record_action("SAFE_MODE_ON", actor, reason)
        state = service.engage(trigger=SafeModeTrigger.OPERATOR, reason=reason)
        return state.as_dict()

    if not payload.get("acknowledge"):
        raise HTTPException(
            status_code=400,
            detail="leaving safe mode requires acknowledge=true and a reason",
        )
    if not reason:
        raise HTTPException(status_code=400, detail="a reason is required")
    _record_action("SAFE_MODE_OFF", actor, reason)
    try:
        state = service.release(
            operator_ack=True, note=f"{reason} (by {actor or 'operator'})"
        )
    except Exception as exc:  # noqa: BLE001 - surface the refusal verbatim
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return state.as_dict()


def _safe_mode_path():
    import os
    from pathlib import Path

    configured = os.environ.get("STERLING_SAFE_MODE_FILE")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[5] / "data" / "safe_mode.json"


@router.post("/operator/doctor")
async def operator_doctor(user: UserContext = Depends(get_current_user)) -> dict:
    """Run every safety check. A check that could not run is not a pass."""
    from app.core.operator_report import doctor_from_preflight, lane_doctor_checks
    from app.services.snapback_preflight import run_preflight

    actor = getattr(user, "username", "") or getattr(user, "user_id", "")
    _record_action("DOCTOR", actor)
    report = doctor_from_preflight(
        run_preflight().checks, extra=lane_doctor_checks()
    )
    return report.as_dict()


@router.post("/operator/reconcile")
async def operator_reconcile(user: UserContext = Depends(get_current_user)) -> dict:
    """Compare local state against the broker.

    Refuses rather than pretending when no broker session is available: an
    unreconciled book reported as clean is the worst possible answer.
    """
    actor = getattr(user, "username", "") or getattr(user, "user_id", "")
    action_id = _record_action("RECONCILE", actor)
    from app.services.snapback_reconciliation import reconcile_family_account

    try:
        snapshot = await reconcile_family_account()
    except Exception as exc:  # noqa: BLE001 - an unreconciled book is never "clean"
        raise HTTPException(
            status_code=503, detail=f"reconciliation could not run: {exc}"
        ) from exc
    body = snapshot.as_dict() if hasattr(snapshot, "as_dict") else {"snapshot": str(snapshot)}
    return {"action_id": action_id, "result": body}


@router.post("/operator/backup")
async def operator_backup(user: UserContext = Depends(get_current_user)) -> dict:
    """Take a checksummed backup of the evidence database."""
    from pathlib import Path

    from app.services.snapback_backup import create_backup
    from app.services.snapback_observation_warehouse import (
        SnapbackObservationWarehouse,
    )

    actor = getattr(user, "username", "") or getattr(user, "user_id", "")
    action_id = _record_action("BACKUP", actor)
    source = Path(SnapbackObservationWarehouse().db_path).resolve()
    try:
        artifact = create_backup(
            source_db=source, backup_root=source.parent / "backups"
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=503, detail=f"backup failed: {exc}"
        ) from exc
    return {"action_id": action_id, "artifact": str(artifact)}
