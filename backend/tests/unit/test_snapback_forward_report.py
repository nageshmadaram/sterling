from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from study.snapback_forward_report import (
    build_forward_summary,
    write_forward_report,
)


RUNTIME_SHA = "9e989dd910995deb5e77385b983e5992c58883c0"
MANIFEST_HASH = "602d28f804e840d046e7b51d020d5718dfd38a08d27d5324ec9d81bfbc4e53e4"


def _empty_records():
    return {
        "opportunities": [],
        "contract_candidates": [],
        "option_quotes": [],
        "futures_quotes": [],
        "decisions": [],
        "paper_fills": [],
        "hedge_rebalances": [],
        "daily_mtm": [],
        "margin_snapshots": [],
        "costs": [],
        "outcomes": [],
        "paper_positions": [],
    }


def test_empty_sample_is_inconclusive_not_zero_performance():
    summary = build_forward_summary(
        records=_empty_records(),
        runtime_sha=RUNTIME_SHA,
        strategy_manifest=MANIFEST_HASH,
    )

    assert summary["evidence_status"] == "INCONCLUSIVE"
    assert summary["completed_trades"] == 0

    # Missing economics are UNKNOWN/null — never fake zero performance.
    assert summary["net_pnl"] is None
    assert summary["mean_net_pnl_per_trade"] is None
    assert summary["win_rate"] is None
    assert summary["profit_factor"] is None
    assert summary["average_win"] is None
    assert summary["average_loss"] is None

    assert summary["runtime_sha"] == RUNTIME_SHA
    assert summary["strategy_manifest"] == MANIFEST_HASH


def test_status_counts_are_reported_exactly():
    records = _empty_records()

    records["opportunities"] = [
        {"status": "PENDING_ENTRY"},
        {"status": "PENDING_ENTRY"},
        {"status": "PROCESSING_ENTRY"},
        {"status": "NO_FILL"},
        {"status": "INCONCLUSIVE"},
        {"status": "CALENDAR_ERROR"},
        {"status": "OPEN_POSITION"},
        {"status": "CLOSED"},
    ]

    records["paper_positions"] = [
        {"status": "OPEN"},
        {"status": "OPEN"},
        {"status": "EXIT_PENDING"},
        {"status": "CLOSED"},
    ]

    summary = build_forward_summary(
        records=records,
        runtime_sha=RUNTIME_SHA,
        strategy_manifest=MANIFEST_HASH,
    )

    assert summary["opportunity_status_counts"]["PENDING_ENTRY"] == 2
    assert summary["opportunity_status_counts"]["PROCESSING_ENTRY"] == 1
    assert summary["opportunity_status_counts"]["NO_FILL"] == 1
    assert summary["opportunity_status_counts"]["INCONCLUSIVE"] == 1
    assert summary["opportunity_status_counts"]["CALENDAR_ERROR"] == 1

    assert summary["open_positions"] == 2
    assert summary["exit_pending"] == 1

    # OPEN + EXIT_PENDING are unresolved exposure.
    assert summary["unresolved_exposures"] == 3


def test_observed_pnl_uses_actual_not_modeled_values():
    records = _empty_records()

    records["outcomes"] = [
        {
            "actual_total_pnl": 100.0,
            "modeled_total_pnl": 900.0,
            "actual_option_pnl": 150.0,
            "actual_futures_pnl": -30.0,
            "actual_costs": 20.0,
            "observed_vs_model_error": -800.0,
        },
        {
            "actual_total_pnl": -40.0,
            "modeled_total_pnl": 500.0,
            "actual_option_pnl": -10.0,
            "actual_futures_pnl": -20.0,
            "actual_costs": 10.0,
            "observed_vs_model_error": -540.0,
        },
        {
            "actual_total_pnl": 60.0,
            "modeled_total_pnl": 600.0,
            "actual_option_pnl": 100.0,
            "actual_futures_pnl": -20.0,
            "actual_costs": 20.0,
            "observed_vs_model_error": -540.0,
        },
    ]

    summary = build_forward_summary(
        records=records,
        runtime_sha=RUNTIME_SHA,
        strategy_manifest=MANIFEST_HASH,
    )

    assert summary["completed_trades"] == 3

    # 100 - 40 + 60
    assert summary["net_pnl"] == pytest.approx(120.0)

    assert summary["mean_net_pnl_per_trade"] == pytest.approx(40.0)

    # 2 wins / 3 completed trades
    assert summary["win_rate"] == pytest.approx(2 / 3)

    assert summary["average_win"] == pytest.approx(80.0)
    assert summary["average_loss"] == pytest.approx(-40.0)

    # gross winners 160 / gross losses 40
    assert summary["profit_factor"] == pytest.approx(4.0)

    # Must remain clearly separate.
    assert summary["modeled_total_pnl"] == pytest.approx(2000.0)
    assert summary["net_pnl"] != summary["modeled_total_pnl"]


