"""Per-lane promotion. Ten separate verdicts, never one combined pass.

The existing authoritative gate already encodes the statistical contract —
60 independent sessions, 300 completed trades, a day-clustered CI lower bound
above zero, positive expectancy at baseline, 2x and 3x costs, positive with the
best 1% removed, drawdown inside 10%, coverage at or above 95%, and no
unresolved exposure. What it does not do is know about lanes.

This module is the lane boundary around it. Evidence is filtered to exactly one
lane before the gate sees it, so Ultra + Scalping + Intraday can never add up
to 300 trades between them and read as a pass. Rows from another lane, rows
with no lane at all, non-authoritative rows and modelled rows are all excluded
first, and the count of what was excluded is reported rather than dropped:
"this lane has 47 eligible trades out of 900 rows" is the answer, and it is a
very different answer from "47 trades".
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from app.core.evidence import (
    PROMOTABLE_CLASSES,
    EvidenceClass,
    eligible_for_lane,
    partition_by_lane,
)
from app.core.lane_registry import LANES, UnknownLane, get_lane

#: The predeclared minimum forward sample, per lane. These mirror the floor
#: hardwired inside the authoritative gate, which no caller can lower: the
#: min_sessions/min_trades arguments below only choose whether a non-pass reads
#: as INCONCLUSIVE (sample too short to judge) or FAILED (long enough, and it
#: failed). Neither can turn a short sample into a pass.
MIN_SESSIONS: int = 60
MIN_TRADES: int = 300

PASSED = "PASSED"
FAILED = "FAILED"
INCONCLUSIVE = "INCONCLUSIVE"


def _gate_module():
    """Import the study gate without requiring `study` to be a package."""
    root = Path(__file__).resolve().parents[2]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from study import snapback_authoritative_gate as gate  # noqa: PLC0415

    return gate


@dataclass(frozen=True)
class LaneEvidence:
    """The rows that actually belong to one lane, and what was left out."""

    lane_key: str
    trade_pnls: tuple[float, ...] = field(default_factory=tuple)
    entry_dates: tuple[str, ...] = field(default_factory=tuple)
    statutory_costs: tuple[float, ...] = field(default_factory=tuple)

    considered: int = 0
    excluded_other_lane: int = 0
    excluded_unattributed: int = 0
    excluded_not_authoritative: int = 0
    excluded_class: int = 0

    @property
    def eligible(self) -> int:
        return len(self.trade_pnls)

    @property
    def sessions(self) -> int:
        return len(set(self.entry_dates))

    def as_dict(self) -> dict[str, Any]:
        return {
            "lane_key": self.lane_key,
            "considered_rows": self.considered,
            "eligible_trades": self.eligible,
            "independent_sessions": self.sessions,
            "excluded": {
                "other_lane": self.excluded_other_lane,
                "unattributed": self.excluded_unattributed,
                "not_authoritative": self.excluded_not_authoritative,
                "evidence_class": self.excluded_class,
            },
        }


def collect_lane_evidence(
    rows: Iterable[Mapping[str, Any]],
    lane_key: str,
    *,
    allowed_classes: Iterable[EvidenceClass] | None = None,
) -> LaneEvidence:
    """Filter rows down to one lane, counting every exclusion."""
    allowed = frozenset(allowed_classes) if allowed_classes else PROMOTABLE_CLASSES

    pnls: list[float] = []
    dates: list[str] = []
    costs: list[float] = []
    considered = other = unattributed = not_auth = wrong_class = 0

    for row in rows:
        considered += 1
        if eligible_for_lane(row, lane_key, allowed_classes=allowed):
            pnls.append(float(row.get("actual_total_pnl") or 0.0))
            dates.append(str(row.get("entry_date") or row.get("entry_ts") or ""))
            costs.append(float(row.get("actual_costs") or 0.0))
            continue

        row_lane = str(row.get("lane_key") or "").strip()
        if not row_lane:
            unattributed += 1
        elif row_lane != lane_key:
            other += 1
        elif not int(row.get("authoritative") or 0):
            not_auth += 1
        else:
            wrong_class += 1

    return LaneEvidence(
        lane_key=lane_key,
        trade_pnls=tuple(pnls),
        entry_dates=tuple(dates),
        statutory_costs=tuple(costs),
        considered=considered,
        excluded_other_lane=other,
        excluded_unattributed=unattributed,
        excluded_not_authoritative=not_auth,
        excluded_class=wrong_class,
    )


def evaluate_lane(
    evidence: LaneEvidence,
    *,
    daily_mtm_equity_series: Sequence[float] | None = None,
    allocation_capital_budget: float = 1_000_000.0,
    unresolved_exposures_count: int = 0,
    quote_coverage_pct: float = 100.0,
    max_allowed_drawdown_pct: float = 10.0,
    min_sessions: int = MIN_SESSIONS,
    min_trades: int = MIN_TRADES,
) -> dict[str, Any]:
    """Run the authoritative gate against one lane's own evidence."""
    gate = _gate_module()
    payload = gate.evaluate_with_verdict(
        trade_pnls=list(evidence.trade_pnls),
        entry_dates=list(evidence.entry_dates),
        statutory_costs=list(evidence.statutory_costs),
        daily_mtm_equity_series=list(daily_mtm_equity_series or []) or None,
        allocation_capital_budget=allocation_capital_budget,
        unresolved_exposures_count=unresolved_exposures_count,
        quote_coverage_pct=quote_coverage_pct,
        max_allowed_drawdown_pct=max_allowed_drawdown_pct,
        entry_sessions_count=evidence.sessions,
        min_sessions=min_sessions,
        min_trades=min_trades,
    )
    payload["lane"] = evidence.as_dict()
    return payload


def evaluate_all_lanes(
    rows: Iterable[Mapping[str, Any]],
    **kwargs: Any,
) -> dict[str, dict[str, Any]]:
    """One verdict per lane. There is deliberately no combined verdict.

    A caller that wants "is Snapback profitable?" is asking a question the
    five-mode model says has no answer: Snapback swing and Snapback scalping
    are different experiments, and averaging them describes neither.
    """
    materialised = list(rows)
    return {
        lane_key: evaluate_lane(
            collect_lane_evidence(materialised, lane_key), **kwargs
        )
        for lane_key in sorted(LANES)
    }


def coverage_report(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    """How much of the evidence store can be attributed at all."""
    materialised = list(rows)
    lanes, unattributed = partition_by_lane(materialised)
    known = {key: len(value) for key, value in sorted(lanes.items()) if key in LANES}
    foreign = {
        key: len(value) for key, value in sorted(lanes.items()) if key not in LANES
    }
    return {
        "total_rows": len(materialised),
        "by_lane": known,
        "unattributed_rows": len(unattributed),
        "unknown_lane_rows": foreign,
    }


def lane_exists(lane_key: str) -> bool:
    strategy, _, mode = lane_key.partition(":")
    try:
        get_lane(strategy, mode)
    except (UnknownLane, ValueError):
        return False
    return True
