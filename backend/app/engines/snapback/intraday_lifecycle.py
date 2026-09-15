"""Deterministic position lifecycle state machine and trailing stop ratcheting.

Implements Specification 02 & 03:
- States: OPEN_SCALP -> LOCKED -> TARGET_PENDING -> RUNNER -> EXIT_REQUIRED -> CLOSED.
- Monotone stop ratcheting: desired_stop = max(prev_stop, high_water - trail_points).
- Absolute hold timeouts (20 bars for Scalp / 12 bars for Intraday).
- Pre-target profit locking after executable gain covers costs + lock objective (1 point).
- Continuation evaluation at target before runner promotion.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional, Tuple

from app.engines.snapback.config import SnapbackConfig
from app.engines.snapback.intraday_models import PositionState


def create_initial_position(
    position_id: str,
    account_id: str,
    contract_symbol: str,
    side: str,
    confirmed_quantity: int,
    entry_vwap: float,
    cfg: SnapbackConfig,
    *,
    now_ms: int = 0,
    tick_size: float = 0.05,
) -> PositionState:
    """Initialize a new PositionState in OPEN_SCALP state with initial stop."""
    initial_stop_points = cfg.scalp_stop_points
    initial_stop = max(0.05, entry_vwap - initial_stop_points)
    initial_stop = round(initial_stop / tick_size) * tick_size
    target_price = entry_vwap + cfg.scalp_target_points

    return PositionState(
        position_id=position_id,
        account_id=account_id,
        contract_symbol=contract_symbol,
        side=side,
        confirmed_quantity=confirmed_quantity,
        remaining_quantity=confirmed_quantity,
        entry_vwap=entry_vwap,
        active_phase="OPEN_SCALP",
        desired_stop=initial_stop,
        active_stop=initial_stop,
        target_price=target_price,
        high_water_mark=entry_vwap,
        bars_held=0,
        created_at_ms=now_ms,
        updated_at_ms=now_ms,
    )


def evaluate_continuation_qualifies(
    bar_open: float,
    bar_high: float,
    bar_low: float,
    bar_close: float,
) -> bool:
    """Check continuation condition: close in upper 35% of completed candle range."""
    candle_range = bar_high - bar_low
    if candle_range <= 0:
        return True
    close_position = (bar_close - bar_low) / candle_range
    return close_position >= 0.65


def advance_position_lifecycle(
    state: PositionState,
    current_bid: float,
    completed_bar: Optional[Dict[str, Any]],
    cfg: SnapbackConfig,
    *,
    now_ms: int = 0,
    is_session_end: bool = False,
    tick_size: float = 0.05,
) -> Tuple[PositionState, Optional[str]]:
    """Advance position lifecycle by one observation or completed bar.

    Returns (new_position_state, optional_exit_reason).
    """
    if state.active_phase == "CLOSED":
        return state, None

    bars_held = state.bars_held + (1 if completed_bar is not None else 0)
    high_water = max(state.high_water_mark, current_bid)
    max_initial_bars = cfg.scalp_max_hold_bars
    max_runner_bars = cfg.scalp_runner_max_bars

    # 1. Immediate exit conditions
    if is_session_end:
        new_state = _update_phase(state, "EXIT_REQUIRED", high_water, bars_held, now_ms)
        return new_state, "session_square_off"

    if current_bid <= state.desired_stop:
        new_state = _update_phase(state, "EXIT_REQUIRED", high_water, bars_held, now_ms)
        return new_state, "stop_loss_hit"

    # Timeout check
    if state.active_phase in ("OPEN_SCALP", "LOCKED") and bars_held >= max_initial_bars:
        new_state = _update_phase(state, "EXIT_REQUIRED", high_water, bars_held, now_ms)
        return new_state, "initial_hold_timeout"

    if state.active_phase == "RUNNER" and bars_held >= max_runner_bars:
        new_state = _update_phase(state, "EXIT_REQUIRED", high_water, bars_held, now_ms)
        return new_state, "runner_max_hold_timeout"

    # 2. Phase transition logic
    new_phase = state.active_phase
    desired_stop = state.desired_stop

    # Lock gains if price reaches entry_vwap + lock_points
    lock_trigger = state.entry_vwap + cfg.scalp_round_trip_cost_points + cfg.scalp_lock_points
    if state.active_phase == "OPEN_SCALP" and current_bid >= lock_trigger:
        new_phase = "LOCKED"
        locked_stop = state.entry_vwap + cfg.scalp_lock_points
        desired_stop = max(desired_stop, round(locked_stop / tick_size) * tick_size)

    # Target trigger evaluation
    if state.active_phase in ("OPEN_SCALP", "LOCKED") and current_bid >= state.target_price:
        qualifies = False
        if completed_bar:
            qualifies = evaluate_continuation_qualifies(
                float(completed_bar.get("open", 0)),
                float(completed_bar.get("high", 0)),
                float(completed_bar.get("low", 0)),
                float(completed_bar.get("close", 0)),
            )

        if qualifies:
            new_phase = "RUNNER"
            # Ratchet stop to trail from high water
            trailed_stop = high_water - cfg.scalp_trail_points
            desired_stop = max(desired_stop, round(trailed_stop / tick_size) * tick_size)
        else:
            new_state = _update_phase(state, "EXIT_REQUIRED", high_water, bars_held, now_ms)
            return new_state, "target_reached_no_continuation"

    # Trailing stop ratcheting for RUNNER phase
    if new_phase == "RUNNER":
        trailed_stop = high_water - cfg.scalp_trail_points
        desired_stop = max(desired_stop, round(trailed_stop / tick_size) * tick_size)

    # Stop monotonic check: never widen stop
    desired_stop = max(state.desired_stop, desired_stop)

    new_state = PositionState(
        position_id=state.position_id,
        account_id=state.account_id,
        contract_symbol=state.contract_symbol,
        side=state.side,
        confirmed_quantity=state.confirmed_quantity,
        remaining_quantity=state.remaining_quantity,
        entry_vwap=state.entry_vwap,
        active_phase=new_phase,
        desired_stop=desired_stop,
        active_stop=state.active_stop,
        target_price=state.target_price,
        high_water_mark=high_water,
        bars_held=bars_held,
        created_at_ms=state.created_at_ms,
        updated_at_ms=now_ms,
    )

    return new_state, None


def _update_phase(
    state: PositionState, new_phase: str, high_water: float, bars_held: int, now_ms: int
) -> PositionState:
    return PositionState(
        position_id=state.position_id,
        account_id=state.account_id,
        contract_symbol=state.contract_symbol,
        side=state.side,
        confirmed_quantity=state.confirmed_quantity,
        remaining_quantity=state.remaining_quantity,
        entry_vwap=state.entry_vwap,
        active_phase=new_phase,
        desired_stop=state.desired_stop,
        active_stop=state.active_stop,
        target_price=state.target_price,
        high_water_mark=high_water,
        bars_held=bars_held,
        created_at_ms=state.created_at_ms,
        updated_at_ms=now_ms,
    )
