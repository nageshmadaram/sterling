"""Which contract a Snapback signal buys, and what it is modelled to cost.

The pick is by DELTA. Every other engine here picks by a moneyness ladder —
ITM2 / ATM / OTM1 — and that is the right vocabulary when an operator is
choosing a strike by hand. It is the wrong one for a rule, because the same
rung is a different amount of leverage at every vol level and tenor: two strikes
in the money on a 10-vol index is a 0.75 delta and on a 30-vol stock is a 0.58.
A sweep over rungs measures the vol regime and reports it as a strike effect.

So the config names a delta, this module finds the listed strike nearest to it,
and the moneyness label is DERIVED for display rather than being the input.

The premium is MODELLED whenever there is no live quote, and every modelled
number travels with the assumption that produced it. A modelled premium that
looks like a quote is the failure this repo has already paid for.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from app.engines.option_contracts import ContractSpec, canonical, spec_for

from .config import SnapbackConfig
from .models import SnapbackSignal
from .pricing import bs_delta, bs_price, strike_for_delta


@dataclass(frozen=True)
class Pick:
    """One contract, plus whether its premium is a price or an assumption."""

    underlying: str
    symbol: str
    option_type: str
    strike: float
    lot_size: int
    exchange: str
    #: Days to expiry ASSUMED for the model. None when a real expiry is known
    #: and has been used instead.
    dte: Optional[int]
    expiry: Optional[str]
    premium: float
    delta: float
    moneyness: str
    #: True when ``premium`` came out of Black-Scholes rather than off a book.
    modelled: bool
    #: The vol the model was run at, annualised. None when not modelled.
    modelled_iv: Optional[float] = None
    tick_size: float = 0.05

    def as_dict(self) -> dict[str, Any]:
        return {
            "underlying": self.underlying,
            "symbol": self.symbol,
            "option_type": self.option_type,
            "strike": self.strike,
            "lot_size": self.lot_size,
            "exchange": self.exchange,
            "dte": self.dte,
            "expiry": self.expiry,
            "premium": round(self.premium, 2),
            "delta": round(self.delta, 3),
            "moneyness": self.moneyness,
            "estimated": self.modelled,
            "modelled_iv": (round(self.modelled_iv, 4)
                            if self.modelled_iv is not None else None),
            "tick_size": self.tick_size,
        }


def moneyness_label(spot: float, strike: float, call: bool) -> str:
    """ITM / ATM / OTM, derived from where the strike actually sits.

    Derived and not stored. The engine's input is a delta, so a stored label
    would be a second, drifting account of the same fact.
    """
    if spot <= 0 or strike <= 0:
        return "ATM"
    gap = (strike - spot) / spot
    if abs(gap) < 0.0025:
        return "ATM"
    in_the_money = gap < 0 if call else gap > 0
    return "ITM" if in_the_money else "OTM"


def pick_for(signal: SnapbackSignal, cfg: SnapbackConfig, *,
             dte: Optional[int] = None, expiry: Optional[str] = None,
             spot: Optional[float] = None,
             iv: Optional[float] = None) -> Optional[Pick]:
    """The contract this signal would buy, priced at the modelled vol.

    ``None`` when the instrument has no published strike step — inventing one
    yields a strike that is not listed, which reads on a board exactly like a
    real contract.
    """
    spec = spec_for(signal.symbol)
    if spec is None:
        return None
    S = float(spot if spot is not None else signal.entry)
    if S <= 0:
        return None
    call = signal.option_type == "CE"
    days = int(dte if dte is not None else cfg.min_dte)
    years = max(days, 1) / 365.0
    vol = float(iv if iv is not None else signal.assumed_iv)
    if vol <= 0:
        return None

    strike = float(strike_for_delta(S, vol, years, cfg.target_delta,
                                    call=call, step=spec.strike_step))
    premium = float(bs_price(S, strike, years, vol, call=call))
    delta = float(bs_delta(S, strike, years, vol, call=call))
    pretty = int(strike) if float(strike).is_integer() else strike
    return Pick(
        underlying=spec.underlying,
        symbol=f"{spec.underlying} {pretty} {signal.option_type}",
        option_type=signal.option_type,
        strike=strike,
        lot_size=spec.lot_size,
        exchange=spec.exchange,
        dte=days,
        expiry=expiry,
        premium=premium,
        delta=delta,
        moneyness=moneyness_label(S, strike, call),
        modelled=True,
        modelled_iv=vol,
    )


def lots_for(pick: Pick, cfg: SnapbackConfig) -> int:
    """How many lots the premium budget buys.

    A bought option's maximum loss IS its premium, so the budget is stated in
    premium rather than in distance-to-a-stop. Returns 0 rather than 1 when a
    single lot breaks the budget: squeezing in the minimum lot is how a 2%
    position quietly becomes an 11% one on an expensive contract.
    """
    if cfg.sizing_mode == "LOTS":
        return max(1, min(int(cfg.lots), int(cfg.max_lots)))
    outlay_per_lot = max(pick.premium, 0.0) * max(pick.lot_size, 1)
    if outlay_per_lot <= 0:
        return 0
    budget = cfg.capital_inr * cfg.premium_pct_of_capital / 100.0
    return int(min(budget // outlay_per_lot, cfg.max_lots))
