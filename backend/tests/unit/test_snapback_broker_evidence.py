"""The broker's numbers outrank its adjectives, and outrank our intent.

A retried callback must not become a second fill, and a status of COMPLETE must
not turn 75 filled of 150 into a whole position.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.services.snapback_broker_evidence import (
    BrokerEventType,
    BrokerEvidenceError,
    BrokerRole,
    broker_event_id,
    build_broker_event,
    classify_fill,
)

NOW = datetime(2026, 9, 17, 9, 20, tzinfo=timezone.utc)
BROKER_TS = datetime(2026, 9, 17, 9, 19, 58, tzinfo=timezone.utc)
PROVENANCE = dict(
    runtime_sha="9a706f584", strategy_sha="5a13542", config_hash="6ecbeb53", rule_hash="e03ddf75",
)


def _event(**over):
    kw = dict(
        opportunity_id="OPP-1", broker="zerodha", role=BrokerRole.OPTION_ENTRY,
        event_type=BrokerEventType.BROKER_ACK, received_ts=NOW,
        evidence_class="BROKER_EXECUTED", broker_order_id="251117000123",
        broker_ts=BROKER_TS, **PROVENANCE,
    )
    kw.update(over)
    return build_broker_event(**kw)


# ─── idempotency ─────────────────────────────────────────────────────────────

def test_the_same_callback_twice_yields_one_id():
    a = _event()
    b = _event()

    assert a.event_id == b.event_id


def test_receive_time_does_not_enter_the_id():
    early = _event(received_ts=NOW)
    late = _event(received_ts=datetime(2026, 9, 17, 15, 30, tzinfo=timezone.utc))

    # A retry arrives later; that must not make it a different event.
    assert early.event_id == late.event_id


def test_a_different_fill_quantity_is_a_different_event():
    a = _event(event_type=BrokerEventType.PARTIAL_FILL, requested_quantity=150, filled_quantity=75)
    b = _event(event_type=BrokerEventType.PARTIAL_FILL, requested_quantity=150, filled_quantity=150)

    assert a.event_id != b.event_id


def test_a_different_order_is_a_different_event():
    assert _event(broker_order_id="A").event_id != _event(broker_order_id="B").event_id


def test_an_undeduplicatable_event_is_refused():
    """A random id would silently admit duplicates."""
    with pytest.raises(BrokerEvidenceError):
        broker_event_id(opportunity_id="OPP-1", broker="zerodha", event_type="BROKER_ACK")


def test_a_pre_broker_intent_keys_on_its_sequence():
    a = broker_event_id(opportunity_id="OPP-1", broker="zerodha",
                        event_type="ORDER_INTENT", intent_sequence=1)
    b = broker_event_id(opportunity_id="OPP-1", broker="zerodha",
                        event_type="ORDER_INTENT", intent_sequence=1)
    c = broker_event_id(opportunity_id="OPP-1", broker="zerodha",
                        event_type="ORDER_INTENT", intent_sequence=2)

    assert a == b != c


# ─── quantities beat status text ─────────────────────────────────────────────

def test_a_short_fill_is_partial_however_it_is_labelled():
    event = _event(event_type=BrokerEventType.FULL_FILL, broker_status="COMPLETE",
                   requested_quantity=150, filled_quantity=75)

    assert event.event_type == BrokerEventType.PARTIAL_FILL
    assert event.filled_quantity == 75
    assert event.pending_quantity == 75


def test_intent_is_never_normalised_up_to_the_request():
    event = _event(event_type=BrokerEventType.PARTIAL_FILL,
                   requested_quantity=150, filled_quantity=75)

    assert event.filled_quantity == 75
    assert event.filled_quantity != 150


def test_a_complete_fill_is_full():
    event = _event(event_type=BrokerEventType.PARTIAL_FILL,
                   requested_quantity=150, filled_quantity=150)

    assert event.event_type == BrokerEventType.FULL_FILL
    assert event.pending_quantity == 0


def test_an_overfill_is_still_full_not_negative_pending():
    event = _event(event_type=BrokerEventType.PARTIAL_FILL,
                   requested_quantity=150, filled_quantity=175)

    assert event.event_type == BrokerEventType.FULL_FILL
    assert event.pending_quantity == 0


def test_an_unknown_quantity_is_never_a_full_fill():
    assert classify_fill(requested_quantity=150, filled_quantity=None) == BrokerEventType.PARTIAL_FILL
    assert classify_fill(requested_quantity=None, filled_quantity=150) == BrokerEventType.PARTIAL_FILL
    assert classify_fill(requested_quantity=150, filled_quantity=0) == BrokerEventType.PARTIAL_FILL


# ─── refusals ────────────────────────────────────────────────────────────────

def test_an_unknown_role_is_refused():
    with pytest.raises(BrokerEvidenceError):
        _event(role="SOMETHING")


def test_an_unknown_event_type_is_refused():
    with pytest.raises(BrokerEvidenceError):
        _event(event_type="MAYBE_FILLED")


@pytest.mark.parametrize("field", ["runtime_sha", "strategy_sha", "config_hash", "rule_hash"])
def test_blank_provenance_is_refused(field):
    with pytest.raises(BrokerEvidenceError):
        _event(**{field: ""})


def test_an_unknown_evidence_class_is_refused():
    with pytest.raises(Exception):
        _event(evidence_class="REAL")


def test_both_clocks_are_kept():
    event = _event()

    assert event.broker_ts == BROKER_TS.isoformat()
    assert event.received_ts == NOW.isoformat()
    assert event.broker_ts != event.received_ts
