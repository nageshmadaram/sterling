"""Item 4b: protection state and the exit sequence (spec 03 §4).

One rule underneath all of it: an acknowledgement is not an outcome. A submitted
stop is not a working stop. A cancel request is not a cancelled order. An
accepted sell is not a closed position. Each gap is a window where the book and
the broker disagree, and the only safe response to "unknown" is to find out
before issuing a rival order — never to assume, and never to let a timer decide.

Nothing here places an order. These are the decisions an executor asks for, kept
separate so they can be tested without a broker and cannot be quietly bypassed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

log = logging.getLogger(__name__)


class ProtectionState(str, Enum):
    # No protection needed: there is no confirmed inventory to protect.
    NONE = "NONE"
    # Inventory exists and is unprotected. The dangerous state.
    REQUIRED = "REQUIRED"
    # A protective order has been sent; the broker has not acknowledged it.
    SUBMITTING = "SUBMITTING"
    # Acknowledged and working.
    ACTIVE = "ACTIVE"
    # A modification is in flight; the old order may or may not still stand.
    MODIFY_PENDING = "MODIFY_PENDING"
    # We do not know what the broker holds. Resolve by evidence, never by time.
    RECONCILING = "RECONCILING"


class ExitSubmissionUnknown(RuntimeError):
    """The working-exit quantity is unknown, so no residual can be computed.

    Guessing sells the position twice or not at all.
    """


_TRANSITIONS = {
    (ProtectionState.NONE, "fill_confirmed"): ProtectionState.REQUIRED,
    (ProtectionState.REQUIRED, "submitted"): ProtectionState.SUBMITTING,
    (ProtectionState.REQUIRED, "position_closed"): ProtectionState.NONE,
    (ProtectionState.SUBMITTING, "ack"): ProtectionState.ACTIVE,
    # A rejection returns to REQUIRED, never NONE: NONE would claim no
    # protection is needed, and the position still exists.
    (ProtectionState.SUBMITTING, "rejected"): ProtectionState.REQUIRED,
    (ProtectionState.SUBMITTING, "unknown"): ProtectionState.RECONCILING,
    (ProtectionState.ACTIVE, "modify_submitted"): ProtectionState.MODIFY_PENDING,
    (ProtectionState.ACTIVE, "triggered"): ProtectionState.NONE,
    (ProtectionState.ACTIVE, "cancelled"): ProtectionState.REQUIRED,
    (ProtectionState.ACTIVE, "position_closed"): ProtectionState.NONE,
    (ProtectionState.ACTIVE, "unknown"): ProtectionState.RECONCILING,
    (ProtectionState.MODIFY_PENDING, "ack"): ProtectionState.ACTIVE,
    (ProtectionState.MODIFY_PENDING, "rejected"): ProtectionState.ACTIVE,
    (ProtectionState.MODIFY_PENDING, "unknown"): ProtectionState.RECONCILING,
    # Only evidence leaves RECONCILING.
    (ProtectionState.RECONCILING, "found_working"): ProtectionState.ACTIVE,
    (ProtectionState.RECONCILING, "found_absent"): ProtectionState.REQUIRED,
    (ProtectionState.RECONCILING, "position_closed"): ProtectionState.NONE,
    # Elapsed time is not information about the broker's book.
    (ProtectionState.RECONCILING, "timeout"): ProtectionState.RECONCILING,
}

_KNOWN_EVENTS = {
    "fill_confirmed", "submitted", "ack", "rejected", "unknown", "modify_submitted",
    "triggered", "cancelled", "position_closed", "found_working", "found_absent",
    "timeout",
}


def next_protection_state(current: ProtectionState, event: str) -> ProtectionState:
    """Advance the protection state. An unmodelled event is an error, not a no-op."""
    state = ProtectionState(current)
    if event not in _KNOWN_EVENTS:
        raise ValueError(f"unknown protection event {event!r}")

    if (state, event) in _TRANSITIONS:
        return _TRANSITIONS[(state, event)]

    # A modelled event that does not apply in this state leaves it alone; the
    # caller has no new information.
    return state


def is_protected(state: ProtectionState) -> bool:
    """Only an acknowledged, working order counts as protection."""
    return ProtectionState(state) is ProtectionState.ACTIVE


def protection_required_quantity(
    *, confirmed_filled: int, requested: Optional[int] = None,
) -> int:
    """Protection covers confirmed inventory.

    Sizing to the requested quantity leaves a short protective leg whenever the
    entry fills partially — protecting a position that does not exist.
    """
    return max(0, int(confirmed_filled))


@dataclass
class ProtectionDeadlineAction:
    stop_new_entries: bool = False
    cancel_entry_remainder: bool = False
    reconcile_first: bool = False
    emergency_exit_quantity: int = 0


def protection_deadline_action(
    *,
    state: ProtectionState,
    deadline_exceeded: bool,
    confirmed_filled: int,
    entry_remainder: int,
) -> ProtectionDeadlineAction:
    """What to do when protection was not established in time (spec 03 §4).

    Stop entries, cancel the unfilled remainder, and run the bounded emergency
    policy against confirmed residual inventory only. If the protection request
    itself is uncertain, reconcile before issuing a conflicting order.
    """
    state = ProtectionState(state)

    if not deadline_exceeded:
        return ProtectionDeadlineAction()

    if state is ProtectionState.ACTIVE:
        # Protection is working; the deadline is moot.
        return ProtectionDeadlineAction()

    if state is ProtectionState.RECONCILING:
        # An emergency sell now could race a stop we cannot see.
        return ProtectionDeadlineAction(
            stop_new_entries=True, cancel_entry_remainder=True, reconcile_first=True,
        )

    log.error(
        "Snapback protection deadline exceeded in %s with %s unprotected",
        state.value, confirmed_filled,
    )
    return ProtectionDeadlineAction(
        stop_new_entries=True,
        cancel_entry_remainder=int(entry_remainder) > 0,
        emergency_exit_quantity=max(0, int(confirmed_filled)),
    )


# ------------------------------------------------------------- exit sequence


def exit_submission_quantity(
    *,
    confirmed_inventory: int,
    already_sold: int,
    committed_to_working_exit: Optional[int],
) -> int:
    """The residual to submit: inventory minus what is sold or already committed.

    `committed_to_working_exit` of None means the working-exit state is unknown.
    That is not zero: treating it as zero resubmits quantity a live order is
    already selling.
    """
    if committed_to_working_exit is None:
        raise ExitSubmissionUnknown(
            "working exit quantity is unknown; reconcile before submitting a rival sell"
        )

    residual = (
        int(confirmed_inventory) - int(already_sold) - int(committed_to_working_exit)
    )
    return max(0, residual)


def state_after_exit_fill(*, remaining_inventory: int) -> str:
    """EXIT_REQUIRED is retained until inventory is actually zero."""
    return "RECONCILING" if int(remaining_inventory) <= 0 else "EXIT_REQUIRED"


def may_mark_closed(*, remaining_inventory: int, reconciled: bool) -> bool:
    """CLOSED means the broker agrees, not that a sell was acknowledged.

    No UI disappearance, target touch, submit acknowledgement or elapsed timer
    closes a position.
    """
    return int(remaining_inventory) <= 0 and bool(reconciled)
