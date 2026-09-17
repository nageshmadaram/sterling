"""The durable intent record: idempotency, recovery and protection state."""
from __future__ import annotations

import pytest

from app.core.horizon import HorizonMode
from app.core.trade_intent import (
    TERMINAL_STATES,
    DuplicateIntent,
    IntentError,
    IntentState,
    IntentStore,
    ProtectionState,
    TradeIntent,
    make_intent_id,
)


def _intent(**over) -> TradeIntent:
    base = dict(
        strategy_id="snapback",
        strategy_version="snapback_core_v1",
        mode=HorizonMode.SWING,
        mode_version="snapback_swing_v1",
        opportunity_id="OPP-1",
        instrument_token=12345,
        exchange="NFO",
        tradingsymbol="NIFTY26SEP24000CE",
        side="BUY",
        quantity=75,
        order_type="LIMIT",
        limit_price=120.5,
        protection_required=True,
        max_loss_budget=9000.0,
        horizon_plan_id="PLAN-1",
        config_hash="cfg123",
        runtime_sha="d" * 40,
        action="entry",
    )
    base.update(over)
    base.setdefault(
        "intent_id",
        make_intent_id(
            strategy_id=base["strategy_id"],
            mode=base["mode"],
            opportunity_id=base["opportunity_id"],
            instrument_token=base["instrument_token"],
            action=base["action"],
        ),
    )
    return TradeIntent(**base)


@pytest.fixture()
def store(tmp_path) -> IntentStore:
    return IntentStore(tmp_path / "intents.db")


# ── validation ────────────────────────────────────────────────────────────


def test_an_intent_carries_its_lane_and_horizon():
    intent = _intent()
    assert intent.lane_key == "snapback:swing"
    assert intent.horizon_plan_id == "PLAN-1"


def test_an_intent_without_a_horizon_plan_is_refused():
    """A position with no time budget has no hard exit."""
    with pytest.raises(IntentError):
        _intent(horizon_plan_id="")


def test_an_intent_without_a_loss_budget_is_refused():
    with pytest.raises(IntentError):
        _intent(max_loss_budget=0.0)


def test_a_limit_order_needs_a_price():
    with pytest.raises(IntentError):
        _intent(order_type="LIMIT", limit_price=None)


@pytest.mark.parametrize("bad", [{"quantity": 0}, {"side": "HOLD"}, {"action": "roll"}])
def test_malformed_intents_are_refused(bad):
    with pytest.raises(IntentError):
        _intent(**bad)


def test_the_mode_must_already_be_canonical():
    with pytest.raises(IntentError):
        _intent(mode="swing")


def test_exit_is_a_declared_action_not_inferred_from_side():
    """A sell that closes a long and a sell that opens a short differ."""
    entry = _intent(side="BUY", action="entry")
    exit_ = _intent(side="SELL", action="exit")
    assert entry.uniqueness_slot != exit_.uniqueness_slot


# ── idempotency ───────────────────────────────────────────────────────────


def test_the_intent_id_is_deterministic_from_the_slot():
    assert _intent().intent_id == _intent().intent_id


def test_reserving_twice_returns_the_same_record(store):
    first = store.reserve(_intent())
    second = store.reserve(_intent())
    assert second.intent.intent_id == first.intent.intent_id
    assert len(store.for_lane("snapback:swing")) == 1


def test_a_retry_after_submission_does_not_create_a_second_order(store):
    stored = store.reserve(_intent())
    store.record_submission(stored.intent.intent_id)
    again = store.reserve(_intent())
    assert again.state is IntentState.SUBMITTED_UNKNOWN
    assert again.safe_to_submit is False
    assert len(store.for_lane("snapback:swing")) == 1


def test_a_different_payload_in_the_same_slot_is_an_error(store):
    """One of the two is wrong; guessing would send the wrong order."""
    store.reserve(_intent())
    with pytest.raises(DuplicateIntent):
        store.reserve(_intent(quantity=150))


def test_two_lanes_on_one_instrument_get_separate_slots(store):
    store.reserve(_intent())
    other = _intent(
        strategy_id="supertrend",
        strategy_version="supertrend_core_v1",
        mode_version="supertrend_swing_v1",
    )
    store.reserve(other)
    assert len(store.for_lane("snapback:swing")) == 1
    assert len(store.for_lane("supertrend:swing")) == 1


def test_two_modes_of_one_strategy_get_separate_slots(store):
    store.reserve(_intent())
    store.reserve(
        _intent(mode=HorizonMode.SCALPING, mode_version="snapback_scalping_v1")
    )
    assert len(store.for_lane("snapback:swing")) == 1
    assert len(store.for_lane("snapback:scalping")) == 1


# ── recovery ──────────────────────────────────────────────────────────────


def test_a_fresh_intent_is_safe_to_submit(store):
    stored = store.reserve(_intent())
    assert stored.state is IntentState.NOT_SUBMITTED
    assert stored.safe_to_submit is True
    assert stored.needs_broker_query is False


