"""The declared vocabulary of evidence events, and the order they may occur in.

Every decision produces evidence, and the set of things that can be said about
a trade is closed. Without a declared vocabulary each writer invents its own
spelling, and a report that counts ``NO_FILL`` silently misses the rows someone
wrote as ``NOFILL`` or ``no_fill`` — which reads as a cleaner execution record
than the one that actually happened.

Failures are first-class here. A refusal, a missing contract and a stale quote
are events in the same lifecycle as a fill, not absences from it.
"""
from __future__ import annotations

from enum import StrEnum
from typing import Final, Mapping


class EvidenceEvent(StrEnum):
    """Everything that may be recorded about one opportunity."""

    # observation
    OPPORTUNITY_OBSERVED = "OPPORTUNITY_OBSERVED"
    SIGNAL_EVALUATED = "SIGNAL_EVALUATED"

    # exactly one of these follows SIGNAL_EVALUATED
    NO_SIGNAL = "NO_SIGNAL"
    SIGNAL_FOUND = "SIGNAL_FOUND"
    SIGNAL_REJECTED = "SIGNAL_REJECTED"

    # only after SIGNAL_FOUND
    CONTRACT_SELECTION = "CONTRACT_SELECTION"
    CAPACITY_EVALUATION = "CAPACITY_EVALUATION"
    EXECUTION_EVALUATION = "EXECUTION_EVALUATION"

    # what execution produced
    NO_FILL = "NO_FILL"
    PAPER_ENTRY = "PAPER_ENTRY"
    SHADOW_INTENT = "SHADOW_INTENT"
    BROKER_INTENT = "BROKER_INTENT"

    # only once a position exists
    POSITION_OPEN = "POSITION_OPEN"
    POSITION_MARK = "POSITION_MARK"
    PROTECTION_STATUS = "PROTECTION_STATUS"
    EXIT_DECISION = "EXIT_DECISION"
    EXIT_OBSERVED = "EXIT_OBSERVED"
    RECONCILED = "RECONCILED"

    #: A programming defect. Never folded into NO_SIGNAL or NO_TRADE — a bug
    #: recorded as an ordinary refusal disappears into the denominator and
    #: makes the strategy look more selective than it is.
    EVIDENCE_ERROR = "EVIDENCE_ERROR"


#: Events that end an opportunity's life without a position.
TERMINAL_WITHOUT_POSITION: Final[frozenset[EvidenceEvent]] = frozenset({
    EvidenceEvent.NO_SIGNAL,
    EvidenceEvent.SIGNAL_REJECTED,
    EvidenceEvent.NO_FILL,
})

#: Events that mean a position exists or existed.
POSITION_EVENTS: Final[frozenset[EvidenceEvent]] = frozenset({
    EvidenceEvent.POSITION_OPEN,
    EvidenceEvent.POSITION_MARK,
    EvidenceEvent.PROTECTION_STATUS,
    EvidenceEvent.EXIT_DECISION,
    EvidenceEvent.EXIT_OBSERVED,
    EvidenceEvent.RECONCILED,
})

