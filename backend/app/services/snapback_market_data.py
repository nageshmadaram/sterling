"""Market data quality and lineage enforcement for Snapback.

Ensures raw market events pass strict validation before entering strategy setup
or execution planners.
"""
from __future__ import annotations

import math
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from dataclasses import dataclass

from app.engines.snapback.config import SnapbackConfig
from app.engines.snapback.intraday_models import (
    DepthLevel, QualityDecision, RawQuoteEvent,
)

_IST = timezone(timedelta(hours=5, minutes=30))


# --------------------------------------------------------------------------- #
# Canonical book arithmetic. One formula, used everywhere.
# --------------------------------------------------------------------------- #


def midpoint(*, bid: float, ask: float) -> Optional[float]:
    """Mid price, or None when the book is not two-sided."""
    if not (math.isfinite(bid) and math.isfinite(ask)):
        return None
    if bid <= 0 or ask <= 0:
        return None
    return (bid + ask) / 2.0


def spread_pct(*, bid: float, ask: float) -> Optional[float]:
    """Spread as a percentage of the MIDPOINT. The only spread formula in Snapback."""
    mid = midpoint(bid=bid, ask=ask)
    if mid is None or mid <= 0:
        return None
    if ask < bid:
        # A crossed book has no meaningful spread.
        return None
    return ((ask - bid) / mid) * 100.0


def _levels(event: RawQuoteEvent, side: str) -> tuple:
    """Visible levels for the side being executed.

    When a provider supplies only top-of-book, that single PROVIDER-OBSERVED level is
    the ladder. Nothing is invented: if the quantity was not observed, there is no
    level at all.
    """
    ladder = event.ask_depth if side == "BUY" else event.bid_depth
    if ladder:
        return tuple(ladder)

    price = event.best_ask if side == "BUY" else event.best_bid
    quantity = event.ask_quantity if side == "BUY" else event.bid_quantity
    if price > 0 and quantity > 0:
        return (DepthLevel(float(price), int(quantity)),)
    return ()


def _valid_levels(ladder) -> list:
    out = []
    for level in ladder:
        price = float(getattr(level, "price", 0.0) or 0.0)
        quantity = int(getattr(level, "quantity", 0) or 0)
        if price > 0 and quantity > 0 and math.isfinite(price):
            out.append(DepthLevel(price, quantity))
    return out


def validate_depth(event: RawQuoteEvent) -> List[str]:
    """Structural problems with the visible ladders."""
    reasons: List[str] = []

    for side, ladder in (("BUY", event.ask_depth), ("SELL", event.bid_depth)):
        levels = _valid_levels(ladder or ())
        if len(levels) < 2:
            continue
        prices = [lv.price for lv in levels]
        # Asks ascend away from the mid; bids descend.
        ordered = prices == sorted(prices) if side == "BUY" else prices == sorted(prices, reverse=True)
        if not ordered:
            reasons.append("invalid_depth_ladder")

    return reasons


def visible_quantity(event: RawQuoteEvent, *, side: str) -> int:
    """Total provider-observed quantity available on the side being executed."""
    return sum(level.quantity for level in _valid_levels(_levels(event, side)))


def depth_vwap(event: RawQuoteEvent, *, side: str, quantity: int) -> Optional[float]:
    """Volume-weighted price of consuming `quantity` from the visible ladder."""
    if quantity <= 0:
        return None

    remaining = int(quantity)
    notional = 0.0
    for level in _valid_levels(_levels(event, side)):
        take = min(remaining, level.quantity)
        notional += take * level.price
        remaining -= take
        if remaining <= 0:
            break

    if remaining > 0:
        return None
    return notional / float(quantity)


def paper_execution_price(*, raw_vwap: float, side: str, slippage_bps: float) -> float:
    """Apply slippage so it always works against the trade, never for it."""
    factor = slippage_bps / 10_000.0
    return raw_vwap * (1.0 + factor) if str(side).upper() == "BUY" else raw_vwap * (1.0 - factor)


def is_stale_decision(decision: QualityDecision) -> bool:
    """Whether this decision refused the quote for AGE. Crossed is not stale."""
    return any(str(code).startswith("stale_quote_age_") for code in (decision.reason_codes or []))


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

    # Depth facts, so nothing downstream has to re-read the ladder.
    depth_reasons = validate_depth(event)
    reasons.extend(depth_reasons)

    top_mismatch = False
    for side, ladder, top in (
        ("BUY", event.ask_depth, event.best_ask),
        ("SELL", event.bid_depth, event.best_bid),
    ):
        levels = _valid_levels(ladder or ())
        if levels and top > 0 and abs(levels[0].price - top) > 1e-9:
            top_mismatch = True
    if top_mismatch:
        reasons.append("book_top_mismatch")

    quoted_spread = spread_pct(bid=event.best_bid, ask=event.best_ask)
    provider_timestamp_valid = event.exchange_timestamp_ms > 0

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
        spread_pct=quoted_spread,
        depth_valid=not depth_reasons,
        visible_bid_quantity=visible_quantity(event, side="SELL"),
        visible_ask_quantity=visible_quantity(event, side="BUY"),
        provider_timestamp_valid=provider_timestamp_valid,
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


