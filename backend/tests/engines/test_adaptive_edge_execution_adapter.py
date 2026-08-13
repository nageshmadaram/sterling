from dataclasses import dataclass

import pytest

from app.engines.adaptive_edge.execution_adapter import (
    BrokerTransport,
    CanonicalExecutionEvent,
    CanonicalExecutionStatus,
    CanonicalOrderIntent,
    ExecutionAdapter,
    IdempotencyRegistry,
)


@dataclass
class FakeTransport(BrokerTransport):
    result: str = "broker-order-1"
    calls: int = 0

    def submit(self, intent: CanonicalOrderIntent) -> str:
        self.calls += 1
        return self.result


def make_intent(**overrides):
    values = dict(
        order_intent_id="oi-1",
        selection_id="sel-1",
        instrument_id="NIFTY-CE",
        side="BUY",
        quantity=50,
        intent_version="v1",
        idempotency_key="idem-1",
        created_at="2026-08-14T03:45:00+00:00",
    )
    values.update(overrides)
    return CanonicalOrderIntent(**values)


def test_same_idempotency_key_does_not_submit_twice():
    transport = FakeTransport()
    adapter = ExecutionAdapter(transport)
    intent = make_intent()
    assert adapter.submit(intent) == adapter.submit(intent) == "broker-order-1"
    assert transport.calls == 1


def test_same_key_with_changed_intent_fails_closed():
    transport = FakeTransport()
    adapter = ExecutionAdapter(transport)
    adapter.submit(make_intent())
    with pytest.raises(ValueError, match="idempotency key reused"):
        adapter.submit(make_intent(quantity=100))
    assert transport.calls == 1


def test_invalid_order_intent_is_rejected_before_broker_call():
    transport = FakeTransport()
    with pytest.raises(ValueError, match="quantity must be positive"):
        ExecutionAdapter(transport).submit(make_intent(quantity=0))
    assert transport.calls == 0


def test_fill_event_requires_positive_quantity_and_price():
    event = CanonicalExecutionEvent(
        execution_event_id="ex-1", order_intent_id="oi-1",
        event_type=CanonicalExecutionStatus.FILLED,
        event_time="2026-08-14T03:45:02+00:00",
        filled_quantity=50, fill_price=120.5,
    )
    assert ExecutionAdapter.normalize_event(event) == event


def test_non_fill_event_cannot_carry_fill_data():
    event = CanonicalExecutionEvent(
        execution_event_id="ex-1", order_intent_id="oi-1",
        event_type=CanonicalExecutionStatus.ACKNOWLEDGED,
        event_time="2026-08-14T03:45:02+00:00", filled_quantity=1,
    )
    with pytest.raises(ValueError, match="non-fill events cannot carry fill data"):
        ExecutionAdapter.normalize_event(event)


def test_unknown_status_remains_unknown():
    event = CanonicalExecutionEvent(
        execution_event_id="ex-1", order_intent_id="oi-1",
        event_type=CanonicalExecutionStatus.UNKNOWN,
        event_time="2026-08-14T03:45:02+00:00",
    )
    assert ExecutionAdapter.normalize_event(event).event_type is CanonicalExecutionStatus.UNKNOWN


def test_invalid_evidence_class_fails_closed():
    event = CanonicalExecutionEvent(
        execution_event_id="ex-1", order_intent_id="oi-1",
        event_type=CanonicalExecutionStatus.UNKNOWN,
        event_time="2026-08-14T03:45:02+00:00", evidence_class="GUESS",
    )
    with pytest.raises(ValueError, match="invalid evidence_class"):
        ExecutionAdapter.normalize_event(event)


def test_registry_conflict_is_detected():
    registry = IdempotencyRegistry()
    registry.record("idem-1", "fingerprint-a", "broker-order-1")
    with pytest.raises(ValueError, match="idempotency key reused"):
        registry.check("idem-1", "fingerprint-b")
