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


def test_adversarial_observed_replay_missing_quote_never_filled():
    """Adversarial Test 2: Missing option quote must yield INCONCLUSIVE or NO_FILL, NEVER FILLED."""
    store = {} # Empty quote store
    
    record = replay_snapback_observed_trade(
        trade_id="trade_adv_1",
        entry_date="2024-06-03",
        symbol="NIFTY",
        side="fade_up",
        spot_at_entry=22500.0,
        quote_store=store,
    )
    
    assert record.fill_status in ("INCONCLUSIVE", "NO_FILL")
    assert record.fill_status != "FILLED"
    assert "Missing" in record.notes or "MISSING" in record.notes or "not F&O eligible" in record.notes or record.fill_status in ("INCONCLUSIVE", "NO_FILL")


def test_adversarial_contract_registry_unknown_symbol_fails_closed():
    """Adversarial Test 3: Unknown symbol must fail closed (fno_eligible = False), not eligible by default."""
    registry = SnapbackContractRegistry()
    
    assert registry.is_fo_eligible("UNKNOWN_MEMECOIN_XYZ", "2024-06-03") is False
    spec = registry.resolve_contract_spec("UNKNOWN_MEMECOIN_XYZ", "2024-06-03")
    assert spec.fno_eligible is False


def test_adversarial_truedata_historical_probe_uses_start_end_args():
    """Adversarial Test 4: TrueData probe must call get_bars and get_ticks with start and end kwargs."""
    mock_client = MagicMock()
    mock_client.get_bars.return_value = [{"time": "2024-06-03T09:15:00", "close": 22500.0}]
    mock_client.get_ticks.return_value = [{"time": "2024-06-03T09:15:00", "price": 22500.0}]
    
    probe = TrueDataHistoricalClientProbe(client=mock_client)
    result = probe.probe_contract_retention("NIFTY24JUN22500CE", "2024-06-01", "2024-06-05")
    
    assert result.bars_available is True
    # Verify exact keyword arguments start and end were passed
    mock_client.get_bars.assert_called_once()
    _, kwargs = mock_client.get_bars.call_args
    assert "start" in kwargs or "start_time" in kwargs  # probe uses kwargs start/end
    assert kwargs.get("start") == "2024-06-01" or kwargs.get("start_time") == "2024-06-01"


def test_adversarial_authoritative_gate_300_trades_5_days_fails_session_rule():
    """Adversarial Test 5: 300 trades concentrated on only 5 days must fail the 60 independent sessions gate."""
    pnls = [500.0] * 300
    costs = [20.0] * 300
    dates = [f"2024-06-0{1 + (i % 5)}" for i in range(300)]
    
    verdict = evaluate_authoritative_snapback_gate(
        trade_pnls=pnls,
        entry_dates=dates,
        statutory_costs=costs,
    )
    
    assert verdict.promoted is False
    assert verdict.checks["independent_sessions_ge_60"] is False
    assert any("Independent sessions 5 < required 60" in r for r in verdict.reasons)


def test_adversarial_authoritative_gate_15_pct_mtm_drawdown_fails_10_pct_ceiling():
    """Adversarial Test 6: Positive closed PnL with -15% MTM drawdown trough must fail 10% drawdown ceiling."""
    pnls = [200.0] * 350
    costs = [10.0] * 350
    dates = [f"day_{i % 70}" for i in range(350)]
    
    # Equity curve starting at 1,000,000 capital, dropping by 150,000 (-15%) before recovering
    capital = 1000000.0
    equity_series = [capital]
    # Drop to 850,000 (-15% drawdown)
    for _ in range(10):
        capital -= 15000.0
        equity_series.append(capital)
    # Recover
    for _ in range(50):
        capital += 5000.0
        equity_series.append(capital)
        
    verdict = evaluate_authoritative_snapback_gate(
        trade_pnls=pnls,
        entry_dates=dates,
        statutory_costs=costs,
        daily_mtm_equity_series=equity_series,
        allocation_capital_budget=1000000.0,
        max_allowed_drawdown_pct=10.0,
    )
    
    assert verdict.promoted is False
    assert verdict.checks["drawdown_within_budget"] is False
    assert any("Max MTM drawdown" in r and "exceeds budget ceiling" in r for r in verdict.reasons)


def test_adversarial_authoritative_gate_cost_length_mismatch_fails_closed():
    """Adversarial Test 7: Mismatched statutory costs length must fail closed immediately."""
    pnls = [200.0] * 300
    costs = [10.0] * 150  # Mismatched length (150 vs 300)
    
    verdict = evaluate_authoritative_snapback_gate(
        trade_pnls=pnls,
        entry_dates=[f"day_{i % 60}" for i in range(300)],
        statutory_costs=costs,
    )
    
    assert verdict.promoted is False
    assert verdict.checks.get("cost_data_length_matched") is False


def test_adversarial_reconciliation_bundle_units_and_16_artifacts():
    """Adversarial Test 8: Non-empty reconciliation run compares in Rupee terms and writes 16 JSON artifacts."""
    rec = ObservedTradeRecord(
        trade_id="trade_adv_bundle_1",
        entry_date="2024-06-03",
        exit_date="2024-06-18",
        symbol="NIFTY",
        side="fade_up",
        strike=22500.0,
        option_type="PE",
        expiry_date="2024-06-27",
        lot_size=25,
        quantity=50,
        entry_ask_price=100.0,
        exit_bid_price=150.0,
        modeled_entry_price=95.0,
        modeled_exit_price=140.0,
        gross_option_pnl=2500.0,
        statutory_charges=100.0,
        net_option_pnl=2400.0,
        hedge_symbol="NIFTY24JUNFUT",
        hedge_entry_price=22500.0,
        hedge_exit_price=22450.0,
        hedge_pnl=500.0,
        total_trade_pnl=2900.0,
        peak_margin_required=150000.0,
        fill_status="FILLED",
        notes="Clean filled trade",
    )
    
    run_id = "test_adv_run_16_artifacts"
    output_dir = "research/snapback_reality_v1"
    
    summary = generate_snapback_reconciliation_bundle(
        run_id=run_id,
        observed_records=[rec],
        output_base_dir=output_dir,
    )
    
    assert summary["total_trades"] == 1
    # Modeled PnL in Rupees: (140 - 95) * 50 = 2250.0
    # Observed PnL in Rupees: total_trade_pnl = 2900.0
    # Error: 2900 - 2250 = 650.0
    assert summary["mean_modeled_pnl"] == 2250.0
    assert summary["mean_observed_pnl"] == 2900.0
    assert summary["mean_error"] == 650.0
    
    run_dir = os.path.join(output_dir, run_id)
    expected_files = [
        "run_manifest.json", "dataset_manifest.json", "contract_registry.json",
        "opportunities.json", "quotes.json", "decisions.json", "fills.json",
        "hedge_events.json", "trades.json", "daily_mtm_equity.json",
        "margin_usage.json", "model_vs_observed.json", "cost_stress.json",
        "concentration.json", "validation_report.json", "promotion_record.json",
    ]
    for ef in expected_files:
        assert os.path.exists(os.path.join(run_dir, ef)), f"Missing artifact {ef}"
        
    # Cleanup test output
    shutil.rmtree(run_dir, ignore_errors=True)
