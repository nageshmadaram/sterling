"""Unit tests for Snapback Single Authoritative Promotion Gate and Quarantined Report Builder."""
import pytest
from study.snapback_authoritative_gate import evaluate_authoritative_snapback_gate
from app.services.snapback_validation import build_validation_report_for_run


def test_authoritative_gate_rejection_on_insufficient_sample_and_drawdown():
    # 50 trades (less than 300 required), 10 sessions (less than 60 required)
    pnls = [100.0] * 50
    costs = [10.0] * 50
    
    verdict = evaluate_authoritative_snapback_gate(
        trade_pnls=pnls,
        entry_sessions_count=10,
        statutory_costs=costs,
        max_allowed_drawdown_pct=20.0,
    )
    
    assert verdict.promoted is False
    assert verdict.checks["independent_sessions_ge_60"] is False
    assert verdict.checks["completed_trades_ge_300"] is False


def test_authoritative_gate_promotion_when_all_gates_cleared():
    # 350 positive trades across 70 sessions
    pnls = [200.0] * 350
    costs = [10.0] * 350
    dates = [f"2024-06-{(i % 70) + 1:02d}" for i in range(350)]
    mtm_series = [1000000.0 + (i * 100.0) for i in range(350)]
    
    verdict = evaluate_authoritative_snapback_gate(
        trade_pnls=pnls,
        entry_dates=dates,
        statutory_costs=costs,
        daily_mtm_equity_series=mtm_series,
        require_mtm_evidence=True,
    )
    
    assert verdict.promoted is True
    assert verdict.completed_trades == 350
    assert verdict.total_sessions == 70
    assert verdict.expectancy_2x_cost > 0.0


def test_quarantined_legacy_validation_report_builder_never_promotes():
    replay_result = {
        "trades": [{"net_pnl": 500.0} for _ in range(100)],
        "opportunities": 100,
    }
    class MockConfig:
        pass
        
    report = build_validation_report_for_run(replay_result, MockConfig())
    assert report.is_promotable is False
    assert "quarantined_legacy_report_builder" in report.limitations
