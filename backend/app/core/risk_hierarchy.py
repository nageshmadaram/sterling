"""Risk limits at five levels, and what happens when one is not configured.

Limits exist at GLOBAL, STRATEGY, UNDERLYING, MODE and POSITION level, and all
of them apply to every request. A per-mode allocation that a strategy-level cap
would already have blocked is not redundant: the strategy cap is what stops five
modes each using their full allocation.

UNDERLYING is the layer that stops ten differently-labelled lanes becoming one
oversized NIFTY position. It sits between STRATEGY and MODE because it is
broader than a lane and narrower than the whole book, and it cuts across
strategies: Snapback and SuperTrend both long NIFTY are one bet with two names.

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
    #: Scope key is the canonical underlying, e.g. ``"NIFTY"``.
    UNDERLYING = "underlying"
    MODE = "mode"
    POSITION = "position"


#: Every level must be satisfied, checked broadest first so the reported
#: refusal is the one with the widest blast radius.
_CHECK_ORDER = (
    RiskLevel.GLOBAL,
    RiskLevel.STRATEGY,
    RiskLevel.UNDERLYING,
    RiskLevel.MODE,
    RiskLevel.POSITION,
)

#: Returned when UNDERLYING limits are declared but the caller did not say which
#: underlying it is about. Skipping the level would be the "absent means
#: unlimited" mistake this module exists to refuse, one layer down.
UNDERLYING_UNKNOWN = "INCONCLUSIVE_UNDERLYING_UNKNOWN"

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

    Scope keys: ``""`` at GLOBAL, the strategy id at STRATEGY, the canonical
    underlying at UNDERLYING, the lane key at MODE, and the lane key again at
    POSITION (a per-position budget belongs to the lane that opens it).
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

    def declares(self, level: RiskLevel) -> bool:
        """Has the operator configured any limit at this level?"""
        return bool(self.limits.get(level))

    def check(
        self,
        *,
        strategy_id: str,
        lane_key: str,
        requested: float,
        used: Mapping[RiskLevel, float] | None = None,
        underlying: str | None = None,
    ) -> RiskDecision:
        """May ``requested`` more risk be taken, given what is already used?

        ``underlying`` is optional only so that a hierarchy which declares no
        UNDERLYING limits behaves exactly as before. Once any are declared, a
        caller that cannot name the underlying gets INCONCLUSIVE rather than a
        skipped level — an unnamed underlying is an unmeasured concentration.
        """
        if requested < 0:
            raise RiskConfigurationError("requested risk must not be negative")
        consumed = used or {}

        underlying_scope = (underlying or "").strip().upper()
        if self.declares(RiskLevel.UNDERLYING) and not underlying_scope:
            return RiskDecision(False, UNDERLYING_UNKNOWN, RiskLevel.UNDERLYING)

        scopes = {
            RiskLevel.GLOBAL: "",
            RiskLevel.STRATEGY: strategy_id.strip().lower(),
            RiskLevel.UNDERLYING: underlying_scope,
            RiskLevel.MODE: lane_key,
            RiskLevel.POSITION: lane_key,
        }

        for level in _CHECK_ORDER:
            if level is RiskLevel.UNDERLYING and not self.declares(level):
                # Nothing declared at this level: the hierarchy is the four it
                # was before, not a level that silently permits everything.
                continue
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

    def missing_scopes(
        self, *, strategy_id: str, lane_key: str, underlying: str | None = None
    ) -> tuple[str, ...]:
        """Which limits a lane would need before it can take any risk."""
        scopes = {
            RiskLevel.GLOBAL: "",
            RiskLevel.STRATEGY: strategy_id.strip().lower(),
            RiskLevel.MODE: lane_key,
            RiskLevel.POSITION: lane_key,
        }
        if self.declares(RiskLevel.UNDERLYING):
            scopes[RiskLevel.UNDERLYING] = (underlying or "").strip().upper()
        return tuple(
            f"{level.value}/{scope or '*'}"
            for level, scope in scopes.items()
            if self.limit_for(level, scope) is None
        )


#: Environment variable holding the limits, as JSON:
#: ``{"global": {"": 100000}, "strategy": {"snapback": 60000},
#:    "underlying": {"NIFTY": 40000},
#:    "mode": {"snapback:swing": 30000}, "position": {"snapback:swing": 10000}}``
#:
#: The ``underlying`` block is optional. Declaring it turns the level on, and
#: from then on a request that cannot name its underlying is refused.
ENV_VAR = "STERLING_RISK_LIMITS"


def configured_hierarchy(env: Mapping[str, str] | None = None) -> "RiskHierarchy | None":
    """The hierarchy the operator configured, or ``None`` when none is set.

    ``None`` means "no hierarchy declared", which is not the same as "no
    limits": callers fall back to whatever other capital checks they have, and
    the doctor reports the gap. A malformed value raises instead of yielding
    ``None``, because an operator who tried to set limits and mistyped must not
    silently get none.
    """
    import json
    import os

    source = env if env is not None else os.environ
    raw = (source.get(ENV_VAR) or "").strip()
    if not raw:
        return None
    try:
        blob = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RiskConfigurationError(f"{ENV_VAR} is not valid JSON: {exc}") from exc
    if not isinstance(blob, dict):
        raise RiskConfigurationError(f"{ENV_VAR} must be a JSON object")

    limits: dict[RiskLevel, dict[str, float]] = {}
    for level_name, scopes in blob.items():
        try:
            level = RiskLevel(str(level_name))
        except ValueError as exc:
            raise RiskConfigurationError(
                f"{ENV_VAR}: unknown risk level {level_name!r}; expected any of "
                f"{[l.value for l in RiskLevel]}"
            ) from exc
        if not isinstance(scopes, dict):
            raise RiskConfigurationError(
                f"{ENV_VAR}: {level_name} must map a scope to a limit"
            )
        limits[level] = {str(k): float(v) for k, v in scopes.items()}
    return RiskHierarchy(limits=limits)
