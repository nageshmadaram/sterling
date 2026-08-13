from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from hashlib import sha256
from typing import Protocol


class CanonicalExecutionStatus(str, Enum):
    SUBMITTED = "SUBMITTED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    REJECTED = "REJECTED"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    EXPIRED = "EXPIRED"
    AMENDED = "AMENDED"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class CanonicalOrderIntent:
    order_intent_id: str
    selection_id: str
    instrument_id: str
    side: str
    quantity: int
    intent_version: str
    idempotency_key: str
    created_at: str

    def fingerprint(self) -> str:
        value = "|".join((self.order_intent_id, self.selection_id, self.instrument_id, self.side, str(self.quantity), self.intent_version))
        return sha256(value.encode("utf-8")).hexdigest()

    def validate(self) -> None:
        for field in ("order_intent_id", "selection_id", "instrument_id", "side", "intent_version", "idempotency_key", "created_at"):
            if not getattr(self, field):
                raise ValueError(f"{field} is required")
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")
        if self.side not in {"BUY", "SELL"}:
            raise ValueError("side must be BUY or SELL")


@dataclass(frozen=True)
class CanonicalExecutionEvent:
    execution_event_id: str
    order_intent_id: str
    event_type: CanonicalExecutionStatus
    event_time: str
    broker_reference: str | None = None
    filled_quantity: int = 0
    fill_price: float | None = None
    evidence_class: str = "OBSERVED"
    receipt_time: str | None = None

    def validate(self) -> None:
        if not self.execution_event_id or not self.order_intent_id or not self.event_time:
            raise ValueError("execution identity and event_time are required")
        if self.filled_quantity < 0:
            raise ValueError("filled_quantity cannot be negative")
        fills = {CanonicalExecutionStatus.PARTIALLY_FILLED, CanonicalExecutionStatus.FILLED}
        if self.event_type in fills:
            if self.filled_quantity <= 0 or self.fill_price is None:
                raise ValueError("fill events require positive quantity and fill_price")
        elif self.filled_quantity != 0 or self.fill_price is not None:
            raise ValueError("non-fill events cannot carry fill data")
        if self.evidence_class not in {"OBSERVED", "RECONSTRUCTED", "MODELED", "ASSUMED", "UNKNOWN"}:
            raise ValueError("invalid evidence_class")


class BrokerTransport(Protocol):
    def submit(self, intent: CanonicalOrderIntent) -> str: ...


@dataclass(frozen=True)
class _IdempotencyRecord:
    fingerprint: str
    result: str


class IdempotencyRegistry:
    def __init__(self) -> None:
        self._records: dict[str, _IdempotencyRecord] = {}

    def check(self, key: str, fingerprint: str) -> str | None:
        record = self._records.get(key)
        if record is None:
            return None
        if record.fingerprint != fingerprint:
            raise ValueError("idempotency key reused with different intent")
        return record.result

    def record(self, key: str, fingerprint: str, result: str) -> None:
        existing = self._records.get(key)
        if existing is not None and existing.fingerprint != fingerprint:
            raise ValueError("idempotency key reused with different intent")
        self._records[key] = _IdempotencyRecord(fingerprint, result)


class ExecutionAdapter:
    def __init__(self, transport: BrokerTransport, registry: IdempotencyRegistry | None = None) -> None:
        self._transport = transport
        self._registry = registry or IdempotencyRegistry()

    def submit(self, intent: CanonicalOrderIntent) -> str:
        intent.validate()
        fingerprint = intent.fingerprint()
        prior = self._registry.check(intent.idempotency_key, fingerprint)
        if prior is not None:
            return prior
        result = self._transport.submit(intent)
        self._registry.record(intent.idempotency_key, fingerprint, result)
        return result

    @staticmethod
    def normalize_event(event: CanonicalExecutionEvent) -> CanonicalExecutionEvent:
        event.validate()
        return event
