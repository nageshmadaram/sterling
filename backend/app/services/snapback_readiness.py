"""One source of truth for what Snapback is allowed to do right now.

    OBSERVE       collecting, not even paper-trading
    PAPER         paper evidence collection (today's state)
    LIVE_ELIGIBLE the authoritative gate passed and the system is healthy
    HALTED        stopped on purpose; protective actions only
    RECONCILING   state is unknown and must be resolved before new exposure

Every input is derived on the server. Nothing here trusts a caller.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List

log = logging.getLogger(__name__)

OBSERVE = "OBSERVE"
PAPER = "PAPER"
LIVE_ELIGIBLE = "LIVE_ELIGIBLE"
HALTED = "HALTED"
RECONCILING = "RECONCILING"


@dataclass
class ReadinessState:
    state: str
    system_status: str
    evidence_verdict: str
    broker_reconciled: bool
    reasons: List[str] = field(default_factory=list)


def readiness_state(uid: str = "default") -> ReadinessState:
    """Derive the current readiness. Unknown never becomes LIVE_ELIGIBLE."""
    reasons: List[str] = []

    system_status = "HALTED"
    broker_connected = False
    unresolved: List[str] = []
    try:
        from app.services.snapback_health import get_prospective_health

        health = get_prospective_health(uid)
        system_status = str(health.get("status") or "HALTED")
        broker_connected = bool(health.get("broker_connected"))
        unresolved = list(health.get("unresolved_errors") or [])
    except Exception as exc:
        reasons.append(f"health_unavailable:{exc}")

    verdict = "INCONCLUSIVE"
    try:
        from app.services.snapback_family_ops import get_family_evidence_verdict

        verdict = str(get_family_evidence_verdict().get("verdict") or "INCONCLUSIVE")
    except Exception as exc:
        reasons.append(f"evidence_unavailable:{exc}")

    halted_by_family = False
    try:
        from app.services.snapback_family_ops import new_trades_halted

        halted_by_family = bool(new_trades_halted())
    except Exception as exc:
        reasons.append(f"halt_state_unavailable:{exc}")
        halted_by_family = True

    # Reconciliation is only claimed when nothing about the book is unknown.
    broker_reconciled = broker_connected and not any(
        code in unresolved
        for code in ("position_reconciliation_mismatch", "recovery_required",
                     "startup_preflight_failed", "database_unavailable")
    )

    if any(code in unresolved for code in ("recovery_required", "position_reconciliation_mismatch")):
        state = RECONCILING
    elif halted_by_family or system_status == "HALTED":
        state = HALTED
    elif verdict == "PASSED" and system_status == "HEALTHY" and broker_reconciled:
        state = LIVE_ELIGIBLE
    elif system_status in ("HEALTHY", "DEGRADED"):
        state = PAPER
    else:
        state = OBSERVE

    if state != LIVE_ELIGIBLE and verdict != "PASSED":
        reasons.append(f"evidence_{verdict.lower()}")

    return ReadinessState(
        state=state,
        system_status=system_status,
        evidence_verdict=verdict,
        broker_reconciled=broker_reconciled,
        reasons=reasons,
    )
