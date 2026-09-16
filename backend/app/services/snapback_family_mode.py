"""Family Mode: one deployment, one strategy, one way to reach the broker.

Hiding a button is not a control. In Family Mode every exposure-increasing broker
write must originate from the canonical execution authority, which takes an explicit
capability for the duration of the call. A direct `client.place_order()` from any
other engine is refused at the transport, so an accidental bypass is impossible
rather than merely unlikely.
"""

from __future__ import annotations

import contextvars
import logging
import os
from contextlib import contextmanager
from typing import Iterator, Optional

log = logging.getLogger(__name__)

# Set only for the duration of a canonical execution call.
_capability: contextvars.ContextVar[Optional[tuple]] = contextvars.ContextVar(
    "snapback_broker_capability", default=None
)

# Anything that can create, increase or alter exposure.
EXPOSURE_OPERATIONS = {
    "place_order",
    "modify_order",          # can change quantity or price of a live order
    "place_gtt",
    "modify_gtt",
    "place_mf_order",
    "place_mf_sip",
    "modify_mf_sip",
    "convert_position",
}

# Protective mutations. Still capability-bound, but permitted while halted: refusing
# to cancel or exit would trap capital rather than protect it.
PROTECTIVE_OPERATIONS = {
    "cancel_order",
    "cancel_order_unsafe",
    "cancel_gtt",
    "cancel_mf_order",
    "cancel_mf_sip",
    "exit_order",
}

GUARDED_OPERATIONS = EXPOSURE_OPERATIONS | PROTECTIVE_OPERATIONS

# Intents a capability can declare.
INTENT_INCREASE = "INCREASE_EXPOSURE"
INTENT_REDUCE = "REDUCE_EXPOSURE"
INTENT_CANCEL = "CANCEL"


def family_mode_enabled() -> bool:
    return str(os.environ.get("STERLING_FAMILY_MODE", "")).strip().lower() in (
        "1", "true", "yes", "on",
    )


@contextmanager
def canonical_broker_capability(
    intent_id: str, *, intent: str = INTENT_INCREASE
) -> Iterator[str]:
    """Grant broker-write capability for one canonical execution intent.

    The declared intent bounds what the capability authorises: a REDUCE_EXPOSURE or
    CANCEL capability cannot be used to open a position.
    """
    token = _capability.set((intent_id, intent))
    try:
        yield intent_id
    finally:
        _capability.reset(token)


def current_capability() -> Optional[str]:
    held = _capability.get()
    return held[0] if held else None


def current_intent() -> Optional[str]:
    held = _capability.get()
    return held[1] if held else None


def guard_broker_write(operation: str) -> None:
    """Refuse a broker write that did not come through canonical execution."""
    if not family_mode_enabled():
        return
    if operation not in GUARDED_OPERATIONS:
        return
    held = _capability.get()
    if held is None:
        log.error(
            "Family Mode: refused %s — broker writes must pass through canonical execution",
            operation,
        )
        raise PermissionError(
            f"Family Mode: {operation} must pass through the canonical execution "
            "authority; direct broker writes are refused"
        )

    _, intent = held
    if operation in EXPOSURE_OPERATIONS and intent != INTENT_INCREASE:
        log.error(
            "Family Mode: refused %s under a %s capability", operation, intent,
        )
        raise PermissionError(
            f"Family Mode: {operation} increases exposure and cannot run under a "
            f"{intent} capability"
        )


# Only a strategy with its own production promotion may be offered to the family.
# Every other engine in this repository is explicitly research-only, unvalidated,
# or measured as economically negative. A research workstation may arm one
# manually; the family product must not make that possible.
FAMILY_VISIBLE_STRATEGIES = frozenset({"snapback"})


def family_visible_strategies() -> frozenset:
    return FAMILY_VISIBLE_STRATEGIES


def guard_family_strategy(strategy_id: str) -> None:
    """Refuse any strategy the family product does not carry.

    Outside Family Mode nothing is restricted: that is the research surface.
    """
    if not family_mode_enabled():
        return

    if str(strategy_id).lower() not in FAMILY_VISIBLE_STRATEGIES:
        raise PermissionError(
            f"FAMILY MODE: {strategy_id} has no production promotion and cannot "
            f"be armed here. Only {sorted(FAMILY_VISIBLE_STRATEGIES)} is carried."
        )
