"""Cost-aware trade planning, feasibility evaluation, and position sizing.

Implements Specification 02:
- Realistic round-trip cost estimation (brokerage, STT, exchange fees, spread).
- Target floor calculation based on minimum required reward/risk ratio:
  T_required = u + min_net_RR * (d + u) where u is unit cost and d is stop distance.
- Attainable target feasibility ceiling evaluation.
- Lot-constrained position sizing subject to cash and session risk budgets.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional, Tuple

from app.engines.snapback.config import SnapbackConfig
from app.engines.snapback.intraday_models import CostEstimate, SetupDecision, TradePlan


def calculate_cost_estimate(
    quantity: int,
    ask_entry: float,
    bid_exit: float,
    cfg: SnapbackConfig,
    *,
    tick_size: float = 0.05,
) -> CostEstimate:
    """Calculate detailed transaction costs and effective entry/exit prices."""
    if quantity <= 0 or ask_entry <= 0 or bid_exit <= 0:
        return CostEstimate(
            quantity=0,
            ask_entry=0.0,
            bid_exit=0.0,
            expected_entry=0.0,
            expected_exit=0.0,
            spread_points=0.0,
            brokerage_fee=0.0,
            stt_and_taxes=0.0,
            total_round_trip_cost=0.0,
            cost_per_unit_points=0.0,
        )

    spread = max(0.0, ask_entry - bid_exit)
    # Brokerage: default min(₹20 per order, 0.03%) or fixed config
    brokerage_per_order = min(20.0, ask_entry * quantity * 0.0003)
    round_trip_brokerage = 2.0 * brokerage_per_order

    # STT & Taxes: 0.125% STT on sell side for options premium + GST/stamp duty
    stt_sell = bid_exit * quantity * 0.00125
    other_taxes = (ask_entry + bid_exit) * quantity * 0.0005
    total_taxes = stt_sell + other_taxes + float(cfg.scalp_fixed_cost_inr)

    total_fees = round_trip_brokerage + total_taxes
    cost_per_unit = (total_fees / float(quantity)) + (spread if spread > 0 else float(cfg.scalp_round_trip_cost_points))

    return CostEstimate(
        quantity=quantity,
        ask_entry=ask_entry,
        bid_exit=bid_exit,
        expected_entry=ask_entry,
        expected_exit=bid_exit,
        spread_points=spread,
        brokerage_fee=round_trip_brokerage,
        stt_and_taxes=total_taxes,
        total_round_trip_cost=total_fees,
        cost_per_unit_points=cost_per_unit,
    )


def calculate_required_target(
    stop_points: float,
    cost_per_unit_points: float,
    min_net_rr: float = 1.0,
) -> float:
    """Calculate minimum target in option points required to achieve net R:R.

    T_required = u + min_net_RR * (d + u)
    """
    u = cost_per_unit_points
    d = stop_points
    return u + min_net_rr * (d + u)


def evaluate_feasibility(
    effective_target: float,
    feasible_ceiling: float,
) -> str:
    """Determine whether the required target is attainable within the horizon."""
    if feasible_ceiling <= 0:
        return "UNMEASURED"
    if effective_target <= feasible_ceiling:
        return "FEASIBLE"
    return "UNATTAINABLE"


def build_trade_plan(
    setup: SetupDecision,
    contract_symbol: str,
    ask_entry: float,
    bid_exit: float,
    lot_size: int,
    cfg: SnapbackConfig,
    available_cash: float,
    remaining_day_loss: float,
    *,
    feasible_ceiling: float = 15.0,
    tick_size: float = 0.05,
    now_ms: int = 0,
) -> TradePlan:
    """Build a cost-aware TradePlan subject to cash and session risk constraints."""
    if not setup.is_eligible or lot_size <= 0 or ask_entry <= 0:
        return TradePlan(
            plan_id=f"plan_rejected_{now_ms}",
            setup_id=setup.setup_id,
            selected_contract_symbol=contract_symbol,
            requested_target_points=cfg.scalp_target_points,
            effective_target_points=0.0,
            feasible_target_ceiling=feasible_ceiling,
            initial_stop_points=cfg.scalp_stop_points,
            quantity=0,
            lots=0,
            cash_required=0.0,
            risk_amount=0.0,
            validity_time_ms=now_ms + 60000,
            policy_hash="rejected",
            feasibility_status="UNATTAINABLE",
        )

    # 1. Determine cost estimate for 1 lot
    cost_1lot = calculate_cost_estimate(lot_size, ask_entry, bid_exit, cfg, tick_size=tick_size)
    stop_points = cfg.scalp_stop_points
    req_target = calculate_required_target(stop_points, cost_1lot.cost_per_unit_points, cfg.scalp_min_net_rr)
    effective_target = max(cfg.scalp_target_points, req_target)
    feasibility = evaluate_feasibility(effective_target, feasible_ceiling)

    # 2. Risk per trade calculation
    per_trade_risk_cap = available_cash * (cfg.scalp_risk_pct / 100.0)
    allowed_risk = min(per_trade_risk_cap, remaining_day_loss)

    single_lot_risk = (stop_points * lot_size) + cost_1lot.total_round_trip_cost
    single_lot_cash = (ask_entry * lot_size) + cost_1lot.total_round_trip_cost

    if single_lot_risk > allowed_risk or single_lot_cash > available_cash or feasibility == "UNATTAINABLE":
        return TradePlan(
            plan_id=f"plan_unaffordable_{now_ms}",
            setup_id=setup.setup_id,
            selected_contract_symbol=contract_symbol,
            requested_target_points=cfg.scalp_target_points,
            effective_target_points=effective_target,
            feasible_target_ceiling=feasible_ceiling,
            initial_stop_points=stop_points,
            quantity=0,
            lots=0,
            cash_required=single_lot_cash,
            risk_amount=single_lot_risk,
            validity_time_ms=now_ms + 60000,
            policy_hash="unaffordable",
            feasibility_status=feasibility,
        )

    # Compute allowed lots (default 1 lot in initial phase)
    max_lots = 1
    quantity = max_lots * lot_size
    cost_total = calculate_cost_estimate(quantity, ask_entry, bid_exit, cfg, tick_size=tick_size)

    cash_req = (ask_entry * quantity) + cost_total.total_round_trip_cost
    risk_amt = (stop_points * quantity) + cost_total.total_round_trip_cost

    return TradePlan(
        plan_id=f"plan_{setup.symbol}_{now_ms}",
        setup_id=setup.setup_id,
        selected_contract_symbol=contract_symbol,
        requested_target_points=cfg.scalp_target_points,
        effective_target_points=round(effective_target / tick_size) * tick_size,
        feasible_target_ceiling=feasible_ceiling,
        initial_stop_points=stop_points,
        quantity=quantity,
        lots=max_lots,
        cash_required=round(cash_req, 2),
        risk_amount=round(risk_amt, 2),
        validity_time_ms=now_ms + 60000,
        policy_hash="v1_baseline",
        feasibility_status=feasibility,
    )