#: Which events may legally follow which. Used to check a recorded sequence
#: after the fact, never to block a write: an impossible sequence is itself
#: evidence of a defect and must be recorded, then reported.
_ALLOWED_NEXT: Final[Mapping[EvidenceEvent, frozenset[EvidenceEvent]]] = {
    EvidenceEvent.OPPORTUNITY_OBSERVED: frozenset({EvidenceEvent.SIGNAL_EVALUATED}),
    EvidenceEvent.SIGNAL_EVALUATED: frozenset({
        EvidenceEvent.NO_SIGNAL,
        EvidenceEvent.SIGNAL_FOUND,
        EvidenceEvent.SIGNAL_REJECTED,
    }),
    EvidenceEvent.SIGNAL_FOUND: frozenset({EvidenceEvent.CONTRACT_SELECTION}),
    EvidenceEvent.CONTRACT_SELECTION: frozenset({
        EvidenceEvent.CAPACITY_EVALUATION,
        EvidenceEvent.NO_FILL,
    }),
    EvidenceEvent.CAPACITY_EVALUATION: frozenset({
        EvidenceEvent.EXECUTION_EVALUATION,
        EvidenceEvent.NO_FILL,
    }),
    EvidenceEvent.EXECUTION_EVALUATION: frozenset({
        EvidenceEvent.NO_FILL,
        EvidenceEvent.PAPER_ENTRY,
        EvidenceEvent.SHADOW_INTENT,
        EvidenceEvent.BROKER_INTENT,
    }),
    EvidenceEvent.PAPER_ENTRY: frozenset({EvidenceEvent.POSITION_OPEN}),
    EvidenceEvent.SHADOW_INTENT: frozenset({EvidenceEvent.POSITION_OPEN, EvidenceEvent.NO_FILL}),
    EvidenceEvent.BROKER_INTENT: frozenset({EvidenceEvent.POSITION_OPEN, EvidenceEvent.NO_FILL}),
    EvidenceEvent.POSITION_OPEN: frozenset({
        EvidenceEvent.POSITION_MARK,
        EvidenceEvent.PROTECTION_STATUS,
        EvidenceEvent.EXIT_DECISION,
        EvidenceEvent.RECONCILED,
    }),
    EvidenceEvent.POSITION_MARK: frozenset({
        EvidenceEvent.POSITION_MARK,
        EvidenceEvent.PROTECTION_STATUS,
        EvidenceEvent.EXIT_DECISION,
        EvidenceEvent.RECONCILED,
    }),
    EvidenceEvent.PROTECTION_STATUS: frozenset({
        EvidenceEvent.POSITION_MARK,
        EvidenceEvent.PROTECTION_STATUS,
        EvidenceEvent.EXIT_DECISION,
        EvidenceEvent.RECONCILED,
    }),
    EvidenceEvent.EXIT_DECISION: frozenset({
        EvidenceEvent.EXIT_OBSERVED,
        EvidenceEvent.RECONCILED,
    }),
    EvidenceEvent.EXIT_OBSERVED: frozenset({EvidenceEvent.RECONCILED}),
    EvidenceEvent.RECONCILED: frozenset(),
}


class UnknownEvidenceEvent(ValueError):
    """An event name outside the declared vocabulary."""


def canonical_event(value: str | EvidenceEvent) -> EvidenceEvent:
    """Map a written event name onto the enum, or refuse.

    Refusing matters more than it looks: a misspelled event silently vanishes
    from every report that filters on the correct spelling.
    """
    if isinstance(value, EvidenceEvent):
        return value
    key = str(value or "").strip().upper()
    try:
        return EvidenceEvent(key)
    except ValueError:
        raise UnknownEvidenceEvent(f"unknown evidence event {value!r}") from None


def may_follow(previous: str | EvidenceEvent, nxt: str | EvidenceEvent) -> bool:
    """Is ``nxt`` a legal successor of ``previous``?"""
    prev = canonical_event(previous)
    return canonical_event(nxt) in _ALLOWED_NEXT.get(prev, frozenset())


def sequence_defects(events: list[str | EvidenceEvent]) -> list[str]:
    """Every illegal transition in a recorded sequence.

    Reports all of them rather than the first: a lifecycle with three gaps is a
    different problem from one with a single misordered pair.
    """
    defects: list[str] = []
    try:
        resolved = [canonical_event(e) for e in events]
    except UnknownEvidenceEvent as exc:
        return [str(exc)]
    if resolved and resolved[0] is not EvidenceEvent.OPPORTUNITY_OBSERVED:
        defects.append(
            f"sequence starts at {resolved[0].value}, not OPPORTUNITY_OBSERVED"
        )
    for a, b in zip(resolved, resolved[1:]):
        if not may_follow(a, b):
            defects.append(f"{a.value} -> {b.value} is not a legal transition")
    return defects
