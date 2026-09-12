"""The contract vocabulary every option engine shares.

One definition per idea, so a value that means something on one strategy's
settings page means exactly the same on another's. These lived only inside
``nifty_orb_options`` and were re-typed by hand in each new engine, which is how
two pages end up offering different words for the same choice — the reader has
to learn a private vocabulary per strategy, and a config copied between them
silently stops meaning what it said.

Nothing here is behaviour. It is the shared *words*, and each engine still
decides what to do with them.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Bar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


#: How an engine picks among the listed expiries it is offered.
#:
#: ``nearest``  the soonest eligible contract
#: ``weekly``   weekly series only (indices; NSE lists no weekly stock options)
#: ``monthly``  monthly series only
#: ``any``      no preference beyond the DTE window
EXPIRY_SELECTIONS: frozenset[str] = frozenset({"nearest", "weekly", "monthly", "any"})

#: The listed series an instrument can have. Single stocks are monthly-only on
#: NSE, which is why several engines carry separate index and stock lists.
EXPIRY_SERIES: frozenset[str] = frozenset({"weekly", "monthly"})

#: Where a strike sits against the money.
MONEYNESS: frozenset[str] = frozenset({"ATM", "ITM", "OTM"})

#: The order contract settings are presented in, everywhere. Kept here so a new
#: engine's settings page has one obvious answer rather than inventing a layout:
#:
#:   Instruments -> Contracts -> (strategy's own sections) -> Session -> Risk
#:
#: "Instruments" is what is watched (indices, single stocks). "Contracts" is
#: which strike and expiry the signal is expressed through. Calling either of
#: them "Universe" merges the two questions into one word.
SECTION_ORDER: tuple[str, ...] = (
    "Instruments", "Contracts", "Session", "Risk",
)


# ── The instrument registry ─────────────────────────────────────────────────
#
# Lot sizes and strike steps are exchange-published facts. They lived inside the
# intraday pack, which made them that pack's private knowledge — so the next
# engine that needed a strike step either imported across an engine boundary or
# retyped the table, and a retyped lot size is a wrong quantity on a real order.
#
# They are here now, and ``intraday.contracts`` re-exports them, so both spellings
# resolve to one table.


@dataclass(frozen=True)
class ContractSpec:
    """Everything about an instrument that decides what a signal trades."""

    underlying: str
    #: Distance between listed strikes.
    strike_step: float
    lot_size: int
    #: Where its options are listed. SENSEX and BANKEX are BSE, the rest NSE.
    exchange: str = "NFO"
    is_index: bool = False


#: Display name -> the name options are listed under.
#:
#: The OHLCV store keeps index candles under their index names ("NIFTY 50")
#: while options are listed under the trading name ("NIFTY"). Both spellings
#: reach every engine here — from the store, from a config an operator edited,
#: and from the universe builder — and treating them as two instruments
#: produces two identical rows for one signal.
CANONICAL: dict[str, str] = {
    "NIFTY 50": "NIFTY",
    "NIFTY BANK": "BANKNIFTY",
    "NIFTY FIN SERVICE": "FINNIFTY",
    "NIFTY MID SELECT": "MIDCPNIFTY",
    "NIFTY NEXT 50": "NIFTYNXT50",
    "BSE SENSEX": "SENSEX",
}

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
    returned upper-cased rather than dropped — refusing them belongs to whoever
    validates a universe, and silently discarding a name an operator typed is
    worse than telling them it is not tradable.
    """
    key = str(name or "").strip().upper()
    return CANONICAL.get(key, key)


def dedupe(names) -> tuple[str, ...]:
    """Canonicalise and drop repeats, preserving the operator's order."""
    out: list[str] = []
    for n in names or ():
        c = canonical(n)
        if c and c not in out:
            out.append(c)
    return tuple(out)


def unknown(names) -> list[str]:
    """Names with no known contract spec, canonicalised first."""
    return sorted({c for c in (canonical(n) for n in names or ()) if c and c not in SPECS})


#: Lot sizes and strike steps derived from the LIVE instrument dump, written by
#: ``study.snapback_backfill``. Loaded lazily and cached.
#:
#: This exists because the hardcoded table above was STALE, and stale here is a
#: wrong quantity on a real order: it had NIFTY at 75 (it is 65), TATASTEEL at a
#: lot of 5500 on a 1.0 step (it is 2750 on 2.5), TCS at 175 (225) and SBIN on a
#: 5.0 step (10.0). Lot sizes are revised by the exchange and a table typed by
#: hand drifts silently from the day it is written.
_GENERATED: dict[str, "ContractSpec"] | None = None


def _generated() -> dict[str, "ContractSpec"]:
    global _GENERATED
    if _GENERATED is None:
        _GENERATED = {}
        try:
            import json
            import pathlib as _p
            raw = json.loads(
                (_p.Path(__file__).with_name("instrument_specs.json")).read_text())
            for name, v in dict(raw).items():
                step = float(v.get("strike_step") or 0)
                lot = int(v.get("lot_size") or 0)
                if step > 0 and lot > 0:
                    _GENERATED[str(name).upper()] = ContractSpec(
                        str(name).upper(), step, lot,
                        str(v.get("exchange") or "NFO"),
                        str(name).upper() in INDEX_NAMES)
        except Exception:                                          # noqa: BLE001
            # No file, or an unreadable one, leaves the hardcoded table in
            # charge. That is the safe direction: fewer instruments, never
            # wrong numbers for the ones that are there.
            _GENERATED = {}
    return _GENERATED


def spec_for(underlying: str):
    """The exchange's numbers when they are available, ours when they are not.

    Generated FIRST. The hardcoded table is the fallback for a checkout with no
    dump, and it is the one that goes stale.
    """
    key = canonical(underlying)
    return _generated().get(key) or SPECS.get(key)


def known_underlyings() -> tuple[str, ...]:
    """Every instrument a contract can be named for, generated plus hardcoded."""
    return tuple(sorted(set(_generated()) | set(SPECS)))


def atm_strike(underlying: str, spot: float):
    """Spot rounded to the instrument's own strike step. ``None`` when unknown.

    Arithmetic, not a guess — the step is a published property. Inventing one
    produces a strike that does not exist.
    """
    spec = spec_for(underlying)
    if spec is None or not spot or spot <= 0:
        return None
    step = spec.strike_step
    return round(round(float(spot) / step) * step, 2)
