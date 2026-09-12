"""Snapback — fade an over-extension with a put, and hedge the market out of it.

The rule, in one line: when an instrument closes through its own 20-session high
AND sits at least 1.5 ATR above its 20-session mean, with the market below its
own 50-session mean, buy the put, hedge its market delta with an index future,
and hold fifteen sessions — or longer, while the trade is already worth 1.5x
what it cost.

**`docs/strategy/snapback/VALIDATION_REPORT.md` is the authority.** Measured on
202 F&O underlyings over nine years, out of sample: **+4.03% per entry day**,
entry-timing permutation **p = 0.016**, break-even vol multiple **2.01** against
a market that charges 1.15-1.30. Six of nine gate checks. NOT promoted — the
deflated Sharpe, the year-consistency bar and the day-clustered interval all
fail — so ``auto_execute`` stays blocked by the validation record.

**The edge is a right TAIL.** The top 1% of trades carry 148% of the P&L; the
median trade loses 15.5%. That one fact decides three settings at once: vertical
spreads are refused because they cap the payoff, the horizon is fifteen sessions
rather than ten, and a winner is allowed to run past it (``runner_mult``). It is
also why the arithmetic and the geometric summaries of this book disagree, and
why both are quoted.

**The hedge is the whole difference.** The signal's edge is RELATIVE: about
+1.5pp against a day-matched unconditional put. A bought put is a large SHORT
position in the market, and over nine rising years that cost far more than the
edge earned. Unhedged, the same windows return -1.08% per entry day at p=0.37
with a break-even of 1.00. See ``hedge.py`` — and note it is a FUTURE, because a
bought index call is convex and measured as a second long-market bet rather than
a hedge.

**Two earlier claims this file made that the data refuted**, kept here because
they are the reason to distrust a small sample:

* A 19-instrument, 3-year measurement gave +6.28% and p=0.029 and said seven of
  nine checks passed. It was period and universe selection — those same names
  average -0.38% over nine years.
* A +4.78% out-of-sample mean, measured with a market gate that could OPEN
  ITSELF: every session before the index EMA had formed was stamped "not above
  the EMA", which under a ``bearish`` filter is the permissive state. Fixing it
  cost 1.32 points. See finding 4 in ``docs/strategy/snapback/AUDIT.md``.
* "More stretch is a stronger signal" is backwards, monotonically, over 9,295
  trades. The old claim rested on 134.
"""
from .config import (CALIBRATED_FIELDS, CALIBRATION, EXIT_MODES, SIDES,
                     SIZING_MODES, STOP_MODES, SnapbackConfig, TUPLE_FIELDS,
                     validate)
from .contracts import Pick, lots_for, moneyness_label, pick_for
from .models import Bars, STRATEGY_ID, SnapbackSignal, ist_day, to_bars
from .pricing import (RISK_FREE, TRADING_DAYS, VRP_BAND, break_even_vrp, bs_delta,
                      bs_price, implied_vol_proxy, realized_vol, strike_for_delta,
                      vrp_margin)
from .strategy import Features, entry_indices, evaluate, evaluate_at, features, fires

STRATEGY_NAME = "Snapback"
CONTRACT_VERSION = "A500.1"

