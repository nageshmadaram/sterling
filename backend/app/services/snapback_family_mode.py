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
_capability: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "snapback_broker_capability", default=None
)

GUARDED_OPERATIONS = {
    "place_order",
    "modify_order",
    "cancel_order_unsafe",
    "place_gtt",
    "modify_gtt",
}


def family_mode_enabled() -> bool:
    return str(os.environ.get("STERLING_FAMILY_MODE", "")).strip().lower() in (
        "1", "true", "yes", "on",
    )


@contextmanager
def canonical_broker_capability(intent_id: str) -> Iterator[str]:
    """Grant broker-write capability for one canonical execution intent."""
    token = _capability.set(intent_id)
    try:
        yield intent_id
    finally:
        _capability.reset(token)


def current_capability() -> Optional[str]:
    return _capability.get()


def guard_broker_write(operation: str) -> None:
    """Refuse a broker write that did not come through canonical execution."""
    if not family_mode_enabled():
        return
    if operation not in GUARDED_OPERATIONS:
        return
    if _capability.get() is None:
        log.error(
            "Family Mode: refused %s — broker writes must pass through canonical execution",
            operation,
        )
        raise PermissionError(
            f"Family Mode: {operation} must pass through the canonical execution "
            "authority; direct broker writes are refused"
        )
