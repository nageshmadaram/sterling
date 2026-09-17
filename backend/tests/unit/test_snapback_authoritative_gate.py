"""Unit tests for Snapback Single Authoritative Promotion Gate and Quarantined Report Builder."""
import math

from study.snapback_authoritative_gate import (
    evaluate_authoritative_snapback_gate,
    evaluate_with_verdict,
)
from app.services.snapback_validation import build_validation_report_for_run


def _dates(n: int, sessions: int = 70):
    return [f"2024-{((i % sessions) // 28) + 1:02d}-{((i % 28) + 1):02d}" for i in range(n)]


def test_authoritative_gate_rejection_on_insufficient_sample_and_drawdown():
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
    pnls = [200.0] * 350
    costs = [10.0] * 350
    dates = _dates(350, sessions=70)
    mtm_series = [1000000.0 + (i * 100.0) for i in range(350)]

    verdict = evaluate_authoritative_snapback_gate(
        trade_pnls=pnls,
        entry_dates=dates,
        entry_sessions_count=70,
        statutory_costs=costs,
        daily_mtm_equity_series=mtm_series,
        require_mtm_evidence=True,
    )

    assert verdict.promoted is True
    assert verdict.completed_trades == 350
    assert verdict.total_sessions == 70
    assert verdict.expectancy_2x_cost > 0.0
    assert verdict.expectancy_3x_cost > 0.0
    assert verdict.expectancy_without_top_1pct > 0.0
    assert verdict.checks["positive_under_3x_costs"] is True
    assert verdict.checks["positive_without_top_1pct"] is True


def test_three_x_cost_stress_is_a_hard_gate():
    # Baseline and 2x stay positive, but 3x costs erase the edge.
    pnls = [25.0] * 300
    costs = [10.0] * 300
    verdict = evaluate_authoritative_snapback_gate(
        trade_pnls=pnls,
        entry_dates=_dates(300),
        entry_sessions_count=70,
        statutory_costs=costs,
        daily_mtm_equity_series=[1_000_000.0 + i for i in range(300)],
    )

    assert verdict.expectancy_2x_cost == 15.0
    assert verdict.expectancy_3x_cost == 5.0
    assert verdict.checks["positive_under_3x_costs"] is True

    pnls = [15.0] * 300
    verdict = evaluate_authoritative_snapback_gate(
        trade_pnls=pnls,
        entry_dates=_dates(300),
        entry_sessions_count=70,
        statutory_costs=costs,
        daily_mtm_equity_series=[1_000_000.0 + i for i in range(300)],
    )
    assert verdict.expectancy_2x_cost == 5.0
    assert verdict.expectancy_3x_cost == -5.0
    assert verdict.promoted is False
    assert verdict.checks["positive_under_3x_costs"] is False


def test_tail_removal_blocks_edge_carried_only_by_three_outliers():
    # 297 small losses plus three enormous winners: positive headline expectancy,
    # but no durable edge once the minimum-three tail stress is applied.
    pnls = [-1.0] * 297 + [200.0, 200.0, 200.0]
    costs = [0.0] * 300
    verdict = evaluate_authoritative_snapback_gate(
        trade_pnls=pnls,
        entry_dates=_dates(300),
        entry_sessions_count=70,
        statutory_costs=costs,
        daily_mtm_equity_series=[1_000_000.0] * 300,
    )

    assert verdict.net_expectancy > 0
    assert verdict.expectancy_without_top_1pct < 0
    assert verdict.checks["positive_without_top_1pct"] is False
    assert verdict.promoted is False


def test_non_finite_economics_fail_closed():
    verdict = evaluate_authoritative_snapback_gate(
        trade_pnls=[100.0] * 299 + [math.nan],
        entry_dates=_dates(300),
        entry_sessions_count=70,
        statutory_costs=[10.0] * 300,
        daily_mtm_equity_series=[1_000_000.0] * 300,
    )
    assert verdict.promoted is False
    assert verdict.checks["trade_pnls_finite"] is False


def test_evaluate_with_verdict_uses_gate_session_count_when_explicit_count_omitted():
    payload = evaluate_with_verdict(
        trade_pnls=[200.0] * 300,
        entry_dates=_dates(300, sessions=70),
        statutory_costs=[10.0] * 300,
        daily_mtm_equity_series=[1_000_000.0 + i for i in range(300)],
    )
    assert payload["total_sessions"] >= 60
    assert payload["verdict"] == "PASSED"


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
