"""Family live gate for Snapback.

Any exposure-increasing live Snapback order requires ALL of:

    authoritative evidence verdict == PASSED
    AND LIVE_ELIGIBLE state
    AND canonical RiskEngine approval
    AND system HEALTHY
    AND broker reconciled

A manual UI toggle cannot bypass it. Protective actions — exiting, flattening and
reconciliation — stay permitted while halted, because blocking them would trap
capital rather than protect it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, List

log = logging.getLogger(__name__)


class LiveIntent(str, Enum):
    ENTER = "ENTER"
    ADD = "ADD"
    EXIT = "EXIT"
    FLATTEN = "FLATTEN"
    RECONCILE = "RECONCILE"


_EXPOSURE_INCREASING = {LiveIntent.ENTER, LiveIntent.ADD}
_PROTECTIVE = {LiveIntent.EXIT, LiveIntent.FLATTEN, LiveIntent.RECONCILE}


@dataclass
class LiveGateDecision:
    allowed: bool
    protective: bool = False
    reasons: List[str] = field(default_factory=list)


def is_exposure_increasing(intent: Any) -> bool:
    try:
        return LiveIntent(intent) in _EXPOSURE_INCREASING
    except Exception:
        # An intent we do not recognise is treated as exposure-increasing.
        return True


def evaluate_live_gate(
    *,
    intent: Any,
    evidence_verdict: str,
    live_eligible: bool,
    risk_approved: bool,
    system_status: str,
    broker_reconciled: bool,
    operator_override: bool = False,
) -> LiveGateDecision:
    """Decide whether one live Snapback action may proceed."""
    try:
        parsed = LiveIntent(intent)
    except Exception:
        log.warning("Snapback family gate: unknown live intent %r denied", intent)
        return LiveGateDecision(allowed=False, reasons=["unknown_intent"])

    if parsed in _PROTECTIVE:
        # Protective work continues regardless of evidence or health state.
        return LiveGateDecision(allowed=True, protective=True)

    reasons: List[str] = []
    if str(evidence_verdict).upper() != "PASSED":
        reasons.append("evidence_not_passed")
    if not live_eligible:
        reasons.append("not_live_eligible")
    if not risk_approved:
        reasons.append("risk_engine_denied")
    if str(system_status).upper() != "HEALTHY":
        reasons.append("system_not_healthy")
    if not broker_reconciled:
        reasons.append("broker_not_reconciled")

    if reasons and operator_override:
        # Recorded deliberately: an override is never allowed to clear these.
        log.warning(
            "Snapback family gate: operator override ignored; blocking reasons=%s", reasons
        )

    return LiveGateDecision(allowed=not reasons, protective=False, reasons=reasons)
