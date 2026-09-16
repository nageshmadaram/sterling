"""Append-only quote evidence.

Every quote the runtime *required* is recorded before any accept/reject decision is
returned, so refusals are measurable. Quote coverage is then

    accepted required observations / all required observations

rather than "good stored quotes / stored quotes", which can only ever look perfect.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

log = logging.getLogger(__name__)


def record_quote_attempt(
    warehouse,
    *,
    opportunity_id: str,
    phase: str,
    leg: str,
    contract_id: str,
    required_for_economics: bool,
    quote_present: bool,
    bid: Optional[float],
    ask: Optional[float],
    provider_timestamp: Optional[str],
    age_ms: Optional[int],
    accepted: bool,
    reason_codes: Optional[Iterable[str]] = None,
    symbol: str = "",
) -> None:
    """Persist one quote attempt. Never raises into the trading path."""
    try:
        warehouse.record_quote_quality_event(
            event_id=f"QE-{uuid.uuid4().hex}",
            opportunity_id=opportunity_id,
            phase=phase,
            leg=leg,
            contract_id=contract_id,
            required_for_economics=1 if required_for_economics else 0,
            quote_present=1 if quote_present else 0,
            bid=bid,
            ask=ask,
            age_ms=age_ms,
            accepted=1 if accepted else 0,
            reason_codes=",".join(str(r) for r in (reason_codes or [])),
            provider_timestamp=provider_timestamp,
            symbol=symbol or contract_id,
        )
    except Exception as exc:
        # Evidence recording must never break execution; a failure is logged loudly.
        log.warning("Snapback quote evidence: could not record attempt for %s: %s",
                    contract_id, exc)


def coverage_from_events(events: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    """Coverage over required attempts. No attempts means UNKNOWN, never 100%."""
    required = 0
    accepted = 0
    for event in events or []:
        try:
            if int(event.get("required_for_economics") or 0) != 1:
                continue
        except (TypeError, ValueError):
            continue
        required += 1
        try:
            if int(event.get("accepted") or 0) == 1:
                accepted += 1
        except (TypeError, ValueError):
            pass

    return {
        "required": required,
        "accepted": accepted,
        "rejected": required - accepted,
        "coverage_pct": (accepted / required * 100.0) if required else None,
    }
