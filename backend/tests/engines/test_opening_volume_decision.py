from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone

from app.engines.option_contracts import Bar
from app.engines.opening_volume_decision import build_opening_decision
from app.engines.opening_volume_leaders import ValidationState, evaluate_leader

IST = timezone(timedelta(hours=5, minutes=30))


def _signal():
    session = date(2026, 9, 3)
    rows = [
        Bar(
            datetime.combine(session - timedelta(days=i), time(9, 15), tzinfo=IST),
            100,
            102,
            99,
            101,
            100,
        )
        for i in range(10, 0, -1)
    ]
    rows.extend(
        [
            Bar(datetime(2026, 9, 3, 9, 15, tzinfo=IST), 100, 102, 99.5, 101.8, 1000),
            Bar(datetime(2026, 9, 3, 9, 16, tzinfo=IST), 101.8, 102.2, 101.7, 102.1, 600),
        ]
    )
    return evaluate_leader(
        "TEST",
        rows,
        as_of=datetime(2026, 9, 3, 9, 17, tzinfo=IST),
        average_turnover_inr=25_000_000,
    )


def test_unknown_evidence_never_increases_the_executable_score():
    decision = build_opening_decision(
        _signal(),
        breadth_alignment="aligned",
        market_context={},
    )

    assert decision["score"]["lower_bound"] < decision["score"]["upper_bound"]
    assert decision["score"]["coverage_pct"] < 100
    assert decision["conviction"]["factors"]["sector"] is None
    assert decision["execution_eligible"] is False
    assert decision["provenance"].startswith("Sterling-owned")


def test_box_y_and_sterling_combo_require_all_fail_closed_gates():
    signal = replace(
        _signal(),
        vwap_aligned=True,
        pdh_pdl_break_aligned=True,
        rsi_14_1m=60.0,
        hold_5m_status=ValidationState.PASS,
        stop_too_wide=False,
        third_day_repeat=False,
    )
    decision = build_opening_decision(
        signal,
        breadth_alignment="aligned",
        market_context={"trend_50dma_aligned": True},
        sector_alignment=True,
    )

    assert decision["conviction"]["passed"] == 7
    assert decision["momentum"]["box_x"] is True
    assert decision["momentum"]["box_y"] is True
    assert decision["sterling_combo"] is True
    assert decision["execution_eligible"] is True


def test_counter_breadth_blocks_momentum_even_with_strong_evidence():
    signal = replace(
        _signal(),
        vwap_aligned=True,
        pdh_pdl_break_aligned=True,
        rsi_14_1m=60.0,
        hold_5m_status=ValidationState.PASS,
        stop_too_wide=False,
        third_day_repeat=False,
    )
    decision = build_opening_decision(
        signal,
        breadth_alignment="against",
        market_context={"trend_50dma_aligned": True},
        sector_alignment=True,
    )

    assert decision["momentum"]["box_x"] is False
    assert decision["execution_eligible"] is False
