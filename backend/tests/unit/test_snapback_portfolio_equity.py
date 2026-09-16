"""Drawdown must be measured on a real portfolio equity curve.

Summing daily_mtm.total_mtm by date loses every closed trade: a position's realized
P&L disappears from later daily rows, so a book that made money and closed looks like
it fell back to zero. The capital base matters just as much — the same rupee drawdown
is 10% of Rs 1,00,000 and 1% of Rs 10,00,000.
"""

from __future__ import annotations

import pytest

from app.services.snapback_portfolio import (
    build_equity_curve,
    max_drawdown_pct,
)


def test_closed_trade_pnl_carries_forward():
    curve = build_equity_curve(
        daily_mtm=[
            {"session_date": "2026-09-17", "opportunity_id": "A", "total_mtm": 500.0},
            {"session_date": "2026-09-18", "opportunity_id": "A", "total_mtm": 800.0},
        ],
        outcomes=[
            {"opportunity_id": "A", "exit_ts": "2026-09-18T15:20:00+05:30",
             "actual_total_pnl": 800.0, "actual_costs": 50.0},
        ],
        costs=[],
    )

    # Day 2 closes the trade: realized 800 stays in equity on the following day.
    by_date = {row["session_date"]: row for row in curve}
    assert by_date["2026-09-18"]["realized_net_pnl_to_date"] == pytest.approx(800.0)
    assert by_date["2026-09-18"]["equity_pnl"] == pytest.approx(800.0)


def test_open_and_realized_are_additive_not_double_counted():
    curve = build_equity_curve(
        daily_mtm=[
            {"session_date": "2026-09-17", "opportunity_id": "A", "total_mtm": 500.0},
            {"session_date": "2026-09-18", "opportunity_id": "A", "total_mtm": 800.0},
            {"session_date": "2026-09-18", "opportunity_id": "B", "total_mtm": -200.0},
        ],
        outcomes=[
            {"opportunity_id": "A", "exit_ts": "2026-09-18T15:20:00+05:30",
             "actual_total_pnl": 800.0, "actual_costs": 0.0},
        ],
        costs=[],
    )

    day2 = [r for r in curve if r["session_date"] == "2026-09-18"][0]

    # A is realized (800) and B is still open (-200). A must not be counted twice.
    assert day2["realized_net_pnl_to_date"] == pytest.approx(800.0)
    assert day2["open_liquidation_mtm"] == pytest.approx(-200.0)
    assert day2["equity_pnl"] == pytest.approx(600.0)


def test_equity_curve_is_ordered_by_session():
    curve = build_equity_curve(
        daily_mtm=[
            {"session_date": "2026-09-18", "opportunity_id": "A", "total_mtm": 10.0},
            {"session_date": "2026-09-17", "opportunity_id": "A", "total_mtm": 5.0},
        ],
        outcomes=[],
        costs=[],
    )

    assert [r["session_date"] for r in curve] == ["2026-09-17", "2026-09-18"]


def test_drawdown_uses_the_peak_of_the_real_curve():
    curve = [
        {"session_date": "d1", "equity_pnl": 0.0},
        {"session_date": "d2", "equity_pnl": 10_000.0},
        {"session_date": "d3", "equity_pnl": 2_000.0},
    ]

    # Peak 10,000 -> trough 2,000 is an 8,000 drawdown.
    assert max_drawdown_pct(curve, allocation_capital=100_000.0) == pytest.approx(8.0)


def test_same_drawdown_against_the_wrong_capital_is_understated():
    curve = [
        {"session_date": "d1", "equity_pnl": 0.0},
        {"session_date": "d2", "equity_pnl": -10_000.0},
    ]

    assert max_drawdown_pct(curve, allocation_capital=100_000.0) == pytest.approx(10.0)
    assert max_drawdown_pct(curve, allocation_capital=1_000_000.0) == pytest.approx(1.0)


def test_zero_capital_is_unknown_not_infinite():
    curve = [{"session_date": "d1", "equity_pnl": -5_000.0}]

    assert max_drawdown_pct(curve, allocation_capital=0.0) is None


def test_gate_capital_comes_from_the_frozen_config_not_a_default():
    from study.snapback_forward_gate import evaluation_capital

    capital, errors = evaluation_capital()

    assert errors == []
    # The frozen config carries Rs 1,00,000, not the old Rs 10,00,000 default.
    assert capital == pytest.approx(100_000.0)


def test_gate_reports_the_capital_it_used():
    from study.snapback_forward_gate import evaluate_forward_gate

    verdict = evaluate_forward_gate(
        records={"outcomes": [], "paper_positions": [], "daily_mtm": [],
                 "option_quotes": [], "quote_quality_events": []}
    )

    assert verdict["allocation_capital_budget"] == pytest.approx(100_000.0)
