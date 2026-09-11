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
