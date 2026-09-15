"""Unit tests for account risk reservation and capital manager."""

import pytest
from app.engines.snapback.config import SnapbackConfig
from app.engines.snapback.intraday_models import TradePlan
from app.services.snapback_execution import AccountRiskManager


def test_reserve_risk_success():
    mgr = AccountRiskManager()
    cfg = SnapbackConfig(trading_mode="scalp", scalp_risk_pct=0.5, scalp_daily_loss_pct=2.0)

    plan = TradePlan(
        plan_id="p1",
        setup_id="s1",
        selected_contract_symbol="NIFTY_PE",
        requested_target_points=5.0,
        effective_target_points=7.6,
        feasible_target_ceiling=15.0,
        initial_stop_points=4.0,
        quantity=50,
        lots=1,
        cash_required=5100.0,
        risk_amount=290.0,
        validity_time_ms=1700000060000,
        policy_hash="v1",
        feasibility_status="FEASIBLE",
    )

    res = mgr.reserve_risk(
        plan=plan,
        account_id="acc1",
        session_id="sess1",
        cfg=cfg,
        available_cash=100000.0,
        settled_session_loss=0.0,
        open_positions_risk=0.0,
        now_ms=1700000000000,
    )

    assert res is not None
    assert res.reserved_cash == 5100.0
    assert res.reserved_risk == 290.0
    assert res.status == "ACTIVE"

    # Release reservation
    assert mgr.release_reservation(res.reservation_id) is True


def test_reserve_risk_exceeds_daily_loss_limit():
    mgr = AccountRiskManager()
    cfg = SnapbackConfig(trading_mode="scalp", scalp_daily_loss_pct=2.0)

    plan = TradePlan(
        plan_id="p1",
        setup_id="s1",
        selected_contract_symbol="NIFTY_PE",
        requested_target_points=5.0,
        effective_target_points=7.6,
        feasible_target_ceiling=15.0,
        initial_stop_points=4.0,
        quantity=50,
        lots=1,
        cash_required=5100.0,
        risk_amount=290.0,
        validity_time_ms=1700000060000,
        policy_hash="v1",
        feasibility_status="FEASIBLE",
    )

    # Settled loss of 1900 on 100k account leaves only 100 INR allowance (less than 290 INR risk needed)
    res = mgr.reserve_risk(
        plan=plan,
        account_id="acc1",
        session_id="sess1",
        cfg=cfg,
        available_cash=100000.0,
        settled_session_loss=1900.0,
        open_positions_risk=0.0,
        now_ms=1700000000000,
    )

    assert res is None