def test_option_and_hedge_pnl_are_reported_separately():
    records = _empty_records()

    records["outcomes"] = [
        {
            "actual_total_pnl": 70.0,
            "modeled_total_pnl": 90.0,
            "actual_option_pnl": 120.0,
            "actual_futures_pnl": -30.0,
            "actual_costs": 20.0,
            "observed_vs_model_error": -20.0,
        }
    ]

    summary = build_forward_summary(
        records=records,
        runtime_sha=RUNTIME_SHA,
        strategy_manifest=MANIFEST_HASH,
    )

    assert summary["gross_option_pnl"] == pytest.approx(120.0)
    assert summary["futures_hedge_pnl"] == pytest.approx(-30.0)
    assert summary["closed_trade_costs"] == pytest.approx(20.0)
    assert summary["net_pnl"] == pytest.approx(70.0)


def test_quote_coverage_and_stale_rejections_are_visible():
    records = _empty_records()

    records["option_quotes"] = [
        {
            "bid": 100.0,
            "ask": 102.0,
            "is_stale": 0,
        },
        {
            "bid": 50.0,
            "ask": 52.0,
            "is_stale": 0,
        },
        {
            "bid": 40.0,
            "ask": 45.0,
            "is_stale": 1,
        },
        {
            "bid": 0.0,
            "ask": 0.0,
            "is_stale": 0,
        },
    ]

    summary = build_forward_summary(
        records=records,
        runtime_sha=RUNTIME_SHA,
        strategy_manifest=MANIFEST_HASH,
    )

    assert summary["option_quote_observations"] == 4
    assert summary["stale_option_quotes"] == 1
    assert summary["invalid_option_quotes"] == 1

    # Only two fresh, executable bid/ask quotes.
    assert summary["usable_option_quotes"] == 2
    assert summary["option_quote_coverage_pct"] == pytest.approx(50.0)


def test_average_spread_is_computed_from_executable_quotes_only():
    records = _empty_records()

    records["option_quotes"] = [
        {"bid": 100.0, "ask": 102.0, "is_stale": 0},
        {"bid": 50.0, "ask": 51.0, "is_stale": 0},
        # Must be ignored.
        {"bid": 0.0, "ask": 100.0, "is_stale": 0},
        {"bid": 100.0, "ask": 99.0, "is_stale": 0},
        {"bid": 10.0, "ask": 20.0, "is_stale": 1},
    ]

    summary = build_forward_summary(
        records=records,
        runtime_sha=RUNTIME_SHA,
        strategy_manifest=MANIFEST_HASH,
    )

    # spread percentage = (ask-bid)/mid * 100
    expected_a = 2.0 / 101.0 * 100.0
    expected_b = 1.0 / 50.5 * 100.0

    assert summary["average_option_spread_pct"] == pytest.approx(
        (expected_a + expected_b) / 2.0
    )


def test_fill_slippage_and_hedge_turnover_are_visible():
    records = _empty_records()

    records["paper_fills"] = [
        {"slippage": 1.25},
        {"slippage": 0.75},
    ]

    records["hedge_rebalances"] = [
        {"prior_hedge_lots": 0, "new_hedge_lots": -2},
        {"prior_hedge_lots": -2, "new_hedge_lots": -1},
        {"prior_hedge_lots": -1, "new_hedge_lots": -3},
    ]

    summary = build_forward_summary(
        records=records,
        runtime_sha=RUNTIME_SHA,
        strategy_manifest=MANIFEST_HASH,
    )

    assert summary["average_fill_slippage"] == pytest.approx(1.0)

    # |0--2| + |-2--1| + |-1--3| = 2 + 1 + 2
    assert summary["hedge_turnover_lots"] == 5


