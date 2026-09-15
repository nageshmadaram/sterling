"""Unit tests for position state machine and trailing stop ratcheting."""

import pytest
from app.engines.snapback.config import SnapbackConfig
from app.engines.snapback.intraday_lifecycle import (
    create_initial_position,
    advance_position_lifecycle,
    evaluate_continuation_qualifies,
)


def test_initial_position_creation():
    cfg = SnapbackConfig(trading_mode="scalp", scalp_stop_points=4.0, scalp_target_points=5.0)
    pos = create_initial_position("p1", "acc1", "NIFTY_PE", "PE", 50, 100.0, cfg, now_ms=1000)

    assert pos.active_phase == "OPEN_SCALP"
    assert pos.desired_stop == 96.0
    assert pos.target_price == 105.0
    assert pos.remaining_quantity == 50


def test_lock_gains_transition():
    cfg = SnapbackConfig(trading_mode="scalp", scalp_round_trip_cost_points=1.0, scalp_lock_points=1.0)
    pos = create_initial_position("p1", "acc1", "NIFTY_PE", "PE", 50, 100.0, cfg, now_ms=1000)

    # Current bid reaching 102.0 (100 + 1 cost + 1 lock) triggers LOCKED phase
    next_pos, exit_reason = advance_position_lifecycle(pos, current_bid=102.5, completed_bar=None, cfg=cfg, now_ms=2000)

    assert exit_reason is None
    assert next_pos.active_phase == "LOCKED"
    assert next_pos.desired_stop == 101.0  # entry 100 + 1 lock = 101


def test_runner_promotion_transition():
    cfg = SnapbackConfig(trading_mode="scalp", scalp_target_points=5.0, scalp_trail_points=2.0)
    pos = create_initial_position("p1", "acc1", "NIFTY_PE", "PE", 50, 100.0, cfg, now_ms=1000)

    # Bar close in upper 35% of range (open=100, high=106, low=99, close=105)
    bar = {"open": 100, "high": 106, "low": 99, "close": 105}
    next_pos, exit_reason = advance_position_lifecycle(pos, current_bid=105.5, completed_bar=bar, cfg=cfg, now_ms=2000)

    assert exit_reason is None
    assert next_pos.active_phase == "RUNNER"
    # high water 105.5 - 2.0 trail = 103.5
    assert next_pos.desired_stop == 103.5


def test_stop_loss_exit():
    cfg = SnapbackConfig(trading_mode="scalp")
    pos = create_initial_position("p1", "acc1", "NIFTY_PE", "PE", 50, 100.0, cfg, now_ms=1000)

    next_pos, exit_reason = advance_position_lifecycle(pos, current_bid=95.0, completed_bar=None, cfg=cfg, now_ms=2000)
    assert exit_reason == "stop_loss_hit"
    assert next_pos.active_phase == "EXIT_REQUIRED"


def test_monotone_stop_ratcheting():
    cfg = SnapbackConfig(trading_mode="scalp", scalp_trail_points=2.0)
    pos = create_initial_position("p1", "acc1", "NIFTY_PE", "PE", 50, 100.0, cfg, now_ms=1000)
    bar = {"open": 100, "high": 107, "low": 99, "close": 106}

    # First advance to RUNNER at 106.0 -> stop = 104.0
    p2, _ = advance_position_lifecycle(pos, current_bid=106.0, completed_bar=bar, cfg=cfg, now_ms=2000)
    assert p2.desired_stop == 104.0

    # Bid drops to 104.5 -> stop MUST NOT widen/decrease below 104.0
    p3, _ = advance_position_lifecycle(p2, current_bid=104.5, completed_bar=bar, cfg=cfg, now_ms=3000)
    assert p3.desired_stop == 104.0
