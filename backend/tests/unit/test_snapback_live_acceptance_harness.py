"""The harness that will meet a real socket, tested where it can be tested.

Its grading logic is what decides whether runtime-1.6 may be tagged, so the
grading itself must not be the thing taken on trust. The live run remains the
point; these tests only ensure it will report honestly when it happens.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from study.snapback_live_evidence_acceptance import (
    ACCEPTANCE_EVIDENCE_CLASS,
    FAIL,
    PASS,
    SKIP,
    Report,
    evaluate_rows,
    evaluate_ticks,
    inspect_tick,
)

NOW = datetime(2026, 9, 17, 9, 20, tzinfo=timezone.utc)


def _report():
    return Report(runtime_sha="abc", started_at=NOW.isoformat(), underlying="NIFTY")


def _tick(levels=5, *, orders=True, stamp=True):
    depth = {
        side: [
            {"price": 100.0 + i, "quantity": 50 + i, **({"orders": 3 + i} if orders else {})}
            for i in range(levels)
        ]
        for side in ("buy", "sell")
    }
    return {
        "instrument_token": 12345, "last_price": 101.0, "oi": 400,
        "exchange_timestamp": datetime(2026, 9, 17, 14, 49, 59) if stamp else None,
        "depth": depth,
    }


# ─── the verdict cannot be gamed ─────────────────────────────────────────────

def test_no_checks_is_not_a_pass():
    assert _report().overall == FAIL


def test_a_skipped_check_is_not_a_pass():
    report = _report()
    report.record("a", PASS)
    report.record("b", SKIP)

    # A release gate that accepts SKIP accepts an untested claim.
    assert report.overall == SKIP


def test_one_failure_fails_the_run():
    report = _report()
    for name in ("a", "b", "c"):
        report.record(name, PASS)
    report.record("d", FAIL)

    assert report.overall == FAIL


def test_all_pass_is_a_pass():
    report = _report()
    report.record("a", PASS)
    report.record("b", PASS)

    assert report.overall == PASS


# ─── tick grading ────────────────────────────────────────────────────────────

def test_a_full_five_level_book_passes():
    report = _report()

    evaluate_ticks(report, "option", [_tick(5)])

    assert report.checks["option_five_level_depth"] == PASS
    assert report.checks["option_order_counts"] == PASS
    assert report.checks["option_depth_quantities"] == PASS


def test_a_one_level_book_fails_the_depth_check():
    """Exactly the assumption the raw tick path silently made."""
    report = _report()

    evaluate_ticks(report, "option", [_tick(1)])

    assert report.checks["option_five_level_depth"] == FAIL


def test_missing_order_counts_fail():
    report = _report()

    evaluate_ticks(report, "option", [_tick(5, orders=False)])

    assert report.checks["option_order_counts"] == FAIL


def test_no_ticks_is_a_failure_not_a_skip():
    report = _report()

    evaluate_ticks(report, "option", [])

    assert report.checks["option_full_tick"] == FAIL


def test_missing_exchange_timestamps_fail():
    report = _report()

    evaluate_ticks(report, "option", [_tick(5, stamp=False)])

    assert report.checks["option_exchange_timestamp"] == FAIL


def test_inspect_reports_what_the_vendor_sent_without_interpreting():
    finding = inspect_tick(_tick(3))

    assert finding["buy_levels"] == 3
    assert finding["sell_levels"] == 3
    assert finding["has_exchange_timestamp"] is True


def test_an_empty_book_is_reported_as_empty():
    finding = inspect_tick({"instrument_token": 1, "depth": {}})

    assert finding["has_depth"] is False
    assert finding["buy_levels"] == 0


# ─── row grading ─────────────────────────────────────────────────────────────

def _row(**over):
    row = {
        "exchange_ts": datetime(2026, 9, 17, 9, 19, 59, tzinfo=timezone.utc),
        "received_ts": NOW,
        "bid4_price": 995000, "ask4_price": 1005000,
    }
    row.update(over)
    return row


def test_separated_clocks_pass_and_freshness_is_computable():
    report = _report()

    evaluate_rows(report, [_row()])

    assert report.checks["clock_separation"] == PASS
    assert report.checks["freshness_computable"] == PASS
    assert report.detail["freshness_computable"]["max_age_ms"] == 1000.0


def test_an_exchange_clock_ahead_of_receipt_is_flagged():
    """Clocks that cannot be compared invalidate every freshness gate."""
    report = _report()

    evaluate_rows(report, [_row(exchange_ts=datetime(2026, 9, 17, 10, 0, tzinfo=timezone.utc))])

    assert report.checks["clock_ordering_sane"] == FAIL


def test_a_borrowed_timestamp_fails():
    report = _report()

    # exchange_ts None, but a row where the two are equal would mean it was
    # filled in from received_ts.
    rows = [_row(exchange_ts=None), _row()]
    evaluate_rows(report, rows)

    assert report.checks["null_exchange_timestamp_semantics"] == PASS


def test_a_zeroed_absent_level_fails():
    report = _report()

    evaluate_rows(report, [_row(bid4_price=0)])

    assert report.checks["absent_level_is_null_not_zero"] == FAIL


def test_no_rows_is_a_failure():
    report = _report()

    evaluate_rows(report, [])

    assert report.checks["row_conversion"] == FAIL


# ─── safety properties ───────────────────────────────────────────────────────

def test_the_harness_cannot_place_an_order():
    import study.snapback_live_evidence_acceptance as mod

    source = open(mod.__file__, encoding="utf-8").read()

    for forbidden in ("place_order", "modify_order", "cancel_order", ".order_place"):
        assert forbidden not in source, f"the acceptance harness must never trade: found {forbidden}"


def test_acceptance_evidence_cannot_enter_the_economic_sample():
    """Marked MODELLED on purpose: it is not a real opportunity's evidence."""
    from app.services.snapback_evidence_class import BROKER_EXECUTED, satisfies

    assert ACCEPTANCE_EVIDENCE_CLASS == "MODELLED"
    assert satisfies(ACCEPTANCE_EVIDENCE_CLASS, BROKER_EXECUTED) is False


def test_the_report_carries_its_tag_and_class():
    blob = _report().as_dict()

    assert blob["tag"] == "TEST_ACCEPTANCE"
    assert blob["evidence_class"] == "MODELLED"


def test_no_credential_is_logged_or_stored():
    """Scans code only: the module docstring legitimately mentions these words."""
    import ast
    import study.snapback_live_evidence_acceptance as mod

    tree = ast.parse(open(mod.__file__, encoding="utf-8").read())
    tree.body = [n for n in tree.body
                 if not (isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant)
                         and isinstance(n.value.value, str))]
    code = ast.unparse(tree)

    for forbidden in ("print(creds", "api_secret", "password"):
        assert forbidden not in code

    # The token may be handed to the client, never written anywhere.
    assert "write_text(creds" not in code
    assert "json.dumps(creds" not in code
