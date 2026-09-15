"""Market data quality and lineage enforcement for Snapback.

Ensures raw market events pass strict validation before entering strategy setup
or execution planners.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from app.engines.snapback.config import SnapbackConfig
from app.engines.snapback.intraday_models import QualityDecision, RawQuoteEvent

_IST = timezone(timedelta(hours=5, minutes=30))


def evaluate_quote_quality(
    event: RawQuoteEvent,
    cfg: SnapbackConfig,
    *,
    now_ms: Optional[int] = None,
    max_age_ms: float = 2000.0,
    clock_tolerance_ms: float = 250.0,
) -> QualityDecision:
    """Validate a raw quote event for context and execution suitability."""
    if now_ms is None:
        now_ms = int(datetime.now(_IST).timestamp() * 1000)

    reasons: List[str] = []
    
    # 1. Finite numeric checks
    finite_values = (
        math.isfinite(event.best_bid)
        and math.isfinite(event.best_ask)
        and math.isfinite(event.last_price)
        and event.exchange_timestamp_ms > 0
    )
    if not finite_values:
        reasons.append("non_finite_or_invalid_values")

    # 2. Two-sided book checks
    two_sided = event.best_bid > 0 and event.best_ask > 0 and event.bid_quantity > 0 and event.ask_quantity > 0
    if not two_sided:
        reasons.append("missing_two_sided_book")

    # 3. Non-crossed book
    non_crossed = two_sided and (event.best_ask >= event.best_bid)
    if two_sided and not non_crossed:
        reasons.append("crossed_book")

    # 4. Timestamps and age
    receive_delay_ms = float(event.received_at_ms - event.exchange_timestamp_ms)
    quote_age_ms = float(now_ms - event.exchange_timestamp_ms)

    if quote_age_ms < -clock_tolerance_ms:
        reasons.append("future_exchange_timestamp")
    elif quote_age_ms > max_age_ms:
        reasons.append(f"stale_quote_age_{quote_age_ms:.0f}ms")

    contract_valid = len(event.contract_id) > 0

    accepted_for_execution = (
        finite_values and two_sided and non_crossed and contract_valid and (len(reasons) == 0)
    )
    accepted_for_context = finite_values and contract_valid and (quote_age_ms <= 60000.0)

    return QualityDecision(
        raw_event_id=f"{event.contract_id}_{event.exchange_timestamp_ms}",
        decision_at_ms=now_ms,
        accepted_for_context=accepted_for_context,
        accepted_for_execution=accepted_for_execution,
        quote_age_ms=quote_age_ms,
        receive_delay_ms=receive_delay_ms,
        two_sided=two_sided,
        non_crossed=non_crossed,
        finite_values=finite_values,
        contract_valid=contract_valid,
        reason_codes=reasons,
    )


def deduplicate_quotes(quotes: List[RawQuoteEvent]) -> List[RawQuoteEvent]:
    """Deduplicate identical consecutive quote events based on timestamp and payload hash."""
    deduped: List[RawQuoteEvent] = []
    seen: set[tuple[str, int, str]] = set()

    for q in quotes:
        key = (q.contract_id, q.exchange_timestamp_ms, q.payload_hash)
        if key not in seen:
            seen.add(key)
            deduped.append(q)

    return deduped
