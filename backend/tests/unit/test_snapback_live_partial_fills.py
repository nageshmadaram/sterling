"""H4: a partial fill is a real position, not a failed one.

Hedging the quantity you asked for when the market gave you half of it creates a naked
futures leg; treating the partial as nothing leaves untracked option inventory at the
broker. Both are worse than stopping.
"""

from __future__ import annotations

import pytest

from app.services.snapback_lifecycle import (
    LiveState,
    hedge_requirement_for_fill,
    next_state_after_entry_fill,
    residual_entry_action,
)


def test_a_full_fill_moves_to_hedging():
    state = next_state_after_entry_fill(requested=850, filled=850)

    assert state == LiveState.HEDGE_REQUIRED


def test_a_partial_fill_is_tracked_as_partial():
    state = next_state_after_entry_fill(requested=850, filled=425)

    assert state == LiveState.ENTRY_PARTIAL


def test_a_zero_fill_stays_submitting():
    state = next_state_after_entry_fill(requested=850, filled=0)

    assert state == LiveState.ENTRY_SUBMITTING


def test_the_hedge_covers_only_confirmed_inventory():
    """The market gave 425 of 850; the hedge must size to 425."""
    requirement = hedge_requirement_for_fill(
        filled_option_quantity=425, option_delta=-0.70, causal_beta=1.0,
        spot=24500.0, futures_price=24500.0, futures_lot_size=75,
    )

    full = hedge_requirement_for_fill(
        filled_option_quantity=850, option_delta=-0.70, causal_beta=1.0,
        spot=24500.0, futures_price=24500.0, futures_lot_size=75,
    )

    assert requirement.lots < full.lots
    assert requirement.filled_option_quantity == 425


def test_no_fill_needs_no_hedge():
    requirement = hedge_requirement_for_fill(
        filled_option_quantity=0, option_delta=-0.70, causal_beta=1.0,
        spot=24500.0, futures_price=24500.0, futures_lot_size=75,
    )

    assert requirement.lots == 0


def test_the_residual_entry_order_is_cancelled_not_left_working():
    action = residual_entry_action(
        requested=850, filled=425, window_expired=True,
    )

    assert action == "CANCEL_REMAINDER"


def test_a_live_window_may_keep_working_the_remainder():
    action = residual_entry_action(
        requested=850, filled=425, window_expired=False,
    )

    assert action == "CONTINUE_WORKING"


def test_a_complete_fill_needs_no_residual_action():
    assert residual_entry_action(requested=850, filled=850, window_expired=False) == "NONE"


def test_a_failed_hedge_is_not_a_normal_open_position():
    from app.services.snapback_lifecycle import state_after_hedge

    assert state_after_hedge(required_lots=1, filled_lots=0) == LiveState.HEDGE_REQUIRED
    assert state_after_hedge(required_lots=2, filled_lots=1) == LiveState.HEDGE_PARTIAL
    assert state_after_hedge(required_lots=1, filled_lots=1) == LiveState.OPEN


def test_an_unhedged_position_blocks_new_entries():
    from app.services.snapback_lifecycle import blocks_new_exposure

    assert blocks_new_exposure(LiveState.HEDGE_REQUIRED) is True
    assert blocks_new_exposure(LiveState.HEDGE_PARTIAL) is True
    assert blocks_new_exposure(LiveState.RECONCILING) is True
    assert blocks_new_exposure(LiveState.OPEN) is False
