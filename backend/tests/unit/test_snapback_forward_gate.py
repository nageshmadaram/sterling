"""Adapter feeding the frozen prospective evidence into the existing authoritative
gate. The adapter measures; it never changes a parameter and never promotes on its
own authority.
"""

from __future__ import annotations

import pytest

from study.snapback_forward_gate import (
    build_gate_inputs,
    evaluate_forward_gate,
)


def _records(n_trades=0, n_sessions=0, pnl=10.0):
    outcomes = []
    positions = []
    for i in range(n_trades):
        session = f"2026-09-{(i % max(n_sessions, 1)) + 1:02d}"
        outcomes.append(
            {
                "opportunity_id": f"OPP-{i}",
                "actual_total_pnl": pnl,
                "actual_option_pnl": pnl,
                "actual_futures_pnl": 0.0,
                "actual_costs": 5.0,
                "entry_ts": f"{session}T09:20:00+05:30",
            }
        )
    return {
        "outcomes": outcomes,
        "paper_positions": positions,
        "daily_mtm": [],
        "option_quotes": [],
    }


def test_empty_sample_is_inconclusive_never_throws():
    verdict = evaluate_forward_gate(records=_records())

    assert verdict["verdict"] == "INCONCLUSIVE"
    assert verdict["completed_trades"] == 0
    assert verdict["total_sessions"] == 0
    assert "missing_requirements" in verdict


def test_small_but_beautiful_sample_stays_inconclusive():
    records = _records(n_trades=20, n_sessions=20, pnl=10000.0)

    verdict = evaluate_forward_gate(records=records)

    assert verdict["verdict"] == "INCONCLUSIVE"
    assert verdict["promoted"] is False
    assert any("300" in m for m in verdict["missing_requirements"])
    assert any("60" in m for m in verdict["missing_requirements"])


def test_inputs_use_observed_values_only():
    records = _records(n_trades=3, n_sessions=3)
    records["paper_positions"] = [
        {"status": "OPEN"},
        {"status": "EXIT_PENDING"},
        {"status": "CLOSED"},
    ]
    records["option_quotes"] = [
        {"bid": 10.0, "ask": 11.0, "is_stale": 0},
        {"bid": 0.0, "ask": 0.0, "is_stale": 0},
    ]
    records["daily_mtm"] = [
        {"session_date": "2026-09-01", "total_mtm": 100.0},
        {"session_date": "2026-09-02", "total_mtm": -50.0},
    ]

    inputs = build_gate_inputs(records=records)

    assert inputs["trade_pnls"] == [10.0, 10.0, 10.0]
    assert inputs["statutory_costs"] == [5.0, 5.0, 5.0]
    assert len(inputs["entry_dates"]) == 3
    assert inputs["unresolved_exposures_count"] == 2
    assert inputs["quote_coverage_pct"] == pytest.approx(50.0)
    # Two sessions of marks become two equity points.
    assert len(inputs["daily_mtm_equity_series"]) == 2


def test_equity_series_is_cumulative_not_per_session_marks():
    records = _records(n_trades=1, n_sessions=1)
    records["daily_mtm"] = [
        {"session_date": "2026-09-01", "total_mtm": 100.0},
        {"session_date": "2026-09-02", "total_mtm": 40.0},
    ]

    inputs = build_gate_inputs(records=records)

    # Two positions marked on the same session must aggregate per session.
    assert inputs["daily_mtm_equity_series"] == [100.0, 40.0]


def test_unresolved_exposures_block_promotion_even_with_enough_trades():
    records = _records(n_trades=400, n_sessions=70, pnl=50.0)
    records["paper_positions"] = [{"status": "EXIT_PENDING"}]

    verdict = evaluate_forward_gate(records=records)

    assert verdict["verdict"] in {"FAILED", "INCONCLUSIVE"}
    assert verdict["promoted"] is False


def test_verdict_is_one_of_exactly_three_values():
    for n in (0, 20, 400):
        verdict = evaluate_forward_gate(records=_records(n_trades=n, n_sessions=min(n, 70)))
        assert verdict["verdict"] in {"PASSED", "FAILED", "INCONCLUSIVE"}


def test_adapter_exposes_no_parameter_authority():
    import study.snapback_forward_gate as mod

    source = open(mod.__file__, encoding="utf-8").read()

    # Reading the gate's own `promoted` flag is fine; writing config is not.
    for forbidden in ("set_config", "SnapbackConfig(", "db.set_", "enabled = True"):
        assert forbidden not in source
