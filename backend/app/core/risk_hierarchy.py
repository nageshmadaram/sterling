"""Risk limits at four levels, and what happens when one is not configured.

Limits exist at GLOBAL, STRATEGY, MODE and POSITION level, and all four apply
to every request. A per-mode allocation that a strategy-level cap would already
have blocked is not redundant: the strategy cap is what stops five modes each
using their full allocation.

The rule worth stating plainly is the one in :meth:`RiskHierarchy.check`. A
missing limit is INCONCLUSIVE_RISK_CONFIGURATION, never "no limit". Treating an
absent number as unlimited is how a config typo becomes an uncapped position,
and it fails in the direction that costs money.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Mapping

from app.core.strategy_identity import stable_hash


class RiskLevel(StrEnum):
    GLOBAL = "global"
    STRATEGY = "strategy"
    MODE = "mode"
    POSITION = "position"


#: Every level must be satisfied, checked broadest first so the reported
#: refusal is the one with the widest blast radius.
_CHECK_ORDER = (
    RiskLevel.GLOBAL,
    RiskLevel.STRATEGY,
    RiskLevel.MODE,
    RiskLevel.POSITION,
)

INCONCLUSIVE = "INCONCLUSIVE_RISK_CONFIGURATION"


class RiskConfigurationError(ValueError):
    """A limit is missing or nonsensical."""


@dataclass(frozen=True)
class RiskDecision:
    allowed: bool
    reason: str = ""
    level: RiskLevel | None = None
    limit: float | None = None
    would_be: float | None = None

    def __bool__(self) -> bool:  # pragma: no cover - convenience only
        return self.allowed


@dataclass(frozen=True)
class RiskHierarchy:
    """Declared limits, keyed by level and then by scope.

    Scope keys: ``""`` at GLOBAL, the strategy id at STRATEGY, the lane key at
    MODE, and the lane key again at POSITION (a per-position budget belongs to
    the lane that opens it).
    """

    limits: Mapping[RiskLevel, Mapping[str, float]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for level, scopes in self.limits.items():
            for scope, value in scopes.items():
                if value < 0:
                    raise RiskConfigurationError(
                        f"{level.value}/{scope or '*'}: negative limit {value}"
                    )

    @property
    def config_hash(self) -> str:
        """Included in the lane identity: a limit change is a config change."""
        return stable_hash(
            {
                level.value: dict(sorted(scopes.items()))
                for level, scopes in sorted(
                    self.limits.items(), key=lambda kv: kv[0].value
                )
            }
        )

    def limit_for(self, level: RiskLevel, scope: str) -> float | None:
        return self.limits.get(level, {}).get(scope)

    def check(
        self,
        *,
        strategy_id: str,
        lane_key: str,
        requested: float,
        used: Mapping[RiskLevel, float] | None = None,
    ) -> RiskDecision:
        """May ``requested`` more risk be taken, given what is already used?"""
        if requested < 0:
            raise RiskConfigurationError("requested risk must not be negative")
        consumed = used or {}
        scopes = {
            RiskLevel.GLOBAL: "",
            RiskLevel.STRATEGY: strategy_id.strip().lower(),
            RiskLevel.MODE: lane_key,
            RiskLevel.POSITION: lane_key,
        }

        for level in _CHECK_ORDER:
            scope = scopes[level]
            limit = self.limit_for(level, scope)
            if limit is None:
                return RiskDecision(
                    False,
                    INCONCLUSIVE,
                    level,
                    None,
                    None,
                )
            already = float(consumed.get(level, 0.0))
            if already < 0:
                raise RiskConfigurationError(
                    f"{level.value}/{scope or '*'}: negative usage {already}"
                )
            projected = already + requested
            if projected > limit:
                return RiskDecision(
                    False,
                    f"RISK_LIMIT_EXCEEDED_{level.value.upper()}",
                    level,
                    limit,
                    projected,
                )

        return RiskDecision(True)

    def missing_scopes(self, *, strategy_id: str, lane_key: str) -> tuple[str, ...]:
        """Which limits a lane would need before it can take any risk."""
        scopes = {
            RiskLevel.GLOBAL: "",
            RiskLevel.STRATEGY: strategy_id.strip().lower(),
            RiskLevel.MODE: lane_key,
            RiskLevel.POSITION: lane_key,
        }
        return tuple(
            f"{level.value}/{scope or '*'}"
            for level, scope in scopes.items()
            if self.limit_for(level, scope) is None
        )
