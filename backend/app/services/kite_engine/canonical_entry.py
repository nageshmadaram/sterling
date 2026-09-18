"""The single door through which a SuperTrend entry reaches the broker.

The engine used to call ``client.place_order_option`` and
``client.place_order_future`` directly. Those paths were not careless — they
reserved a durable intent, checked capital, checked the risk budget, honoured
the idempotency key — but they were a *second* implementation of admission, and
a second implementation is a second place for the rules to drift. The safety
recheck immediately before the broker send, in particular, existed only inside
:class:`~app.services.execution_service.CanonicalExecutionService`, so an
operator engaging SAFE_MODE during a SuperTrend entry could still watch the
order go out.

So every exposure *increase* now goes through the canonical service. Exits are
untouched and deliberately so: routing a close through an admission authority
is how a safety state turns into trapped capital.

The journal reservation moves with the order. The canonical service reserves,
claims, acknowledges and records submission uncertainty itself, so a caller
that also reserved would create two intents for one trade — the callers below
hand over their evidence and stop journalling.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from app.services.execution_service import (
    CanonicalExecutionService,
    ExecutionRequest,
    ExecutionResult,
    ExposureEffect,
    RiskApproval,
)

log = logging.getLogger(__name__)

__all__ = ["EntryEvidence", "submit_entry", "STRATEGY_ID"]

#: The strategy id the SuperTrend/Kite engine journals under. Unchanged from the
#: legacy path so existing intents and fills stay attributable.
STRATEGY_ID = "sterling-kite"


@dataclass(frozen=True)
class EntryEvidence:
    """What the engine measured before deciding it could afford this entry.

    These are facts the caller already established — broker-observed available
    capital, the capital this order commits — and they are passed rather than
    re-derived so the canonical service can bind them into the risk approval it
    requires. ``available_capital`` must be a broker reading: the canonical
    service refuses an exposure increase whose capital evidence is missing, and
    a caller-invented number is exactly what that refusal is for.
    """

    available_capital: float
    capital_required: float
    generation_id: str
    signal_id: str


async def submit_entry(
    *,
    client: Any,
    uid: str,
    account_id: str,
    symbol: str,
    exchange: str,
    side: str,
    quantity: int,
    evidence: EntryEvidence,
    order_type: str = "market_order",
    limit_price: float | None = None,
    stop_loss: float | None = None,
    product: str = "NRML",
    payload: Optional[Mapping[str, Any]] = None,
    tag: str = "",
) -> ExecutionResult:
    """Submit one exposure-increasing SuperTrend order, canonically.

    Returns the canonical :class:`ExecutionResult`. Callers map its ``status``
    onto their own reply shape; nothing here logs to the board or touches the
    position registry, because those belong to the caller that knows what the
    order meant.
    """
    request_payload: dict[str, Any] = dict(payload or {})
    request_payload.setdefault("product", product)
    if stop_loss is not None and stop_loss > 0:
        # Inert for a MARKET order at Kite — as it was on the legacy path — but
        # recorded so the journal says which stop the decision assumed.
        request_payload.setdefault("stop_loss", stop_loss)

    request = ExecutionRequest(
        uid=uid,
        account_id=account_id,
        strategy_id=STRATEGY_ID,
        generation_id=evidence.generation_id,
        signal_id=evidence.signal_id,
        exchange=exchange,
        symbol=symbol,
        side=side.upper(),
        quantity=int(quantity),
        tag=tag,
        exposure_effect=ExposureEffect.INCREASE_EXPOSURE,
        order_type=order_type,
        price=float(limit_price or 0.0),
        capital_required=float(evidence.capital_required),
        available_capital=float(evidence.available_capital),
        payload=request_payload,
    )

    approval = RiskApproval(
        approval_id=f"kite-{int(time.time() * 1000)}",
        uid=uid,
        account_id=account_id,
        symbol=symbol,
        side=side.upper(),
        quantity=int(quantity),
        generation_id=evidence.generation_id,
        approved=True,
        reason="SuperTrend engine risk budget, capital and session checks passed",
        timestamp_ms=int(time.time() * 1000),
        available_capital=float(evidence.available_capital),
        capital_required=float(evidence.capital_required),
        strategy_id=STRATEGY_ID,
        signal_id=evidence.signal_id,
    )

    return await CanonicalExecutionService().submit_order(
        request, broker_client=client, risk_approval=approval
    )
