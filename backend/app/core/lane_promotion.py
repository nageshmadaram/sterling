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

import math
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


class Exclusion:
    """Why one row was kept out of a lane's economic sample.

    Each code names a specific missing fact rather than a generic "bad row",
    because the report has to distinguish "this lane has no evidence yet" from
    "this lane has evidence nobody can read".
    """

    OTHER_LANE = "OTHER_LANE"
    UNATTRIBUTED = "UNATTRIBUTED"
    NOT_AUTHORITATIVE = "NOT_AUTHORITATIVE"
    EVIDENCE_CLASS = "EVIDENCE_CLASS"

    MISSING_ACTUAL_PNL = "MISSING_ACTUAL_PNL"
    NONFINITE_ACTUAL_PNL = "NONFINITE_ACTUAL_PNL"
    MISSING_ACTUAL_COST = "MISSING_ACTUAL_COST"
    NONFINITE_ACTUAL_COST = "NONFINITE_ACTUAL_COST"
    NEGATIVE_ACTUAL_COST = "NEGATIVE_ACTUAL_COST"
    MISSING_ENTRY_DATE = "MISSING_ENTRY_DATE"
    IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
    MISSING_IDENTITY = "MISSING_IDENTITY"

    #: A row excluded for one of these was supposed to be authoritative evidence
    #: and could not be read. That is a data-quality failure, not an absence, and
    #: it must make the lane INCONCLUSIVE rather than quietly shrinking the
    #: sample to the rows that happen to be readable.
    DATA_QUALITY = frozenset(
        {
            MISSING_ACTUAL_PNL,
            NONFINITE_ACTUAL_PNL,
            MISSING_ACTUAL_COST,
            NONFINITE_ACTUAL_COST,
            NEGATIVE_ACTUAL_COST,
            MISSING_ENTRY_DATE,
            IDENTITY_MISMATCH,
            MISSING_IDENTITY,
        }
    )


