"""Quote keys built from stored identity, never from a canonical name.

``f"NFO:{symbol}"`` is right for an NSE index option and wrong for a SENSEX one,
which trades on BFO. A rebuilt key either fails outright or, worse, resolves to
a different instrument — and a position marked against a different instrument
is a wrong number that looks like a right one.

The identity observed at signal time is stored on the opportunity. Everything
after entry — MTM, intraday risk, hedging, exit, reconciliation — resolves its
venue from that record through this module. When the record cannot answer, the
caller gets an exception and records a quote refusal, rather than a guess.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

from app.core.logging import get_logger

log = get_logger(__name__)

#: Cash exchange -> the exchange its derivatives trade on.
_DERIVATIVE_EXCHANGE = {"NSE": "NFO", "BSE": "BFO"}

VALID_CASH_EXCHANGES = frozenset(_DERIVATIVE_EXCHANGE)
VALID_DERIVATIVE_EXCHANGES = frozenset(_DERIVATIVE_EXCHANGE.values())


class VenueUnknown(LookupError):
    """No stored venue for this position, so no quote key can be built."""

    code = "VENUE_IDENTITY_MISSING"


def quote_key(exchange: str, tradingsymbol: str) -> str:
    """``EXCHANGE:TRADINGSYMBOL``, with both halves checked.

    Refuses an empty or unknown exchange instead of producing ``":NIFTY"`` or
    ``"MCX:NIFTY"``, either of which would come back as a missing quote and be
    misread as an absent market rather than a broken lookup.
    """
    venue = str(exchange or "").strip().upper()
    symbol = str(tradingsymbol or "").strip()
    if not symbol:
        raise VenueUnknown("cannot build a quote key without a tradingsymbol")
    if venue not in VALID_CASH_EXCHANGES | VALID_DERIVATIVE_EXCHANGES:
        raise VenueUnknown(
            f"{VenueUnknown.code}: unusable exchange {exchange!r} for {symbol!r}"
        )
    return f"{venue}:{symbol}"


def derivative_exchange_for(cash_exchange: str) -> str:
    venue = str(cash_exchange or "").strip().upper()
    try:
        return _DERIVATIVE_EXCHANGE[venue]
    except KeyError:
        raise VenueUnknown(
            f"{VenueUnknown.code}: no derivative venue for cash exchange "
            f"{cash_exchange!r}"
        ) from None


class PositionVenue:
    """Where one position's three legs trade.

    ``cash`` prices the underlying; ``derivative`` prices both the option and
    the hedge future, which always share a venue with each other.
    """

    __slots__ = ("cash_exchange", "derivative_exchange", "cash_tradingsymbol", "source")

    def __init__(
        self,
        *,
        cash_exchange: str,
        derivative_exchange: str,
        cash_tradingsymbol: str,
        source: str,
    ) -> None:
        self.cash_exchange = cash_exchange
        self.derivative_exchange = derivative_exchange
        self.cash_tradingsymbol = cash_tradingsymbol
        self.source = source

    def spot_key(self) -> str:
        return quote_key(self.cash_exchange, self.cash_tradingsymbol)

    def derivative_key(self, tradingsymbol: str) -> str:
        return quote_key(self.derivative_exchange, tradingsymbol)

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return (
            f"PositionVenue({self.cash_exchange}/{self.derivative_exchange}"
            f" {self.cash_tradingsymbol!r} via {self.source})"
        )


def _venue_from_row(row: Mapping[str, Any], source: str) -> Optional[PositionVenue]:
    cash = str(row.get("cash_exchange") or "").strip().upper()
    if cash not in VALID_CASH_EXCHANGES:
        return None
    option_venue = str(row.get("option_exchange") or "").strip().upper()
    if option_venue not in VALID_DERIVATIVE_EXCHANGES:
        option_venue = derivative_exchange_for(cash)
    symbol = str(
        row.get("cash_tradingsymbol") or row.get("symbol") or ""
    ).strip()
    if not symbol:
        return None
    return PositionVenue(
        cash_exchange=cash,
        derivative_exchange=option_venue,
        cash_tradingsymbol=symbol,
        source=source,
    )


def venue_from_opportunity(row: Mapping[str, Any]) -> PositionVenue:
    """The venue recorded on an opportunity row. Raises when it is absent."""
    venue = _venue_from_row(row, "opportunity")
    if venue is None:
        raise VenueUnknown(
            f"{VenueUnknown.code}: opportunity "
            f"{row.get('opportunity_id') or '<unknown>'} has no stored exchange"
        )
    return venue


def resolve_position_venue(
    warehouse: Any, position: Mapping[str, Any], *, cache: Optional[dict] = None
) -> PositionVenue:
    """The venue for an open position, from what was stored at signal time.

    The position row is consulted first — newer rows carry their own venue — and
    the opportunity row is the fallback for positions opened before those
    columns existed. Neither answering raises: an unfindable venue must stop the
    mark, not silently become NSE/NFO.
    """
    opportunity_id = str(position.get("opportunity_id") or "")
    if cache is not None and opportunity_id in cache:
        return cache[opportunity_id]

    venue = _venue_from_row(position, "position")
    if venue is None:
        row = None
        try:
            row = warehouse.get_opportunity_by_id(opportunity_id)
        except Exception as exc:  # noqa: BLE001 - an unreadable store is an unknown
            raise VenueUnknown(
                f"{VenueUnknown.code}: opportunity {opportunity_id} unreadable: {exc}"
            ) from exc
        if row:
            venue = _venue_from_row(row, "opportunity")

    if venue is None:
        raise VenueUnknown(
            f"{VenueUnknown.code}: no stored exchange for position "
            f"{opportunity_id or '<unknown>'}; refusing to rebuild a quote key"
        )

    if cache is not None and opportunity_id:
        cache[opportunity_id] = venue
    return venue
