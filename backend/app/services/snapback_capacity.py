"""Can this trade actually be funded?

A profitable book that the account could never have funded is not evidence. Capacity
is checked against the frozen capital, existing reserved margin, the real option
premium cash, broker-observed hedge margin and a fee reserve — and an unknown input
is INCONCLUSIVE_CAPACITY, never an assumption.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Set

log = logging.getLogger(__name__)


@dataclass
class CapacityDecision:
    allowed: bool
    status: str
    required_capital: Optional[float] = None
    available_capital: Optional[float] = None
    reasons: List[str] = field(default_factory=list)


def evaluate_capacity(
    *,
    capital: Optional[float],
    reserved_margin: Optional[float],
    option_premium_cash: Optional[float],
    hedge_margin: Optional[float],
    fee_reserve: Optional[float],
    open_positions: int,
    max_open_positions: int,
    underlying: str,
    open_underlyings: Optional[Set[str]] = None,
) -> CapacityDecision:
    """Decide whether one paper entry is fundable."""
    reasons: List[str] = []
    open_underlyings = set(open_underlyings or set())

    # Unknown inputs are never assumed. A guessed margin is a fabricated constraint.
    unknown: List[str] = []
    if capital is None or float(capital) <= 0:
        unknown.append("capital_unavailable")
    if hedge_margin is None:
        unknown.append("hedge_margin_unavailable")
    if option_premium_cash is None:
        unknown.append("option_premium_unavailable")

    if unknown:
        return CapacityDecision(
            allowed=False,
            status="INCONCLUSIVE_CAPACITY",
            reasons=unknown,
        )

    required = float(option_premium_cash) + float(hedge_margin) + float(fee_reserve or 0.0)
    available = float(capital) - float(reserved_margin or 0.0)

    if required > available:
        reasons.append("insufficient_capital")

    if max_open_positions and open_positions >= max_open_positions:
        reasons.append("max_open_positions")

    if underlying and underlying in open_underlyings:
        reasons.append("underlying_already_open")

    if reasons:
        return CapacityDecision(
            allowed=False,
            status="NO_CAPACITY",
            required_capital=required,
            available_capital=available,
            reasons=reasons,
        )

    return CapacityDecision(
        allowed=True,
        status="CAPACITY_OK",
        required_capital=required,
        available_capital=available,
    )


async def observed_hedge_margin(client, *, tradingsymbol: str, quantity: int,
                                exchange: str = "NFO", transaction_type: str = "BUY") -> Optional[float]:
    """Broker-observed margin for the hedge leg. None when the broker cannot answer."""
    try:
        payload = [{
            "exchange": exchange,
            "tradingsymbol": tradingsymbol,
            "transaction_type": transaction_type,
            "variety": "regular",
            "product": "NRML",
            "order_type": "MARKET",
            "quantity": int(quantity),
        }]
        result = await client.order_margins(payload)
        if not result:
            return None
        first = result[0] if isinstance(result, list) else result
        total = first.get("total") if isinstance(first, dict) else None
        return float(total) if total is not None else None
    except Exception as exc:
        log.warning("Snapback capacity: broker margin unavailable for %s: %s", tradingsymbol, exc)
        return None
