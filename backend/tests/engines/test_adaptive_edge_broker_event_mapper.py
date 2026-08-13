import pytest

from app.engines.adaptive_edge.broker_event_mapper import (
    BrokerEventMapper,
    BrokerExecutionEvent,
)
from app.engines.adaptive_edge.execution_adapter import CanonicalExecutionStatus


def event(status: str, **kwargs):
    return BrokerExecutionEvent(
        broker_event_id="broker-event-1",
        order_intent_id="oi-1",
        broker_status=status,
        event_time="2026-08-14T03:45:02+00:00",
        **kwargs,
    )


def test_declared_status_is_mapped_explicitly():
    mapper = BrokerEventMapper({"COMPLETE": CanonicalExecutionStatus.FILLED})
    result = mapper.map(event("COMPLETE", filled_quantity=50, fill_price=120.5))
    assert result.event_type is CanonicalExecutionStatus.FILLED
    assert result.filled_quantity == 50
    assert result.fill_price == 120.5


def test_unknown_provider_status_fails_closed_to_unknown():
    mapper = BrokerEventMapper({"COMPLETE": CanonicalExecutionStatus.FILLED})
    result = mapper.map(event("UNDOCUMENTED_STATUS"))
    assert result.event_type is CanonicalExecutionStatus.UNKNOWN


def test_unknown_status_with_fill_data_is_still_validated():
    mapper = BrokerEventMapper({})
    with pytest.raises(ValueError, match="fill events require"):
        mapper.map(event("UNDOCUMENTED_STATUS", filled_quantity=50, fill_price=120.5))
