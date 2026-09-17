"""What a number is allowed to prove.

Sterling keeps producing plausible numbers from sources that cannot support the
conclusion drawn from them. The quarantined ``snapback_observed_replay`` is the
clearest case: it was named and documented as an observed-market replay while
substituting modeled prices for missing quotes, so a modeled result could be read
as execution evidence by anyone who trusted the filename.

The fix is to stop treating provenance as a convention. Every economic row
carries its class, the classes are totally ordered, and a claim declares the
class it requires. A row may always be used for a weaker claim than its class
supports; it may never be used for a stronger one, and there is no code path that
raises a row's class.

    MODELLED         prices came from a model      research only
    OBSERVED_MARKET  real quotes, no broker        execution-reality research
    BROKER_SHADOW    broker accepted, not filled   operational validation
    BROKER_EXECUTED  real fill at a real price     prospective economic evidence

The ordering is the whole point: promotion reads BROKER_EXECUTED, so no amount of
modeled or shadow evidence can accumulate into a promotion, however large the
sample gets.
"""

from __future__ import annotations

from typing import Iterable, Optional

__all__ = [
    "EvidenceClass",
    "EvidenceClassError",
    "MODELLED",
    "OBSERVED_MARKET",
    "BROKER_SHADOW",
    "BROKER_EXECUTED",
    "EVIDENCE_CLASSES",
    "rank",
    "parse",
    "satisfies",
    "require",
    "weakest",
]

MODELLED = "MODELLED"
OBSERVED_MARKET = "OBSERVED_MARKET"
BROKER_SHADOW = "BROKER_SHADOW"
BROKER_EXECUTED = "BROKER_EXECUTED"

#: Strictly increasing strength. Index is the rank; never reorder.
EVIDENCE_CLASSES = (MODELLED, OBSERVED_MARKET, BROKER_SHADOW, BROKER_EXECUTED)

_RANK = {name: i for i, name in enumerate(EVIDENCE_CLASSES)}


class EvidenceClassError(ValueError):
    """An unknown class, or a claim asking for more than its evidence supports."""


class EvidenceClass:
    """Namespace of the constants, for call sites that prefer a qualified name."""

    MODELLED = MODELLED
    OBSERVED_MARKET = OBSERVED_MARKET
    BROKER_SHADOW = BROKER_SHADOW
    BROKER_EXECUTED = BROKER_EXECUTED
    ALL = EVIDENCE_CLASSES


def parse(value: Optional[str]) -> str:
    """Normalise a stored class, refusing anything not on the list.

    Missing is refused rather than defaulted. A row whose provenance was never
    recorded is not evidence, and defaulting it to the weakest class would make
    it silently usable for research — which is how the fail-open authority bug
    in runtime-1.4 worked.
    """
    if value is None:
        raise EvidenceClassError("evidence class is missing; a row without provenance is not evidence")
    name = str(value).strip().upper()
    if name not in _RANK:
        raise EvidenceClassError(f"unknown evidence class {value!r}; expected one of {', '.join(EVIDENCE_CLASSES)}")
    return name


def rank(value: Optional[str]) -> int:
    """Position in the ordering. Higher supports strictly more."""
    return _RANK[parse(value)]


def satisfies(actual: Optional[str], required: Optional[str]) -> bool:
    """True when ``actual`` evidence is strong enough for a ``required`` claim."""
    return rank(actual) >= rank(required)


def require(actual: Optional[str], required: Optional[str], *, context: str = "") -> str:
    """Return ``actual``, or raise if it cannot support ``required``."""
    if not satisfies(actual, required):
        where = f" ({context})" if context else ""
        raise EvidenceClassError(
            f"{parse(actual)} evidence cannot support a claim requiring {parse(required)}{where}"
        )
    return parse(actual)


def weakest(values: Iterable[Optional[str]]) -> str:
    """The class of a conclusion drawn from several rows.

    A trade is only as provable as its least provable leg: a real option fill
    hedged against a modeled future is not execution evidence. Refuses an empty
    sequence rather than inventing a class for a conclusion with no inputs.
    """
    ranks = [rank(v) for v in values]
    if not ranks:
        raise EvidenceClassError("no evidence supplied; a conclusion from nothing has no class")
    return EVIDENCE_CLASSES[min(ranks)]
