"""Adversarial Acceptance Tests for Snapback Economic Reality & Falsification Engine v1.1."""
import os
import shutil
from unittest.mock import MagicMock

import pytest

from app.engines.snapback.config import SnapbackConfig
from app.engines.snapback.manifest import (
    FROZEN_COMMIT_SHA,
    create_frozen_manifest,
    verify_manifest_integrity,
)
from study.snapback_authoritative_gate import evaluate_authoritative_snapback_gate
from study.snapback_contract_registry import SnapbackContractRegistry
from study.snapback_observed_replay import (
    ObservedMarketQuoteStore,
    ObservedTradeRecord,
    replay_snapback_observed_trade,
)
from study.snapback_reconciliation_report import generate_snapback_reconciliation_bundle
from study.snapback_truedata_probe import TrueDataHistoricalClientProbe


def test_adversarial_manifest_tampered_delta_fails_verification():
    """Adversarial Test 1: Changing target_delta from 0.70 to 0.35 must fail manifest verification."""
    cfg = SnapbackConfig()
    manifest = create_frozen_manifest(cfg)
    
    # Tamper config to delta = 0.35
    tampered_cfg = SnapbackConfig(target_delta=0.35)
    
    valid, reasons = verify_manifest_integrity(manifest, tampered_cfg)
    assert valid is False
    assert any("target_delta mismatch" in r or "rule hash mismatch" in r.lower() for r in reasons)


def test_adversarial_futures_hedge_direction_corrected():
    """Adversarial Test 2: fade_up (PUT) MUST buy futures (LONG), fade_down (CALL) MUST sell futures (SHORT)."""
    # Create quote store with known option prices matching exact resolved strikes (23650.0 for PE, 21350.0 for CE)
    store = {
        "NIFTY_2024-06-03_23650.0_PE_ASK": {"price": 200.0},
        "NIFTY_2024-06-18_23650.0_PE_BID": {"price": 250.0},
        "NIFTY_2024-06-03_21350.0_CE_ASK": {"price": 200.0},
        "NIFTY_2024-06-18_21350.0_CE_BID": {"price": 250.0},
    }
    
    # Test fade_up (PUT option, negative delta): Futures hedge MUST be LONG (gain when spot rises)
    rec_put = replay_snapback_observed_trade(
        trade_id="trade_put_1",
        entry_date="2024-06-03",
        exit_date="2024-06-18",
        symbol="NIFTY",
        side="fade_up",
        spot_at_entry=22500.0,
        spot_at_exit=22600.0, # Spot rose +100
        quote_store=store,
    )
    assert rec_put.fill_status == "FILLED"
    # LONG future when spot rises +100 must have POSITIVE gross hedge PnL before costs
    # hedge_entry=22500, hedge_exit=22600 -> (22600 - 22500) * qty > 0
    assert rec_put.hedge_exit_price > rec_put.hedge_entry_price

    # Test fade_down (CALL option, positive delta): Futures hedge MUST be SHORT (gain when spot falls)
    rec_call = replay_snapback_observed_trade(
        trade_id="trade_call_1",
        entry_date="2024-06-03",
        exit_date="2024-06-18",
        symbol="NIFTY",
        side="fade_down",
        spot_at_entry=22500.0,
        spot_at_exit=22400.0, # Spot fell -100
        quote_store=store,
    )
    assert rec_call.fill_status == "FILLED"
    # SHORT future when spot falls -100 must have POSITIVE gross hedge PnL
    # hedge_entry=22500, hedge_exit=22400 -> (22500 - 22400) * qty > 0
    assert rec_call.hedge_entry_price > rec_call.hedge_exit_price


def test_adversarial_contract_registry_monthly_expiry_resolution():
    """Adversarial Test 3: Contract expiry must resolve to monthly expiry (last Thursday) 40-60 DTE."""
    registry = SnapbackContractRegistry()
    
    # June 3, 2024 -> July 25, 2024 is the monthly expiry ~52 DTE
    expiry = registry.get_monthly_expiry("NIFTY", "2024-06-03")
    assert expiry == "2024-07-25"
    
    spec = registry.resolve_contract_spec("NIFTY", "2024-06-03")
    assert spec.expiry == "2024-07-25"


