"""Test doubles that speak the canonical execution service's language.

SuperTrend entries now go through
:func:`app.services.kite_engine.canonical_entry.submit_entry`, which submits via
``CanonicalExecutionService``. That service sends with ``place_order`` — the
generic transport entry — and requires the client to state which account it is
authenticated against.

The doubles in these tests were written against the old bypass: they implement
``place_order_option`` / ``place_order_future`` and nothing else. Rather than
rewrite every assertion, this mixin teaches a double the two things the
canonical service needs, and routes the canonical send back onto whichever
vehicle-specific recorder the test already asserts on.

Applying it is not a way of keeping the old path alive: the order still passes
the risk approval, the safety recheck at the broker boundary and the durable
journal. Only the last hop — which recorder captures the call — is mapped back.
"""
from __future__ import annotations

__all__ = ["CanonicalBrokerDouble"]


class CanonicalBrokerDouble:
    """Give a fake Kite client an account identity and a ``place_order``."""

    #: The canonical service refuses to send unless the client names the account
    #: it is authenticated against and that name matches the request.
    _account_id = "test-account"

    async def get_margins(self, segment: str = "equity"):
        """A simulated account still has to be able to state its balance.

        An entry — paper or live — is refused when capital cannot be read, so a
        double that cannot answer this question can no longer place anything.
        """
        return {"available": {"live_balance": 1_000_000.0, "cash": 1_000_000.0}}

    async def place_order(self, symbol=None, side=None, size=None, **kwargs):
        # The canonical service passes every identity spelling it knows; take
        # whichever the double was built to read.
        symbol = symbol or kwargs.get("tradingsymbol") or kwargs.get("symbol")
        side = side or kwargs.get("transaction_type") or kwargs.get("side")
        quantity = size if size is not None else kwargs.get("quantity")

        for drop in ("tradingsymbol", "transaction_type", "quantity", "symbol",
                     "side", "size", "price"):
            kwargs.pop(drop, None)

        # A Kite futures tradingsymbol ends in FUT; an option's ends in CE/PE.
        # That is how the real contract master distinguishes them, so it is how
        # the double picks which recorder the test is watching.
        is_future = str(symbol or "").upper().endswith("FUT")
        place = getattr(self, "place_order_future" if is_future else "place_order_option", None)
        if place is None:
            place = getattr(self, "place_order_option", None) or getattr(self, "place_order_future")
        return await place(symbol, str(side or "").lower(), quantity, **kwargs)