class EconomicRowError(ValueError):
    """An authoritative row whose economics cannot be read. Carries its code."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code = code


def _finite_number(value: Any, *, missing: str, nonfinite: str) -> float:
    """Parse an economic figure, refusing every stand-in for "we do not know".

    ``float(row.get(x) or 0.0)`` was the original spelling, and it turned three
    different facts — nobody recorded it, the trade broke even, and the field
    held ``NaN`` — into the same ₹0. A promotion sample built that way reports a
    number for evidence that was never observed.
    """
    if value is None or value == "":
        raise EconomicRowError(missing, f"{missing}: field is absent")
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise EconomicRowError(missing, f"{missing}: {value!r} is not a number") from exc
    if not math.isfinite(parsed):
        raise EconomicRowError(nonfinite, f"{nonfinite}: {value!r}")
    return parsed


@dataclass(frozen=True)
class EconomicObservation:
    """One completed trade whose economics were fully observed."""

    pnl: float
    cost: float
    entry_date: str
    identity_hash: str


def validated_economic_row(
    row: Mapping[str, Any],
    *,
    identity_hash: str | None = None,
) -> EconomicObservation:
    """Read one row's economics, or refuse with the code saying what is missing.

    ``identity_hash`` is the identity the caller is collecting for. A row from a
    different revision of the same lane describes a different experiment, and
    pooling the two manufactures a sample size nobody measured.
    """
    row_identity = str(row.get("identity_hash") or "").strip()
    if identity_hash is not None:
        if not row_identity:
            raise EconomicRowError(
                Exclusion.MISSING_IDENTITY,
                "authoritative economic row carries no identity_hash",
            )
        if row_identity != identity_hash:
            raise EconomicRowError(
                Exclusion.IDENTITY_MISMATCH,
                f"row identity {row_identity} != {identity_hash}",
            )

    pnl = _finite_number(
        row.get("actual_total_pnl"),
        missing=Exclusion.MISSING_ACTUAL_PNL,
        nonfinite=Exclusion.NONFINITE_ACTUAL_PNL,
    )
    cost = _finite_number(
        row.get("actual_costs"),
        missing=Exclusion.MISSING_ACTUAL_COST,
        nonfinite=Exclusion.NONFINITE_ACTUAL_COST,
    )
    if cost < 0:
        # Costs are charges. A negative one is a sign error or a rebate nobody
        # declared, and either way the trade's net result is not what it says.
        raise EconomicRowError(Exclusion.NEGATIVE_ACTUAL_COST, f"actual_costs={cost}")

    entry_date = str(row.get("entry_date") or row.get("entry_ts") or "").strip()
    if not entry_date:
        raise EconomicRowError(
            Exclusion.MISSING_ENTRY_DATE,
            "a trade with no entry date cannot be clustered by session",
        )

    return EconomicObservation(
        pnl=pnl, cost=cost, entry_date=entry_date, identity_hash=row_identity
    )


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

    #: The identity these rows belong to, when the caller asked for one.
    identity_hash: str = ""

    considered: int = 0
    excluded_other_lane: int = 0
    excluded_unattributed: int = 0
    excluded_not_authoritative: int = 0
    excluded_class: int = 0

    #: Authoritative rows for this lane whose economics could not be read, by
    #: code. These are not merely absent from the sample: they are rows that were
    #: supposed to count and cannot, which is a different answer entirely.
    unreadable: Mapping[str, int] = field(default_factory=dict)

    @property
    def eligible(self) -> int:
        return len(self.trade_pnls)

    @property
    def sessions(self) -> int:
        return len(set(self.entry_dates))

    @property
    def unreadable_rows(self) -> int:
        return sum(self.unreadable.values())

    @property
    def economics_readable(self) -> bool:
        """Whether every row that should have counted actually could."""
        return self.unreadable_rows == 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "lane_key": self.lane_key,
            "identity_hash": self.identity_hash,
            "considered_rows": self.considered,
            "eligible_trades": self.eligible,
            "independent_sessions": self.sessions,
            "excluded": {
                "other_lane": self.excluded_other_lane,
                "unattributed": self.excluded_unattributed,
                "not_authoritative": self.excluded_not_authoritative,
                "evidence_class": self.excluded_class,
            },
            "unreadable_economics": dict(sorted(self.unreadable.items())),
            "unreadable_rows": self.unreadable_rows,
            "economics_readable": self.economics_readable,
        }


def collect_lane_evidence(
    rows: Iterable[Mapping[str, Any]],
    lane_key: str,
    *,
    allowed_classes: Iterable[EvidenceClass] | None = None,
    identity_hash: str | None = None,
    required_vehicle: str | None = None,
) -> LaneEvidence:
    """Filter rows down to one lane, counting every exclusion.

    A row that belongs to the lane but whose economics cannot be read is counted
    in ``unreadable`` rather than defaulted into the sample. The old spelling
    turned a missing observed P&L into ₹0 and a missing cost into ₹0, so a trade
    nobody had measured contributed a break-even result and shrank the measured
    effect toward zero while inflating the count.

    Pass ``required_vehicle`` to collect one execution vehicle. A lane that
    has run as both bought options and futures has produced two experiments,
    and the futures challenger must never inherit the options sample.

    Pass ``identity_hash`` to collect one revision of a lane. Without it the
    caller is pooling every revision that ever wrote under this lane key, which
    is only safe for a coverage report, never for a promotion decision.
    """
    allowed = frozenset(allowed_classes) if allowed_classes else PROMOTABLE_CLASSES

    pnls: list[float] = []
    dates: list[str] = []
    costs: list[float] = []
    considered = other = unattributed = not_auth = wrong_class = 0
    unreadable: dict[str, int] = {}

    for row in rows:
        considered += 1
        if eligible_for_lane(
            row, lane_key, allowed_classes=allowed, required_vehicle=required_vehicle
        ):
            try:
                observation = validated_economic_row(row, identity_hash=identity_hash)
            except EconomicRowError as exc:
                unreadable[exc.code] = unreadable.get(exc.code, 0) + 1
                continue
            pnls.append(observation.pnl)
            dates.append(observation.entry_date)
            costs.append(observation.cost)
            continue

        row_lane = str(row.get("lane_key") or "").strip()
        row_vehicle = str(row.get("execution_vehicle") or "").strip().upper()
        if not row_lane:
            unattributed += 1
        elif row_lane != lane_key:
            other += 1
        elif required_vehicle is not None and row_vehicle != str(required_vehicle).upper():
            # Right lane, wrong instrument. Counted as another lane's row
            # because economically that is exactly what it is.
            other += 1
        elif not int(row.get("authoritative") or 0):
            not_auth += 1
        else:
            wrong_class += 1

    return LaneEvidence(
        lane_key=lane_key,
        identity_hash=identity_hash or "",
        trade_pnls=tuple(pnls),
        entry_dates=tuple(dates),
        statutory_costs=tuple(costs),
        considered=considered,
        excluded_other_lane=other,
        excluded_unattributed=unattributed,
        excluded_not_authoritative=not_auth,
        excluded_class=wrong_class,
        unreadable=dict(unreadable),
    )


def lane_identities(
    rows: Iterable[Mapping[str, Any]],
    lane_key: str,
    *,
    allowed_classes: Iterable[EvidenceClass] | None = None,
) -> tuple[str, ...]:
    """The distinct identities that wrote promotable rows under one lane key.

    More than one means the lane has been re-frozen and each revision is its own
    experiment. They must be evaluated separately, never pooled.
    """
    allowed = frozenset(allowed_classes) if allowed_classes else PROMOTABLE_CLASSES
    found = {
        str(row.get("identity_hash") or "").strip()
        for row in rows
        if eligible_for_lane(row, lane_key, allowed_classes=allowed)
    }
    return tuple(sorted(found))


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
    """Run the authoritative gate against one lane's own evidence.

    Unreadable economics short-circuit the statistics. Scoring the rows that
    happen to parse would answer a question nobody asked — "how did the readable
    subset do?" — and that subset is not a random sample: whatever broke the
    recording may well correlate with the outcome.
    """
    if not evidence.economics_readable:
        return {
            "verdict": INCONCLUSIVE,
            "reasons": [
                f"{count} authoritative row(s) excluded as {code}"
                for code, count in sorted(evidence.unreadable.items())
            ],
            "lane": evidence.as_dict(),
            "data_quality": "UNREADABLE_ECONOMICS",
        }

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
    payload["data_quality"] = "OK"
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


def evaluate_lane_identities(
    rows: Iterable[Mapping[str, Any]],
    lane_key: str,
    **kwargs: Any,
) -> dict[str, dict[str, Any]]:
    """One verdict per identity within a lane.

    ``snapback:swing`` at rule revision v1 and the same lane at v2 are different
    experiments that happen to share a name. Grouping only by lane key would let
    a re-frozen lane inherit the sample its predecessor collected.
    """
    materialised = list(rows)
    return {
        identity: evaluate_lane(
            collect_lane_evidence(materialised, lane_key, identity_hash=identity),
            **kwargs,
        )
        for identity in lane_identities(materialised, lane_key)
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
