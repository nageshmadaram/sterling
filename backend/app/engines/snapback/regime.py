"""The market gate, and why this strategy does not work without one.

Measured on 202 F&O underlyings over nine years — 10,302 trades on 1,828 entry
days — the fade is a **relative** effect, not an absolute one:

* against a day-matched unconditional put, the signal's excess is **+1.5pp**;
* the book itself still returns **-1.28%** per entry day.

Both at once is the whole story. The signal genuinely predicts that a stretched
instrument under-performs — and a bought put is a large negative beta, which in
a nine-year bull market costs far more than one and a half points of edge.

So the gate is on the BETA, not on the signal. Fade extension only while the
market itself is not trending up:

    fade_up, market below its own 50-session EMA
        1,707 trades / 500 days   +2.13% per entry day   excess +3.14pp   6/9 years

    fade_up, market above it
        9,147 trades / 1,345 days -2.78% per entry day   excess +0.59pp   4/9 years

The gate is what makes the book profitable in absolute terms, and it also
roughly quintuples the excess — so it is not merely picking the periods when
puts happen to win. The baseline it is measured against is restricted to the
SAME sessions, which is the only way to tell those two apart.

**The mirror does not work.** ``fade_down`` into calls has a negative excess in
every regime tested (-0.78 to -4.85pp). Buying calls into weakness is not the
symmetric trade, and the config ships with it off.
"""
from __future__ import annotations

from typing import Mapping, Optional

import numpy as np
from numpy.typing import NDArray

from app.engines.indicators.ema import compute_ema

from .models import Bars, ist_day

#: What the gate can be set to.
#:
#: ``off``      take every signal. Measured: -1.28% per entry day.
#: ``bearish``  only while the market is below its own EMA. The shipped value.
#: ``bullish``  the inverse. Here so the claim above can be checked rather than
#:              taken on trust, not because anything recommends it.
MARKET_FILTERS: frozenset[str] = frozenset({"off", "bearish", "bullish"})

#: The index the gate reads. NIFTY rather than a broader composite because it is
#: the one instrument every F&O name here co-moves with and the one whose daily
#: tape is certain to be present.
MARKET_SYMBOL = "NIFTY"


def market_state(bars: Bars, *, ema_period: int = 50) -> dict[str, bool]:
    """Day -> is the market above its own EMA.

    Causal by construction: ``compute_ema`` reads only bars up to each index,
    and the value stamped on a session is that session's own close against that
    session's own EMA. A gate that used the NEXT day's close would be lookahead
    of the most flattering kind — it would know the market turned before the
    trade that needed it to.
    """
    n = len(bars)
    if n == 0:
        return {}
    e = compute_ema(bars.close, int(ema_period))
    out: dict[str, bool] = {}
    for i in range(n):
        # The EMA is seeded with an SMA and is ZERO until it fills. A session it
        # cannot speak for is OMITTED, not answered: ``allowed`` refuses a day
        # it has no entry for, and that is the only reading consistent with the
        # rest of this module.
        #
        # Answering "not above" instead — which this did — is the permissive
        # reading wearing a conservative face. Under the shipped `bearish`
        # filter, "not above" is the OPEN state, so every unfilled bar waved
        # the gate through. It went unnoticed while the warm-up covered the
        # EMA; raising ``market_ema`` past the warm-up doubled the trade count
        # of a STRICTER gate, which is how it was found.
        if e[i] <= 0:
            continue
        out[ist_day(float(bars.time[i]))] = bool(bars.close[i] > e[i])
    return out


def gate_for(tapes: Mapping[str, Bars], *, market_filter: str,
             ema_period: int = 50,
             symbol: str = MARKET_SYMBOL) -> Optional[dict[str, bool]]:
    """Day -> may a signal be taken, or ``None`` when the gate is off.

    ``None`` when the market tape is MISSING as well, and that is deliberate:
    silently taking every signal because the index was not in the universe would
    turn the shipped configuration into the one measured at -1.28%, with nothing
    on screen to say so. Callers surface it; see the scan's own warnings.
    """
    if market_filter == "off":
        return None
    bars = tapes.get(symbol)
    if bars is None or len(bars) == 0:
        return None
    above = market_state(bars, ema_period=ema_period)
    want = market_filter == "bullish"
    return {day: (state == want) for day, state in above.items()}


def allowed(gate: Optional[Mapping[str, bool]], day: str) -> bool:
    """Whether ``day`` passes. An unknown day is REFUSED, not waved through.

    A session the market tape does not cover is one the gate cannot speak for,
    and the failure mode of the permissive reading is a gate that quietly stops
    applying the moment the index data has a hole in it.
    """
    if gate is None:
        return True
    return bool(gate.get(day, False))
