"""E17: a paper fill must be executable against visible depth.

Filling a whole lot at top-of-book price regardless of the quantity shown there is
free liquidity that the market never offered.
"""

from __future__ import annotations

import pytest

from app.engines.snapback.config import SnapbackConfig
from app.engines.snapback.intraday_models import DepthLevel, RawQuoteEvent
from app.services.snapback_market_data import (
    depth_vwap,
    evaluate_execution_quote,
    paper_execution_price,
    validate_depth,
    visible_quantity,
)

NOW = 1_800_000_000_000

LADDER = (DepthLevel(100.00, 300), DepthLevel(100.10, 300), DepthLevel(100.20, 250))


def _event(*, ask_depth=LADDER, bid_depth=None, ts=NOW):
    bid_depth = bid_depth if bid_depth is not None else (
        DepthLevel(99.90, 300), DepthLevel(99.80, 300), DepthLevel(99.70, 250)
    )
    return RawQuoteEvent(
        contract_id="NIFTY26OCT25000PE", exchange_timestamp_ms=ts, received_at_ms=ts,
        best_bid=bid_depth[0].price if bid_depth else 0.0,
        best_ask=ask_depth[0].price if ask_depth else 0.0,
        bid_quantity=bid_depth[0].quantity if bid_depth else 0,
        ask_quantity=ask_depth[0].quantity if ask_depth else 0,
        bid_depth=tuple(bid_depth), ask_depth=tuple(ask_depth),
    )


def test_visible_quantity_sums_the_ladder():
    assert visible_quantity(_event(), side="BUY") == 850
    assert visible_quantity(_event(), side="SELL") == 850


def test_one_lot_can_use_multiple_depth_levels():
    """The best ask alone is 300; the lot is 850. The ladder must be used."""
    event = _event()

    assert event.ask_quantity < 850
    assert visible_quantity(event, side="BUY") >= 850

    vwap = depth_vwap(event, side="BUY", quantity=850)
    expected = (100.00 * 300 + 100.10 * 300 + 100.20 * 250) / 850

    assert vwap == pytest.approx(expected)


def test_a_book_one_short_cannot_fill():
    short = (DepthLevel(100.00, 300), DepthLevel(100.10, 300), DepthLevel(100.20, 249))

    decision = evaluate_execution_quote(
        _event(ask_depth=short), cfg=SnapbackConfig(), side="BUY",
        required_quantity=850, slippage_bps=20.0, now_ms=NOW,
    )

    assert decision.accepted is False
    assert "insufficient_visible_depth" in decision.reason_codes
    assert decision.visible_quantity == 849


def test_missing_depth_is_not_executable():
    event = _event(ask_depth=())

    decision = evaluate_execution_quote(
        event, cfg=SnapbackConfig(), side="BUY", required_quantity=850,
        slippage_bps=20.0, now_ms=NOW,
    )

    assert decision.accepted is False
    assert "depth_missing" in decision.reason_codes


def test_invalid_levels_are_ignored_and_can_make_the_book_insufficient():
    ladder = (DepthLevel(100.00, 300), DepthLevel(100.10, 0), DepthLevel(100.20, -50))
    event = _event(ask_depth=ladder)

    assert visible_quantity(event, side="BUY") == 300

    decision = evaluate_execution_quote(
        event, cfg=SnapbackConfig(), side="BUY", required_quantity=850,
        slippage_bps=20.0, now_ms=NOW,
    )
    assert decision.accepted is False


def test_a_non_monotonic_ask_ladder_is_malformed():
    bad = (DepthLevel(100.00, 300), DepthLevel(99.50, 300))

    assert "invalid_depth_ladder" in validate_depth(_event(ask_depth=bad))


def test_a_non_monotonic_bid_ladder_is_malformed():
    bad = (DepthLevel(99.00, 300), DepthLevel(99.50, 300))

    assert "invalid_depth_ladder" in validate_depth(_event(bid_depth=bad))


def test_a_sufficient_book_is_accepted_with_its_vwap():
    decision = evaluate_execution_quote(
        _event(), cfg=SnapbackConfig(), side="BUY", required_quantity=850,
        slippage_bps=20.0, now_ms=NOW,
    )

    expected_vwap = (100.00 * 300 + 100.10 * 300 + 100.20 * 250) / 850

    assert decision.accepted is True
    assert decision.raw_vwap == pytest.approx(expected_vwap)
    assert decision.execution_price == pytest.approx(expected_vwap * 1.002)


def test_slippage_always_worsens_the_price():
    assert paper_execution_price(raw_vwap=100.0, side="BUY", slippage_bps=20.0) == pytest.approx(100.20)
    assert paper_execution_price(raw_vwap=100.0, side="SELL", slippage_bps=20.0) == pytest.approx(99.80)


def test_slippage_cannot_improve_a_price():
    buy = paper_execution_price(raw_vwap=100.0, side="BUY", slippage_bps=20.0)
    sell = paper_execution_price(raw_vwap=100.0, side="SELL", slippage_bps=20.0)

    assert buy > 100.0
    assert sell < 100.0


def test_a_stale_book_is_rejected_before_depth_is_considered():
    decision = evaluate_execution_quote(
        _event(ts=NOW - 60_000), cfg=SnapbackConfig(), side="BUY",
        required_quantity=850, slippage_bps=20.0, now_ms=NOW,
    )

    assert decision.accepted is False
    assert "quote_quality_rejected" in decision.reason_codes


def test_a_wide_spread_is_rejected():
    wide = _event(ask_depth=(DepthLevel(200.0, 900),))

    decision = evaluate_execution_quote(
        wide, cfg=SnapbackConfig(max_spread_pct=2.0), side="BUY",
        required_quantity=850, slippage_bps=20.0, now_ms=NOW,
    )

    assert decision.accepted is False
    assert "spread_too_wide" in decision.reason_codes


def test_an_invalid_required_quantity_is_refused():
    decision = evaluate_execution_quote(
        _event(), cfg=SnapbackConfig(), side="BUY", required_quantity=0,
        slippage_bps=20.0, now_ms=NOW,
    )

    assert decision.accepted is False
    assert "invalid_required_quantity" in decision.reason_codes


def test_selling_consumes_the_bid_ladder():
    decision = evaluate_execution_quote(
        _event(), cfg=SnapbackConfig(), side="SELL", required_quantity=850,
        slippage_bps=5.0, now_ms=NOW,
    )

    expected_vwap = (99.90 * 300 + 99.80 * 300 + 99.70 * 250) / 850

    assert decision.accepted is True
    assert decision.raw_vwap == pytest.approx(expected_vwap)
    assert decision.execution_price == pytest.approx(expected_vwap * 0.9995)
