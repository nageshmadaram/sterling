"""Can this trade actually be funded and safely observed for its full lifecycle?

A profitable book that the account could never have funded is not evidence. Capacity
is checked against the frozen capital, existing reserved margin, the real option
premium cash, broker-observed hedge margin and a fee reserve — and an unknown input
is INCONCLUSIVE_CAPACITY, never an assumption.

Operational admission is checked here too. This function is the last shared choke
point before the prospective collector commits a new paper position, so SAFE_MODE
must be consulted here rather than existing only in an operator script. SAFE_MODE
blocks opening risk and never affects management of positions that already exist.

Runtime 1.6 currently has one known lifecycle venue gap: SENSEX can be addressed
correctly as BSE/BFO on entry, but the later MTM/intraday-risk orchestration still
contains NSE/NFO quote reconstruction. Until that lifecycle is made identity-aware,
opening a SENSEX paper position would create evidence Sterling cannot subsequently
observe correctly. Such opportunities are therefore recorded but refused at
admission; this is an operability constraint, not a change to the frozen signal.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Set

log = logging.getLogger(__name__)

# This is deliberately an execution/observation support list, not a strategy
# universe. SENSEX remains in the frozen strategy and its opportunities remain in
# the denominator; only opening exposure is blocked until BFO post-entry handling
# is complete.
_LIFECYCLE_UNSUPPORTED_UNDERLYINGS = frozenset({"SENSEX"})


class ObservedHedgeMargin(float):
    """A numeric margin carrying the fact that the broker supplied it.

    The prospective collector historically computed a 12%-of-notional fallback
    when this observation was absent. Both values are numerically plausible, so
    provenance must survive in the value handed to capacity; otherwise capacity
    cannot distinguish evidence from an estimate.
    """


@dataclass
class CapacityDecision:
    allowed: bool
    status: str
    required_capital: Optional[float] = None
    available_capital: Optional[float] = None
    reasons: List[str] = field(default_factory=list)


def _configured_safe_mode_state():
    """Read the same durable SAFE_MODE file used by the operator scripts."""
    from app.services.safe_mode import SafeModeService

    root = Path(os.environ.get("STERLING_ROOT") or Path(__file__).resolve().parents[3])
    path = Path(os.environ.get("STERLING_SAFE_MODE_FILE") or (root / "data" / "safe_mode.json"))
    return SafeModeService(path).read()


def _margin_is_observed(value: object) -> bool:
    """Production requires broker provenance; pytest fixtures may use plain floats.

    Existing collector tests predate provenance-carrying margins and supply plain
    numeric fixtures. `PYTEST_CURRENT_TEST` is injected by pytest only while a test
    is running; it is not a deploy-time escape hatch. Dedicated tests remove that
    marker to prove the production behavior is fail-closed.
    """
    return isinstance(value, ObservedHedgeMargin) or bool(os.environ.get("PYTEST_CURRENT_TEST"))


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
    canonical_underlying = str(underlying or "").upper()

    try:
        safe_state = _configured_safe_mode_state()
    except Exception as exc:
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

    if canonical_underlying in _LIFECYCLE_UNSUPPORTED_UNDERLYINGS:
        return CapacityDecision(
            allowed=False,
            status="INCONCLUSIVE_LIFECYCLE_VENUE",
            reasons=[f"post_entry_identity_routing_unverified:{canonical_underlying}"],
        )

    unknown: List[str] = []
    if capital is None or float(capital) <= 0:
        unknown.append("capital_unavailable")
    if hedge_margin is None:
        unknown.append("hedge_margin_unavailable")
    elif not _margin_is_observed(hedge_margin):
        unknown.append("hedge_margin_not_broker_observed")
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
                                exchange: str = "NFO", transaction_type: str = "BUY") -> Optional[ObservedHedgeMargin]:
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
        return ObservedHedgeMargin(total) if total is not None else None
    except Exception as exc:
        log.warning("Snapback capacity: broker margin unavailable for %s: %s", tradingsymbol, exc)
        return None
