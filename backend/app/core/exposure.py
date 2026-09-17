"""Cross-lane exposure coordination.

Two lanes can fire on the same underlying on the same morning. Opening both
because they carry different labels assumes they are independent risks, and
they are not: two long calls on NIFTY at the same expiry are one larger bet
with two names on it, and a long from one lane against a short from another can
be two positions paying two sets of costs to hold nothing.

So every requested exposure is compared against everything already open, the
relationship is classified, and the policy for that relationship must be
declared. An undeclared relationship refuses. That default is the whole point:
the failure mode this prevents is a combination nobody thought about being
allowed because nobody thought about it.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final, Iterable, Mapping


class Interaction(StrEnum):
    """How a requested exposure relates to one that already exists."""

    #: Different underlying entirely.
    UNRELATED = "unrelated"
    #: Same underlying, different expiry.
    SAME_UNDERLYING = "same_underlying"
    #: Same underlying and expiry, different strike or option type.
    SAME_EXPIRY = "same_expiry"
    #: Byte-for-byte the same contract.
    SAME_CONTRACT = "same_contract"


class ExposureVerdict(StrEnum):
    ALLOW = "allow"
    REFUSE = "refuse"


@dataclass(frozen=True)
class Exposure:
    """One position, or one request for a position.

    Contract identity is carried verbatim rather than reconstructed from a
    canonical name: a SENSEX option lives on BFO and an NIFTY option on NFO, and
    rebuilding ``"NFO:" + symbol`` is how a BSE position becomes unfindable.
    """

    lane_key: str
    underlying: str
    exchange: str
    tradingsymbol: str
    direction: str
    quantity: int
    expiry: str = ""
    strike: float = 0.0
    option_type: str = ""

    def _contract_key(self) -> tuple:
        return (self.exchange.upper(), self.tradingsymbol.upper())

    def interaction_with(self, other: "Exposure") -> Interaction:
        if self._contract_key() == other._contract_key():
            return Interaction.SAME_CONTRACT
        if self.underlying.upper() != other.underlying.upper():
            return Interaction.UNRELATED
        if self.expiry and self.expiry == other.expiry:
            return Interaction.SAME_EXPIRY
        return Interaction.SAME_UNDERLYING


@dataclass(frozen=True)
class ExposureDecision:
    allowed: bool
    reason: str = ""
    interaction: Interaction | None = None
    conflicting_lane: str = ""

    def __bool__(self) -> bool:  # pragma: no cover - convenience only
        return self.allowed


#: Declared policy, keyed by (interaction, same_direction). Anything absent
#: refuses. ``None`` marks a relationship that has been considered and
#: deliberately left undecided, so its refusal reads as a policy gap rather
#: than an oversight.
_POLICY: Final[Mapping[tuple[Interaction, bool], ExposureVerdict | None]] = {
    (Interaction.UNRELATED, True): ExposureVerdict.ALLOW,
    (Interaction.UNRELATED, False): ExposureVerdict.ALLOW,
    # Same contract, same side is adding to a position — averaging down by
    # another name, and prohibited outright.
    (Interaction.SAME_CONTRACT, True): ExposureVerdict.REFUSE,
    # Same contract, opposite side would net to nothing while paying two
    # spreads and two sets of charges, and it makes the position's true size
    # unreadable in the broker book.
    (Interaction.SAME_CONTRACT, False): ExposureVerdict.REFUSE,
    # Same underlying and expiry, same side: correlated near-duplicate risk.
    (Interaction.SAME_EXPIRY, True): ExposureVerdict.REFUSE,
    # Opposite sides on one expiry is a spread. It may well be sensible, but it
    # has never been specified, sized or costed, so it is not allowed by
    # accident.
    (Interaction.SAME_EXPIRY, False): None,
    (Interaction.SAME_UNDERLYING, True): ExposureVerdict.REFUSE,
    (Interaction.SAME_UNDERLYING, False): None,
}


class ExposureCoordinator:
    """Decides whether a requested exposure may join the open book."""

    def __init__(
        self,
        policy: Mapping[tuple[Interaction, bool], ExposureVerdict | None] | None = None,
    ) -> None:
        self._policy = dict(policy) if policy is not None else dict(_POLICY)

    def evaluate(
        self, requested: Exposure, existing: Iterable[Exposure]
    ) -> ExposureDecision:
        """First conflict wins, checked most-specific relationship first.

        Ordering matters for the message: told that a request conflicts with
        three positions, an operator acts on the closest one.
        """
        candidates = [
            (requested.interaction_with(held), held) for held in existing
        ]
        order = {
            Interaction.SAME_CONTRACT: 0,
            Interaction.SAME_EXPIRY: 1,
            Interaction.SAME_UNDERLYING: 2,
            Interaction.UNRELATED: 3,
        }
        candidates.sort(key=lambda pair: order[pair[0]])

        for interaction, held in candidates:
            same_direction = (
                requested.direction.strip().lower() == held.direction.strip().lower()
            )
            verdict = self._policy.get((interaction, same_direction), None)

            if verdict is ExposureVerdict.ALLOW:
                continue
            if verdict is ExposureVerdict.REFUSE:
                return ExposureDecision(
                    False,
                    f"EXPOSURE_CONFLICT_{interaction.value.upper()}",
                    interaction,
                    held.lane_key,
                )
            return ExposureDecision(
                False,
                "EXPOSURE_INTERACTION_UNDECLARED",
                interaction,
                held.lane_key,
            )

        return ExposureDecision(True)


def is_averaging_down(requested: Exposure, existing: Iterable[Exposure]) -> bool:
    """Would this request add to a position already open in the same direction?

    Automated averaging down and martingale sizing are prohibited. A future
    strategy that intentionally scales in must be a separately versioned rule
    with its own exposure limits, not a quiet consequence of two lanes agreeing.
    """
    for held in existing:
        if (
            requested.interaction_with(held) is Interaction.SAME_CONTRACT
            and requested.direction.strip().lower() == held.direction.strip().lower()
        ):
            return True
    return False
