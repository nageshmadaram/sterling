"""Gather the nine permission inputs from their real owners, once.

:mod:`app.core.capital_permission` is deliberately pure: it takes facts and
returns a verdict, so it can be tested without a broker, a database or a repo.
This module is the other half — the part that goes and asks each owner. It is
separate because "what does the rule say?" and "what is true right now?" fail
in different ways, and a failure to read a fact must show up as UNKNOWN rather
than as a rule that quietly answered no for the wrong reason.

Anything that cannot be determined is passed through as ``None``, which the
gate treats as a refusal and reports as unknown. Guessing ``False`` would be
the same verdict today and a lie in the report.
"""
from __future__ import annotations

from typing import Any

from app.services import lane_gate_inputs
from app.core.capital_permission import (
    CapitalPermission,
    EntryPermissionInputs,
    may_send_entry,
)

__all__ = ["lane_permission", "all_lane_permissions", "render_permissions"]


def _safety_may_increase(strategy_id: str) -> bool | None:
    try:
        from app.services.safety_supervisor import snapshot

        return bool(snapshot(strategy_id=strategy_id).may_increase_exposure)
    except Exception:  # noqa: BLE001 - an unreadable authority is unknown, not "yes"
        return None


def _vehicle_live_capable(lane_key: str) -> bool | None:
    from app.core.execution_vehicle import (
        VehicleError,
        is_authoritative_live_vehicle,
        lane_vehicle,
    )

    try:
        return is_authoritative_live_vehicle(lane_vehicle(lane_key))
    except VehicleError:
        return None


def _release_certified() -> bool | None:
    try:
        from app.services.release_certification import certification_report

        return bool(certification_report().release_ready)
    except Exception:  # noqa: BLE001
        return None


def _binding_live_ready() -> bool | None:
    try:
        from app.services.account_binding_service import binding_health

        health = binding_health()
        if not health.get("account_binding_readable"):
            return None
        return bool(health.get("account_binding_live_ready"))
    except Exception:  # noqa: BLE001
        return None


def lane_permission(
    lane_key: str,
    *,
    release_certified: bool | None = None,
    binding_ready: bool | None = None,
) -> CapitalPermission:
    """The real-money verdict for one lane, read from live state."""
    from app.core.lane_registry import LANES, LIVE_EXECUTION_ENABLED

    lane = LANES.get(lane_key)
    strategy = lane.strategy_id if lane else ""

    return may_send_entry(
        EntryPermissionInputs(
            lane_key=lane_key,
            global_live_switch=LIVE_EXECUTION_ENABLED,
            release_certified=(
                _release_certified() if release_certified is None else release_certified
            ),
            account_binding_ready=(
                _binding_live_ready() if binding_ready is None else binding_ready
            ),
            safety_may_increase_exposure=_safety_may_increase(strategy),
            lane_state=lane.state.value if lane else "",
            # Three of these are now derived from live state rather than left
            # unknown: the shadow path writes records, the manifest can be
            # compared, and the economic gate can be run on broker rows. Each
            # still answers None when the question genuinely cannot be answered,
            # and `may_send_entry` refuses on None.
            lane_promotion_passed=lane_gate_inputs.lane_promotion_passed(lane_key),
            lane_identity_matches_frozen=lane_gate_inputs.lane_identity_matches_frozen(lane_key),
            shadow_gate_passed=lane_gate_inputs.shadow_gate_passed(lane_key),
            # The risk hierarchy and exposure coordinator are asked per order,
            # not per lane: there is no lane-level answer to derive here, and
            # inventing one would be a claim about an order nobody has placed.
            risk_gate_passed=None,
            exposure_gate_passed=None,
            vehicle_live_capable=_vehicle_live_capable(lane_key),
        )
    )


def all_lane_permissions() -> list[dict[str, Any]]:
    from app.core.lane_registry import LANES

    certified = _release_certified()
    binding = _binding_live_ready()
    return [
        lane_permission(key, release_certified=certified, binding_ready=binding).as_dict()
        for key in LANES
    ]


def render_permissions() -> str:
    from app.core.lane_registry import LANES

    certified = _release_certified()
    binding = _binding_live_ready()
    blocks = [
        lane_permission(key, release_certified=certified, binding_ready=binding).render()
        for key in LANES
    ]
    blocks.append(
        "No lane sends real money until every line above clears for that lane."
    )
    return "\n\n".join(blocks)
