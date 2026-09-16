"""Broker identity for a Snapback underlying, captured once on Day-T.

Reconstructing `f"NSE:{symbol}"` at T+1 assumes the canonical Sterling name is also
the provider's. It is not: SENSEX trades on BSE with options on BFO, indices carry
provider symbols like "NIFTY 50", and a rebuilt symbol either fails outright or —
worse — resolves to a different instrument. The identity observed at signal time is
stored and is the only identity the entry path may use.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)


class IdentityError(Exception):
    """The broker identity for this underlying is missing or unusable."""


# Cash exchange -> the exchange its derivatives trade on.
_OPTION_EXCHANGE = {
    "NSE": "NFO",
    "BSE": "BFO",
}

VALID_CASH_EXCHANGES = frozenset(_OPTION_EXCHANGE)


@dataclass(frozen=True)
class UnderlyingIdentity:
    canonical_symbol: str

    cash_exchange: str
    cash_tradingsymbol: str
    cash_instrument_token: int

    option_exchange: str
    option_underlying_name: str

    def validate(self) -> None:
        """Raise unless this identity can actually address the broker."""
        if not self.canonical_symbol:
            raise IdentityError("identity has no canonical symbol")
        if self.cash_exchange not in VALID_CASH_EXCHANGES:
            raise IdentityError(
                f"{self.canonical_symbol}: unsupported cash exchange "
                f"{self.cash_exchange!r}"
            )
        if self.option_exchange != _OPTION_EXCHANGE[self.cash_exchange]:
            raise IdentityError(
                f"{self.canonical_symbol}: option exchange {self.option_exchange!r} "
                f"does not belong to cash exchange {self.cash_exchange!r}"
            )
        if not self.cash_tradingsymbol:
            raise IdentityError(f"{self.canonical_symbol}: no provider trading symbol")
        if not self.cash_instrument_token:
            raise IdentityError(f"{self.canonical_symbol}: no provider instrument token")

    def cash_quote_key(self) -> str:
        """The quote key for the underlying, built from the stored exchange."""
        return f"{self.cash_exchange}:{self.cash_tradingsymbol}"

    def option_quote_key(self, tradingsymbol: str) -> str:
        return f"{self.option_exchange}:{tradingsymbol}"

    def as_row(self) -> Dict[str, Any]:
        return {
            "cash_exchange": self.cash_exchange,
            "cash_tradingsymbol": self.cash_tradingsymbol,
            "cash_instrument_token": int(self.cash_instrument_token or 0),
            "option_exchange": self.option_exchange,
            "option_underlying_name": self.option_underlying_name,
        }


def _get(row: Any, key: str, default=None):
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def identity_from_instrument(row: Any, *, canonical_symbol: str) -> UnderlyingIdentity:
    """Build the identity from an actual instrument-master row."""
    exchange = str(_get(row, "exchange", "") or "").upper()
    if not exchange:
        segment = str(_get(row, "segment", "") or "").upper()
        exchange = segment.split("-")[0] if segment else ""

    return UnderlyingIdentity(
        canonical_symbol=canonical_symbol,
        cash_exchange=exchange,
        cash_tradingsymbol=str(_get(row, "tradingsymbol", "") or ""),
        cash_instrument_token=int(
            _get(row, "instrument_token", 0) or _get(row, "token", 0) or 0
        ),
        option_exchange=_OPTION_EXCHANGE.get(exchange, ""),
        option_underlying_name=str(
            _get(row, "name", "") or canonical_symbol
        ),
    )


def identity_from_opportunity(row: Any) -> UnderlyingIdentity:
    """Read back the identity stored on Day-T. A later lookup cannot override it."""
    if row is None:
        raise IdentityError("no opportunity row")

    identity = UnderlyingIdentity(
        canonical_symbol=str(_get(row, "symbol", "") or ""),
        cash_exchange=str(_get(row, "cash_exchange", "") or "").upper(),
        cash_tradingsymbol=str(_get(row, "cash_tradingsymbol", "") or ""),
        cash_instrument_token=int(_get(row, "cash_instrument_token", 0) or 0),
        option_exchange=str(_get(row, "option_exchange", "") or "").upper(),
        option_underlying_name=str(_get(row, "option_underlying_name", "") or ""),
    )
    identity.validate()
    return identity
