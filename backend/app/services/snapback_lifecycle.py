"""Live position lifecycle.

Two failure modes drive every rule here. Hedging the quantity you ASKED for when the
market gave you half of it creates a naked futures leg; treating a partial as nothing
leaves untracked option inventory sitting at the broker. And after a timeout, the one
action that can double real exposure is a blind retry — so an unknown submission is
resolved by asking the broker, never by sending another order.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)


class LiveState(str, Enum):
    PLANNED = "PLANNED"
    ENTRY_SUBMITTING = "ENTRY_SUBMITTING"
    ENTRY_PARTIAL = "ENTRY_PARTIAL"
    ENTRY_FILLED = "ENTRY_FILLED"
    HEDGE_REQUIRED = "HEDGE_REQUIRED"
    HEDGE_PARTIAL = "HEDGE_PARTIAL"
    OPEN = "OPEN"
    RUNNER = "RUNNER"
    EXIT_REQUIRED = "EXIT_REQUIRED"
    EXIT_PENDING = "EXIT_PENDING"
    EXIT_PARTIAL = "EXIT_PARTIAL"
    RECONCILING = "RECONCILING"
    CLOSED = "CLOSED"


# States where the book is not what the ledger says, so nothing new may be opened.
_BLOCKING = {
    LiveState.ENTRY_PARTIAL,
    LiveState.HEDGE_REQUIRED,
    LiveState.HEDGE_PARTIAL,
    LiveState.EXIT_PARTIAL,
    LiveState.RECONCILING,
}


def blocks_new_exposure(state: LiveState) -> bool:
    return LiveState(state) in _BLOCKING


def next_state_after_entry_fill(*, requested: int, filled: int) -> LiveState:
    """Where an entry stands once the broker has reported fills."""
    if filled <= 0:
        return LiveState.ENTRY_SUBMITTING
    if filled < requested:
        return LiveState.ENTRY_PARTIAL
    return LiveState.HEDGE_REQUIRED


def state_after_hedge(*, required_lots: int, filled_lots: int) -> LiveState:
    """An unhedged or half-hedged position is not a normal open position."""
    if required_lots <= 0:
        return LiveState.OPEN
    if filled_lots <= 0:
        return LiveState.HEDGE_REQUIRED
    if filled_lots < required_lots:
        return LiveState.HEDGE_PARTIAL
    return LiveState.OPEN


def residual_entry_action(*, requested: int, filled: int, window_expired: bool) -> str:
    """What to do with the unfilled remainder of an entry order."""
    if filled >= requested:
        return "NONE"
    return "CANCEL_REMAINDER" if window_expired else "CONTINUE_WORKING"


@dataclass(frozen=True)
class HedgeRequirement:
    filled_option_quantity: int
    raw_quantity: float
    lots: int
    lot_size: int

    @property
    def quantity(self) -> int:
        return self.lots * self.lot_size


def hedge_requirement_for_fill(
    *,
    filled_option_quantity: int,
    option_delta: float,
    causal_beta: float,
    spot: float,
    futures_price: float,
    futures_lot_size: int,
) -> HedgeRequirement:
    """Size the hedge to CONFIRMED option inventory, never to the requested quantity."""
    if filled_option_quantity <= 0 or futures_price <= 0 or futures_lot_size <= 0:
        return HedgeRequirement(
            filled_option_quantity=max(0, filled_option_quantity),
            raw_quantity=0.0, lots=0, lot_size=max(0, futures_lot_size),
        )

    raw = (
        abs(float(option_delta)) * float(causal_beta)
        * float(filled_option_quantity) * float(spot)
    ) / float(futures_price)
    lots = int(round(raw / futures_lot_size)) if futures_lot_size else 0

    return HedgeRequirement(
        filled_option_quantity=int(filled_option_quantity),
        raw_quantity=raw, lots=max(0, lots), lot_size=int(futures_lot_size),
    )


# --------------------------------------------------------------------------- #
# Unknown submissions
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class UnknownSubmission:
    intent_id: str
    resolution: str                    # FOUND | ABSENT | UNRESOLVED
    safe_to_resubmit: bool
    order_id: Optional[str] = None
    filled_quantity: int = 0
    details: Dict[str, Any] = field(default_factory=dict)


def _get(row: Any, key: str, default=None):
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


async def resolve_unknown_submission(
    *,
    client,
    intent_id: str,
    tradingsymbol: str,
) -> UnknownSubmission:
    """Ask the broker what happened to an order whose outcome we never saw.

    Only ABSENT — the broker demonstrably has no order and no trade for this intent —
    permits a resubmission. An unreachable broker is UNRESOLVED, which is not
    permission.
    """
    orders: List[Any] = []
    trades: List[Any] = []

    try:
        orders = list(await client.get_orders() or [])
    except Exception as exc:
        log.warning("Unknown submission %s: orders unreadable: %s", intent_id, exc)
        return UnknownSubmission(
            intent_id=intent_id, resolution="UNRESOLVED", safe_to_resubmit=False,
            details={"orders_error": str(exc)},
        )

    try:
        trades = list(await client.get_trades() or [])
    except Exception as exc:
        log.warning("Unknown submission %s: trades unreadable: %s", intent_id, exc)
        return UnknownSubmission(
            intent_id=intent_id, resolution="UNRESOLVED", safe_to_resubmit=False,
            details={"trades_error": str(exc)},
        )

    for order in orders:
        if str(_get(order, "tag", "") or "") != intent_id:
            continue
        if tradingsymbol and str(_get(order, "tradingsymbol", "") or "") != tradingsymbol:
            continue
        filled = 0
        try:
            filled = int(float(_get(order, "filled_quantity", 0) or 0))
        except (TypeError, ValueError):
            filled = 0
        return UnknownSubmission(
            intent_id=intent_id, resolution="FOUND", safe_to_resubmit=False,
            order_id=str(_get(order, "order_id", "") or ""), filled_quantity=filled,
            details={"status": str(_get(order, "status", "") or "")},
        )

    for trade in trades:
        if str(_get(trade, "tag", "") or "") != intent_id:
            continue
        quantity = 0
        try:
            quantity = int(float(_get(trade, "quantity", 0) or 0))
        except (TypeError, ValueError):
            quantity = 0
        return UnknownSubmission(
            intent_id=intent_id, resolution="FOUND", safe_to_resubmit=False,
            order_id=str(_get(trade, "order_id", "") or ""), filled_quantity=quantity,
            details={"source": "trade"},
        )

    return UnknownSubmission(
        intent_id=intent_id, resolution="ABSENT", safe_to_resubmit=True,
    )
