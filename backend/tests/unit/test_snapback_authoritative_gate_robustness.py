"""Predeclared economic robustness gates for the forward Snapback sample."""

from study.snapback_authoritative_gate import evaluate_authoritative_snapback_gate


def _dates(n):
    return [f"session-{i % 70:02d}" for i in range(n)]


def _mtm(n):
    return [1_000_000.0 + i * 10.0 for i in range(n)]


def test_uniform_edge_must_survive_3x_costs_and_top_tail_removal():
    n = 300
    verdict = evaluate_authoritative_snapback_gate(
        trade_pnls=[100.0] * n,
        entry_dates=_dates(n),
        statutory_costs=[10.0] * n,
        daily_mtm_equity_series=_mtm(n),
        entry_sessions_count=70,
    )

    assert verdict.checks["positive_under_3x_costs"] is True
    assert verdict.checks["positive_without_top_1pct"] is True
    assert verdict.expectancy_3x_cost > 0
    assert verdict.expectancy_without_top_1pct > 0
    assert verdict.promoted is True


def test_edge_that_disappears_at_3x_costs_cannot_promote():
    n = 300
    verdict = evaluate_authoritative_snapback_gate(
        trade_pnls=[15.0] * n,
        entry_dates=_dates(n),
        statutory_costs=[10.0] * n,
        daily_mtm_equity_series=_mtm(n),
        entry_sessions_count=70,
    )

    assert verdict.checks["positive_under_2x_costs"] is True
    assert verdict.checks["positive_under_3x_costs"] is False
    assert verdict.promoted is False


def test_top_three_winners_cannot_carry_the_entire_edge():
    n = 300
    pnls = [-10.0] * (n - 3) + [5_000.0, 5_000.0, 5_000.0]
    verdict = evaluate_authoritative_snapback_gate(
        trade_pnls=pnls,
        entry_dates=_dates(n),
        statutory_costs=[1.0] * n,
        daily_mtm_equity_series=_mtm(n),
        entry_sessions_count=70,
        max_allowed_drawdown_pct=100.0,
    )

    assert verdict.net_expectancy > 0
    assert verdict.checks["positive_without_top_1pct"] is False
    assert verdict.expectancy_without_top_1pct < 0
    assert verdict.promoted is False