def test_a_submitted_unknown_intent_is_never_resubmitted(store):
    """Query the broker first. Resubmitting doubles a real position."""
    stored = store.record_submission(store.reserve(_intent()).intent.intent_id)
    assert stored.state is IntentState.SUBMITTED_UNKNOWN
    assert stored.safe_to_submit is False
    assert stored.needs_broker_query is True


def test_recovery_survives_a_restart(store, tmp_path):
    store.record_submission(store.reserve(_intent()).intent.intent_id)
    reopened = IntentStore(tmp_path / "intents.db")
    pending = reopened.pending_recovery()
    assert [s.state for s in pending] == [IntentState.SUBMITTED_UNKNOWN]
    assert pending[0].needs_broker_query is True


def test_an_intent_can_never_become_unsent_again(store):
    intent_id = store.reserve(_intent()).intent.intent_id
    store.record_submission(intent_id)
    with pytest.raises(IntentError):
        store.record_broker_state(intent_id, IntentState.NOT_SUBMITTED)


@pytest.mark.parametrize("terminal", sorted(TERMINAL_STATES))
def test_a_finished_order_does_not_change_outcome(store, terminal):
    intent_id = store.reserve(_intent()).intent.intent_id
    store.record_submission(intent_id)
    store.record_broker_state(intent_id, terminal, broker_order_id="B1")
    with pytest.raises(IntentError):
        store.record_broker_state(intent_id, IntentState.OPEN)


def test_terminal_intents_drop_out_of_recovery(store):
    intent_id = store.reserve(_intent()).intent.intent_id
    store.record_submission(intent_id)
    store.record_broker_state(
        intent_id, IntentState.FILLED, broker_order_id="B1", filled_quantity=75
    )
    assert store.pending_recovery() == []


def test_a_partial_fill_still_needs_resolving(store):
    intent_id = store.reserve(_intent()).intent.intent_id
    store.record_submission(intent_id)
    store.record_broker_state(
        intent_id, IntentState.PARTIAL, broker_order_id="B1", filled_quantity=25
    )
    pending = store.pending_recovery()
    assert len(pending) == 1
    assert pending[0].filled_quantity == 25


# ── protection ────────────────────────────────────────────────────────────


def test_a_fill_starts_unprotected(store):
    """An API call returning without raising is not confirmation."""
    intent_id = store.reserve(_intent()).intent.intent_id
    store.record_submission(intent_id)
    stored = store.record_broker_state(
        intent_id, IntentState.FILLED, broker_order_id="B1", filled_quantity=75
    )
    assert stored.protection is ProtectionState.UNPROTECTED
    assert stored.position_is_unprotected is True
    assert len(store.unprotected_positions()) == 1


def test_confirming_protection_clears_the_naked_flag(store):
    intent_id = store.reserve(_intent()).intent.intent_id
    store.record_submission(intent_id)
    store.record_broker_state(
        intent_id, IntentState.FILLED, broker_order_id="B1", filled_quantity=75
    )
    stored = store.record_protection(intent_id, ProtectionState.CONFIRMED)
    assert stored.position_is_unprotected is False
    assert store.unprotected_positions() == []


def test_a_failed_protection_order_leaves_the_position_naked(store):
    intent_id = store.reserve(_intent()).intent.intent_id
    store.record_submission(intent_id)
    store.record_broker_state(
        intent_id, IntentState.FILLED, broker_order_id="B1", filled_quantity=75
    )
    store.record_protection(intent_id, ProtectionState.CONFIRMED)
    stored = store.record_protection(
        intent_id, ProtectionState.FAILED, note="stop order vanished from the book"
    )
    assert stored.position_is_unprotected is True


def test_a_partial_fill_is_also_exposed(store):
    intent_id = store.reserve(_intent()).intent.intent_id
    store.record_submission(intent_id)
    store.record_broker_state(
        intent_id, IntentState.PARTIAL, broker_order_id="B1", filled_quantity=25
    )
    assert store.unprotected_positions()[0].filled_quantity == 25


def test_an_intent_that_never_wanted_protection_is_not_flagged(store):
    intent = _intent(protection_required=False)
    store.reserve(intent)
    store.record_submission(intent.intent_id)
    stored = store.record_broker_state(
        intent.intent_id, IntentState.FILLED, broker_order_id="B1", filled_quantity=75
    )
    assert stored.position_is_unprotected is False


def test_one_broker_order_id_cannot_be_claimed_twice(store):
    import sqlite3

    first = _intent()
    second = _intent(opportunity_id="OPP-2")
    store.reserve(first)
    store.reserve(second)
    store.record_submission(first.intent_id)
    store.record_submission(second.intent_id)
    store.record_broker_state(first.intent_id, IntentState.OPEN, broker_order_id="B1")
    with pytest.raises(sqlite3.IntegrityError):
        store.record_broker_state(
            second.intent_id, IntentState.OPEN, broker_order_id="B1"
        )
