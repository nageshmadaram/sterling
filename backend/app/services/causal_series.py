"""Causal Point-in-Time Data Matching Utilities.

Enforces strict causal invariants for historical simulation and replay:
1. Quote/Bar timestamp MUST be <= decision timestamp (no future lookahead).
2. Quote/Bar MUST NOT be older than max_age_ms (no stale data).
3. Selection MUST pick the latest available point-in-time snapshot.
"""
from typing import Any, Dict, List, Optional, TypeVar

T = TypeVar("T")


def latest_asof(
    items: List[Dict[str, Any]],
    decision_ts_ms: int,
    max_age_ms: int = 60000,
    timestamp_key: str = "timestamp_ms",
) -> Optional[Dict[str, Any]]:
    """Return the latest item at or before ``decision_ts_ms`` within ``max_age_ms`` window.

    Returns ``None`` if no quote exists at or before ``decision_ts_ms`` within ``max_age_ms``.
    Future quotes (timestamp > decision_ts_ms) are strictly ignored.
    """
    candidates = [
        item
        for item in items
        if item.get(timestamp_key, 0) <= decision_ts_ms
        and (decision_ts_ms - item.get(timestamp_key, 0)) <= max_age_ms
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda x: x.get(timestamp_key, 0))


class AsOfSeries:
    """Helper wrapper for querying point-in-time data series causally."""

    def __init__(self, data: List[Dict[str, Any]], timestamp_key: str = "timestamp_ms"):
        # Sort chronologically by timestamp_key
        self.data = sorted(data, key=lambda x: x.get(timestamp_key, 0))
        self.timestamp_key = timestamp_key

    def latest_at_or_before(self, decision_ts_ms: int, max_age_ms: int = 60000) -> Optional[Dict[str, Any]]:
        return latest_asof(self.data, decision_ts_ms, max_age_ms, self.timestamp_key)
