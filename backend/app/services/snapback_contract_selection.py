"""One selector, called by production and by the evidence recorder alike.

Runtime 1.6 forbids a second implementation of the pick. A research copy and a
production copy drift, and the drift is invisible until an audit compares two
numbers that were never computed by the same code — which is how the quarantined
replay came to select contracts at ``sigma=0.25`` while production used the
signal's own ``assumed_iv``.

DISCREPANCY WITH THE 1.6 SPECIFICATION, recorded rather than silently resolved.

The specification describes ``select_snapback_contract(candidates, inputs)`` as
choosing from the candidate universe. Production does not do that. The frozen
implementation in ``snapback.contracts.pick_for`` solves for a strike in closed
form::

    strike = strike_for_delta(S, vol, years, cfg.target_delta, call=..., step=...)

and rounds it to the instrument's published strike step. No chain is consulted,
and there is no code path in which the candidate list could influence the result.

Making the selection depend on candidates here would change which contract
Snapback buys, which is a strategy change during a frozen prospective
experiment. So the frozen behaviour is preserved exactly: ``candidates`` does not
and must not affect ``selected_strike``. The candidate universe is used for the
thing it can honestly answer — whether the computed strike was actually listed —
and that verdict is reported alongside the selection rather than overriding it.

``listed`` is therefore a measurement, not a gate. It records a defect class
nobody has measured: every Snapback trade to date assumed that a strike on the
step grid exists on the exchange, and nothing has ever checked it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Optional, Sequence

__all__ = [
    "SELECTOR_VERSION",
    "SelectionInputs",
    "SelectionResult",
    "SelectionError",
    "LISTED_YES",
    "LISTED_NO",
    "LISTED_UNKNOWN",
    "select_snapback_contract",
]

#: Bump on any change to the selection maths. Persisted with every selection, so
#: a later audit can tell which rule produced a stored contract.
SELECTOR_VERSION = "snapback_delta_closed_form_v1"

LISTED_YES = "LISTED"
LISTED_NO = "NOT_LISTED"
LISTED_UNKNOWN = "UNKNOWN"


class SelectionError(ValueError):
    """Inputs cannot produce a selection. Never resolved by a default."""


@dataclass(frozen=True)
class SelectionInputs:
    """Everything the frozen rule consumes. No field has an economic default."""

    opportunity_id: str
    underlying: str
    option_type: str                  # "CE" | "PE"
    spot: float
    assumed_iv: float
    target_delta: float
    dte_days: int
    strike_step: float
    valuation_ts: Optional[datetime] = None
    expiry: Optional[str] = None
    candidate_universe_hash: Optional[str] = None

    def __post_init__(self) -> None:
        if self.option_type not in ("CE", "PE"):
            raise SelectionError(f"option_type must be CE or PE, got {self.option_type!r}")
        # Each of these is a value the quarantined replay silently defaulted.
        if not self.spot or float(self.spot) <= 0:
            raise SelectionError("spot is required and must be positive; an unpriced underlying is INCONCLUSIVE")
        if self.assumed_iv is None or float(self.assumed_iv) <= 0:
            raise SelectionError("assumed_iv is required and must be positive; never substitute a house vol")
        if not (0.0 < float(self.target_delta) < 1.0):
            raise SelectionError(f"target_delta out of range: {self.target_delta!r}")
        if int(self.dte_days) <= 0:
            raise SelectionError("dte_days must be positive; a fabricated tenor is not a tenor")
        if float(self.strike_step) <= 0:
            raise SelectionError("strike_step is required; inventing one yields an unlisted strike")


@dataclass(frozen=True)
class SelectionResult:
    """The chosen contract, plus whether the exchange actually listed it."""

    opportunity_id: str
    underlying: str
    option_type: str
    selected_strike: float
    computed_delta: float
    expiry: Optional[str]
    dte_days: int
    spot: float
    assumed_iv: float
    target_delta: float
    strike_step: float
    selector_version: str
    candidate_universe_hash: Optional[str]
    #: LISTED / NOT_LISTED / UNKNOWN. A measurement, never a veto.
    listed: str
    selected_instrument_token: Optional[int] = None
    selected_tradingsymbol: Optional[str] = None
    selected_exchange: Optional[str] = None
    selected_lot_size: Optional[int] = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "opportunity_id": self.opportunity_id,
            "underlying": self.underlying,
            "option_type": self.option_type,
            "selected_strike": self.selected_strike,
            "computed_delta": self.computed_delta,
            "expiry": self.expiry,
            "dte_days": self.dte_days,
            "spot": self.spot,
            "assumed_iv": self.assumed_iv,
            "target_delta": self.target_delta,
            "strike_step": self.strike_step,
            "selector_version": self.selector_version,
            "candidate_universe_hash": self.candidate_universe_hash,
            "listed": self.listed,
            "selected_instrument_token": self.selected_instrument_token,
            "selected_tradingsymbol": self.selected_tradingsymbol,
            "selected_exchange": self.selected_exchange,
            "selected_lot_size": self.selected_lot_size,
        }


def select_snapback_contract(
    inputs: SelectionInputs,
    candidates: Optional[Sequence[Any]] = None,
) -> SelectionResult:
    """Apply the frozen rule, then report whether the result was listed.

    ``candidates`` is deliberately the second argument and deliberately optional:
    it cannot influence the strike, and passing it buys only the listedness
    verdict and the real contract's token, symbol and lot size when a match
    exists. Omitting it yields ``UNKNOWN`` rather than an assumed ``LISTED``.
    """
    from app.engines.snapback.pricing import bs_delta, strike_for_delta

    call = inputs.option_type == "CE"
    years = max(int(inputs.dte_days), 1) / 365.0

    # Identical call to the frozen production path. Any divergence here is a
    # strategy change, and the parity fixtures exist to make that impossible to
    # land by accident.
    strike = float(
        strike_for_delta(
            float(inputs.spot),
            float(inputs.assumed_iv),
            years,
            float(inputs.target_delta),
            call=call,
            step=float(inputs.strike_step),
        )
    )
    delta = float(bs_delta(float(inputs.spot), strike, years, float(inputs.assumed_iv), call=call))

    listed = LISTED_UNKNOWN
    token = symbol = exchange = None
    lot_size = None

    if candidates is not None:
        listed = LISTED_NO
        for c in candidates:
            c_type = getattr(c, "instrument_type", None)
            if c_type is not None and str(c_type).upper() != inputs.option_type:
                continue
            if inputs.expiry is not None and str(getattr(c, "expiry", "")) != str(inputs.expiry):
                continue
            if abs(float(getattr(c, "strike", float("nan"))) - strike) < 1e-6:
                listed = LISTED_YES
                token = getattr(c, "instrument_token", None)
                symbol = getattr(c, "tradingsymbol", None)
                exchange = getattr(c, "exchange", None)
                lot_size = getattr(c, "lot_size", None)
                break

    return SelectionResult(
        opportunity_id=inputs.opportunity_id,
        underlying=inputs.underlying.upper(),
        option_type=inputs.option_type,
        selected_strike=strike,
        computed_delta=delta,
        expiry=inputs.expiry,
        dte_days=int(inputs.dte_days),
        spot=float(inputs.spot),
        assumed_iv=float(inputs.assumed_iv),
        target_delta=float(inputs.target_delta),
        strike_step=float(inputs.strike_step),
        selector_version=SELECTOR_VERSION,
        candidate_universe_hash=inputs.candidate_universe_hash,
        listed=listed,
        selected_instrument_token=int(token) if token is not None else None,
        selected_tradingsymbol=str(symbol) if symbol is not None else None,
        selected_exchange=str(exchange) if exchange is not None else None,
        selected_lot_size=int(lot_size) if lot_size is not None else None,
    )
