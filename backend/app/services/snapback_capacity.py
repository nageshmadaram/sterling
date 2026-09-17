"""Can this trade actually be funded?

A profitable book that the account could never have funded is not evidence. Capacity
is checked against the frozen capital, existing reserved margin, the real option
premium cash, broker-observed hedge margin and a fee reserve — and an unknown input
is INCONCLUSIVE_CAPACITY, never an assumption.

Operational admission is checked here too.  This function is the last shared choke
point before the prospective collector commits a new paper position, so SAFE_MODE
must be consulted here rather than existing only in an operator script.  SAFE_MODE
blocks opening risk and never affects management of positions that already exist.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Set

log = logging.getLogger(__name__)


@dataclass
class CapacityDecision:
    allowed: bool
    status: str
    required_capital: Optional[float] = None
    available_capital: Optional[float] = None
    reasons: List[str] = field(default_factory=list)


def _configured_safe_mode_state():
    """Read the same durable SAFE_MODE file used by the operator scripts.

    Missing state is NORMAL by the SafeModeService contract.  An unreadable or
    unrecognised file is SAFE_MODE, so a corrupted switch cannot silently permit
    new exposure.  The path can be overridden in tests and deployments with
    STERLING_SAFE_MODE_FILE.
    """
    from app.services.safe_mode import SafeModeService

    root = Path(os.environ.get("STERLING_ROOT") or Path(__file__).resolve().parents[3])
    path = Path(os.environ.get("STERLING_SAFE_MODE_FILE") or (root / "data" / "safe_mode.json"))
    return SafeModeService(path).read()


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
    """Decide whether one paper entry is fundable and operationally admissible."""
    reasons: List[str] = []
    open_underlyings = set(open_underlyings or set())

    # Operational safety outranks economics.  Do this before capacity arithmetic
    # so an operator-requested stop is reported as the reason the entry was blocked.
    try:
        safe_state = _configured_safe_mode_state()
    except Exception as exc:  # fail closed: inability to read the switch is uncertainty
        return CapacityDecision(
            allowed=False,
            status="SAFE_MODE",
            reasons=[f"safe_mode_unavailable:{type(exc).__name__}"],
        )
    if safe_state.active:
        return CapacityDecision(
            allowed=False,
            status="SAFE_MODE",
            reasons=["safe_mode_active", *[f"trigger:{t}" for t in safe_state.triggers]],
        )

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