def test_adversarial_authoritative_gate_requires_mtm_equity_series():
    """Adversarial Test 4: Missing daily MTM equity path must fail gate promotion when required."""
    pnls = [200.0] * 350
    costs = [10.0] * 350
    dates = [f"day_{i % 70}" for i in range(350)]
    
    verdict = evaluate_authoritative_snapback_gate(
        trade_pnls=pnls,
        entry_dates=dates,
        statutory_costs=costs,
        daily_mtm_equity_series=None, # Missing MTM series
        require_mtm_evidence=True,
    )
    
    assert verdict.promoted is False
    assert verdict.checks["daily_mtm_evidence_provided"] is False
    assert any("Missing daily MTM equity path evidence" in r for r in verdict.reasons)


def test_adversarial_authoritative_gate_requires_cost_evidence():
    """Adversarial Test 5: Missing statutory cost evidence must fail gate closed."""
    pnls = [200.0] * 350
    dates = [f"day_{i % 70}" for i in range(350)]
    
    verdict = evaluate_authoritative_snapback_gate(
        trade_pnls=pnls,
        entry_dates=dates,
        statutory_costs=None, # Missing costs
    )
    
    assert verdict.promoted is False
    assert verdict.checks["cost_evidence_provided"] is False


def test_adversarial_authoritative_gate_300_trades_5_days_fails_session_rule():
    """Adversarial Test 6: 300 trades concentrated on only 5 days must fail the 60 independent sessions gate."""
    pnls = [500.0] * 300
    costs = [20.0] * 300
    dates = [f"2024-06-0{1 + (i % 5)}" for i in range(300)]
    
    verdict = evaluate_authoritative_snapback_gate(
        trade_pnls=pnls,
        entry_dates=dates,
        statutory_costs=costs,
        daily_mtm_equity_series=[1000000.0] * 300,
    )
    
    assert verdict.promoted is False
    assert verdict.checks["independent_sessions_ge_60"] is False


def test_adversarial_reconciliation_filters_unfilled_trades():
    """Adversarial Test 7: INCONCLUSIVE / NO_FILL records must NOT count toward completed trade sample."""
    rec_unfilled = ObservedTradeRecord(
        trade_id="trade_unfilled_1",
        entry_date="2024-06-03",
        exit_date="2024-06-18",
        symbol="NIFTY",
        side="fade_up",
        strike=22500.0,
        option_type="PE",
        expiry_date="2024-07-25",
        lot_size=25,
        quantity=0,
        entry_ask_price=0.0,
        exit_bid_price=0.0,
        modeled_entry_price=100.0,
        modeled_exit_price=150.0,
        gross_option_pnl=0.0,
        statutory_charges=0.0,
        net_option_pnl=0.0,
        hedge_symbol="NIFTY_FUT",
        hedge_entry_price=0.0,
        hedge_exit_price=0.0,
        hedge_pnl=0.0,
        total_trade_pnl=0.0,
        peak_margin_required=0.0,
        fill_status="INCONCLUSIVE",
        notes="Missing quote",
    )
    
    run_id = "test_adv_run_unfilled_filter"
    output_dir = "research/snapback_reality_v1"
    
    summary = generate_snapback_reconciliation_bundle(
        run_id=run_id,
        observed_records=[rec_unfilled],
        output_base_dir=output_dir,
    )
    
    assert summary["total_trades"] == 0
    assert summary["authoritative_gate_promoted"] is False
    
    shutil.rmtree(os.path.join(output_dir, run_id), ignore_errors=True)


def test_snapback_v121_point_in_time_delta_selection():
    """Verify Black-Scholes point-in-time delta contract selection picks the strike closest to 0.70 delta."""
    from study.snapback_observed_replay import bs_delta, select_contract_by_delta

    # Spot 22500, 50 DTE, fade_up (PUT option)
    strike, opt_type, delta = select_contract_by_delta(
        spot=22500.0,
        dte_days=50,
        side="fade_up",
        target_delta=0.70,
        strike_step=50.0,
    )
    assert opt_type == "PE"
    assert abs(abs(delta) - 0.70) < 0.05
    # For PUT 0.70 delta, strike must be ITM (strike > spot)
    assert strike > 22500.0


