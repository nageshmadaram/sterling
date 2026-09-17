"""What the broker actually said, recorded once however many times it says it.

Two rules decide almost everything here.

**The broker outranks the local journal.** When Sterling intended 150 and the
broker reports 75 filled, the truth is 75 and 75 remaining. Normalising that back
to 150 because the order intended 150 is how a position becomes half the size
Sterling believes it is holding, with a hedge sized for the belief.

**A callback may arrive twice.** Brokers retry, sockets reconnect, and pollers
overlap a push. Deduplication therefore cannot rest on a random UUID minted at
receive time, because the second copy would mint a different one and land as a
second fill. The event id is derived from the broker's own facts, so replaying a
callback yields ALREADY_RECORDED rather than a duplicate position.

Status text is never trusted over quantities. A broker that says COMPLETE while
reporting a filled quantity below the requested one is reporting a partial fill,
and the quantities win.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any, Optional

__all__ = [
    "BROKER_EVIDENCE_SCHEMA_VERSION",
    "BrokerRole",
    "BrokerEventType",
    "BrokerEvidenceError",
    "BrokerEvidenceEvent",
    "broker_event_id",
    "classify_fill",
    "build_broker_event",
]

BROKER_EVIDENCE_SCHEMA_VERSION = "1"


class BrokerEvidenceError(ValueError):
    """The broker event cannot be recorded truthfully."""


class BrokerRole:
    OPTION_ENTRY = "OPTION_ENTRY"
    HEDGE_ENTRY = "HEDGE_ENTRY"
    PROTECTION = "PROTECTION"
    OPTION_EXIT = "OPTION_EXIT"
    HEDGE_EXIT = "HEDGE_EXIT"

    ALL = frozenset({OPTION_ENTRY, HEDGE_ENTRY, PROTECTION, OPTION_EXIT, HEDGE_EXIT})


class BrokerEventType:
    ORDER_INTENT = "ORDER_INTENT"
    ORDER_SUBMITTED = "ORDER_SUBMITTED"
    BROKER_ACK = "BROKER_ACK"
    PARTIAL_FILL = "PARTIAL_FILL"
    FULL_FILL = "FULL_FILL"
    REJECTED = "REJECTED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"
    PROTECTION_SUBMITTED = "PROTECTION_SUBMITTED"
    PROTECTION_ACTIVE = "PROTECTION_ACTIVE"
    RECONCILED = "RECONCILED"

    ALL = frozenset({
        ORDER_INTENT, ORDER_SUBMITTED, BROKER_ACK, PARTIAL_FILL, FULL_FILL,
        REJECTED, CANCEL_REQUESTED, CANCELLED, PROTECTION_SUBMITTED,
        PROTECTION_ACTIVE, RECONCILED,
    })

    #: Events that assert something about quantity actually traded.
    FILLS = frozenset({PARTIAL_FILL, FULL_FILL})


@dataclass(frozen=True)
class BrokerEvidenceEvent:
    """One broker fact. Immutable, deduplicated on the broker's own data."""

    schema_version: str

    event_id: str
    opportunity_id: str

    broker: str

    broker_order_id: Optional[str]
    parent_order_id: Optional[str]

    instrument_token: Optional[int]
    tradingsymbol: Optional[str]

    role: str
    event_type: str

    requested_quantity: Optional[int]
    filled_quantity: Optional[int]
    pending_quantity: Optional[int]

    requested_price: Optional[float]
    average_fill_price: Optional[float]

    broker_status: Optional[str]

    broker_ts: Optional[str]
    received_ts: str

    evidence_class: str

    runtime_sha: str
    strategy_sha: str
    config_hash: str
    rule_hash: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def broker_event_id(
    *,
    opportunity_id: str,
    broker: str,
    event_type: str,
    broker_order_id: Optional[str] = None,
    source_event_id: Optional[str] = None,
    broker_ts: Optional[datetime] = None,
    filled_quantity: Optional[int] = None,
    broker_status: Optional[str] = None,
    intent_sequence: Optional[int] = None,
) -> str:
    """Derive an id from the broker's facts, never from receive time.

    Before a broker order id exists, the event is one of Sterling's own intents,
    so it is keyed on the opportunity and a caller-supplied intent sequence. That
    sequence must be stable across a restart — a counter reset to zero would make
    a fresh intent collide with an old one.
    """
    if broker_order_id is None and source_event_id is None and intent_sequence is None:
        raise BrokerEvidenceError(
            "an event with no broker order id, no source event id and no intent "
            "sequence cannot be deduplicated; a random id would admit duplicates"
        )

    payload = {
        "opportunity_id": opportunity_id,
        "broker": broker,
        "event_type": event_type,
        "broker_order_id": broker_order_id,
        "source_event_id": source_event_id,
        "broker_ts": broker_ts.isoformat() if isinstance(broker_ts, datetime) else broker_ts,
        "filled_quantity": filled_quantity,
        "broker_status": broker_status,
        "intent_sequence": intent_sequence,
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def classify_fill(*, requested_quantity: Optional[int], filled_quantity: Optional[int]) -> str:
    """PARTIAL_FILL or FULL_FILL, decided on quantities rather than status text.

    An unknown quantity is never a full fill. A broker that cannot tell us how
    much traded has not told us the trade completed.
    """
    if filled_quantity is None or requested_quantity is None:
        return BrokerEventType.PARTIAL_FILL
    if int(filled_quantity) <= 0:
        return BrokerEventType.PARTIAL_FILL
    if int(filled_quantity) >= int(requested_quantity):
        return BrokerEventType.FULL_FILL
    return BrokerEventType.PARTIAL_FILL


def build_broker_event(
    *,
    opportunity_id: str,
    broker: str,
    role: str,
    event_type: str,
    received_ts: datetime,
    evidence_class: str,
    runtime_sha: str,
    strategy_sha: str,
    config_hash: str,
    rule_hash: str,
    broker_order_id: Optional[str] = None,
    parent_order_id: Optional[str] = None,
    source_event_id: Optional[str] = None,
    intent_sequence: Optional[int] = None,
    instrument_token: Optional[int] = None,
    tradingsymbol: Optional[str] = None,
    requested_quantity: Optional[int] = None,
    filled_quantity: Optional[int] = None,
    requested_price: Optional[float] = None,
    average_fill_price: Optional[float] = None,
    broker_status: Optional[str] = None,
    broker_ts: Optional[datetime] = None,
) -> BrokerEvidenceEvent:
    """Assemble one broker event, reclassifying fills against the quantities."""
    from app.services.snapback_evidence_class import parse as parse_evidence_class

    if role not in BrokerRole.ALL:
        raise BrokerEvidenceError(f"unknown broker role {role!r}")
    if event_type not in BrokerEventType.ALL:
        raise BrokerEvidenceError(f"unknown broker event type {event_type!r}")

    for name, value in (("runtime_sha", runtime_sha), ("strategy_sha", strategy_sha),
                        ("config_hash", config_hash), ("rule_hash", rule_hash)):
        if not value:
            raise BrokerEvidenceError(f"{name} is required; an event without provenance proves nothing")

    # The broker's numbers overrule its adjectives, and overrule our intent.
    if event_type in BrokerEventType.FILLS:
        event_type = classify_fill(
            requested_quantity=requested_quantity, filled_quantity=filled_quantity,
        )

    pending = None
    if requested_quantity is not None and filled_quantity is not None:
        pending = max(0, int(requested_quantity) - int(filled_quantity))

    return BrokerEvidenceEvent(
        schema_version=BROKER_EVIDENCE_SCHEMA_VERSION,
        event_id=broker_event_id(
            opportunity_id=opportunity_id, broker=broker, event_type=event_type,
            broker_order_id=broker_order_id, source_event_id=source_event_id,
            broker_ts=broker_ts, filled_quantity=filled_quantity,
            broker_status=broker_status, intent_sequence=intent_sequence,
        ),
        opportunity_id=opportunity_id,
        broker=broker,
        broker_order_id=broker_order_id,
        parent_order_id=parent_order_id,
        instrument_token=int(instrument_token) if instrument_token is not None else None,
        tradingsymbol=tradingsymbol,
        role=role,
        event_type=event_type,
        requested_quantity=int(requested_quantity) if requested_quantity is not None else None,
        filled_quantity=int(filled_quantity) if filled_quantity is not None else None,
        pending_quantity=pending,
        requested_price=float(requested_price) if requested_price is not None else None,
        average_fill_price=float(average_fill_price) if average_fill_price is not None else None,
        broker_status=broker_status,
        broker_ts=broker_ts.isoformat() if isinstance(broker_ts, datetime) else broker_ts,
        received_ts=received_ts.isoformat(),
        evidence_class=parse_evidence_class(evidence_class),
        runtime_sha=runtime_sha,
        strategy_sha=strategy_sha,
        config_hash=config_hash,
        rule_hash=rule_hash,
    )
