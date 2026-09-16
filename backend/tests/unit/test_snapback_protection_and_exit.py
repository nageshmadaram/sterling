"""Item 4b: protection states, and the exit sequence that survives partial fills.

Spec 03 §4 and the exit transaction. The rule underneath every case here: an
acknowledgement is not an outcome. A submitted stop is not a working stop, a
cancel request is not a cancelled order, and a sell that was accepted is not a
position that is closed. Each of those gaps is a window where the book and the
broker disagree, and the only safe response to "unknown" is to find out before
issuing a rival order.
"""

from __future__ import annotations

import pytest

from app.services.snapback_protection import (
    ProtectionState,
    exit_submission_quantity,
    next_protection_state,
    protection_deadline_action,
    protection_required_quantity,
)


# --------------------------------------------------------- protection states


def test_a_position_with_no_fill_needs_no_protection():
    assert protection_required_quantity(confirmed_filled=0) == 0


def test_protection_covers_the_confirmed_fill_not_the_intended_one():
    """Protecting the requested quantity leaves a short position on a partial
    fill; protecting the fill is the only quantity that actually exists."""
    assert protection_required_quantity(confirmed_filled=50, requested=75) == 50


@pytest.mark.parametrize("current,event,expected", [
    (ProtectionState.NONE, "fill_confirmed", ProtectionState.REQUIRED),
    (ProtectionState.REQUIRED, "submitted", ProtectionState.SUBMITTING),
    (ProtectionState.SUBMITTING, "ack", ProtectionState.ACTIVE),
    (ProtectionState.ACTIVE, "modify_submitted", ProtectionState.MODIFY_PENDING),
    (ProtectionState.MODIFY_PENDING, "ack", ProtectionState.ACTIVE),
    (ProtectionState.ACTIVE, "position_closed", ProtectionState.NONE),
])
def test_the_happy_path_transitions(current, event, expected):
    assert next_protection_state(current, event) == expected


@pytest.mark.parametrize("current", [
    ProtectionState.SUBMITTING, ProtectionState.MODIFY_PENDING,
])
def test_an_unknown_response_goes_to_reconciling(current):
    """A timeout is not a rejection. The order may be working."""
    assert next_protection_state(current, "unknown") == ProtectionState.RECONCILING


def test_reconciling_resolves_only_on_evidence():
    assert next_protection_state(ProtectionState.RECONCILING, "found_working") == (
        ProtectionState.ACTIVE
    )
    assert next_protection_state(ProtectionState.RECONCILING, "found_absent") == (
        ProtectionState.REQUIRED
    )


def test_a_timer_does_not_resolve_reconciling():
    """Elapsed time is not information about the broker's book."""
    assert next_protection_state(ProtectionState.RECONCILING, "timeout") == (
        ProtectionState.RECONCILING
    )


def test_a_rejection_returns_to_required_not_to_none():
    """NONE would mean "no protection needed"; the position still exists."""
    assert next_protection_state(ProtectionState.SUBMITTING, "rejected") == (
        ProtectionState.REQUIRED
    )


def test_an_unknown_event_is_refused():
    with pytest.raises(ValueError):
        next_protection_state(ProtectionState.ACTIVE, "wat")


def test_protection_is_not_considered_established_until_acknowledged():
    from app.services.snapback_protection import is_protected

    assert is_protected(ProtectionState.ACTIVE) is True
    for state in (ProtectionState.NONE, ProtectionState.REQUIRED,
                  ProtectionState.SUBMITTING, ProtectionState.MODIFY_PENDING,
                  ProtectionState.RECONCILING):
        assert is_protected(state) is False


# ------------------------------------------------------ the protection deadline


def test_missing_the_deadline_stops_entries_and_cancels_the_remainder():
    action = protection_deadline_action(
        state=ProtectionState.REQUIRED, deadline_exceeded=True,
        confirmed_filled=50, entry_remainder=25,
    )

    assert action.stop_new_entries is True
    assert action.cancel_entry_remainder is True
    assert action.emergency_exit_quantity == 50


def test_the_emergency_exit_covers_only_confirmed_inventory():
    """Selling the intended size would open a short leg of its own."""
    action = protection_deadline_action(
        state=ProtectionState.REQUIRED, deadline_exceeded=True,
        confirmed_filled=25, entry_remainder=50,
    )

    assert action.emergency_exit_quantity == 25


def test_an_uncertain_protection_request_is_reconciled_before_a_rival_order():
    action = protection_deadline_action(
        state=ProtectionState.RECONCILING, deadline_exceeded=True,
        confirmed_filled=50, entry_remainder=0,
    )

    assert action.reconcile_first is True
    assert action.emergency_exit_quantity == 0


def test_within_the_deadline_nothing_is_forced():
    action = protection_deadline_action(
        state=ProtectionState.SUBMITTING, deadline_exceeded=False,
        confirmed_filled=50, entry_remainder=25,
    )

    assert action.stop_new_entries is False
    assert action.emergency_exit_quantity == 0


# -------------------------------------------------------------- exit sequence


def test_the_exit_submits_only_the_unsold_residual():
    """Submitting the whole position again would sell what a working exit has
    already sold."""
    assert exit_submission_quantity(
        confirmed_inventory=75, already_sold=25, committed_to_working_exit=0,
    ) == 50


def test_quantity_committed_to_a_known_working_exit_is_not_resubmitted():
    assert exit_submission_quantity(
        confirmed_inventory=75, already_sold=0, committed_to_working_exit=75,
    ) == 0


def test_an_unknown_working_exit_blocks_submission_entirely():
    """Guessing here sells the position twice, or not at all."""
    from app.services.snapback_protection import ExitSubmissionUnknown

    with pytest.raises(ExitSubmissionUnknown):
        exit_submission_quantity(
            confirmed_inventory=75, already_sold=0,
            committed_to_working_exit=None,
        )


def test_the_residual_is_never_negative():
    assert exit_submission_quantity(
        confirmed_inventory=75, already_sold=100, committed_to_working_exit=0,
    ) == 0


def test_a_partial_exit_stays_exit_required_until_inventory_is_zero():
    from app.services.snapback_protection import state_after_exit_fill

    assert state_after_exit_fill(remaining_inventory=25) == "EXIT_REQUIRED"
    assert state_after_exit_fill(remaining_inventory=0) == "RECONCILING"


def test_a_position_closes_only_after_reconciliation():
    from app.services.snapback_protection import may_mark_closed

    assert may_mark_closed(remaining_inventory=0, reconciled=True) is True
    assert may_mark_closed(remaining_inventory=0, reconciled=False) is False
    assert may_mark_closed(remaining_inventory=25, reconciled=True) is False


def test_protection_is_resized_to_remaining_inventory_after_a_partial_exit():
    assert protection_required_quantity(confirmed_filled=50) == 50
    assert protection_required_quantity(confirmed_filled=0) == 0