# --------------------------------------------------------------------------- #
# Execution feasibility. Generic quality answers "is this book truthful?";
# this answers "can it execute THIS quantity?".
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ExecutionQuoteDecision:
    accepted: bool
    side: str
    required_quantity: int
    visible_quantity: int
    raw_vwap: Optional[float]
    execution_price: Optional[float]
    spread_pct: Optional[float]
    quote_quality: QualityDecision
    reason_codes: tuple = ()


def evaluate_execution_quote(
    event: RawQuoteEvent,
    *,
    cfg: SnapbackConfig,
    side: str,
    required_quantity: int,
    slippage_bps: float,
    now_ms: Optional[int] = None,
    require_full_visible_depth: Optional[bool] = None,
) -> ExecutionQuoteDecision:
    """Can this book execute `required_quantity` right now, and at what price?"""
    from app.engines.snapback.policy import EXECUTION_POLICY

    if require_full_visible_depth is None:
        require_full_visible_depth = EXECUTION_POLICY.require_full_visible_depth

    side = str(side).upper()
    quality = evaluate_quote_quality(event, cfg, now_ms=now_ms)
    reasons: List[str] = []

    if not quality.accepted_for_execution:
        reasons.append("quote_quality_rejected")

    if int(required_quantity) <= 0:
        reasons.append("invalid_required_quantity")

    quoted_spread = quality.spread_pct
    max_spread = float(getattr(cfg, "max_spread_pct", 0) or 0)
    if quoted_spread is not None and max_spread > 0 and quoted_spread > max_spread:
        reasons.append("spread_too_wide")

    ladder_problems = validate_depth(event)
    reasons.extend(code for code in ladder_problems if code not in reasons)

    available = visible_quantity(event, side=side)
    if not _valid_levels(_levels(event, side)):
        reasons.append("depth_missing")
    elif require_full_visible_depth and available < int(required_quantity or 0):
        reasons.append("insufficient_visible_depth")

    raw_vwap = None
    execution_price = None
    if not reasons:
        raw_vwap = depth_vwap(event, side=side, quantity=int(required_quantity))
        if raw_vwap is None:
            reasons.append("insufficient_visible_depth")
        else:
            execution_price = paper_execution_price(
                raw_vwap=raw_vwap, side=side, slippage_bps=slippage_bps,
            )

    return ExecutionQuoteDecision(
        accepted=not reasons,
        side=side,
        required_quantity=int(required_quantity or 0),
        visible_quantity=available,
        raw_vwap=raw_vwap,
        execution_price=execution_price,
        spread_pct=quoted_spread,
        quote_quality=quality,
        reason_codes=tuple(reasons),
    )


def evaluate_underlying_context_quote(
    event: RawQuoteEvent,
    *,
    now_ms: Optional[int] = None,
    max_age_ms: Optional[float] = None,
    clock_tolerance_ms: float = 250.0,
) -> QualityDecision:
    """Validate the underlying price used to size a hedge.

    Sterling does not execute the underlying, so a two-sided book is not required —
    but the price must be identified, timestamped and as fresh as the option and
    futures quotes it is combined with. The loose 60-second context window is not
    acceptable for sizing a live hedge.
    """
    from app.engines.snapback.policy import EXECUTION_POLICY

    if max_age_ms is None:
        max_age_ms = float(EXECUTION_POLICY.underlying_context_max_age_ms)
    if now_ms is None:
        now_ms = int(datetime.now(_IST).timestamp() * 1000)

    reasons: List[str] = []

    provider_timestamp_valid = event.exchange_timestamp_ms > 0
    if not provider_timestamp_valid:
        reasons.append("missing_provider_timestamp")

    price_ok = math.isfinite(event.last_price) and event.last_price > 0
    if not price_ok:
        reasons.append("non_positive_underlying_price")

    contract_valid = len(event.contract_id) > 0
    if not contract_valid:
        reasons.append("missing_contract_identity")

    quote_age_ms = float(now_ms - event.exchange_timestamp_ms) if provider_timestamp_valid else float("inf")
    if provider_timestamp_valid:
        if quote_age_ms < -clock_tolerance_ms:
            reasons.append("future_exchange_timestamp")
        elif quote_age_ms > max_age_ms:
            reasons.append(f"stale_quote_age_{quote_age_ms:.0f}ms")

    accepted = not reasons

    return QualityDecision(
        raw_event_id=f"{event.contract_id}_{event.exchange_timestamp_ms}",
        decision_at_ms=now_ms,
        accepted_for_context=accepted,
        accepted_for_execution=accepted,
        quote_age_ms=quote_age_ms if provider_timestamp_valid else 0.0,
        receive_delay_ms=float(event.received_at_ms - event.exchange_timestamp_ms),
        two_sided=False,
        non_crossed=True,
        finite_values=price_ok,
        contract_valid=contract_valid,
        reason_codes=reasons,
        spread_pct=None,
        depth_valid=False,
        provider_timestamp_valid=provider_timestamp_valid,
    )
