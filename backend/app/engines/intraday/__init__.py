"""Three intraday option strategies on one 5-minute tape.

* ``pivot_break``     — a strong candle closing through EMA9 and a Fibonacci pivot.
* ``ma_ribbon``       — the 55 EMA crossing the WHOLE 8/13/21 ribbon, never one line.
* ``vwap_supertrend`` — a SuperTrend(18, 1.46) flip confirmed by the VWAP side.

They share a universe, a session window and a contract picker, and nothing here
touches a broker: bars in, signals out, so the live scan, the simulation and the
tests run the same code.
"""
from .config import (CALIBRATED_FIELDS, CALIBRATION, IntradayConfig, PIVOT_PERIODS,
                     PIVOT_TYPES, STOP_SOURCES, STRATEGY_KEYS, TIMEFRAMES,
                     TRAIL_MODES, TUPLE_FIELDS)
from .indicators import FibPivots, fib_pivots, session_vwap, vwap_is_volume_weighted
from .position import (ContractRef, IntradayPosition, align_to_tick,
                       premium_stop_for, should_exit, update_trail)
from .models import Bars, IntradaySignal, STRATEGY_IDS, resample, to_bars
from .strategies import (EVALUATORS, Evaluation, evaluate_all, evaluate_ma_ribbon,
                         evaluate_pivot_break, evaluate_vwap_supertrend,
                         pivots_for, prior_period_hlc, ribbon_lines,
                         ribbon_should_exit, thesis_broken, apply_dynamic_levels)

STRATEGY_ID = "intraday"
STRATEGY_NAME = "Intraday Pack"
CONTRACT_VERSION = "A400.1"

#: Per-strategy identity, published so the board, the replay dock and the
#: settings page all name a strategy the same way without three private lists.
DESCRIPTORS: dict[str, dict] = {
    "pivot_break": {
        "id": "pivot_break",
        "name": "Pivot Break",
        "tag": "PB",
        "tagline": "Strong candle closing through EMA9 and a Fibonacci pivot.",
        "how_it_works": (
            "On 5-minute candles, waits for a candle with a decisive body to close "
            "through both the 9 EMA and a standard Fibonacci pivot level in the same "
            "direction. Buys the CE on a break up and the PE on a break down, stops at "
            "that candle's low or high, and runs a 1:2 target with a 1:3 runner behind "
            "a breakeven-then-trailing stop."
        ),
    },
    "ma_ribbon": {
        "id": "ma_ribbon",
        "name": "MA Ribbon",
        "tag": "MR",
        "tagline": "The 55 EMA crossing the whole 8/13/21 ribbon.",
        "how_it_works": (
            "Four EMAs — 8, 13, 21 and 55. When the 55 crosses below every one of the "
            "others it buys the CE and holds until the 55 crosses back above all of "
            "them; the mirror buys the PE. A cross of only one line is explicitly not a "
            "signal, which is the rule most implementations of this get wrong."
        ),
    },
    "vwap_supertrend": {
        "id": "vwap_supertrend",
        "name": "VWAP SuperTrend",
        "tag": "VS",
        "tagline": "SuperTrend(18, 1.46) flip confirmed by the VWAP side.",
        "how_it_works": (
            "When SuperTrend turns red and the candle closes below session VWAP, buys "
            "the PE with VWAP as the stop; when it turns green and the candle closes "
            "above VWAP, buys the CE. Fixed 20-point objective, trailing along VWAP "
            "once 12 points are banked."
        ),
    },
}


def descriptor() -> dict:
    """Static identity, plus what the walk-forward harness has actually found.

    ``validated`` is a MEASUREMENT, never a checklist. It is read from the
    validation record rather than hardcoded, so the day a strategy clears the
    harness the board and the settings page stop calling it unproven — and the
    day it stops clearing, they start again. A hardcoded False was honest while
    nothing had been measured and would have become a lie the moment something
    was.
    """
    standings: dict[str, dict] = {}
    try:
        from app.services.intraday_validation import load as _load
        standings = {k: v.as_dict() for k, v in _load().items()}
    except Exception:                                              # noqa: BLE001
        standings = {}
    strategies = []
    for key in STRATEGY_KEYS:
        meta = dict(DESCRIPTORS[key])
        st = standings.get(key) or {}
        meta["validated"] = bool(st.get("promoted"))
        meta["validation"] = st or None
        strategies.append(meta)
    return {
        "id": STRATEGY_ID,
        "name": STRATEGY_NAME,
        "contract_version": CONTRACT_VERSION,
        "tagline": "Three 5-minute option strategies on one tape.",
        "strategies": strategies,
        "provenance": "Specified by the operator; see docs/strategy/intraday/",
        # The pack is validated only when every strategy in it is.
        "validated": bool(strategies) and all(s["validated"] for s in strategies),
        "calibration": CALIBRATION,
        "calibrated_fields": sorted(CALIBRATED_FIELDS),
    }


__all__ = [
    "STRATEGY_ID", "STRATEGY_NAME", "CONTRACT_VERSION", "STRATEGY_KEYS",
    "STRATEGY_IDS", "DESCRIPTORS", "descriptor",
    "IntradayConfig", "CALIBRATION", "CALIBRATED_FIELDS", "TUPLE_FIELDS",
    "TIMEFRAMES", "PIVOT_TYPES", "PIVOT_PERIODS", "STOP_SOURCES", "TRAIL_MODES",
    "Bars", "IntradaySignal", "to_bars", "resample",
    "FibPivots", "fib_pivots", "session_vwap", "vwap_is_volume_weighted",
    "Evaluation", "EVALUATORS", "evaluate_all", "evaluate_pivot_break",
    "evaluate_ma_ribbon", "evaluate_vwap_supertrend", "ribbon_should_exit",
    "ribbon_lines", "pivots_for", "prior_period_hlc", "thesis_broken",
    "apply_dynamic_levels",
    "IntradayPosition", "ContractRef", "premium_stop_for", "update_trail",
    "should_exit", "align_to_tick",
]
