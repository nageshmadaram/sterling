"""E15/E16: one book, one quality decision, one spread formula.

Every Snapback consumer must read the same QualityDecision rather than
re-interpreting bid/ask, and a stored quote's staleness must be that decision's
verdict — not a hard-coded flag written next to it.
"""

from __future__ import annotations

import pytest

from app.engines.snapback.config import SnapbackConfig
from app.engines.snapback.intraday_models import DepthLevel, RawQuoteEvent
from app.services.snapback_market_data import (
    evaluate_quote_quality,
    is_stale_decision,
    midpoint,
    spread_pct,
)

NOW = 1_800_000_000_000


def _event(bid=100.0, ask=102.0, *, bid_qty=500, ask_qty=500, ts=NOW, contract="OPT"):
    return RawQuoteEvent(
        contract_id=contract, exchange_timestamp_ms=ts, received_at_ms=ts,
        best_bid=bid, best_ask=ask, bid_quantity=bid_qty, ask_quantity=ask_qty,
        last_price=(bid + ask) / 2 if bid and ask else 0.0,
    )


# ------------------------------------------------------------------ spread


def test_spread_uses_the_midpoint():
    # 100 / 102 -> mid 101 -> 1.980198...%
    assert spread_pct(bid=100.0, ask=102.0) == pytest.approx(2.0 / 101.0 * 100.0)


def test_midpoint_is_the_arithmetic_mean():
    assert midpoint(bid=100.0, ask=102.0) == pytest.approx(101.0)


def test_a_touching_book_has_zero_spread():
    assert spread_pct(bid=100.0, ask=100.0) == pytest.approx(0.0)


@pytest.mark.parametrize("bid,ask", [(0.0, 102.0), (100.0, 0.0), (-1.0, 102.0)])
def test_a_non_positive_side_has_no_spread(bid, ask):
    assert spread_pct(bid=bid, ask=ask) is None


def test_a_crossed_book_has_no_usable_spread():
    assert spread_pct(bid=102.0, ask=100.0) is None


# ----------------------------------------------------------------- quality


def test_a_fresh_two_sided_book_is_executable():
    decision = evaluate_quote_quality(_event(), SnapbackConfig(), now_ms=NOW)

    assert decision.accepted_for_execution is True
    assert decision.reason_codes == []
    assert decision.spread_pct == pytest.approx(2.0 / 101.0 * 100.0)


def test_a_stale_quote_is_refused_and_says_so():
    decision = evaluate_quote_quality(
        _event(ts=NOW - 10_000), SnapbackConfig(), now_ms=NOW,
    )

    assert decision.accepted_for_execution is False
    assert any(code.startswith("stale_quote_age_") for code in decision.reason_codes)
    assert is_stale_decision(decision) is True


def test_a_crossed_book_is_refused_but_is_not_stale():
    decision = evaluate_quote_quality(_event(bid=102.0, ask=100.0), SnapbackConfig(), now_ms=NOW)

    assert decision.accepted_for_execution is False
    assert "crossed_book" in decision.reason_codes
    # Crossed is a different defect from stale, and must not be recorded as stale.
    assert is_stale_decision(decision) is False


def test_a_future_timestamp_is_refused():
    decision = evaluate_quote_quality(
        _event(ts=NOW + 5_000), SnapbackConfig(), now_ms=NOW,
    )

    assert decision.accepted_for_execution is False
    assert "future_exchange_timestamp" in decision.reason_codes


def test_a_fresh_quote_is_not_stale():
    decision = evaluate_quote_quality(_event(), SnapbackConfig(), now_ms=NOW)

    assert is_stale_decision(decision) is False


def test_quality_reports_visible_quantities():
    event = RawQuoteEvent(
        contract_id="OPT", exchange_timestamp_ms=NOW, received_at_ms=NOW,
        best_bid=100.0, best_ask=100.1, bid_quantity=300, ask_quantity=300,
        bid_depth=(DepthLevel(100.0, 300), DepthLevel(99.9, 400)),
        ask_depth=(DepthLevel(100.1, 300), DepthLevel(100.2, 400)),
    )

    decision = evaluate_quote_quality(event, SnapbackConfig(), now_ms=NOW)

    assert decision.visible_bid_quantity == 700
    assert decision.visible_ask_quantity == 700
    assert decision.depth_valid is True


def test_a_book_top_that_disagrees_with_its_depth_is_rejected():
    event = RawQuoteEvent(
        contract_id="OPT", exchange_timestamp_ms=NOW, received_at_ms=NOW,
        best_bid=100.0, best_ask=102.0, bid_quantity=300, ask_quantity=300,
        # Top of the ladder does not match best_ask.
        ask_depth=(DepthLevel(105.0, 300),),
        bid_depth=(DepthLevel(100.0, 300),),
    )

    decision = evaluate_quote_quality(event, SnapbackConfig(), now_ms=NOW)

    assert decision.accepted_for_execution is False
    assert "book_top_mismatch" in decision.reason_codes


def test_provider_timestamp_validity_is_reported():
    good = evaluate_quote_quality(_event(), SnapbackConfig(), now_ms=NOW)
    bad = evaluate_quote_quality(_event(ts=0), SnapbackConfig(), now_ms=NOW)

    assert good.provider_timestamp_valid is True
    assert bad.provider_timestamp_valid is False
