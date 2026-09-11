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

from dataclasses import dataclass
from typing import Optional

#: Display name -> the name options are listed under.
#:
#: The OHLCV store keeps index candles under their index names ("NIFTY 50")
#: while options are listed under the trading name ("NIFTY"). Both spellings
#: reach this engine — from the store, from a config an operator edited, and
#: from the universe builder — and treating them as two instruments produces
#: two identical rows for one signal, which is what it did.
CANONICAL: dict[str, str] = {
    "NIFTY 50": "NIFTY",
    "NIFTY BANK": "BANKNIFTY",
    "NIFTY FIN SERVICE": "FINNIFTY",
    "NIFTY MID SELECT": "MIDCPNIFTY",
    "NIFTY NEXT 50": "NIFTYNXT50",
    "BSE SENSEX": "SENSEX",
}


@dataclass(frozen=True)
class ContractSpec:
    """Everything about an instrument that decides what a signal trades."""

    underlying: str
    #: Distance between listed strikes.
    strike_step: float
    lot_size: int
    #: Where its options are listed. SENSEX is BSE, everything else NSE.
    exchange: str = "NFO"
    is_index: bool = False


#: The instruments this pack knows how to name a contract for.
#:
#: Lot sizes and strike steps are exchange-published facts, kept here so a
#: replayed row can name a contract without a broker session. They move
#: occasionally; a wrong lot size shows a wrong quantity, which is why the
#: live path reads the real one from the instrument dump and only the
#: history falls back to this.
SPECS: dict[str, ContractSpec] = {
    "NIFTY": ContractSpec("NIFTY", 50.0, 75, "NFO", True),
    "BANKNIFTY": ContractSpec("BANKNIFTY", 100.0, 30, "NFO", True),
    "FINNIFTY": ContractSpec("FINNIFTY", 50.0, 65, "NFO", True),
    "MIDCPNIFTY": ContractSpec("MIDCPNIFTY", 25.0, 120, "NFO", True),
    "NIFTYNXT50": ContractSpec("NIFTYNXT50", 100.0, 25, "NFO", True),
    "SENSEX": ContractSpec("SENSEX", 100.0, 20, "BFO", True),
    "RELIANCE": ContractSpec("RELIANCE", 10.0, 500),
    "HDFCBANK": ContractSpec("HDFCBANK", 10.0, 550),
    "ICICIBANK": ContractSpec("ICICIBANK", 10.0, 700),
    "INFY": ContractSpec("INFY", 20.0, 400),
    "TCS": ContractSpec("TCS", 20.0, 175),
    "SBIN": ContractSpec("SBIN", 5.0, 750),
    "AXISBANK": ContractSpec("AXISBANK", 10.0, 625),
    "BAJFINANCE": ContractSpec("BAJFINANCE", 10.0, 750),
    "BAJAJFINSV": ContractSpec("BAJAJFINSV", 10.0, 500),
    "BHARTIARTL": ContractSpec("BHARTIARTL", 10.0, 475),
    "LT": ContractSpec("LT", 20.0, 150),
    "KOTAKBANK": ContractSpec("KOTAKBANK", 10.0, 400),
    "ADANIENT": ContractSpec("ADANIENT", 10.0, 300),
    "ADANIPORTS": ContractSpec("ADANIPORTS", 10.0, 800),
    "TATASTEEL": ContractSpec("TATASTEEL", 1.0, 5500),
}

INDEX_NAMES: frozenset[str] = frozenset(k for k, v in SPECS.items() if v.is_index)
STOCK_NAMES: frozenset[str] = frozenset(k for k, v in SPECS.items() if not v.is_index)


def canonical(name: str) -> str:
    """The name this instrument's OPTIONS are listed under.

    Idempotent, so it is safe to apply on read and on write. Unknown names are
    returned upper-cased rather than dropped — refusing them is
    :func:`validate_universe`'s job, and silently discarding a name an operator
    typed is worse than telling them it is not tradable.
    """
    key = str(name or "").strip().upper()
    return CANONICAL.get(key, key)


def dedupe(names: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    """Canonicalise and drop repeats, preserving the operator's order.

    ``("NIFTY", "NIFTY 50")`` is ONE instrument. Scanning it twice produced two
    identical rows on the board for one signal, each inviting a separate trade.
    """
    out: list[str] = []
    for n in names or ():
        c = canonical(n)
        if c and c not in out:
            out.append(c)
    return tuple(out)


def unknown(names: tuple[str, ...] | list[str]) -> list[str]:
    """Names with no known contract spec, canonicalised first."""
    return sorted({c for c in (canonical(n) for n in names or ()) if c and c not in SPECS})


def spec_for(underlying: str) -> Optional[ContractSpec]:
    return SPECS.get(canonical(underlying))


def atm_strike(underlying: str, spot: float) -> Optional[float]:
    """The at-the-money strike: spot rounded to the instrument's strike step.

    Arithmetic, not a guess — the step is a published property. ``None`` when
    the instrument is not one this pack knows, because inventing a step would
    produce a strike that does not exist.
    """
    spec = spec_for(underlying)
    if spec is None or not spot or spot <= 0:
        return None
    step = spec.strike_step
    return round(round(float(spot) / step) * step, 2)


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
