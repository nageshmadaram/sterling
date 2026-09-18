"""Record what the SuperTrend engine wanted to do, and whether it could have.

The shadow execution service has existed since the previous release, and
nothing wrote to it: the engines never produced a `ShadowIntent`, so the only
thing exercising the service was its own test suite. This module is the missing
half — the point in the live path where an entry decision becomes a shadow
record.

It is deliberately not part of the send. `record_entry_intent` is called before
the canonical submission, observes the book, and returns; whether an order
follows is the capital-permission layer's decision, not this one's. That
ordering is what makes the record honest in both directions:

  * when the lane may not send — which is every lane today — the record is the
    entire evidence of what the strategy would have done;
  * when a lane eventually may, the same record sits beside the real fill, and
    the difference between them is the execution slippage nobody can estimate
    from a backtest.

Nothing here can place an order. The service refuses at construction any client
that exposes `place_order`, so the reader handed to it is a market reader in
the type system's opinion as well as the author's.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from app.core.execution_vehicle import ExecutionVehicle
from app.core.shadow_record import BookObservation, ShadowRecord
from app.services.shadow_execution import (
    MarketFacts,
    ShadowExecutionService,
    ShadowIntent,
)

log = logging.getLogger(__name__)

__all__ = ["record_entry_intent", "book_from_quote"]


class _MarketReader:
    """A read-only façade over the broker client.

    The shadow service refuses any object exposing an order method, and a Kite
    client exposes several. Rather than weaken that check — which is the one
    thing standing between a shadow path and a real order — the client is
    wrapped so only the reads it needs are reachable.
    """

    def __init__(self, client: Any) -> None:
        self._get_quote = getattr(client, "get_quote", None)
        self._get_ltp = getattr(client, "get_ltp", None)

    async def get_quote(self, instruments):
        if self._get_quote is None:
            raise AttributeError("client exposes no get_quote")
        return await self._get_quote(instruments)

    async def get_ltp(self, instruments):
        if self._get_ltp is None:
            raise AttributeError("client exposes no get_ltp")
        return await self._get_ltp(instruments)


def book_from_quote(quote: Mapping[str, Any] | None, *, observed_at: str) -> BookObservation | None:
    """Best bid and offer from one Kite quote payload.

    Every field is optional and a missing one stays ``None``. A depth reading
    that could not be taken is not a zero-sized book; recording it as one would
    turn "we do not know" into "nobody was bidding", which is a much stronger
    claim than the data supports.
    """
    if not quote:
        return None
    depth = quote.get("depth") or {}
    buys = depth.get("buy") or []
    sells = depth.get("sell") or []
    best_buy = buys[0] if buys else {}
    best_sell = sells[0] if sells else {}

    def _f(value):
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def _i(value):
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    return BookObservation(
        observed_at=observed_at,
        bid=_f(best_buy.get("price")),
        ask=_f(best_sell.get("price")),
        bid_qty=_i(best_buy.get("quantity")),
        ask_qty=_i(best_sell.get("quantity")),
        last=_f(quote.get("last_price")),
    )


async def record_entry_intent(
    *,
    client: Any,
    lane_key: str,
    symbol: str,
    exchange: str,
    quantity: int,
    vehicle: ExecutionVehicle | str,
    reference_price: float | None,
    signal_at: str | None = None,
    broker_margin: float | None = None,
    margin_available: float | None = None,
    protection_feasible: bool | None = None,
    notes: str = "",
    service: Optional[ShadowExecutionService] = None,
) -> ShadowRecord | None:
    """Observe the market for one intended entry and append a shadow record.

    Returns the record, or ``None`` when the intent could not even be formed.
    Never raises into the caller: a failure to record evidence must not cancel
    a decision the strategy already made, and must not be mistaken for one —
    it is logged, and the absence of a record is itself visible in the daily
    shadow coverage report.
    """
    now = datetime.now(timezone.utc).isoformat()
    try:
        service = service or ShadowExecutionService(market_reader=_MarketReader(client))
    except Exception as exc:  # noqa: BLE001
        log.warning("shadow: service unavailable for %s: %s", symbol, exc)
        return None

    key = f"{exchange}:{symbol}"
    quote: Mapping[str, Any] | None = None
    try:
        quotes = await service.market_reader.get_quote([key])
        quote = (quotes or {}).get(key)
    except Exception as exc:  # noqa: BLE001
        # An unreadable book is recorded as unknown, not as an empty one. The
        # shadow service refuses to claim a fill against a book it never saw.
        log.info("shadow: no book for %s: %s", key, exc)

    try:
        intent = ShadowIntent(
            lane_key=lane_key,
            session_date=now[:10],
            signal_at=signal_at or now,
            contract=symbol,
            quantity=int(quantity),
            reference_price=reference_price,
            execution_vehicle=vehicle,
            selected_at=now,
            notes=notes,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("shadow: intent rejected for %s: %s", symbol, exc)
        return None

    facts = MarketFacts(
        book=book_from_quote(quote, observed_at=now),
        contract_listed=None if quote is None else True,
        quote_age_seconds=0.0 if quote is not None else None,
        broker_margin=broker_margin,
        margin_available=margin_available,
        protection_feasible=protection_feasible,
    )

    try:
        return service.record(intent, facts)
    except Exception as exc:  # noqa: BLE001
        log.warning("shadow: could not record %s: %s", symbol, exc)
        return None
