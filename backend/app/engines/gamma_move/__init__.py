"""Gamma Move -- buy the option that writers are covering at a level.

An F&O single stock trades into a spot support or resistance level. The strike
sitting on that level carries the chain's heaviest open interest, because that is
where the writers are. When spot breaks through, they cover: open interest falls,
volume spikes and the premium rises on the same fifteen-minute bar, and because
the strike is going in-the-money at the same time the premium accelerates faster
than delta alone explains. That acceleration is gamma. Hold one day, two at most.

Transcribed from a public podcast walkthrough; see ``docs/strategy/gamma-move/``
for the source, the contract (A310), and the calibration that set every threshold
in :mod:`.config`.

**The measured result, stated up front:** the entry triple on its own did not
separate from the unconditional population. The edge lives behind the level
filter. See ``config`` and ``docs/strategy/gamma-move/VALIDATION_REPORT.md``.

This package is self-contained strategy mathematics: no broker, no socket, no
clock. Every other engine in this codebase is untouched by it.
"""
from __future__ import annotations

from .config import (CALIBRATED_FIELDS, CALIBRATION, EXIT_POLICIES, GammaMoveConfig,
                     LEVEL_TIMEFRAMES, RESEARCH_ONLY_EXIT_POLICIES, SIZING_MODES,
                     STOP_BASES, STOP_MODES, TRIGGER_TIMEFRAMES)
from .exit import (build_exit_event, exit_order_price, initial_stop, realised_inr,
                   should_exit, swing_low_stop, target_price, update_trail)
from .levels import find_levels, live_levels, option_type_for, swing_pivots
from .models import (Candle, ExitEvent, GammaSignal, InstrumentRef, OICandle,
                     PositionState, SpotLevel, StrikeCandidate, TradeRecord,
                     TriggerMetrics, align_to_tick, q2)
from .regime import regime_allows, regime_of, regime_reason
from .replay import replay_contract, summarise
from .selection import (days_to_expiry, expiry_in_window, is_chain_wall, pick_strike,
                        select_expiry, spot_through_or_at_strike, strikes_near_level)
from .sizing import at_risk_inr, deployed_inr, lots_for, risk_multiplier, sizing_blocker
from .strategy import Decision, GammaMoveStrategy, Intent, Phase, SessionState
from .trigger import evaluate, evaluate_bar, session_day, slice_session, volume_baseline

STRATEGY_ID = "gamma_move"
STRATEGY_NAME = "Gamma Move"
CONTRACT_VERSION = "A310.2"

__all__ = [
    "STRATEGY_ID", "STRATEGY_NAME", "CONTRACT_VERSION",
    "GammaMoveConfig", "CALIBRATION", "CALIBRATED_FIELDS", "EXIT_POLICIES",
    "LEVEL_TIMEFRAMES", "TRIGGER_TIMEFRAMES", "STOP_BASES", "SIZING_MODES",
    "STOP_MODES", "RESEARCH_ONLY_EXIT_POLICIES",
    "GammaMoveStrategy", "SessionState", "Decision", "Intent", "Phase",
    "Candle", "OICandle", "InstrumentRef", "SpotLevel", "StrikeCandidate",
    "TriggerMetrics", "GammaSignal", "PositionState", "ExitEvent", "TradeRecord",
    "q2", "align_to_tick",
    "find_levels", "live_levels", "swing_pivots", "option_type_for",
    "select_expiry", "expiry_in_window", "days_to_expiry", "strikes_near_level",
    "pick_strike", "is_chain_wall", "spot_through_or_at_strike",
    "evaluate", "evaluate_bar", "slice_session", "volume_baseline", "session_day",
    "regime_of", "regime_allows", "regime_reason",
    "swing_low_stop", "initial_stop", "target_price", "update_trail", "should_exit",
    "exit_order_price", "build_exit_event", "realised_inr",
    "lots_for", "sizing_blocker", "risk_multiplier", "at_risk_inr", "deployed_inr",
    "replay_contract", "summarise",
]
