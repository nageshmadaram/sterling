"""The intersection that has to hold before real money may be committed.

There is no "LIVE ON" switch in Sterling, and this module is why. A global
switch makes every lane eligible at once, which means the first lane to earn
capital silently authorises the nine that did not. Permission is instead the
conjunction of nine independent facts, each owned by a different part of the
system, and every one of them can only ever remove permission.

Two design choices matter more than the list itself:

* **Unknown is never permission.** Each input is a tri-state where that is
  honest — ``None`` means "could not be determined" — and ``None`` refuses. A
  promotion verdict that could not be read is not a pass.
* **Every refusal is reported, not just the first.** An operator asking "why is
  this lane not live?" needs the whole answer; fixing one blocker only to meet
  the next is how a safety review turns into a game of whack-a-mole.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final, Mapping

__all__ = [
    "CapitalPermission",
    "EntryPermissionInputs",
    "may_send_entry",
    "LIVE_LANE_STATES",
]

#: Lane states in which real capital is at stake.
LIVE_LANE_STATES: Final[frozenset[str]] = frozenset({"LIVE_MINIMUM", "LIVE_SCALED"})

#: Human text for each refusal code, so the dashboard never shows a bare token.
_REASONS: Final[Mapping[str, str]] = {
    "GLOBAL_LIVE_DISABLED": "real-money execution is globally disabled",
    "RELEASE_NOT_CERTIFIED": "this build is not a certified release",
    "ACCOUNT_BINDING_NOT_READY": "no ACTIVE, live-ready broker account binding",
    "SAFETY_BLOCKS_EXPOSURE": "the safety supervisor refuses new exposure",
    "LANE_NOT_LIVE_STATE": "the lane is not in LIVE_MINIMUM or LIVE_SCALED",
    "LANE_PROMOTION_NOT_PASSED": "the lane's economic gate has not passed",
    "LANE_IDENTITY_DRIFT": "the running lane identity is not the frozen one",
    "SHADOW_GATE_NOT_PASSED": "the lane's shadow execution gate has not passed",
    "RISK_GATE_NOT_PASSED": "the risk hierarchy refuses this exposure",
    "EXPOSURE_GATE_NOT_PASSED": "the exposure coordinator refuses this exposure",
    "VEHICLE_NOT_LIVE_CAPABLE": "the lane's execution vehicle is research-only",
    "UNKNOWN_INPUT": "a required permission input could not be determined",
}


@dataclass(frozen=True)
class EntryPermissionInputs:
    """Every fact the decision needs, supplied by its owner.

    All booleans default to the refusing value. A caller that forgets to pass
    one gets "no", which is the only safe direction for a default to point.
    """

    lane_key: str

    global_live_switch: bool | None = False
    release_certified: bool | None = False
    account_binding_ready: bool | None = False
    safety_may_increase_exposure: bool | None = False

    lane_state: str = ""
    lane_promotion_passed: bool | None = False
    lane_identity_matches_frozen: bool | None = False

    shadow_gate_passed: bool | None = False
    risk_gate_passed: bool | None = False
    exposure_gate_passed: bool | None = False

    vehicle_live_capable: bool | None = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "lane_key": self.lane_key,
            "global_live_switch": self.global_live_switch,
            "release_certified": self.release_certified,
            "account_binding_ready": self.account_binding_ready,
            "safety_may_increase_exposure": self.safety_may_increase_exposure,
            "lane_state": self.lane_state,
            "lane_promotion_passed": self.lane_promotion_passed,
            "lane_identity_matches_frozen": self.lane_identity_matches_frozen,
            "shadow_gate_passed": self.shadow_gate_passed,
            "risk_gate_passed": self.risk_gate_passed,
            "exposure_gate_passed": self.exposure_gate_passed,
            "vehicle_live_capable": self.vehicle_live_capable,
        }


@dataclass(frozen=True)
class CapitalPermission:
    """Whether one lane may send a real entry, and every reason it may not."""

    allowed: bool
    lane_key: str
    blockers: tuple[str, ...] = field(default_factory=tuple)
    unknowns: tuple[str, ...] = field(default_factory=tuple)

    def __bool__(self) -> bool:  # pragma: no cover - convenience
        return self.allowed

    @property
    def reasons(self) -> tuple[str, ...]:
        return tuple(_REASONS.get(code, code) for code in self.blockers)

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "lane_key": self.lane_key,
            "blockers": list(self.blockers),
            "reasons": list(self.reasons),
            "unknowns": list(self.unknowns),
        }

    def render(self) -> str:
        if self.allowed:
            return f"{self.lane_key}: may send real entries."
        lines = [f"{self.lane_key}: REAL-MONEY ENTRY REFUSED"]
        for code, reason in zip(self.blockers, self.reasons):
            lines.append(f"  {code:<28}{reason}")
        if self.unknowns:
            lines.append(
                "  unknown: " + ", ".join(self.unknowns) + " (unknown never permits)"
            )
        return "\n".join(lines)


#: (input attribute, refusal code) in the order an operator should read them:
#: global facts first, then the account, then the lane's own evidence.
_CHECKS: Final[tuple[tuple[str, str], ...]] = (
    ("global_live_switch", "GLOBAL_LIVE_DISABLED"),
    ("release_certified", "RELEASE_NOT_CERTIFIED"),
    ("account_binding_ready", "ACCOUNT_BINDING_NOT_READY"),
    ("safety_may_increase_exposure", "SAFETY_BLOCKS_EXPOSURE"),
    ("lane_promotion_passed", "LANE_PROMOTION_NOT_PASSED"),
    ("lane_identity_matches_frozen", "LANE_IDENTITY_DRIFT"),
    ("shadow_gate_passed", "SHADOW_GATE_NOT_PASSED"),
    ("risk_gate_passed", "RISK_GATE_NOT_PASSED"),
    ("exposure_gate_passed", "EXPOSURE_GATE_NOT_PASSED"),
    ("vehicle_live_capable", "VEHICLE_NOT_LIVE_CAPABLE"),
)


def may_send_entry(inputs: EntryPermissionInputs) -> CapitalPermission:
    """Evaluate §13. Every ``False`` and every ``None`` refuses."""
    blockers: list[str] = []
    unknowns: list[str] = []

    state = str(inputs.lane_state or "").strip().upper()
    if state not in LIVE_LANE_STATES:
        blockers.append("LANE_NOT_LIVE_STATE")

    for attribute, code in _CHECKS:
        value = getattr(inputs, attribute)
        if value is None:
            unknowns.append(attribute)
            blockers.append(code)
        elif not value:
            blockers.append(code)

    # Ordered as declared, with the lane-state refusal kept where the operator
    # expects it rather than always first.
    order = ["GLOBAL_LIVE_DISABLED", "RELEASE_NOT_CERTIFIED", "ACCOUNT_BINDING_NOT_READY",
             "SAFETY_BLOCKS_EXPOSURE", "LANE_NOT_LIVE_STATE", "LANE_PROMOTION_NOT_PASSED",
             "LANE_IDENTITY_DRIFT", "SHADOW_GATE_NOT_PASSED", "RISK_GATE_NOT_PASSED",
             "EXPOSURE_GATE_NOT_PASSED", "VEHICLE_NOT_LIVE_CAPABLE"]
    blockers.sort(key=lambda code: order.index(code) if code in order else len(order))

    return CapitalPermission(
        allowed=not blockers,
        lane_key=inputs.lane_key,
        blockers=tuple(blockers),
        unknowns=tuple(unknowns),
    )