def test_model_optimism_is_reported_not_hidden():
    records = _empty_records()

    records["outcomes"] = [
        {
            "actual_total_pnl": 100.0,
            "modeled_total_pnl": 150.0,
            "actual_option_pnl": 120.0,
            "actual_futures_pnl": 0.0,
            "actual_costs": 20.0,
            "observed_vs_model_error": -50.0,
        },
        {
            "actual_total_pnl": -20.0,
            "modeled_total_pnl": 80.0,
            "actual_option_pnl": 0.0,
            "actual_futures_pnl": 0.0,
            "actual_costs": 20.0,
            "observed_vs_model_error": -100.0,
        },
    ]

    summary = build_forward_summary(
        records=records,
        runtime_sha=RUNTIME_SHA,
        strategy_manifest=MANIFEST_HASH,
    )

    assert summary["net_pnl"] == pytest.approx(80.0)
    assert summary["modeled_total_pnl"] == pytest.approx(230.0)

    # Positive number = model more optimistic than observed.
    assert summary["model_optimism"] == pytest.approx(150.0)


def test_tail_concentration_is_visible():
    records = _empty_records()

    records["outcomes"] = [
        {
            "actual_total_pnl": x,
            "modeled_total_pnl": x,
            "actual_option_pnl": x,
            "actual_futures_pnl": 0.0,
            "actual_costs": 0.0,
            "observed_vs_model_error": 0.0,
        }
        for x in [1000.0, 100.0, 90.0, 80.0, -50.0]
    ]

    summary = build_forward_summary(
        records=records,
        runtime_sha=RUNTIME_SHA,
        strategy_manifest=MANIFEST_HASH,
    )

    assert summary["top_1pct_positive_pnl_share"] is not None

    # Tiny samples use at least one trade.
    positive_total = 1000 + 100 + 90 + 80
    assert summary["top_1pct_positive_pnl_share"] == pytest.approx(
        1000 / positive_total
    )


def test_report_writer_produces_required_artifacts(tmp_path):
    records = _empty_records()
    records["outcomes"] = [
        {
            "opportunity_id": "OPP-1",
            "actual_total_pnl": 10.0,
            "modeled_total_pnl": 12.0,
            "actual_option_pnl": 15.0,
            "actual_futures_pnl": -2.0,
            "actual_costs": 3.0,
            "observed_vs_model_error": -2.0,
        }
    ]

    summary = build_forward_summary(
        records=records,
        runtime_sha=RUNTIME_SHA,
        strategy_manifest=MANIFEST_HASH,
    )

    artifacts = write_forward_report(
        summary=summary,
        records=records,
        output_root=tmp_path,
        report_date=date(2026, 9, 16),
    )

    assert artifacts.run_manifest.exists()
    assert artifacts.evidence_summary.exists()
    assert artifacts.trades_csv.exists()
    assert artifacts.daily_mtm_csv.exists()
    assert artifacts.rejection_summary_csv.exists()
    assert artifacts.validation_report.exists()

    saved_summary = json.loads(
        artifacts.evidence_summary.read_text(encoding="utf-8")
    )

    assert saved_summary["runtime_sha"] == RUNTIME_SHA
    assert saved_summary["net_pnl"] == pytest.approx(10.0)

    report = artifacts.validation_report.read_text(encoding="utf-8")

    assert RUNTIME_SHA in report
    assert "INCONCLUSIVE" in report


def test_daily_report_never_declares_live_eligibility():
    records = _empty_records()

    # Even absurdly good small-sample economics cannot become a promotion.
    records["outcomes"] = [
        {
            "actual_total_pnl": 10000.0,
            "modeled_total_pnl": 10000.0,
            "actual_option_pnl": 10000.0,
            "actual_futures_pnl": 0.0,
            "actual_costs": 0.0,
            "observed_vs_model_error": 0.0,
        }
        for _ in range(10)
    ]

    summary = build_forward_summary(
        records=records,
        runtime_sha=RUNTIME_SHA,
        strategy_manifest=MANIFEST_HASH,
    )

    assert "live_eligible" not in summary
    assert summary["evidence_status"] == "INCONCLUSIVE"