#: One entry per side, published so the board, the settings page and the replay
#: dock name a side the same way without three private lists.
DESCRIPTORS: dict[str, dict] = {
    "fade_up": {
        "id": "fade_up",
        "name": "Fade the push",
        "tag": "FU",
        "option_type": "PE",
        "tagline": "Buys puts when an instrument closes through its 20-session "
                   "high while stretched above its own mean.",
        "how_it_works": (
            "A close through the prior 20 sessions' high, with the close at "
            "least 1.5 ATR above the 20-session EMA and the market below its own "
            "50-session EMA, buys a put about 40 days out at the configured "
            "delta and holds it fifteen sessions — longer while it is already "
            "worth 1.5x what it cost, because the edge is a right tail and a "
            "session count closes the trades that pay for the rest. The thesis "
            "completes when the instrument returns to that mean."
        ),
        "evidence": "Measured on 202 underlyings over nine years, out of "
                    "sample and MARKET-NEUTRAL: +4.03% per entry day, "
                    "entry-timing p=0.016, break-even vol multiple 2.01 against "
                    "a market charging 1.15-1.30, Sharpe 0.61, worst drawdown "
                    "-29% at 2% of capital per position. Six of nine gate "
                    "checks. UNHEDGED the same windows return -1.08% at p=0.37 "
                    "— the edge is relative, and a bought put is a large short "
                    "position in a market that rose for nine years.",
    },
    "fade_down": {
        "id": "fade_down",
        "name": "Fade the flush",
        "tag": "FD",
        "option_type": "CE",
        "tagline": "The mirror — buys calls into a stretched 20-session low.",
        "how_it_works": (
            "A close through the prior 20 sessions' low, with the close at least "
            "1.5 ATR below the 20-session EMA, buys a call at the configured "
            "delta on the same tenor and horizon."
        ),
        "evidence": "OFF, and for a pricing reason as well as a performance "
                    "one. Realised vol RISES to 1.25x trailing after a downside "
                    "break, so the real call costs more than this engine models "
                    "it at and every result on this side is overstated by an "
                    "unknown amount. Its excess over a day-matched baseline is "
                    "negative in every regime tested (-0.78 to -4.85pp).",
    },
}

SIDE_KEYS: tuple[str, ...] = ("fade_up", "fade_down")


def descriptor() -> dict:
    """Static identity plus what the walk-forward harness has actually found.

    ``validated`` is a MEASUREMENT read from the validation record, never a
    hardcoded flag. A hardcoded ``False`` is honest while nothing has been
    measured and becomes a lie the moment something is; a hardcoded ``True``
    never stops being one.
    """
    record: dict = {}
    try:
        from app.services.snapback_validation import load as _load
        record = _load() or {}
    except Exception:                                              # noqa: BLE001
        record = {}
    return {
        "id": STRATEGY_ID,
        "name": STRATEGY_NAME,
        "contract_version": CONTRACT_VERSION,
        "tagline": "Fades an over-extension with a put and hedges the market "
                   "out of it. Relative-value, not directional.",
        "sides": [dict(DESCRIPTORS[k]) for k in SIDE_KEYS],
        "provenance": "Measured in backend/study/snapback_research.py over "
                      "202 F&O underlyings and 2,232 sessions. Six of nine gate "
                      "checks; docs/strategy/snapback/VALIDATION_REPORT.md is "
                      "the authority and explains both what the hedge changed "
                      "and what the earlier 3-year result actually was.",
        "validated": bool(record.get("promoted")),
        "validation": record or None,
        "calibration": CALIBRATION,
        "calibrated_fields": sorted(CALIBRATED_FIELDS),
        "vrp_band": list(VRP_BAND),
    }


__all__ = [
    "STRATEGY_ID", "STRATEGY_NAME", "CONTRACT_VERSION", "SIDE_KEYS",
    "DESCRIPTORS", "descriptor",
    "SnapbackConfig", "validate", "CALIBRATION", "CALIBRATED_FIELDS",
    "TUPLE_FIELDS", "SIDES", "EXIT_MODES", "SIZING_MODES", "STOP_MODES",
    "Bars", "to_bars", "ist_day", "SnapbackSignal",
    "Features", "features", "fires", "evaluate", "evaluate_at", "entry_indices",
    "Pick", "pick_for", "lots_for", "moneyness_label",
    "bs_price", "bs_delta", "strike_for_delta", "realized_vol",
    "implied_vol_proxy", "break_even_vrp", "vrp_margin",
    "VRP_BAND", "RISK_FREE", "TRADING_DAYS",
]
