"""Which option a signal on an underlying would actually buy.

The board's whole job is to name a tradable thing. A row that says
``NIFTY / EQUITY`` and quotes 23,438 is naming the index, and an operator
cannot buy the index — so the row is describing the thesis and calling it a
trade.

Live, the scanner resolves a real contract from the broker's instrument dump.
The replayed history has no broker and no dump, so it resolves the strike
ARITHMETICALLY: the ATM strike is the spot rounded to the instrument's strike
step, which is a published property of the instrument and not a guess.

The expiry is deliberately NOT guessed. Weekly expiry weekdays have changed
more than once and differ by exchange, so a date computed from a rule here
would be wrong for some of the history it is labelling. A replayed row says
which STRIKE and which SIDE — both of which are determined — and leaves the
expiry unset, which the board renders as unknown rather than as a claim.
"""
from __future__ import annotations

from typing import Optional

# The instrument registry is shared. It used to be defined here, which made lot
# sizes and strike steps the intraday pack's private knowledge — so the next
# engine that needed a strike step either imported across an engine boundary or
# retyped the table. These names are re-exported so every existing import of
# ``intraday.contracts`` keeps working and resolves to the one table.
from app.engines.option_contracts import (  # noqa: F401
    CANONICAL, INDEX_NAMES, STOCK_NAMES, SPECS, ContractSpec, atm_strike,
    canonical, dedupe, spec_for, unknown,
)


def estimated_contract(underlying: str, spot: float,
                       option_type: str) -> Optional[dict]:
    """The contract a replayed signal would have bought, as far as it is known.

    ``expiry`` is None ON PURPOSE. Weekly expiry weekdays have changed more
    than once and differ by exchange, so a date computed from a rule here would
    be wrong for part of the history it is labelling. The strike and the side
    are determined; the expiry is not, and the row says so rather than
    inventing one.

    ``estimated`` travels with it so nothing downstream can mistake this for a
    contract resolved against the broker's real instrument list.
    """
    spec = spec_for(underlying)
    strike = atm_strike(underlying, spot)
    if spec is None or strike is None:
        return None
    pretty = int(strike) if float(strike).is_integer() else strike
    return {
        "symbol": f"{spec.underlying} {pretty} {option_type}",
        "strike": strike,
        "option_type": option_type,
        "expiry": None,
        "dte": None,
        "lot_size": spec.lot_size,
        "token": 0,
        "exchange": spec.exchange,
        "tick_size": 0.05,
        "estimated": True,
        "moneyness": "ATM",
    }
