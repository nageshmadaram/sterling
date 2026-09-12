"""Taking the market out of the trade, and what that costs.

**Why.** Measured over nine years and 202 underlyings, the signal has a real
excess of about +1.5 percentage points against a day-matched unconditional put —
the instruments it picks do under-perform — and the book still loses, because a
bought put is a large SHORT position in a market that rose for nine years. The
edge is relative; the expression is directional. This module removes the
difference.

**Why a linear hedge and not a second bought option.** The obvious
options-only version is to buy an index CALL alongside the put. Measured, that
beat the analytically-hedged book by almost a point — which is the tell that it
is not a hedge. A bought call is convex: over a rising sample it gains MORE than
the put's market loss, so the "hedge" quietly becomes a second long-market,
long-vol position that happens to have been on the right side of the tape. An
index FUTURE offsets exactly, by construction, and cannot flatter the result.

**What it costs.** A future is margin, not premium, so the hedge does not consume
the premium budget — but it does consume margin, and that is a real constraint
this engine cannot see. The round trip is charged on NOTIONAL at the index
futures schedule, which is roughly 4 basis points and about two orders of
magnitude cheaper per rupee of exposure than the option schedule.

**Beta is estimated causally**, on a trailing window, and never from the window
the trade runs in.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional

import numpy as np

from .models import Bars, ist_day

#: How the market exposure is removed.
#:
#: ``none``            the directional put. Measured -1.08% per entry day.
#: ``index_futures``   short the put's market delta with an index future.
HEDGE_MODES: frozenset[str] = frozenset({"none", "index_futures"})

#: Sessions of trailing returns behind the beta estimate. Long enough to be
#: stable, short enough to follow a name whose beta actually moves.
BETA_WINDOW = 60

#: Beta is CLAMPED. An unclamped regression on a name with a quiet trailing
#: window produces betas above three, and a hedge sized off one of those is a
#: bigger market position than the trade it was meant to neutralise.
BETA_BOUNDS: tuple[float, float] = (0.2, 2.5)


@dataclass(frozen=True)
class FuturesCost:
    """One round trip on an index future, charged on NOTIONAL.

    A different schedule from the option one and on a different base. Charging
    the option schedule against an index notional overstates the cost by roughly
    two orders of magnitude — a mistake this repo has made and shipped a number
    from.
    """

    brokerage_per_order: float = 20.0
    #: STT on the SELL side of a future, on notional.
    stt_sell_pct: float = 0.02
    exchange_pct: float = 0.0019
    gst_pct: float = 18.0
    misc_pct: float = 0.0021
    #: Half the spread on an index future, as a percentage of notional. Two
    #: orders of magnitude tighter than a monthly single-stock option's.
    slippage_pct: float = 0.01
    #: Annualised cost of CARRY on the hedge, as a percentage of notional.
    #:
    #: The hedge is LONG index futures — the put is short the market, so the
    #: offset is long it — and a future trades above spot by the cost of carry,
    #: ``(r - q)``, converging down to spot as it expires. A long position pays
    #: that convergence.
    #:
    #: It matters because the harness prices the hedge off SPOT index returns,
    #: which already contain the full equity return, so the modelled hedge is
    #: too generous by exactly the basis. The number is not small: the hedge
    #: notional runs about six times the option's premium outlay, so 5.2% a year
    #: over a ten-session hold is roughly 0.85% of the outlay per trade against
    #: a mean trade return of +1.4%. Leaving it out flattered the book by more
    #: than half its edge.
    #:
    #: 5.2% = a ~6.5% risk-free against a ~1.3% Nifty dividend yield.
    carry_pct: float = 5.2

    def carry(self, notional: float, days: float) -> float:
        """Basis the long futures leg gives up over ``days`` sessions held."""
        if notional <= 0 or days <= 0:
            return 0.0
        return round(notional * self.carry_pct / 100.0 * float(days) / 365.0, 2)

    def round_trip(self, notional: float) -> float:
        if notional <= 0:
            return 0.0
        brokerage = 2 * self.brokerage_per_order
        exch = 2 * notional * self.exchange_pct / 100.0
        stt = notional * self.stt_sell_pct / 100.0
        gst = (brokerage + exch) * self.gst_pct / 100.0
        misc = 2 * notional * self.misc_pct / 100.0
        slip = 2 * notional * self.slippage_pct / 100.0
        return round(brokerage + exch + stt + gst + misc + slip, 2)


#: Betas keyed by the IDENTITY of the two tapes they were computed from.
#:
#: A replay recomputes every symbol's beta, and the permutation test replays the
#: whole book hundreds of times — 45 seconds of identical arithmetic per round.
#: Keyed on identity rather than content because a study loads its tapes once
#: and hands the same ``Bars`` objects to every replay; a different tape is a
#: different object and simply misses the cache rather than reading a stale one.
_BETA_CACHE: dict[tuple[int, int, int], dict[str, float]] = {}
_BETA_KEEP: list = []


def rolling_beta(bars: Bars, market: Bars, *,
                 window: int = BETA_WINDOW) -> dict[str, float]:
    """Day -> the instrument's beta against the market on trailing returns.

    Aligned on CALENDAR days rather than bar index: two instruments listed at
    different times have different bar counts for the same session, and pairing
    them by position silently regresses one name's June on another's July.

    ``out[day]`` uses returns STRICTLY BEFORE that session, so a trade entered on
    it is sized by a number that existed beforehand.
    """
    if len(bars) < window + 2 or len(market) < window + 2:
        return {}
    key = (id(bars), id(market), int(window))
    hit = _BETA_CACHE.get(key)
    if hit is not None:
        return hit
    m_at = {ist_day(float(market.time[i])): i for i in range(len(market))}
    m_ret = np.diff(np.log(np.maximum(market.close, 1e-12)), prepend=np.nan)
    s_ret = np.diff(np.log(np.maximum(bars.close, 1e-12)), prepend=np.nan)
    days = [ist_day(float(t)) for t in bars.time]
    out: dict[str, float] = {}
    lo, hi = BETA_BOUNDS
    for i in range(window + 1, len(bars)):
        j = m_at.get(days[i])
        if j is None or j < window + 1:
            continue
        sr = s_ret[i - window:i]
        mr = m_ret[j - window:j]
        if len(sr) != len(mr):
            continue
        if not (np.all(np.isfinite(sr)) and np.all(np.isfinite(mr))):
            continue
        var = float(np.var(mr, ddof=1))
        if var <= 0:
            continue
        b = float(np.cov(sr, mr, ddof=1)[0, 1] / var)
        out[days[i]] = float(min(max(b, lo), hi))
    _BETA_CACHE[key] = out
    # Hold a reference so these ids cannot be reused by a different object while
    # the cache still answers for them.
    _BETA_KEEP.append((bars, market))
    return out


def clear_beta_cache() -> None:
    _BETA_CACHE.clear()
    _BETA_KEEP.clear()


def market_pnl(*, delta: float, beta: float, spot_in: float, qty: int,
               market_in: float, market_out: float) -> float:
    """Rupees of the trade's P&L that came from the MARKET, to first order.

    First order is the right order here: the position is a 0.70-delta option, so
    delta explains almost all of it, and the gamma term over a ten-session hold
    on an index-sized move is small against the spread being measured. It is
    also the term a futures hedge removes EXACTLY, which is the point — the
    residual of a first-order hedge is a real cost of hedging, not an artefact
    of the estimate.
    """
    if market_in <= 0 or qty <= 0:
        return 0.0
    return float(delta) * float(beta) * (float(market_out) / float(market_in) - 1.0) \
        * float(spot_in) * int(qty)


def hedge_notional(*, delta: float, beta: float, spot_in: float,
                   qty: int) -> float:
    """Rupees of index exposure the hedge has to carry."""
    return abs(float(delta) * float(beta) * float(spot_in) * int(qty))


def rebalanced(*, deltas: "list[float]", beta: float, spots: "list[float]",
               qty: int, market: "list[float]", cost: Optional[FuturesCost] = None
               ) -> tuple[float, float]:
    """Market P&L removed by a DAILY-rebalanced hedge, and what it cost.

    ``deltas[t]``, ``spots[t]`` and ``market[t]`` are the option's delta, the
    instrument's close and the index's close on each session held, entry first.
    The hedge carried over session t is sized on day t's delta and earns day
    t-to-t+1's index move — so nothing in it reads a price that had not printed.

    **Why not size it once at entry.** Because a static hedge cannot be shown to
    be right, only assumed to be. As it happens it very nearly is: measured, the
    static and rebalanced books differ by 0.05 percentage points per entry day
    (+2.21% against +2.16%), because the two errors offset — a put moving into
    the money has a bigger |delta|, but the spot its exposure is measured
    against has fallen by the same move.

    That offset is worth knowing rather than relying on. An audit that held the
    notional at the ENTRY spot and varied only the delta reported the residual
    at 27% of the edge; the full calculation says 2%. The lesson is the one this
    file exists for — a hedge you have not computed properly is a claim, and the
    cheap way to stop making claims is to compute it.

    Cost is the round trip on the opening notional plus the turnover of each
    adjustment. The drift is small — a ten-session hold moves the mean |delta|
    from 0.70 to 0.72 — so correctness here costs a few basis points.
    """
    cost = cost or FuturesCost()
    n = min(len(deltas), len(spots), len(market))
    if n < 2 or qty <= 0:
        return 0.0, 0.0
    removed = 0.0
    prev_notional = 0.0
    turnover = 0.0
    carry = 0.0
    for t in range(n - 1):
        if market[t] <= 0:
            continue
        exposure = float(deltas[t]) * float(beta) * float(spots[t]) * int(qty)
        removed += exposure * (float(market[t + 1]) / float(market[t]) - 1.0)
        # One session of basis convergence on the position actually carried.
        carry += cost.carry(abs(exposure), 1.0)
        turnover += abs(abs(exposure) - prev_notional)
        prev_notional = abs(exposure)
    # Closing the last position is turnover too.
    turnover += prev_notional
    return removed, round(cost.round_trip(turnover / 2.0) + carry, 2)


def apply(net: float, *, delta: float, beta: float, spot_in: float, qty: int,
          market_in: float, market_out: float,
          cost: Optional[FuturesCost] = None) -> tuple[float, float, float]:
    """Net after a STATIC entry-delta hedge, the market P&L removed, and its cost.

    Kept for a caller with only two market marks, which cannot do better.
    :func:`rebalanced` is what the replay uses; measured, the two differ by
    0.05 percentage points per entry day.
    """
    cost = cost or FuturesCost()
    removed = market_pnl(delta=delta, beta=beta, spot_in=spot_in, qty=qty,
                         market_in=market_in, market_out=market_out)
    charged = cost.round_trip(hedge_notional(delta=delta, beta=beta,
                                             spot_in=spot_in, qty=qty))
    return float(net) - removed - charged, removed, charged
