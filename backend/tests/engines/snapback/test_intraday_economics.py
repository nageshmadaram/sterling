"""Unit tests for cost calculation, target feasibility, and trade planning."""

import pytest
from app.engines.snapback.config import SnapbackConfig
from app.engines.snapback.intraday_models import SetupDecision
from app.engines.snapback.intraday_economics import (
    calculate_cost_estimate,
    calculate_required_target,
    evaluate_feasibility,
    build_trade_plan,
)


def test_calculate_cost_estimate():
    cfg = SnapbackConfig(trading_mode="scalp")
    cost = calculate_cost_estimate(quantity=50, ask_entry=100.0, bid_exit=99.0, cfg=cfg)

    assert cost.quantity == 50
    assert cost.spread_points == 1.0
    assert cost.total_round_trip_cost > 0
    assert cost.cost_per_unit_points > 1.0


def test_calculate_required_target_formula():
    # Example from spec: u=1.8, d=4, min_rr=1.0 -> T_required = 1.8 + 1.0 * (4 + 1.8) = 7.6
    req_target = calculate_required_target(stop_points=4.0, cost_per_unit_points=1.8, min_net_rr=1.0)
    assert req_target == pytest.approx(7.6)


def test_evaluate_feasibility():
    assert evaluate_feasibility(effective_target=7.6, feasible_ceiling=10.0) == "FEASIBLE"
    assert evaluate_feasibility(effective_target=12.0, feasible_ceiling=10.0) == "UNATTAINABLE"
    assert evaluate_feasibility(effective_target=7.6, feasible_ceiling=0.0) == "UNMEASURED"


def test_build_trade_plan_success():
    cfg = SnapbackConfig(trading_mode="scalp", scalp_target_points=5.0, scalp_stop_points=4.0)
    setup = SetupDecision(
        setup_id="s1",
        timestamp_ms=1700000000000,
        symbol="NIFTY",
        side="PE",
        trigger_price=20000.0,
        mean_target=19900.0,
        invalidation_reference=20050.0,
        band_reentry_confirmed=True,
        is_eligible=True,
    )

    plan = build_trade_plan(
        setup=setup,
        contract_symbol="NIFTY_PE",
        ask_entry=100.0,
        bid_exit=99.0,
        lot_size=50,
        cfg=cfg,
        available_cash=100000.0,
        remaining_day_loss=2000.0,
        feasible_ceiling=15.0,
        now_ms=1700000000000,
    )

    assert plan.quantity == 50
    assert plan.lots == 1
    assert plan.feasibility_status == "FEASIBLE"
    assert plan.effective_target_points >= 5.0
    assert plan.cash_required > 5000.0
    assert plan.risk_amount > 200.0


def test_build_trade_plan_unaffordable_risk():
    cfg = SnapbackConfig(trading_mode="scalp")
    setup = SetupDecision(
        setup_id="s1",
        timestamp_ms=1700000000000,
        symbol="NIFTY",
        side="PE",
        trigger_price=20000.0,
        mean_target=19900.0,
        invalidation_reference=20050.0,
        band_reentry_confirmed=True,
        is_eligible=True,
    )

    # Remaining day loss limit of 10 INR is way too low for 1 lot risk
    plan = build_trade_plan(
        setup=setup,
        contract_symbol="NIFTY_PE",
        ask_entry=100.0,
        bid_exit=99.0,
        lot_size=50,
        cfg=cfg,
        available_cash=100000.0,
        remaining_day_loss=10.0,
        now_ms=1700000000000,
    )

    assert plan.quantity == 0
    assert plan.lots == 0
