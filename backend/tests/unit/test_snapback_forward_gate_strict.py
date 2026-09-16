"""Missing evidence is never zero evidence.

A table read failure, a malformed outcome, a missing entry date or a missing actual
P&L/cost must make the verdict INCONCLUSIVE with data_quality_ok false — never a
valid-looking zero, and never a counted completed trade.
"""

from __future__ import annotations

import pytest

from study.snapback_forward_gate import evaluate_forward_gate

# Authority is now explicit at the writer, so fixtures must declare it too: a row
# with no `source` is deliberately not evidence any more.
_AUTH_FIXTURE = {"source": "PROSPECTIVE_PAPER", "authoritative": 1}


def _auth(row: dict, build: str = "") -> dict:
    """Stamp a fixture row with a complete, self-consistent authority."""
    out = dict(_AUTH_FIXTURE)
    if build:
        out["runtime_build_sha"] = build
    out.update(row)
    return out




def _records(outcomes=None, **over):
    base = {
        "outcomes": outcomes or [],
        "paper_positions": [],
        "daily_mtm": [],
        "option_quotes": [],
        "quote_quality_events": [
            {"required_for_economics": 1, "accepted": 1, **_AUTH_FIXTURE} for _ in range(20)
        ],
    }
    base.update(over)
    return base


def _good(i=0, pnl=10.0):
    return {
        "opportunity_id": f"OPP-{i}",
        "actual_total_pnl": pnl,
        "actual_option_pnl": pnl,
        "actual_futures_pnl": 0.0,
        "actual_costs": 5.0,
        "entry_ts": f"2026-09-{(i % 28) + 1:02d}T09:20:00+05:30",
        "authoritative": 1, "source": "PROSPECTIVE_PAPER",
    }


def test_clean_small_sample_is_inconclusive_but_data_quality_is_ok():
    verdict = evaluate_forward_gate(records=_records([_good(i) for i in range(5)]))

    assert verdict["verdict"] == "INCONCLUSIVE"
    assert verdict["data_quality_ok"] is True
    assert verdict["data_quality_errors"] == []


def test_table_read_failure_is_not_an_empty_sample():
    verdict = evaluate_forward_gate(
        records=_records([_good(0)]),
        load_errors=["outcomes: database disk image is malformed"],
    )

    assert verdict["verdict"] == "INCONCLUSIVE"
    assert verdict["data_quality_ok"] is False
    assert any("outcomes" in e for e in verdict["data_quality_errors"])


def test_missing_actual_total_pnl_is_not_zero():
    bad = _good(0)
    bad["actual_total_pnl"] = None

    verdict = evaluate_forward_gate(records=_records([bad]))

    assert verdict["data_quality_ok"] is False
    assert verdict["verdict"] == "INCONCLUSIVE"
    # The malformed row must not be counted as a completed trade.
    assert verdict["completed_trades"] == 0


def test_missing_costs_is_not_zero():
    bad = _good(0)
    del bad["actual_costs"]

    verdict = evaluate_forward_gate(records=_records([bad]))

    assert verdict["data_quality_ok"] is False
    assert verdict["completed_trades"] == 0
    assert any("actual_costs" in e for e in verdict["data_quality_errors"])


def test_missing_entry_date_is_rejected():
    bad = _good(0)
    bad["entry_ts"] = ""

    verdict = evaluate_forward_gate(records=_records([bad]))

    assert verdict["data_quality_ok"] is False
    assert verdict["completed_trades"] == 0


def test_non_numeric_pnl_is_rejected_not_coerced():
    bad = _good(0)
    bad["actual_total_pnl"] = "n/a"

    verdict = evaluate_forward_gate(records=_records([bad]))

    assert verdict["data_quality_ok"] is False
    assert verdict["completed_trades"] == 0


def test_one_malformed_row_taints_the_whole_verdict():
    rows = [_good(i) for i in range(4)]
    rows[2]["actual_option_pnl"] = None

    verdict = evaluate_forward_gate(records=_records(rows))

    assert verdict["data_quality_ok"] is False
    assert verdict["verdict"] == "INCONCLUSIVE"
    # The clean rows are still counted, the malformed one is not.
    assert verdict["completed_trades"] == 3


def test_load_forward_records_reports_errors_rather_than_hiding_them():
    from study.snapback_forward_report import load_forward_records

    class BrokenWarehouse:
        def get_records_by_table(self, table, **kw):
            if table == "outcomes":
                raise RuntimeError("database disk image is malformed")
            return []

    records, errors = load_forward_records(BrokenWarehouse(), strict=True)

    assert records["outcomes"] == []
    assert any("outcomes" in e for e in errors)


def test_report_summary_marks_data_quality(tmp_path):
    from study.snapback_forward_report import build_forward_summary

    summary = build_forward_summary(
        records=_records([_good(0)]),
        runtime_sha="sha",
        strategy_manifest="m",
        load_errors=["outcomes: read failed"],
    )

    assert summary["data_quality_ok"] is False
    assert summary["evidence_status"] == "INCONCLUSIVE"
    assert any("outcomes" in e for e in summary["data_quality_errors"])
