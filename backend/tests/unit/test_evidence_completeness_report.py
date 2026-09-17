"""The daily report must never let a broken session read as a clean one.

Unknown coverage is the trap: a session that cannot say how many quotes it
needed has unknown coverage, and unknown must not render or evaluate as 100%.
"""
from __future__ import annotations

import pytest

from app.core.evidence_completeness import (
    COVERAGE_FLOOR,
    SessionStatus,
    build_daily_report,
    build_lane_rows,
    classify_session,
    render_daily_report,
    unattributed_summary,
)
from app.core.lane_registry import LANES


def _session(**overrides):
    row = {
        "session_date": "2026-09-18",
        "scanner_status": "COMPLETE",
        "market_gate_status": "OPEN",
        "universe_expected": 14,
        "universe_scanned": 14,
        "decisions_recorded": 14,
        "symbol_failures": 0,
        "entry_phase_status": "COMPLETE",
        "eod_phase_status": "COMPLETE",
        "evidence_gap_codes_json": "[]",
        "quotes_required": 100,
        "quotes_observed": 100,
    }
    row.update(overrides)
    return row


def _trade(lane_key, **overrides):
    row = {
        "lane_key": lane_key,
        "session_date": "2026-09-18",
        "authoritative": 1,
        "evidence_class": "paper",
        "trade_pnl": 10.0,
    }
    row.update(overrides)
    return row


class TestSessionStatus:
    def test_a_clean_session_is_complete(self):
        assert classify_session(_session()) is SessionStatus.COMPLETE

    def test_a_missing_row_is_a_system_error_not_a_closed_market(self):
        # Nothing recorded and nothing ran are indistinguishable. The safe
        # reading is the one that does not add a session to the denominator.
        assert classify_session(None) is SessionStatus.SYSTEM_ERROR
        assert classify_session({}) is SessionStatus.SYSTEM_ERROR

    def test_a_holiday_is_market_closed(self):
        assert (
            classify_session(_session(market_gate_status="HOLIDAY"))
            is SessionStatus.MARKET_CLOSED
        )

    def test_a_failed_scan_is_a_system_error(self):
        assert (
            classify_session(_session(scanner_status="FAILED"))
            is SessionStatus.SYSTEM_ERROR
        )

    def test_a_gap_code_makes_a_finished_scan_incomplete(self):
        row = _session(evidence_gap_codes_json='["stale_quote_window"]')
        assert classify_session(row) is SessionStatus.INCOMPLETE

    def test_a_partial_universe_is_incomplete(self):
        assert (
            classify_session(_session(universe_scanned=9))
            is SessionStatus.INCOMPLETE
        )


class TestUsability:
    def test_a_clean_day_is_usable(self):
        report = build_daily_report(
            session_date="2026-09-18",
            session_row=_session(),
            release_tag="t",
            runtime_sha="a" * 40,
        )
        assert report.usable_as_evidence

    def test_unknown_quote_coverage_is_not_full_coverage(self):
        # No quote denominator AND no universe denominator: coverage is
        # genuinely unknown, and unknown must not pass the 95% floor.
        report = build_daily_report(
            session_date="2026-09-18",
            session_row=_session(
                quotes_required=0, universe_expected=0, universe_scanned=0
            ),
            release_tag="t",
            runtime_sha="a" * 40,
        )
        assert report.quote_coverage is None
        assert not report.usable_as_evidence
        assert "unknown" in render_daily_report(report)

    def test_thin_coverage_fails_the_floor(self):
        report = build_daily_report(
            session_date="2026-09-18",
            session_row=_session(quotes_observed=80),
            release_tag="t",
            runtime_sha="a" * 40,
        )
        assert report.quote_coverage == pytest.approx(0.8)
        assert report.quote_coverage < COVERAGE_FLOOR
        assert not report.usable_as_evidence

    def test_a_gap_code_survives_into_the_report(self):
        report = build_daily_report(
            session_date="2026-09-18",
            session_row=_session(evidence_gap_codes_json='["broker_disconnect"]'),
            release_tag="t",
            runtime_sha="a" * 40,
        )
        assert report.evidence_gap_codes == ("broker_disconnect",)
        assert not report.usable_as_evidence

    def test_an_unresolved_position_blocks_usability(self):
        report = build_daily_report(
            session_date="2026-09-18",
            session_row=_session(),
            evidence_rows=[
                _trade("snapback:swing", trade_pnl=None, unresolved=True)
            ],
            release_tag="t",
            runtime_sha="a" * 40,
        )
        assert report.positions_unresolved == 1
        assert not report.usable_as_evidence


class TestLaneRows:
    def test_every_lane_appears_even_when_idle(self):
        rows, _ = build_lane_rows([])
        assert {row.lane_key for row in rows} == set(LANES)

    def test_lanes_never_pool(self):
        rows, _ = build_lane_rows(
            [
                *[_trade("snapback:swing") for _ in range(5)],
                *[_trade("snapback:scalping") for _ in range(3)],
            ]
        )
        by_key = {row.lane_key: row for row in rows}
        assert by_key["snapback:swing"].trades_completed == 5
        assert by_key["snapback:scalping"].trades_completed == 3
        assert by_key["supertrend:swing"].trades_completed == 0

    def test_a_modelled_row_is_excluded_but_counted(self):
        rows, _ = build_lane_rows(
            [_trade("snapback:swing", evidence_class="modelled")]
        )
        lane = next(r for r in rows if r.lane_key == "snapback:swing")
        assert lane.trades_completed == 0
        assert lane.non_promotable_rows == 1

    def test_a_non_authoritative_row_is_excluded(self):
        rows, _ = build_lane_rows([_trade("snapback:swing", authoritative=0)])
        lane = next(r for r in rows if r.lane_key == "snapback:swing")
        assert lane.trades_completed == 0
        assert lane.non_promotable_rows == 1

    def test_unattributed_rows_are_reported_not_dropped(self):
        rows = [
            _trade("snapback:swing"),
            {"session_date": "2026-09-18", "trade_pnl": 1.0, "evidence_class": "paper"},
        ]
        lane_rows, unattributed = build_lane_rows(rows)
        assert unattributed == 1
        assert sum(r.trades_completed for r in lane_rows) == 1
        assert unattributed_summary(rows) == {"paper": 1}

    def test_refusals_are_reason_coded(self):
        rows, _ = build_lane_rows(
            [
                _trade("snapback:swing", trade_pnl=None, refusal_reason="stale_quote"),
                _trade("snapback:swing", trade_pnl=None, refusal_reason="stale_quote"),
                _trade(
                    "snapback:swing", trade_pnl=None, refusal_reason="no_listed_contract"
                ),
            ]
        )
        lane = next(r for r in rows if r.lane_key == "snapback:swing")
        assert lane.signals_refused == 3
        assert lane.refusal_reasons == {"stale_quote": 2, "no_listed_contract": 1}

    def test_unknown_lane_coverage_is_not_ok(self):
        rows, _ = build_lane_rows([], quote_coverage={"snapback:swing": None})
        lane = next(r for r in rows if r.lane_key == "snapback:swing")
        assert lane.quote_coverage is None
        assert not lane.coverage_ok


def test_render_shows_all_ten_lanes_and_the_build():
    report = build_daily_report(
        session_date="2026-09-18",
        session_row=_session(),
        evidence_rows=[_trade("snapback:swing")],
        release_tag="handoff-1.0",
        runtime_sha="c" * 40,
    )
    out = render_daily_report(report)

    for lane_key in LANES:
        assert lane_key in out
    assert "handoff-1.0" in out
    assert "cccccccccccc" in out
    assert "Usable as evidence: YES" in out
