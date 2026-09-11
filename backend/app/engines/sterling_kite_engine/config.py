"""Knobs for the 1H Heikin-Ashi Sterling Kite Engine."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Tuple
from typing import Literal, Optional, Tuple

from app.engines.common.exit_counter import ExitMode  # shared for unification with directional

TrailTarget = Literal["fast", "mid", "slow"]
CandleBasis = Literal["raw", "heikin_ashi"]

# How many red SuperTrend lines trigger an auto-exit.
# Entry = all three lines green + green arrow (fresh full alignment).
# Exit modes are the COUNTER of that entry (see common/exit_counter.py for shared logic):
#   one_red          — exit when ANY one ST line turns red (tightest)
#   two_red          — exit when any TWO ST lines turn red
#   three_red        — exit when ALL THREE ST lines turn red (full reversal)
#   three_red_signal — exit when all three red AND a fresh red arrow (counter-entry)



@dataclass(frozen=True)
class SterlingKiteEngineConfig:
    """Configuration for the Sterling Kite Engine.

    ``fast``/``mid``/``slow`` are named by flip-responsiveness (driven by the
    multiplier), matching the source spec. Each is a ``(period, multiplier)``
    pair, verbatim from the spec.
    """

    fast: Tuple[int, float] = (21, 1.0)
    mid: Tuple[int, float] = (14, 2.0)
    slow: Tuple[int, float] = (7, 3.0)
    # The live Zerodha comparison chart is Heikin-Ashi. Keep the scanner on that
    # same displayed candle basis by default so the visible three-green alignment
    # and the confirmation arrow are the exact event evaluated by the engine.
    # Raw OHLC remains available only as an explicit research/backtest override.
    candle_basis: CandleBasis = "heikin_ashi"
    # Which ST line trails the stop / triggers the exit flip. ``fast`` (the tightest
    # band, mult 1.0) is the most robust choice in the 7.5y IS/OOS sweep: stripped of
    # the options wrapper it is OOS-positive on 4/4 indices, vs 3/4 for ``mid`` and
    # 0/4 for ``slow`` (study/kite_st_exit_analysis.md). A tighter trail also banks
    # the move faster → less theta bleed on long options.
    trail_target: TrailTarget = "fast"
    # ── Auto-exit mode ────────────────────────────────────────────────────────
    # Controls how many SuperTrend lines must turn red before the position exits.
    # "one_red" is the tightest (original behaviour — trail_target flip = one line
    # going red). "three_red_signal" is the loosest (holds until a full counter-entry).
    # The trailing stop rides the BEST still-green line, tightening as lines flip.
    # MEASURED default (study/kite_st_exit_mode_sweep.py, real 7.5y 1H, IS/OOS):
    # ``one_red`` is best on BOTH lenses — delta1 mean OOS +4.0% (3/4 idx +) vs
    # two_red −6.4% / three_red −18.4%; options −134% vs −184% / −338%. Tighter exits
    # win; the earlier ``two_red`` default was asserted, never measured, and lost. It
    # also matches live behaviour (the monotonic premium ratchet already pinned exits
    # near one_red). Looser modes stay selectable but are worse on this data.
    exit_mode: ExitMode = "one_red"
    # ── Direction ─────────────────────────────────────────────────────────────
    # Whether a BEAR alignment may open a position.
    #
    # MEASURED (study/kite_st_best.py, real 7.5y 1H, 4 indices, delta-1, costs and
    # slippage included): the short book is worth NOTHING. Over 1102 short trades
    # it netted -4,911 rupees — not a loss to fear, but noise that doubles the
    # fill count and widens the drawdown for no return. The long book over the
    # same period netted +3,143,906.
    #
    #   both sides:  2218 trades, PF 1.25, Sharpe 0.67, max DD -26.0%
    #   long only:   1134 trades, PF 1.54, Sharpe 0.90, max DD -19.9%
    #
    # Long-only earns slightly MORE money on half the trades with a quarter less
    # drawdown, so False is the default. The long book's timing is separately
    # significant against random entries of identical exposure (portfolio
    # p = 0.0006, z = 3.28), which the short book's is not.
    #
    # Set True to restore the symmetric behaviour; nothing else changes.
    allow_short: bool = False
    # ── Exit-mode-aligned trail (opt-in; default OFF = validated fast-trail) ────
    # OFF: the price stop rides the tightest still-green line (``best_trail_line_value``).
    #      Because the tightest (fast) line flips FIRST, its breach ≈ one_red, which
    #      pre-empts a two_red/three_red counter → the exit_mode knob is near-inert live.
    # ON:  the price stop rides the line whose flip is the ``exit_mode``-th red
    #      (one_red→fast, two_red→mid, three_red→slow), so the stop breach and the red
    #      count fire together and the exit_mode actually governs how much room a trade
    #      gets. Kept as an opt-in research lever, but the sweep it was built to enable
    #      (``study/kite_st_exit_mode_sweep.py``) has now RUN and found tighter exits
    #      strictly better (see ``exit_mode`` above) — so widening the trail to honour a
    #      looser mode is NOT recommended. Default OFF = the validated tightest/fast trail.
    exit_aligned_trail: bool = False
    # Enforce the trailing stop as a REAL exit: an entry is dead the first bar price
    # trades through the trail, not merely when ``exit_mode`` many lines have flipped.
    #
    # The comment above ("the price stop pre-empts a two_red/three_red counter → the
    # exit_mode knob is near-inert live") described the intended behaviour, but nothing
    # implemented it — ``evaluate_item`` only ever counted reds. Under the live
    # ``three_red_signal`` setting that let a position sit indefinitely below its own
    # stop while the board reported it running at "0/3 red". OFF restores the
    # red-counter-only rule, for reproducing older study runs.
    price_stop_exit: bool = True
    # Removed from the live engine + UI + API (provably inert: 0.0 P&L change across
    # 7.5y — it keyed off the slow/widest ST, which always flips after the trail has
    # already exited). Retained here ONLY so the offline study scripts that documented
    # this can still construct the config; the live exit is the trail_target flip.
    early_lock: bool = False
    early_lock_profit_r: float = 1.0
    #: How many 1H bars a derivative contract's own latest bar may lag the
    #: underlying's and still be AUTO-EXECUTED. 0 = it must be current. See the field
    #: of the same name on ``EngineConfigModel`` for why "fresh" is not "current".
    max_contract_staleness_bars: int = 0
    #: Enforce a 3-day (18 hourly bars) time-decay exit on stock options if momentum stalls
    theta_time_stop: bool = True
    #: Hard time-stop cap in bars (e.g. 48 for ~8 trading days on 1H). 0 = off.
    time_stop_bars: int = 0
    #: Minimum ADX threshold to allow entry (e.g. 25.0). None = off.
    adx_min: Optional[float] = None
    #: Minimum ATR percentile threshold (e.g. 50.0). None = off.
    atr_pct_min: Optional[float] = None

    @property
    def warmup(self) -> int:
        """Bars to skip before all three SuperTrends are valid."""
        return max(self.fast[0], self.mid[0], self.slow[0])

    def params(self, target: TrailTarget) -> Tuple[int, float]:
        return {"fast": self.fast, "mid": self.mid, "slow": self.slow}[target]

    @property
    def exit_red_count(self) -> int:
        """Number of red ST lines needed to trigger exit (1, 2, or 3)."""
        from app.engines.common.exit_counter import get_exit_threshold
        return get_exit_threshold(self.exit_mode)

    @property
    def exit_needs_signal(self) -> bool:
        """True when exit_mode = three_red_signal (also requires a fresh counter-arrow)."""
        from app.engines.common.exit_counter import exit_needs_counter_signal
        return exit_needs_counter_signal(self.exit_mode)