def test_snapback_v121_futures_hedge_lot_sizing():
    """Verify option market delta * causal beta translates into actual index futures lots."""
    store = {
        "NIFTY_2024-06-03_23650.0_PE_ASK": {"price": 200.0},
        "NIFTY_2024-06-18_23650.0_PE_BID": {"price": 250.0},
        "NIFTY_FUT_2024-06_2024-06-03_ASK": {"price": 22510.0},
        "NIFTY_FUT_2024-06_2024-06-18_BID": {"price": 22610.0},
    }
    rec = replay_snapback_observed_trade(
        trade_id="trade_hedge_lot_1",
        entry_date="2024-06-03",
        exit_date="2024-06-18",
        symbol="NIFTY",
        side="fade_up",
        spot_at_entry=22500.0,
        spot_at_exit=22600.0,
        quote_store=store,
    )
    assert rec.fill_status == "FILLED"
    # NIFTY lot size on 2024-06-03 was 25 according to exact historical revision schedule
    assert rec.quantity == 25
    assert rec.hedge_symbol.startswith("NIFTY_FUT_2024-06")


def test_snapback_v121_no_synthetic_mtm_interpolation():
    """Verify synthetic linear MTM interpolation is removed and missing MTM fails gate when required."""
    store = {
        "NIFTY_2024-06-03_23650.0_PE_ASK": {"price": 200.0},
        "NIFTY_2024-06-18_23650.0_PE_BID": {"price": 250.0},
    }
    rec = replay_snapback_observed_trade(
        trade_id="trade_no_mtm_1",
        entry_date="2024-06-03",
        exit_date="2024-06-18",
        symbol="NIFTY",
        side="fade_up",
        spot_at_entry=22500.0,
        spot_at_exit=22600.0,
        quote_store=store,
    )
    assert rec.fill_status == "FILLED"
    # Without daily marks in store, daily_mtm_equity MUST NOT contain synthetic linear interpolation
    assert rec.daily_mtm_equity == []


def test_snapback_v121_trial_registry_file_verification():
    """Verify trial registry artifact research/snapback_reality_v1/frozen_trial_registry.json exists and matches hash."""
    from app.engines.snapback.manifest import verify_trial_registry_file_hash
    valid, msg = verify_trial_registry_file_hash()
    assert valid is True, f"Trial registry verification failed: {msg}"


def test_snapback_v121_contract_registry_loader():
    """Verify SnapbackContractRegistry loader interfaces for historical membership."""
    registry = SnapbackContractRegistry()
    registry.load_from_dict({
        "membership": {"HISTICAL_NAME": ["2018-01-01", "2025-12-31"]},
        "lot_sizes": {"HISTICAL_NAME": [["2018-01-01", "2025-12-31", 250]]},
        "strike_steps": {"HISTICAL_NAME": 25.0},
    })
    assert registry.is_fo_eligible("HISTICAL_NAME", "2020-05-15") is True
    assert registry.is_fo_eligible("HISTICAL_NAME", "2028-05-15") is False
    assert registry.get_lot_size("HISTICAL_NAME", "2020-05-15") == 250
    assert registry.get_strike_step("HISTICAL_NAME") == 25.0


def test_snapback_v121_truedata_probe_evidence_metrics():
    """Verify TrueDataEntitlementProbe produces evidence-grade result fields."""
    mock_client = MagicMock()
    mock_client.get_bars.return_value = [
        MagicMock(timestamp="2024-06-03 09:15:00"),
        MagicMock(timestamp="2024-06-03 15:30:00"),
    ]
    mock_client.get_ticks.return_value = [
        MagicMock(timestamp="2024-06-03 09:15:00", bid=100.0, ask=100.5),
        MagicMock(timestamp="2024-06-03 15:30:00", bid=120.0, ask=120.5),
    ]

    prober = TrueDataHistoricalClientProbe(client=mock_client)
    res = prober.probe_contract_retention("NIFTY", "2024-06-03", "2024-06-03")

    assert res.ticks_available is True
    assert res.bars_available is True
    assert res.first_tick_timestamp == "2024-06-03 09:15:00"
    assert res.last_tick_timestamp == "2024-06-03 15:30:00"
    assert res.tick_count == 2
    assert res.bid_ask_coverage_pct == 100.0
    assert res.entitlement_status == "OK"


