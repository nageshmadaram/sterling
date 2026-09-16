"""Stale lifecycle detection.

An entry lease that never completed, or an exit that latched and never liquidated,
are both states where the book is not what the ledger says. They must alert, not sit
quietly: the alert rules already existed, but nothing ever computed their inputs.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)

# The entry lease is held for one opening window; three runner ticks past that is stuck.
PROCESSING_ENTRY_STALE_SECONDS = 15 * 60

# A latched exit waits only for an executable quote; a session's worth of waiting is stuck.
EXIT_PENDING_STALE_SECONDS = 60 * 60


def _age_seconds(iso_ts: Optional[str], now: Optional[datetime] = None) -> Optional[float]:
    if not iso_ts:
        return None
    try:
        moment = datetime.fromisoformat(str(iso_ts).replace("Z", "+00:00"))
    except Exception:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    return (now - moment).total_seconds()


def stale_lifecycle_state(warehouse, *, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Count and age the stuck states. An unreadable warehouse fails closed."""
    now = now or datetime.now(timezone.utc)
    result: Dict[str, Any] = {
        "exit_pending_count": 0,
        "processing_entry_count": 0,
        "oldest_exit_pending_age_s": None,
        "oldest_processing_entry_age_s": None,
        "exit_pending_stale": False,
        "processing_entry_stale": False,
        "errors": [],
    }

    try:
        positions = warehouse.get_records_by_table("paper_positions") or []
    except Exception as exc:
        result["errors"].append(f"paper_positions:{exc}")
        result["exit_pending_stale"] = True
        positions = []

    for row in positions:
        if str(dict(row).get("status") or "").upper() != "EXIT_PENDING":
            continue
        result["exit_pending_count"] += 1
        age = _age_seconds(dict(row).get("pending_exit_ts"), now)
        if age is None:
            # A latched exit with no timestamp cannot be aged, so treat it as stuck.
            result["exit_pending_stale"] = True
            continue
        current = result["oldest_exit_pending_age_s"]
        result["oldest_exit_pending_age_s"] = age if current is None else max(current, age)

    if float(result["oldest_exit_pending_age_s"] or 0) > EXIT_PENDING_STALE_SECONDS:
        result["exit_pending_stale"] = True

    try:
        opportunities = warehouse.get_records_by_table("opportunities") or []
    except Exception as exc:
        result["errors"].append(f"opportunities:{exc}")
        result["processing_entry_stale"] = True
        opportunities = []

    now_ms = int((now.timestamp()) * 1000)
    for row in opportunities:
        data = dict(row)
        if str(data.get("status") or "").upper() != "PROCESSING_ENTRY":
            continue
        result["processing_entry_count"] += 1
        started = data.get("processing_started_at_ms") or 0
        try:
            started_ms = int(started)
        except (TypeError, ValueError):
            started_ms = 0
        if started_ms <= 0:
            result["processing_entry_stale"] = True
            continue
        age = max(0.0, (now_ms - started_ms) / 1000.0)
        current = result["oldest_processing_entry_age_s"]
        result["oldest_processing_entry_age_s"] = age if current is None else max(current, age)

    if float(result["oldest_processing_entry_age_s"] or 0) > PROCESSING_ENTRY_STALE_SECONDS:
        result["processing_entry_stale"] = True

    return result
