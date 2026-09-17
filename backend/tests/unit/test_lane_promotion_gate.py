"""Per-lane promotion: the no-pooling boundary and the pinned thresholds."""
from __future__ import annotations

import pytest

from app.core.evidence import EvidenceClass
from app.core.lane_promotion import (
    MIN_SESSIONS,
    MIN_TRADES,
    collect_lane_evidence,
    coverage_report,
    evaluate_all_lanes,
    evaluate_lane,
    lane_exists,
)

LANE = "snapback:swing"


def _rows(n: int, *, lane: str = LANE, pnl: float = 400.0, sessions: int | None = None,
          evidence_class: str = EvidenceClass.PAPER.value, authoritative: int = 1):
    """n completed trades, spread over `sessions` distinct entry days."""
    spread = sessions if sessions is not None else n
    return [
        {
            "lane_key": lane,
            "authoritative": authoritative,
            "evidence_class": evidence_class,
            "actual_total_pnl": pnl,
            "actual_costs": 40.0,
            "entry_date": f"2026-{1 + (i % spread) // 28:02d}-{1 + (i % spread) % 28:02d}",
        }
        for i in range(n)
    ]


def _verdict(rows, **kw):
    return evaluate_lane(collect_lane_evidence(rows, LANE), **kw)


# ── the lane boundary ─────────────────────────────────────────────────────


def test_every_lane_gets_its_own_verdict_and_there_is_no_combined_one():
    verdicts = evaluate_all_lanes([])
    assert len(verdicts) == 10
    assert "snapback" not in verdicts
    assert "overall" not in verdicts


def test_other_lanes_cannot_make_up_the_trade_count():
    """Ultra + Scalping + Intraday must never add up to 300."""
    rows = (
        _rows(150, lane="snapback:scalping")
        + _rows(150, lane="snapback:intraday")
        + _rows(10, lane=LANE)
    )
    evidence = collect_lane_evidence(rows, LANE)
    assert evidence.eligible == 10
    assert evidence.excluded_other_lane == 300
    assert _verdict(rows)["verdict"] != "PASSED"


def test_the_other_strategy_cannot_satisfy_this_lane():
    rows = _rows(400, lane="supertrend:swing") + _rows(5, lane=LANE)
    assert collect_lane_evidence(rows, LANE).eligible == 5


def test_legacy_rows_with_no_lane_are_excluded_and_counted():
    rows = _rows(500, lane="") + _rows(3)
    evidence = collect_lane_evidence(rows, LANE)
    assert evidence.eligible == 3
    assert evidence.excluded_unattributed == 500
    # Reported, not dropped: "3 of 503" is a different answer from "3".
    assert evidence.as_dict()["considered_rows"] == 503


def test_non_authoritative_and_modelled_rows_are_excluded_separately():
    rows = (
        _rows(7, authoritative=0)
        + _rows(11, evidence_class=EvidenceClass.MODELLED.value)
        + _rows(2)
    )
    evidence = collect_lane_evidence(rows, LANE)
    assert evidence.eligible == 2
    assert evidence.excluded_not_authoritative == 7
    assert evidence.excluded_class == 11


def test_broker_and_shadow_rows_do_count():
    for cls in (EvidenceClass.BROKER, EvidenceClass.SHADOW):
        assert collect_lane_evidence(_rows(4, evidence_class=cls.value), LANE).eligible == 4


def test_coverage_report_names_unattributed_and_foreign_rows():
    rows = _rows(3) + _rows(2, lane="") + _rows(1, lane="gamma_move:swing")
    report = coverage_report(rows)
    assert report["total_rows"] == 6
    assert report["by_lane"] == {LANE: 3}
    assert report["unattributed_rows"] == 2
    assert report["unknown_lane_rows"] == {"gamma_move:swing": 1}


def test_lane_exists_rejects_a_strategy_outside_the_two():
    assert lane_exists(LANE) is True
    assert lane_exists("gamma_move:swing") is False
    assert lane_exists("snapback:positional") is False


# ── the pinned thresholds ─────────────────────────────────────────────────


def test_the_declared_minimum_sample():
    assert (MIN_SESSIONS, MIN_TRADES) == (60, 300)


def test_299_trades_is_inconclusive_not_a_pass():
    rows = _rows(299, sessions=70)
    verdict = _verdict(rows, daily_mtm_equity_series=[1_000_000.0, 1_050_000.0])
    assert verdict["verdict"] == "INCONCLUSIVE"
    assert verdict["checks"]["completed_trades_ge_300"] is False


def test_59_sessions_is_inconclusive_not_a_pass():
    rows = _rows(400, sessions=59)
    verdict = _verdict(rows, daily_mtm_equity_series=[1_000_000.0, 1_050_000.0])
    assert verdict["verdict"] == "INCONCLUSIVE"
    assert verdict["checks"]["independent_sessions_ge_60"] is False


def test_a_sufficient_profitable_sample_can_pass():
    """Otherwise every failure test above would be vacuous."""
    rows = _rows(400, sessions=80, pnl=400.0)
    verdict = _verdict(
        rows, daily_mtm_equity_series=[1_000_000.0 + i * 500 for i in range(80)]
    )
    assert verdict["checks"]["completed_trades_ge_300"] is True
    assert verdict["checks"]["independent_sessions_ge_60"] is True
    assert verdict["verdict"] == "PASSED"


def test_a_losing_sample_of_the_same_size_fails():
    rows = _rows(400, sessions=80, pnl=-400.0)
    verdict = _verdict(
        rows, daily_mtm_equity_series=[1_000_000.0 - i * 500 for i in range(80)]
    )
    assert verdict["verdict"] == "FAILED"


def test_unresolved_exposure_blocks_a_pass():
    rows = _rows(400, sessions=80)
    verdict = _verdict(
        rows,
        daily_mtm_equity_series=[1_000_000.0 + i * 500 for i in range(80)],
        unresolved_exposures_count=1,
    )
    assert verdict["verdict"] != "PASSED"


def test_coverage_below_95_percent_blocks_a_pass():
    rows = _rows(400, sessions=80)
    verdict = _verdict(
        rows,
        daily_mtm_equity_series=[1_000_000.0 + i * 500 for i in range(80)],
        quote_coverage_pct=94.9,
    )
    assert verdict["verdict"] != "PASSED"


def test_no_caller_can_lower_the_300_trade_floor_into_a_pass():
    """The floor lives inside the gate, not in the caller's arguments.

    min_sessions/min_trades only choose how a non-pass is *worded* —
    INCONCLUSIVE when the sample is short, FAILED when it is long enough to
    judge. Neither can turn a 100-trade sample into PASSED, however profitable
    it looks.
    """
    rows = _rows(100, sessions=30)
    mtm = [1_000_000.0 + i * 500 for i in range(30)]

    default = _verdict(rows, daily_mtm_equity_series=mtm)
    assert default["verdict"] == "INCONCLUSIVE"

    lowered = _verdict(
        rows, daily_mtm_equity_series=mtm, min_sessions=30, min_trades=100
    )
    assert lowered["verdict"] == "FAILED"
    assert lowered["promoted"] is False
    assert lowered["checks"]["completed_trades_ge_300"] is False
    assert lowered["checks"]["independent_sessions_ge_60"] is False


@pytest.mark.parametrize("lane_key", sorted(evaluate_all_lanes([])))
def test_no_lane_can_pass_on_an_empty_store(lane_key):
    assert evaluate_all_lanes([])[lane_key]["verdict"] == "INCONCLUSIVE"
