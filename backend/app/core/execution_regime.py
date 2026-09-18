"""Paper, shadow and broker answer different questions. Keep them apart.

A paper fill says what the strategy would have done against observed data. A
shadow record says whether the broker and the book could realistically have
executed it. A broker fill says what actually happened to real money. Adding
them up produces a number that describes none of the three, and it is a
flattering number, because paper is the most permissive of them.

The promotion gate's default promotable set is all three. That is the right
default for "does this lane have any forward evidence at all?" and the wrong
one for "is this lane profitable?", so this module makes pooling an explicit,
predeclared decision with a named policy, and always reports each regime's
statistics separately whether or not they are pooled.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final, Iterable, Mapping

from app.core.evidence import EvidenceClass, eligible_for_lane

__all__ = [
    "REGIMES",
    "PromotionPolicy",
    "SEPARATE_REGIMES",
    "POOLED_FORWARD",
    "regime_statistics",
    "assert_pooling_declared",
]

#: The three execution regimes, in increasing order of what they prove.
REGIMES: Final[tuple[EvidenceClass, ...]] = (
    EvidenceClass.PAPER,
    EvidenceClass.SHADOW,
    EvidenceClass.BROKER,
)

_QUESTION: Final[Mapping[EvidenceClass, str]] = {
    EvidenceClass.PAPER: "What would the strategy do under observed market data?",
    EvidenceClass.SHADOW: "Could the broker and the book realistically have executed it?",
    EvidenceClass.BROKER: "What actually happened with real capital?",
}


class PoolingNotDeclared(ValueError):
    """Two regimes were about to be added together without a policy saying so."""


@dataclass(frozen=True)
class PromotionPolicy:
    """Which regimes a promotion verdict is allowed to add together.

    ``declared_at`` and ``rationale`` are required for a policy that pools.
    Predeclaration is the whole safeguard: a pooling rule chosen after seeing
    which combination passes is not a policy, it is a result being selected.
    """

    policy_id: str
    pooled_classes: frozenset[EvidenceClass] = field(default_factory=frozenset)
    declared_at: str = ""
    rationale: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "pooled_classes", frozenset(self.pooled_classes))
        if len(self.pooled_classes) > 1 and not (self.declared_at and self.rationale):
            raise PoolingNotDeclared(
                f"policy {self.policy_id!r} pools "
                f"{', '.join(sorted(c.value for c in self.pooled_classes))} but does "
                "not say when or why it was declared"
            )

    def pools(self, *classes: EvidenceClass) -> bool:
        return set(classes).issubset(self.pooled_classes)

    def as_dict(self) -> dict[str, Any]:
        return {
            "policy_id": self.policy_id,
            "pooled_classes": sorted(c.value for c in self.pooled_classes),
            "declared_at": self.declared_at,
            "rationale": self.rationale,
        }


#: The post-67 default: every regime is judged on its own. This is a change of
#: policy, not of arithmetic — nothing is discarded, it is reported separately.
SEPARATE_REGIMES: Final[PromotionPolicy] = PromotionPolicy(
    policy_id="separate_regimes_v1",
    pooled_classes=frozenset(),
)

#: The pooled alternative, available only to a lane that predeclares it.
POOLED_FORWARD: Final[PromotionPolicy] = PromotionPolicy(
    policy_id="pooled_forward_v1",
    pooled_classes=frozenset(REGIMES),
    declared_at="2026-09-18",
    rationale=(
        "Used only where a lane's predeclared design states that paper, shadow "
        "and broker rows are interchangeable for its statistic. Defaults do not "
        "select it; a lane must name it."
    ),
)


def regime_statistics(
    rows: Iterable[Mapping[str, Any]],
    lane_key: str,
) -> dict[str, dict[str, Any]]:
    """Per-regime counts for one lane, always reported separately."""
    materialised = list(rows)
    out: dict[str, dict[str, Any]] = {}
    for regime in REGIMES:
        subset = [
            row
            for row in materialised
            if eligible_for_lane(row, lane_key, allowed_classes={regime})
        ]
        sessions = {
            str(row.get("entry_date") or "").strip()
            for row in subset
            if str(row.get("entry_date") or "").strip()
        }
        out[regime.value] = {
            "question": _QUESTION[regime],
            "trades": len(subset),
            "sessions": len(sessions),
        }
    return out


def assert_pooling_declared(
    policy: PromotionPolicy,
    classes: Iterable[EvidenceClass],
) -> None:
    """Refuse to pool regimes the policy did not predeclare."""
    requested = {EvidenceClass(c) for c in classes}
    if len(requested) <= 1:
        return
    if not requested.issubset(policy.pooled_classes):
        missing = ", ".join(sorted(c.value for c in requested - policy.pooled_classes))
        raise PoolingNotDeclared(
            f"policy {policy.policy_id!r} does not permit pooling {missing}; "
            "each execution regime answers a different question"
        )
