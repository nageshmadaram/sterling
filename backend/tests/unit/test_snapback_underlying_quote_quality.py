"""E18: the spot used to size a hedge is evidence, not a bare float.

A hedge sized from a stale or timestamp-less last price is a position whose size
nobody can reconstruct or defend.
"""

from __future__ import annotations

import pytest

from app.engines.snapback.config import SnapbackConfig
from app.engines.snapback.intraday_models import RawQuoteEvent
from app.services.snapback_market_data import evaluate_underlying_context_quote

NOW = 1_800_000_000_000


def _underlying(*, last=24500.0, ts=NOW, contract="NIFTY 50"):
    return RawQuoteEvent(
        contract_id=contract, exchange_timestamp_ms=ts, received_at_ms=ts,
        best_bid=0.0, best_ask=0.0, bid_quantity=0, ask_quantity=0,
        last_price=last,
    )


def test_a_fresh_underlying_quote_can_size_a_hedge():
    decision = evaluate_underlying_context_quote(_underlying(), now_ms=NOW)

    assert decision.accepted_for_execution is True
    assert decision.reason_codes == []


def test_the_underlying_does_not_need_a_two_sided_book():
    """Sterling does not execute the underlying, so it needs a price, not a book."""
    decision = evaluate_underlying_context_quote(_underlying(), now_ms=NOW)

    assert decision.accepted_for_execution is True


def test_a_stale_underlying_quote_cannot_size_a_hedge():
    decision = evaluate_underlying_context_quote(
        _underlying(ts=NOW - 30_000), now_ms=NOW,
    )

    assert decision.accepted_for_execution is False
    assert any(code.startswith("stale_quote_age_") for code in decision.reason_codes)


def test_execution_freshness_is_used_not_the_loose_context_window():
    # 10 seconds would pass the old 60-second context rule and must not pass here.
    decision = evaluate_underlying_context_quote(
        _underlying(ts=NOW - 10_000), now_ms=NOW,
    )

    assert decision.accepted_for_execution is False


def test_a_missing_timestamp_is_refused():
    decision = evaluate_underlying_context_quote(_underlying(ts=0), now_ms=NOW)

    assert decision.accepted_for_execution is False
    assert decision.provider_timestamp_valid is False


def test_a_non_positive_price_is_refused():
    decision = evaluate_underlying_context_quote(_underlying(last=0.0), now_ms=NOW)

    assert decision.accepted_for_execution is False


def test_a_missing_contract_identity_is_refused():
    decision = evaluate_underlying_context_quote(_underlying(contract=""), now_ms=NOW)

    assert decision.accepted_for_execution is False


def test_a_future_timestamp_is_refused():
    decision = evaluate_underlying_context_quote(
        _underlying(ts=NOW + 10_000), now_ms=NOW,
    )

    assert decision.accepted_for_execution is False
    assert "future_exchange_timestamp" in decision.reason_codes


def test_the_policy_declares_the_underlying_freshness():
    from app.engines.snapback.policy import EXECUTION_POLICY

    assert EXECUTION_POLICY.underlying_context_max_age_ms == 2_000


def test_entry_path_validates_the_underlying_before_using_it():
    import inspect

    from app.services import snapback as sb

    source = inspect.getsource(sb.process_prospective_pending_entries)

    assert "evaluate_underlying_context_quote" in source
    assert "INCONCLUSIVE_UNDERLYING_QUOTE" in source
