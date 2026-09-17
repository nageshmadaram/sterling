"""One top-level health status, derived from component signals.

Sterling already reports health in many places — preflight, startup, the
reconciliation recovery state, the safe-mode file. What it lacked was a single
value an operator can act on without knowing which subsystem produced it, and a
rule for combining them that cannot accidentally report NORMAL.

The combining rule is severity-ordered and fail-closed: an unknown component
is an error, not a pass, and the most severe component wins. A component that
reports nothing at all is the most dangerous case of all, because silence is
indistinguishable from health.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final, Mapping


class SystemHealth(StrEnum):
    """What the operator sees, and what each value tells them to do."""

    #: Observe normally.
    NORMAL = "normal"
    #: Do not enable new trading. Existing positions are still managed.
    SAFE_MODE = "safe_mode"
    #: Do not trade. Market data cannot be trusted.
    DATA_ERROR = "data_error"
    #: Check the broker first.
    BROKER_ERROR = "broker_error"
    #: The evidence store is unhealthy; nothing recorded can be trusted.
    EVIDENCE_ERROR = "evidence_error"
    #: Run reconcile.
    RECOVERY_REQUIRED = "recovery_required"


#: Most severe last. RECOVERY_REQUIRED outranks the rest because it is the one
#: state that says exposure may exist that Sterling cannot account for.
_SEVERITY: Final[tuple[SystemHealth, ...]] = (
    SystemHealth.NORMAL,
    SystemHealth.SAFE_MODE,
    SystemHealth.DATA_ERROR,
    SystemHealth.BROKER_ERROR,
    SystemHealth.EVIDENCE_ERROR,
    SystemHealth.RECOVERY_REQUIRED,
)

_RANK: Final[Mapping[SystemHealth, int]] = {
    value: index for index, value in enumerate(_SEVERITY)
}

#: Components every health report must include. A missing one is an error
#: rather than an omission: "nobody checked the broker" and "the broker is
#: fine" must not produce the same status.
REQUIRED_COMPONENTS: Final[frozenset[str]] = frozenset(
    {"broker", "market_data", "evidence", "reconciliation", "safe_mode"}
)

#: What a missing component degrades to.
_MISSING_COMPONENT_HEALTH: Final[Mapping[str, SystemHealth]] = {
    "broker": SystemHealth.BROKER_ERROR,
    "market_data": SystemHealth.DATA_ERROR,
    "evidence": SystemHealth.EVIDENCE_ERROR,
    "reconciliation": SystemHealth.RECOVERY_REQUIRED,
    "safe_mode": SystemHealth.SAFE_MODE,
}


@dataclass(frozen=True)
class ComponentHealth:
    """One subsystem's verdict, and why."""

    name: str
    status: SystemHealth
    detail: str = ""


@dataclass(frozen=True)
class HealthReport:
    """The composed status. ``overall`` is what the dashboard shows."""

    overall: SystemHealth
    components: tuple[ComponentHealth, ...] = field(default_factory=tuple)
    missing: tuple[str, ...] = field(default_factory=tuple)

    @property
    def may_open_new_exposure(self) -> bool:
        """Only a fully NORMAL system opens new risk."""
        return self.overall is SystemHealth.NORMAL

    @property
    def may_manage_existing_exposure(self) -> bool:
        """Always. Monitoring, protection repair and exit are never blocked."""
        return True

    def as_dict(self) -> dict[str, object]:
        return {
            "overall": self.overall.value,
            "may_open_new_exposure": self.may_open_new_exposure,
            "components": [
                {"name": c.name, "status": c.status.value, "detail": c.detail}
                for c in self.components
            ],
            "missing_components": list(self.missing),
        }


def worst(*statuses: SystemHealth) -> SystemHealth:
    """The most severe of several statuses."""
    if not statuses:
        return SystemHealth.RECOVERY_REQUIRED
    return max(statuses, key=lambda s: _RANK[s])


def compose_health(components: Mapping[str, ComponentHealth]) -> HealthReport:
    """Combine component verdicts into one status, failing closed on gaps."""
    missing = tuple(sorted(REQUIRED_COMPONENTS - set(components)))
    reported = tuple(components[name] for name in sorted(components))

    statuses = [c.status for c in reported]
    statuses.extend(_MISSING_COMPONENT_HEALTH[name] for name in missing)

    synthesised = tuple(
        ComponentHealth(
            name=name,
            status=_MISSING_COMPONENT_HEALTH[name],
            detail="component did not report; treated as failed",
        )
        for name in missing
    )

    return HealthReport(
        overall=worst(*statuses) if statuses else SystemHealth.RECOVERY_REQUIRED,
        components=tuple(sorted(reported + synthesised, key=lambda c: c.name)),
        missing=missing,
    )
