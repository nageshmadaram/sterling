"""Comprehensive unit tests for Snapback market data quality decisions and deduplication."""

import pytest
from app.engines.snapback.config import SnapbackConfig
from app.engines.snapback.intraday_models import RawQuoteEvent
from app.services.snapback_market_data import evaluate_quote_quality, deduplicate_quotes


def test_evaluate_quote_quality_valid():
    cfg = SnapbackConfig(trading_mode="scalp")
    event = RawQuoteEvent(
        contract_id="NIFTY26SEP20000PE",
        exchange_timestamp_ms=1000000,
        received_at_ms=1000050,
        best_bid=100.0,
        best_ask=101.0,
        bid_quantity=50,
        ask_quantity=50,
        last_price=100.5,
    )

    decision = evaluate_quote_quality(event, cfg, now_ms=1000500)

    assert decision.accepted_for_execution is True
    assert decision.accepted_for_context is True
    assert decision.two_sided is True
    assert decision.non_crossed is True
    assert decision.finite_values is True
    assert len(decision.reason_codes) == 0


def test_evaluate_quote_quality_non_finite():
    cfg = SnapbackConfig(trading_mode="scalp")
    event = RawQuoteEvent(
        contract_id="NIFTY26SEP20000PE",
        exchange_timestamp_ms=1000000,
        received_at_ms=1000050,
        best_bid=float("nan"),
        best_ask=101.0,
        bid_quantity=50,
        ask_quantity=50,
        last_price=100.5,
    )

    decision = evaluate_quote_quality(event, cfg, now_ms=1000500)

    assert decision.accepted_for_execution is False
    assert decision.finite_values is False
    assert "non_finite_or_invalid_values" in decision.reason_codes


def test_evaluate_quote_quality_crossed_book():
    cfg = SnapbackConfig(trading_mode="scalp")
    event = RawQuoteEvent(
        contract_id="NIFTY26SEP20000PE",
        exchange_timestamp_ms=1000000,
        received_at_ms=1000050,
        best_bid=105.0,  # bid > ask -> crossed
        best_ask=100.0,
        bid_quantity=50,
        ask_quantity=50,
        last_price=100.5,
    )

    decision = evaluate_quote_quality(event, cfg, now_ms=1000500)

    assert decision.accepted_for_execution is False
    assert decision.non_crossed is False
    assert "crossed_book" in decision.reason_codes


def test_evaluate_quote_quality_stale_and_future_timestamps():
    cfg = SnapbackConfig(trading_mode="scalp")
    
    # Stale quote (5000ms old > 2000ms ceiling)
    stale_event = RawQuoteEvent(
        contract_id="NIFTY26SEP20000PE",
        exchange_timestamp_ms=1000000,
        received_at_ms=1000050,
        best_bid=100.0,
        best_ask=101.0,
        bid_quantity=50,
        ask_quantity=50,
    )
    stale_decision = evaluate_quote_quality(stale_event, cfg, now_ms=1005500)
    assert stale_decision.accepted_for_execution is False
    assert any("stale_quote_age" in r for r in stale_decision.reason_codes)

    # Future quote (timestamp in future beyond 250ms clock tolerance)
    future_event = RawQuoteEvent(
        contract_id="NIFTY26SEP20000PE",
        exchange_timestamp_ms=1001000,
        received_at_ms=1000050,
        best_bid=100.0,
        best_ask=101.0,
        bid_quantity=50,
        ask_quantity=50,
    )
    future_decision = evaluate_quote_quality(future_event, cfg, now_ms=1000000)
    assert future_decision.accepted_for_execution is False
    assert "future_exchange_timestamp" in future_decision.reason_codes


def test_deduplicate_quotes():
    q1 = RawQuoteEvent("C1", 1000, 1005, 100.0, 101.0, 50, 50, payload_hash="h1")
    q2 = RawQuoteEvent("C1", 1000, 1005, 100.0, 101.0, 50, 50, payload_hash="h1")  # duplicate
    q3 = RawQuoteEvent("C1", 2000, 2005, 101.0, 102.0, 50, 50, payload_hash="h2")

    deduped = deduplicate_quotes([q1, q2, q3])
    assert len(deduped) == 2
    assert deduped == [q1, q3]
