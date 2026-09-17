"""Read-only surface over the two-strategy, five-mode model.

Every endpoint here reports. None of them opens, closes or sizes anything, and
none of them can change a lane's state: promotion is an evidence decision made
deliberately, not an HTTP call. The operator write actions that do exist
(SAFE_MODE, reconcile) already live in their own routers and are not duplicated
here.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

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
