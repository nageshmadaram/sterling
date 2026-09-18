"""Evidence classes, and the rule about which rows may reach a promotion gate.

Not all evidence is the same kind of claim. A modelled option price, a replayed
tape, a paper fill and a real broker fill answer different questions, and
pooling them produces a number that describes nothing. So every authoritative
row carries its class, and only explicitly authorised classes enter promotion
statistics.

The second rule here is about rows written before the five-mode taxonomy
existed. Those rows are kept verbatim — an evidence store is append-only, and
back-filling a lane label onto a row whose lane nobody recorded would invent
the attribution. They are simply not eligible for any lane's gate.
"""
from __future__ import annotations

from enum import StrEnum
from typing import Any, Final, Iterable, Mapping


class EvidenceClass(StrEnum):
    """How a row was produced."""

    #: Prices or fills derived from a model rather than observed.
    MODELLED = "modelled"
    #: Replayed against a recorded tape.
    REPLAY = "replay"
    #: Simulated fills against live observed quotes and depth.
    PAPER = "paper"
    #: A real intent priced against the live book, but never sent.
    SHADOW = "shadow"
    #: A real broker order with a real fill.
    BROKER = "broker"
    #: Produced by the release acceptance harness.
    ACCEPTANCE = "acceptance"


#: Classes that may contribute to a forward economic gate. Modelled and replay
#: evidence are research inputs: they cannot show whether a trade was fillable,
#: and a gate built on them measures the model, not the market. Acceptance rows
#: certify the runtime and say nothing about strategy profitability.
PROMOTABLE_CLASSES: Final[frozenset[EvidenceClass]] = frozenset(
    {EvidenceClass.PAPER, EvidenceClass.SHADOW, EvidenceClass.BROKER}
)

#: Sentinel written into the lane columns of rows that predate the taxonomy.
#: Empty rather than a word, so a report that forgets to filter shows a blank
#: instead of a plausible-looking lane name.
UNATTRIBUTED_LANE: Final[str] = ""


class EvidenceAttributionError(ValueError):
    """A row cannot be attributed to a lane, so it cannot be counted in one."""


def is_promotable_class(value: str | EvidenceClass | None) -> bool:
    """May a row of this class reach a promotion gate?

    An unknown or missing class is not promotable. A row that cannot say how it
    was produced is not evidence of anything.
    """
    if value is None:
        return False
    try:
        return EvidenceClass(str(value)) in PROMOTABLE_CLASSES
    except ValueError:
        return False


def row_lane(row: Mapping[str, Any]) -> str | None:
    """The lane a row belongs to, or ``None`` when it predates the taxonomy."""
    lane = str(row.get("lane_key") or UNATTRIBUTED_LANE).strip()
    return lane or None


def eligible_for_lane(
    row: Mapping[str, Any],
    lane_key: str,
    *,
    allowed_classes: Iterable[EvidenceClass] | None = None,
    required_vehicle: str | None = None,
) -> bool:
    """May ``row`` be counted toward ``lane_key``'s economic gate?

    Every condition below has produced a real overstatement somewhere:

    * a row from another lane pooled in to reach a trade count;
    * a legacy row with no lane label counted as "probably this one";
    * a non-authoritative row counted because nobody checked the flag;
    * a modelled row counted as though the fill had been observed;
    * an option-buying row counted toward a futures challenger, because the
      lane key matched and nobody compared the execution vehicle.

    ``required_vehicle`` is opt-in so existing single-vehicle callers keep
    working, but once supplied a row that declares no vehicle is refused: the
    absence of a vehicle is not evidence that it was the right one.
    """
    allowed = frozenset(allowed_classes) if allowed_classes else PROMOTABLE_CLASSES
    if row_lane(row) != lane_key:
        return False
    if required_vehicle is not None:
        declared = str(row.get("execution_vehicle") or "").strip().upper()
        if declared != str(required_vehicle).strip().upper():
            return False
    if not int(row.get("authoritative") or 0):
        return False
    try:
        return EvidenceClass(str(row.get("evidence_class") or "")) in allowed
    except ValueError:
        return False


def partition_by_lane(
    rows: Iterable[Mapping[str, Any]],
) -> tuple[dict[str, list[Mapping[str, Any]]], list[Mapping[str, Any]]]:
    """Split rows into per-lane buckets plus the unattributed remainder.

    The remainder is returned rather than dropped: a report must be able to say
    "and 412 older rows belong to no lane", which is a fact about coverage. A
    silent drop makes an incomplete sample look complete.
    """
    lanes: dict[str, list[Mapping[str, Any]]] = {}
    unattributed: list[Mapping[str, Any]] = []
    for row in rows:
        lane = row_lane(row)
        if lane is None:
            unattributed.append(row)
        else:
            lanes.setdefault(lane, []).append(row)
    return lanes, unattributed
