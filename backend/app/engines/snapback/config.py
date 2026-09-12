"""Knobs for Snapback.

Every default below is either MEASURED on 2023-09-13..2026-09-11 daily bars for
19 F&O instruments, or is a judgement call that says so. ``CALIBRATED_FIELDS``
names the measured ones, so the settings page can mark the rest as unmeasured
rather than rendering a bare number a reader may take for a result.

The field this engine lives or dies by is ``target_delta``. It is the leverage,
and it is the reason this config has no "ITM2/ATM/OTM1" ladder: a fixed strike
offset is a different delta at every vol level and every tenor, so a ladder
control silently changes the size of the bet when vol moves.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Any, Iterable, Literal

#: Which way a setup may fire. ``fade_up`` buys puts into strength and is the
#: better-evidenced half; ``fade_down`` buys calls into weakness and is not.
SIDES: frozenset[str] = frozenset({"fade_up", "fade_down"})

#: How the trade ends. ``horizon`` is the measured one — the edge is a mean over
#: a holding period, and an exit that cuts it early collects a different
#: distribution from the one that was measured.
EXIT_MODES: frozenset[str] = frozenset({"horizon", "mean_touch", "either"})

SIZING_MODES: frozenset[str] = frozenset({"PREMIUM_PCT", "LOTS"})
from .regime import MARKET_FILTERS  # noqa: E402,F401
from .hedge import HEDGE_MODES  # noqa: E402,F401

#: How the scanned universe is chosen.
#:
#: ``curated``  the shared 14-name high-liquidity registry plus the indices —
#:              what every other engine here scans.
#: ``fno``      every underlying with a published lot size and strike step, from
#:              the live instrument dump. Liquidity is then enforced on the
#:              CONTRACT at scan time (quoted spread, premium, open interest)
#:              rather than by a static list of names.
#:
#: ``fno`` is the default because the name-level list was the binding
#: constraint on this engine's sample and on its live scan: the rule fired 90
#: times across the F&O list between July and September 2026 and exactly zero
#: times among the fourteen curated names in the same window. A list is also
#: the wrong instrument for the question — what decides whether a trade is
#: fillable is the spread on the contract, and that is measurable at scan time.
UNIVERSE_MODES: frozenset[str] = frozenset({"curated", "fno"})
STOP_MODES: frozenset[str] = frozenset({"broker", "monitor", "both"})

#: Index instruments this engine will name a contract for.
_INDEX_DEFAULTS: tuple[str, ...] = ("NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX")

#: The single stocks it was measured on. Not "every F&O name": a stock whose
#: options are thin turns a 0.5% modelled slippage into a fiction, and the
#: measurement has nothing to say about names it never saw.
_STOCK_DEFAULTS: tuple[str, ...] = (
    "RELIANCE", "HDFCBANK", "ICICIBANK", "INFY", "TCS", "SBIN", "AXISBANK",
    "LT", "BHARTIARTL", "KOTAKBANK", "BAJFINANCE", "BAJAJFINSV",
    "ADANIENT", "ADANIPORTS", "TATASTEEL",
)

#: Fields whose default came out of a measurement rather than a judgement.
CALIBRATED_FIELDS: frozenset[str] = frozenset({
    "lookback_days", "min_stretch_atr", "hold_days", "min_dte", "target_delta",
    "allow_fade_down", "market_filter", "market_ema", "hedge_mode",
    "smile_slope", "runner_mult", "runner_trail_pct",
})

#: What was run, so a number on the settings page can be traced to a run.
CALIBRATION: dict[str, Any] = {
    "dataset": "daily bars for 202 F&O underlyings with published contract specs",
    "span": "2017-09..2026-09 (2,232 sessions, 430,223 bars)",
    "priced_at": "IV = 20-day realised vol x VRP, with an ASYMMETRIC put skew "
                 "(1.6 below spot, 0.5 above); exit valued at the same vol; "
                 "the index-futures hedge charged carry at 5.2%/yr",
    "statistics": "day-clustered, moving-block bootstrap, entry-date permutation",
    "study": "backend/study/snapback_research.py",
    "verdict": "NOT PROMOTED, 6 of 9 checks — out of sample +4.03% per entry "
                "day, permutation p=0.0164, break-even VRP 2.01 against a "
                "1.15-1.30 market. See docs/strategy/snapback/VALIDATION_REPORT.md",
}

TUPLE_FIELDS: frozenset[str] = frozenset({"scan_indices", "scan_stocks"})


@dataclass(frozen=True)
class SnapbackConfig:
    # ── power ────────────────────────────────────────────────────────────────
    enabled: bool = False
    #: OFF, and it stays off until a walk-forward run promotes this engine.
    #: Every engine in this repo that shipped with automatic execution on was
    #: turned off again within the week.
    auto_execute: bool = False

    # ── what is watched ──────────────────────────────────────────────────────
    universe_mode: str = "fno"
    scan_indices: tuple[str, ...] = _INDEX_DEFAULTS
    #: Only read when ``universe_mode`` is ``curated``.
    scan_stocks: tuple[str, ...] = _STOCK_DEFAULTS
    scan_stock_contracts: bool = True
    #: Cap on how many single stocks an ``fno`` scan covers, most liquid first.
    #: A full pass is one daily-candle request plus one chain lookup per name;
    #: the cap is here so an operator can trade the cost off against coverage
    #: rather than discovering it as a slow scan.
    max_universe: int = 200

    # ── what makes it fire ───────────────────────────────────────────────────
    #: Sessions the close must exceed the high of. MEASURED: 20 and 60 both
    #: work and 20 gives roughly twice the sample, which is the whole
    #: difference between a result and an anecdote at this trade count.
    lookback_days: int = 20
    #: How far from its own 20-day mean the instrument must be, in ATR(14).
    #:
    #: MEASURED, and the direction is the OPPOSITE of what an earlier version of
    #: this file claimed. Over 9,295 trades the return falls monotonically as
    #: stretch rises: 1.5-2 ATR -0.97%, 2-2.5 -0.84%, 2.5-3 -1.73%, 3-4 -5.88%,
    #: 4+ -6.98%. The old claim that 3 ATR was a stronger signal came from 134
    #: trades.
    #:
    #: 1.5 is kept as the floor because it is the least-bad band and because
    #: lowering it further stops being "an extension" at all. Raising it makes
    #: the strategy worse, not more selective.
    min_stretch_atr: float = 1.5
    #: Buy calls into weakness as well as puts into strength.
    #:
    #: OFF, and the reason is a PRICING one rather than a performance one.
    #: Realised vol over the ten sessions after a downside break runs at 1.25x
    #: the trailing vol that prices the option, so the real call at that moment
    #: costs materially more than this engine models it at and every fade-down
    #: return is overstated by an unknown amount. After an UPSIDE break the same
    #: ratio is 0.86-0.92 — markets rise quietly — so the put is if anything
    #: modelled too dear.
    #:
    #: That asymmetry is the leverage effect, not a quirk of this sample: spot
    #: and vol are negatively correlated, so a put bought into strength is long
    #: a vega that pays when the fade works and a call bought into weakness is
    #: long one that fights it.
    #:
    #: Turning it on roughly doubles the trade count and, measured out of
    #: sample, takes the Sharpe from 1.59 to 0.78, the drawdown from -10.4% to
    #: -27.1%, and the break-even VRP from 2.24 to 1.71.
    allow_fade_down: bool = False
    #: Only fade while the market itself is not trending up.
    #:
    #: THE load-bearing setting, and the one that decides whether this strategy
    #: makes money at all. Measured on 202 underlyings over nine years: without
    #: it the book returns -1.28% per entry day; with it, +2.13%, and the excess
    #: over a day-matched unconditional put goes from +1.5pp to +3.1pp.
    #:
    #: The reason is beta, not the signal. A bought put is a large short
    #: position in the market, and the signal's own edge is about one and a half
    #: points — nowhere near enough to pay for being short a bull market. So the
    #: gate is on the beta. See ``regime.py``.
    market_filter: str = "bearish"
    #: Sessions in the market EMA the gate reads.
    #:
    #: 50, and it is NOT the best number on the full sample — 100 to 200 all
    #: read better there (Sharpe 1.0 to 1.4 against 0.72). It is kept because
    #: the response is not a curve: 75 sessions reads WORSE than either
    #: neighbour (Sharpe 0.50), which is the shape of noise rather than of a
    #: parameter. Slower gates also cut the sample hard — 455 entry days at 50,
    #: 324 at 150 — and out of sample the 100-session version won on the mean
    #: and lost on the Sharpe, the compounded return, the drawdown and the year
    #: count, with its fold-selected book going NEGATIVE.
    market_ema: int = 50
    #: Remove the market from the trade.
    #:
    #: The signal's edge is RELATIVE: about +1.5pp against a day-matched
    #: unconditional put. The expression is DIRECTIONAL: a bought put is a large
    #: short position in the market. Over nine rising years the second costs
    #: more than the first earns, and that gap is the whole reason the
    #: directional book loses.
    #:
    #: ``index_futures`` shorts the put's own market delta, beta-weighted, with
    #: an index future. A future is used rather than a bought index call because
    #: a bought call is CONVEX: measured, it beat an exact hedge by almost a
    #: point, which means it had stopped being a hedge and become a second
    #: long-market position that the bull run paid for.
    #:
    #: NOTE this changes what the strategy needs: a future is MARGIN, not
    #: premium, so it does not consume the premium budget but does consume
    #: margin this engine cannot see.
    hedge_mode: str = "index_futures"
    #: Bars a symbol waits before the same side may fire on it again. Without
    #: it a market grinding to new highs fires on every session of the grind.
    cooldown_days: int = 5
    #: Refuse a signal whose instrument is too quiet for the trade to clear its
    #: own costs. ATR at signal time, in basis points of price. 0 = off.
    min_atr_bp: float = 0.0

    # ── which contract ───────────────────────────────────────────────────────
    #: Magnitude, for both sides.
    #:
    #: 0.70, which is an IN-the-money option, and the reasoning is arithmetic
    #: rather than empirical: the edge here is a directional drift, so the
    #: contract that monetises it best is the one carrying the most DELTA per
    #: rupee of theta and vega. A cheap out-of-the-money put shows a higher
    #: mean return because it is cheap, and a lower break-even VRP because
    #: almost all of what you paid was time value.
    #:
    #: Measured on the narrow 19-name sample at break-even VRP 2.24 against 1.97
    #: for a 0.55 delta. That sample was later falsified as a whole, so treat the
    #: ARGUMENT — delta per rupee of theta — as the reason and the ranking as
    #: unproven. On the nine-year sample the break-even is 1.00 at any delta.
    target_delta: float = 0.70
    #: Sell a further out-of-the-money option against the one bought, turning the
    #: outright into a vertical SPREAD. 0 = outright.
    #:
    #: This is aimed at the engine's weakest input rather than at its return.
    #: No store here holds option prices, so the premium is modelled, and the
    #: break-even VRP exists to say how wrong that model can be. A spread's
    #: value is a DIFFERENCE of two premiums, and both move with the vol
    #: assumption together — so most of the modelling error cancels, where an
    #: outright absorbs all of it.
    #:
    #: It also cuts the cash outlay and the theta roughly in half, at the cost
    #: of capping the payoff. The move being captured is a one-to-two ATR
    #: reversion, so the cap is far out of the way of the distribution's middle.
    short_leg_delta: float = 0.0
    #: Days to expiry at entry, minimum.
    #:
    #: MEASURED, and the direction is counter-intuitive: a 14-day contract shows
    #: a HIGHER mean return (cheaper, so more leverage) and a LOWER break-even
    #: VRP (1.48 against 1.77 at 35 days on indices). Theta per rupee of premium
    #: runs at about 1/(2T), so a short contract spends its edge on decay and
    #: leaves no room for the premium to be dearer than modelled. Mean return is
    #: the flattering metric here; break-even is the honest one.
    #:
    #: 40, which is 25 sessions of life left at the 15-session exit. Long
    #: enough that the contract is never priced on its expiry cliff, short
    #: enough not to tie up premium the horizon does not use.
    min_dte: int = 40
    max_dte: int = 60
    expiry_series_indices: tuple[str, ...] = ("monthly",)
    #: NSE lists no weekly single-stock options. This is a fact, not a choice.
    expiry_series_stocks: tuple[str, ...] = ("monthly",)
    min_option_premium: float = 10.0
    max_spread_pct: float = 2.0
    #: Open interest, in UNITS, on the contract itself.
    #:
    #: Non-zero now that the universe is the whole F&O list rather than a
    #: hand-picked fourteen. This is the guard that replaced the name list, and
    #: it is the better one: it asks whether THIS contract is liquid rather than
    #: whether someone once thought the underlying was.
    min_option_oi: float = 50_000.0

    # ── how it ends ──────────────────────────────────────────────────────────
    exit_mode: str = "horizon"
    #: Sessions held.
    #:
    #: 15, raised from 10 after the audit, and the mechanism comes first: the
    #: edge is a right TAIL — the top 1% of trades carry 148% of the book's
    #: P&L — and a tail needs time to develop. A ten-session horizon cuts some
    #: of it off.
    #:
    #: MEASURED, and consistently under two different pricing models, which is
    #: why it is not a fit. Flat-vol: hold 15 gives +3.06% per entry day against
    #: +1.54% at 10. Realistic skew: +3.40% against +2.01%, Sharpe 0.68 against
    #: 0.59, compounded +104% against +74%, and 6 of 9 calendar years positive
    #: against 5. Twenty sessions is worse than fifteen on every one of those.
    hold_days: int = 15
    #: Give back at most this much of the premium before closing. A stop on the
    #: PREMIUM rather than on spot, because that is the thing that can go to
    #: zero while the spot thesis is still technically intact.
    #:
    #: KEPT despite costing mean return, and the disagreement is the point. On a
    #: tail-driven book the arithmetic and geometric answers part company:
    #: removing the stop takes the mean from +2.01% to +3.93% per entry day and
    #: the COMPOUNDED return from +74% to +56%, with the drawdown going from
    #: -46% to -58%. The stop does cut trades that would have become tail
    #: winners; it buys more than it costs in the only currency that compounds.
    premium_stop_pct: float = 35.0
    #: Ratchet: give back at most this much of the best premium seen. 0 = off.
    premium_trail_pct: float = 0.0
    #: Hold PAST the horizon while the trade is already worth this multiple of
    #: what it cost. 0 = off, close at the horizon whatever the trade is doing.
    #:
    #: The motivation was stated before the measurement, which is the only
    #: reason this one is trusted: the top 1% of trades carry 148% of this
    #: book's P&L, so a fixed horizon closes the few trades that pay for all the
    #: rest. Raising the horizon itself from 10 to 15 sessions was already worth
    #: +1.4 points per entry day. A runner asks for the remainder of the tail
    #: WITHOUT holding every loser longer — a trade below the multiple still
    #: closes on its horizon, unchanged.
    #:
    #: MEASURED, and confirmed OUT OF SAMPLE on the same windows, which is where
    #: most of this file's rejected ideas died:
    #:
    #:   out of sample   +3.46% -> +4.03% per entry day, break-even VRP
    #:                   1.754 -> 2.012, Sharpe 0.55 -> 0.61, on 409 entry days
    #:                   against 411 — it changes the EXIT, not the sample
    #:   full sample     Sharpe 0.72 -> 0.90, compounded +119% -> +200%,
    #:                   7 of 9 calendar years positive against 6
    #:
    #: The cost is a deeper drawdown, -26.1% to -29.4% out of sample. That is
    #: the trade being made: a right tail is bought with variance.
    runner_mult: float = 1.5
    #: Once running, close when the premium gives back this much of its best.
    #: Only read when ``runner_mult`` is set.
    #:
    #: The give-back is what makes a runner a rule rather than a hope. Measured
    #: at 1.5x: 15% gives +4.09% per entry day and 25% gives +3.48%, but 40%
    #: collapses to +1.86% and no ratchet at all to +2.35% — the tail is given
    #: back faster than it accrues. 25 is the interior of the pair that works
    #: rather than the better of the two, because choosing between two readings
    #: this close is fitting.
    runner_trail_pct: float = 25.0
    #: Close if the underlying returns to its own 20-day mean — the thesis
    #: completing, rather than a price target. Only read in the mean_touch and
    #: either exit modes.
    mean_touch_ema: int = 20

    # ── size and protection ──────────────────────────────────────────────────
    #: LOTS, not PREMIUM_PCT, and it is a usability fix rather than a
    #: preference. A 0.70-delta monthly option on this universe is ₹30,000 to
    #: ₹200,000 of premium PER LOT and an option cannot be sized below one lot,
    #: so a percentage budget on any ordinary account rounds to zero and NOTHING
    #: EVER ARMS. That is what the shipped default did: every signal came back
    #: "one lot costs more than the premium budget".
    #:
    #: One lot per signal, with ``max_open_positions`` bounding the count and
    #: ``min_outlay_inr`` on every row saying what it costs, is the honest
    #: default. PREMIUM_PCT is still there for an account large enough that a
    #: percentage is a real constraint rather than a refusal.
    sizing_mode: str = "LOTS"
    #: A bought option's maximum loss IS its premium, so the honest sizing
    #: question is how much premium to put at risk, not where a stop sits.
    premium_pct_of_capital: float = 2.0
    capital_inr: float = 100_000.0
    lots: int = 1
    max_lots: int = 5
    #: 20, not 5, and the reason is the hedge. The cap exists to bound market
    #: exposure; once the market is hedged out, what it bounds is idiosyncratic
    #: risk, which diversifies rather than accumulating. Measured: capped at 5
    #: the hedged book returns +0.52% per entry day and compounds -81%; taking
    #: every signal it returns +2.84% and compounds +91%. The cap was throwing
    #: away 73% of candidates and choosing among the rest ALPHABETICALLY.
    max_open_positions: int = 20
    #: Refuse a second position on the same underlying, either side. Two fades
    #: of one instrument are one bet with two tickets.
    one_position_per_underlying: bool = True
    stop_mode: str = "both"

    # ── modelling ────────────────────────────────────────────────────────────
    #: Sessions of realised vol behind the modelled premium and the strike pick.
    rv_window: int = 20
    #: What the engine ASSUMES it is paying, as a multiple of realised vol, when
    #: no live quote is available. Reported beside every modelled premium so the
    #: assumption is never invisible. India VIX has run 1.15-1.30x.
    assumed_vrp: float = 1.22
    #: Steepness of the put skew, in vol points per unit of log-moneyness.
    #:
    #: Pricing every strike at one ATM vol is the flat-vol trap, and this repo
    #: has shipped a number from it before: a far-OTM wing looked like a +455%
    #: edge flat and became -79.5% under a realistic smile. An out-of-the-money
    #: put is DEARER than a flat model says, and this engine buys puts — so a
    #: zero here flatters every low-delta configuration.
    #:
    #: 1.6 prices a 25-delta 35-day put about 4 vol points over ATM on a
    #: 12-vol index, which is the right order for NIFTY. 0 restores the flat
    #: model, and the difference between the two is the honest error bar on any
    #: strike choice away from the money.
    smile_slope: float = 1.6
    #: Steepness on the IN-the-money side of the put skew.
    #:
    #: A real equity skew is steep below spot and much flatter above, so using
    #: one slope both ways hands an in-the-money put a discount the market does
    #: not give — and this engine buys in-the-money puts. Measured at 0.70
    #: delta: a symmetric 1.6 slope reports +4.70% per entry day, a realistic
    #: flatter in-the-money wing reports less, and the gap is the size of the
    #: assumption rather than of the edge.
    #:
    #: 0.5 is roughly a third of the out-of-the-money steepness, which is the
    #: shape NIFTY actually quotes.
    smile_itm_slope: float = 0.5

    # ------------------------------------------------------------------ api

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for f in fields(self):
            v = getattr(self, f.name)
            out[f.name] = list(v) if isinstance(v, tuple) else v
        return out

    def sides(self) -> tuple[str, ...]:
        return ("fade_up", "fade_down") if self.allow_fade_down else ("fade_up",)

    def universe(self) -> tuple[str, ...]:
        """The CURATED universe. ``fno`` mode is resolved by the service, which
        is the only layer that can see the instrument dump."""
        stocks = tuple(self.scan_stocks) if self.scan_stock_contracts else ()
        return tuple(dict.fromkeys((*self.scan_indices, *stocks)))

    def warmup_bars(self) -> int:
        """Bars before anything may fire.

        The binding input is the 20-day EMA, which is not merely undefined
        before bar 20 — it is WRONG, and a seeded EMA reads plausible while it
        is still wrong. The realised-vol window and the breakout lookback stack
        on top of it, and 60 bars of margin keeps the first signal honest.

        ``market_ema`` is in here too, and it was not: the market gate reads a
        SEPARATE tape, so a warm-up that covered every per-instrument window
        still left the index EMA unfilled whenever the gate was slower than 60
        sessions. Every walk-forward fold then gated on an EMA that had not
        formed.
        """
        return max(self.lookback_days, self.rv_window, self.mean_touch_ema,
                   self.market_ema) + 60

    def warnings(self) -> list[str]:
        """Things an operator should know that are not errors.

        Separate from validation on purpose: a configuration can be legal and
        still be one this engine has no evidence for, and refusing it would be
        wrong while saying nothing would be worse.
        """
        out: list[str] = []
        if self.auto_execute:
            out.append("Automatic execution is on. This engine has not been "
                       "promoted by the walk-forward gate.")
        if self.allow_fade_down:
            out.append("Fade-down is on. Realised vol RISES after a downside "
                       "break (1.25x trailing), so the modelled call premium is "
                       "too cheap and this side's results are overstated by an "
                       "unknown amount.")
        if self.min_dte < 21:
            out.append(f"At {self.min_dte} days to expiry the break-even VRP "
                       f"falls towards the 1.15-1.30 the market charges — the "
                       f"trade has little room for the premium to be dearer "
                       f"than modelled.")
        if self.target_delta < 0.35:
            out.append("Below 0.35 delta the contract is mostly time value; "
                       "the measured break-even VRP falls with it.")
        if self.universe_mode == "curated":
            out.append("Curated universe: fourteen stocks plus the indices. "
                       "Measured between July and September 2026, this rule "
                       "fired 90 times across the F&O list and zero times among "
                       "those fourteen.")
        if self.min_option_oi <= 0 and self.universe_mode == "fno":
            out.append("Open-interest floor is off while scanning the whole "
                       "F&O list. Nothing then stops a signal naming a contract "
                       "with no book behind it.")
        if (self.sizing_mode == "PREMIUM_PCT"
                and self.capital_inr * self.premium_pct_of_capital / 100 < 60_000):
            out.append(
                f"Premium budget is ₹"
                f"{self.capital_inr * self.premium_pct_of_capital / 100:,.0f} per "
                f"position. A {self.target_delta:.2f}-delta monthly option is "
                f"₹50,000 to ₹200,000 of premium PER LOT on this universe, so "
                f"few signals — possibly none — will be affordable. Raise the "
                f"capital or the percentage, or lower the target delta.")
        if self.max_open_positions < 10 and self.hedge_mode != "none":
            out.append(f"A cap of {self.max_open_positions} drops most signals "
                       f"and picks among the rest by name. Measured, capping a "
                       f"HEDGED book at 5 takes it from +2.84% per entry day "
                       f"to +0.52%.")
        if self.hedge_mode == "none":
            out.append("Unhedged. The signal's edge is RELATIVE (+1.5pp against "
                       "a day-matched baseline) and a bought put is a large "
                       "short position in the market; over nine years the "
                       "second cost more than the first earned.")
        if self.hedge_mode == "index_futures":
            out.append("The index-futures hedge needs MARGIN, which this engine "
                       "does not model. The premium budget below covers the "
                       "option leg only.")
        if self.smile_slope <= 0:
            out.append("Skew is OFF — every strike is priced at one ATM vol. "
                       "An out-of-the-money put is dearer than that in reality, "
                       "so this flatters any low-delta setting. It is the "
                       "flat-vol trap this repo has shipped a number from once.")
        if self.market_filter == "off":
            out.append("Market gate is OFF. Measured over nine years and 202 "
                       "underlyings, the ungated book returns -1.28% per entry "
                       "day; gated to a market below its own EMA it returns "
                       "+2.13%. The gate is on the put's BETA, not on the "
                       "signal.")
        if self.market_filter == "bullish":
            out.append("Market gate is INVERTED — fading only while the market "
                       "trends UP. Measured excess in that regime is +0.6pp "
                       "against +3.1pp for the shipped setting.")
        if self.exit_mode != "horizon":
            out.append("The measured edge is a mean over the whole holding "
                       "period. An early exit collects a different "
                       "distribution from the one that was measured.")
        return out


def validate(values: dict[str, Any], base: SnapbackConfig | None = None) -> SnapbackConfig:
    """Apply a partial change, refusing anything the engine cannot honour.

    Unknown keys raise. A silently dropped setting is worse than a 422: the UI
    has no way to tell that it did not take, and this repo has shipped that bug.
    """
    cfg = base or SnapbackConfig()
    known = {f.name: f for f in fields(SnapbackConfig)}
    unknown = sorted(set(values) - set(known))
    if unknown:
        raise ValueError(f"unknown settings: {', '.join(unknown)}")

    merged = cfg.as_dict()
    merged.update(values)

    for name in TUPLE_FIELDS:
        v = merged.get(name)
        if v is None:
            v = ()
        if isinstance(v, str):
            raise ValueError(f"{name} must be a list of instrument names")
        merged[name] = tuple(str(x).strip().upper() for x in v if str(x).strip())

    if merged["hedge_mode"] not in HEDGE_MODES:
        raise ValueError(f"hedge_mode must be one of {sorted(HEDGE_MODES)}")
    if merged["market_filter"] not in MARKET_FILTERS:
        raise ValueError(f"market_filter must be one of {sorted(MARKET_FILTERS)}")
    if merged["universe_mode"] not in UNIVERSE_MODES:
        raise ValueError(f"universe_mode must be one of {sorted(UNIVERSE_MODES)}")
    if merged["exit_mode"] not in EXIT_MODES:
        raise ValueError(f"exit_mode must be one of {sorted(EXIT_MODES)}")
    if merged["sizing_mode"] not in SIZING_MODES:
        raise ValueError(f"sizing_mode must be one of {sorted(SIZING_MODES)}")
    if merged["stop_mode"] not in STOP_MODES:
        raise ValueError(f"stop_mode must be one of {sorted(STOP_MODES)}")

    _range(merged, "lookback_days", 2, 250, int)
    _range(merged, "hold_days", 1, 60, int)
    _range(merged, "cooldown_days", 0, 250, int)
    _range(merged, "min_dte", 1, 365, int)
    _range(merged, "max_dte", 1, 365, int)
    _range(merged, "rv_window", 5, 250, int)
    _range(merged, "mean_touch_ema", 2, 250, int)
    _range(merged, "lots", 1, 100, int)
    _range(merged, "max_lots", 1, 100, int)
    _range(merged, "max_open_positions", 1, 50, int)
    _range(merged, "max_universe", 1, 500, int)
    _range(merged, "market_ema", 2, 250, int)
    _range(merged, "min_stretch_atr", 0.0, 10.0, float)
    _range(merged, "target_delta", 0.05, 0.95, float)
    _range(merged, "short_leg_delta", 0.0, 0.95, float)
    _range(merged, "premium_stop_pct", 1.0, 100.0, float)
    _range(merged, "premium_trail_pct", 0.0, 100.0, float)
    _range(merged, "runner_mult", 0.0, 20.0, float)
    _range(merged, "runner_trail_pct", 0.0, 100.0, float)
    _range(merged, "premium_pct_of_capital", 0.01, 100.0, float)
    _range(merged, "capital_inr", 1000.0, 1e10, float)
    _range(merged, "assumed_vrp", 0.5, 3.0, float)
    _range(merged, "smile_slope", 0.0, 6.0, float)
    _range(merged, "smile_itm_slope", 0.0, 6.0, float)
    _range(merged, "min_atr_bp", 0.0, 10_000.0, float)
    _range(merged, "min_option_premium", 0.0, 1e6, float)
    _range(merged, "max_spread_pct", 0.0, 100.0, float)
    _range(merged, "min_option_oi", 0.0, 1e12, float)

    if merged["short_leg_delta"] and merged["short_leg_delta"] >= merged["target_delta"]:
        raise ValueError(
            f"short_leg_delta ({merged['short_leg_delta']}) must be BELOW "
            f"target_delta ({merged['target_delta']}) — the sold leg is the "
            f"further out-of-the-money one, and inverting them turns a debit "
            f"spread into a credit spread with a different risk entirely")
    if merged["max_dte"] < merged["min_dte"]:
        raise ValueError("max_dte cannot be below min_dte")
    # A runner that may hold to the horizon and no further is not a runner; it
    # is a horizon exit wearing a second name, and it would read as one in the
    # exit reasons.
    if merged["runner_mult"] and merged["runner_mult"] <= 1.0:
        raise ValueError(
            f"runner_mult ({merged['runner_mult']}) must be ABOVE 1.0 — it is "
            f"a multiple of what the trade COST, so 1.0 or below lets every "
            f"unprofitable trade run as well")
    # A holding period that outlives the contract prices an expired option at
    # intrinsic and calls it an exit. Refuse rather than silently truncate.
    if merged["hold_days"] >= merged["min_dte"]:
        raise ValueError(
            f"hold_days ({merged['hold_days']}) must be shorter than min_dte "
            f"({merged['min_dte']}) — the contract has to outlive the trade")
    if merged["universe_mode"] == "curated" and not merged["scan_indices"] and not (
            merged["scan_stock_contracts"] and merged["scan_stocks"]):
        raise ValueError("nothing to scan: pick at least one index or stock")

    return SnapbackConfig(**merged)


def _range(d: dict[str, Any], key: str, lo: float, hi: float, cast) -> None:
    try:
        v = cast(d[key])
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be a number") from exc
    if not (lo <= v <= hi):
        raise ValueError(f"{key} must be between {lo} and {hi}")
    d[key] = v
