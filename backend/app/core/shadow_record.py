"""Broker shadow: a real intent, priced against the real book, never sent.

Paper fills are simulated against quotes the system chose to believe. Shadow
records are the bridge to reality: the same decision, the same contract, the
same quantity, measured against the broker's actual book, actual margin and
actual listedness — with the order deliberately not submitted.

The single rule this module exists to enforce is that **a no-fill is a result**.
Everywhere a shadow record cannot demonstrate a fill, the outcome is recorded
as ``NO_FILL`` and carries no P&L. Converting a no-fill into a synthetic fill at
the last quote is the one shortcut that would make every metric here worthless,
because it would delete exactly the evidence the shadow phase exists to gather.

Timing drift is measured for the same reason. A signal at 09:20:03 whose
contract was selected at 09:20:41 was not tradable at the signal's price, and
the drift column is what makes that visible instead of arguable.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from statistics import mean, median
from typing import Any, Final, Iterable, Mapping, Sequence

from app.core.evidence import EvidenceClass
from app.core.horizon import HorizonMode, canonical_mode
from app.core.lane_registry import lane_key as make_lane_key


class ShadowOutcome(StrEnum):
    """What the book said would have happened to this intent."""

    FILLED = "FILLED"
    PARTIAL = "PARTIAL"
    #: The book could not fill it. A result, never a rounding error.
    NO_FILL = "NO_FILL"
    #: Refused before reaching the book — margin, capacity, listedness, safety.
    REFUSED = "REFUSED"
    #: The observation itself failed. Neither a fill nor a no-fill.
    UNOBSERVED = "UNOBSERVED"


#: Outcomes that say something about fillability. ``UNOBSERVED`` does not: it
#: says the measurement failed, so it is excluded from the rate rather than
#: counted as a no-fill, which would blame the market for a broken feed.
_MEASURED: Final[frozenset[ShadowOutcome]] = frozenset(
    {ShadowOutcome.FILLED, ShadowOutcome.PARTIAL, ShadowOutcome.NO_FILL}
)


class RefusalReason(StrEnum):
    """Why an intent never reached the book. Every one is evidence."""

    STALE_QUOTE = "stale_quote"
    NO_LISTED_CONTRACT = "no_listed_contract"
    INSUFFICIENT_DEPTH = "insufficient_depth"
    UNKNOWN_MARGIN = "unknown_margin"
    INSUFFICIENT_MARGIN = "insufficient_margin"
    SAFE_MODE = "safe_mode"
    CAPACITY = "capacity"
    VENUE_LIMITATION = "venue_limitation"
    PROTECTION_INFEASIBLE = "protection_infeasible"


@dataclass(frozen=True)
class BookObservation:
    """What the book looked like at one instant. All fields optional but honest.

    ``None`` means not observed. It never means zero: a missing depth reading
    and an empty book are different facts, and only one of them is the market's
    fault.
    """

    observed_at: str
    bid: float | None = None
    ask: float | None = None
    bid_qty: int | None = None
    ask_qty: int | None = None
    last: float | None = None

    @property
    def spread(self) -> float | None:
        if self.bid is None or self.ask is None:
            return None
        return self.ask - self.bid

    @property
    def mid(self) -> float | None:
        if self.bid is None or self.ask is None:
            return None
        return (self.ask + self.bid) / 2

    @property
    def spread_bps(self) -> float | None:
        mid, spread = self.mid, self.spread
        if mid is None or spread is None or mid <= 0:
            return None
        return spread / mid * 10_000

    def as_dict(self) -> dict[str, Any]:
        return {
            "observed_at": self.observed_at,
            "bid": self.bid,
            "ask": self.ask,
            "bid_qty": self.bid_qty,
            "ask_qty": self.ask_qty,
            "last": self.last,
            "spread": self.spread,
            "spread_bps": self.spread_bps,
        }


@dataclass(frozen=True)
class ShadowRecord:
    """One shadow decision, end to end, per §9.1 of the handoff roadmap."""

    lane_key: str
    session_date: str

    signal_at: str
    contract: str | None
    selected_at: str | None

    intended_quantity: int
    observed_at_selection: BookObservation | None = None

    #: What the broker said it would cost to hold this. ``None`` is its own
    #: result: an unknown margin is a refusal reason, not a zero.
    broker_margin: float | None = None
    margin_available: float | None = None

    #: Could the protective order have been placed at all?
    protection_feasible: bool | None = None

    outcome: ShadowOutcome = ShadowOutcome.UNOBSERVED
    refusal_reason: RefusalReason | None = None

    filled_quantity: int = 0
    #: The price the book would have paid. Only ever set for FILLED/PARTIAL.
    hypothetical_fill_price: float | None = None
    #: The price the decision assumed. Slippage is the difference.
    reference_price: float | None = None

    #: The book after the intent's own timestamp, used to check whether the
    #: quantity was really available rather than momentarily quoted.
    observed_after: BookObservation | None = None

    exit_outcome: str | None = None
    notes: str = ""

    evidence_class: EvidenceClass = EvidenceClass.SHADOW

    def __post_init__(self) -> None:
        if self.outcome in {ShadowOutcome.FILLED, ShadowOutcome.PARTIAL}:
            if self.filled_quantity <= 0:
                raise ValueError(
                    f"{self.lane_key}: outcome {self.outcome} with no filled quantity"
                )
        elif self.filled_quantity or self.hypothetical_fill_price is not None:
            # This is the synthetic-fill guard. A NO_FILL that carries a price
            # is exactly the fabrication the shadow phase exists to prevent.
            raise ValueError(
                f"{self.lane_key}: outcome {self.outcome} must not carry a fill "
                f"(quantity={self.filled_quantity}, price={self.hypothetical_fill_price})"
            )
        if self.outcome is ShadowOutcome.REFUSED and self.refusal_reason is None:
            raise ValueError(f"{self.lane_key}: REFUSED without a refusal reason")

    @property
    def mode(self) -> str:
        return self.lane_key.split(":", 1)[-1]

    @property
    def fill_ratio(self) -> float | None:
        if self.intended_quantity <= 0:
            return None
        if self.outcome not in _MEASURED:
            return None
        return self.filled_quantity / self.intended_quantity

    @property
    def slippage(self) -> float | None:
        """Signed difference between the fill and the price the decision used."""
        if self.hypothetical_fill_price is None or self.reference_price is None:
            return None
        return self.hypothetical_fill_price - self.reference_price

    @property
    def selection_drift_seconds(self) -> float | None:
        """Seconds between the signal and the contract actually being chosen."""
        return _delta_seconds(self.signal_at, self.selected_at)

    @property
    def observation_drift_seconds(self) -> float | None:
        """Seconds between the signal and the book observation used to price it."""
        if self.observed_at_selection is None:
            return None
        return _delta_seconds(self.signal_at, self.observed_at_selection.observed_at)

    @property
    def depth_sufficient(self) -> bool | None:
        """Was the requested size actually quoted? ``None`` when depth is unknown."""
        book = self.observed_at_selection
        if book is None or book.ask_qty is None:
            return None
        return book.ask_qty >= self.intended_quantity

    def as_dict(self) -> dict[str, Any]:
        return {
            "lane_key": self.lane_key,
            "session_date": self.session_date,
            "signal_at": self.signal_at,
            "contract": self.contract,
            "selected_at": self.selected_at,
            "intended_quantity": self.intended_quantity,
            "observed_at_selection": (
                self.observed_at_selection.as_dict()
                if self.observed_at_selection
                else None
            ),
            "broker_margin": self.broker_margin,
            "margin_available": self.margin_available,
            "protection_feasible": self.protection_feasible,
            "outcome": str(self.outcome),
            "refusal_reason": str(self.refusal_reason) if self.refusal_reason else None,
            "filled_quantity": self.filled_quantity,
            "hypothetical_fill_price": self.hypothetical_fill_price,
            "reference_price": self.reference_price,
            "observed_after": (
                self.observed_after.as_dict() if self.observed_after else None
            ),
            "exit_outcome": self.exit_outcome,
            "notes": self.notes,
            "evidence_class": str(self.evidence_class),
            "fill_ratio": self.fill_ratio,
            "slippage": self.slippage,
            "selection_drift_seconds": self.selection_drift_seconds,
            "observation_drift_seconds": self.observation_drift_seconds,
            "depth_sufficient": self.depth_sufficient,
        }


def _delta_seconds(start: str | None, end: str | None) -> float | None:
    if not start or not end:
        return None
    try:
        a = datetime.fromisoformat(start)
        b = datetime.fromisoformat(end)
    except ValueError:
        return None
    if (a.tzinfo is None) != (b.tzinfo is None):
        # Comparing a naive stamp with an aware one would silently assume a
        # timezone and produce a drift figure that is wrong by hours.
        return None
    return (b - a).total_seconds()


def shadow_lane_key(strategy_id: str, mode: str | HorizonMode) -> str:
    """Lane key for a shadow record, canonicalising the mode first."""
    return make_lane_key(strategy_id, canonical_mode(mode))


@dataclass(frozen=True)
class ShadowMetrics:
    """The §9.2 metrics for one lane."""

    lane_key: str

    intents: int = 0
    measured: int = 0
    filled: int = 0
    partial: int = 0
    no_fill: int = 0
    refused: int = 0
    unobserved: int = 0

    refusals_by_reason: Mapping[str, int] = field(default_factory=dict)

    median_spread_bps: float | None = None
    median_slippage: float | None = None
    mean_fill_ratio: float | None = None

    depth_sufficient: int = 0
    depth_unknown: int = 0

    margin_unknown: int = 0
    protection_infeasible: int = 0
    protection_unknown: int = 0

    contracts_unavailable: int = 0

    median_selection_drift_seconds: float | None = None
    max_selection_drift_seconds: float | None = None

    @property
    def fillability_rate(self) -> float | None:
        """Share of measured intents the book would have filled, even partly.

        The denominator excludes unobserved intents on purpose. A broken feed
        is an operations problem; counting it as a no-fill would blame the
        market and quietly understate fillability.
        """
        if self.measured <= 0:
            return None
        return (self.filled + self.partial) / self.measured

    @property
    def no_fill_rate(self) -> float | None:
        if self.measured <= 0:
            return None
        return self.no_fill / self.measured

    @property
    def refusal_rate(self) -> float | None:
        if self.intents <= 0:
            return None
        return self.refused / self.intents

    def as_dict(self) -> dict[str, Any]:
        return {
            "lane_key": self.lane_key,
            "intents": self.intents,
            "measured": self.measured,
            "filled": self.filled,
            "partial": self.partial,
            "no_fill": self.no_fill,
            "refused": self.refused,
            "unobserved": self.unobserved,
            "fillability_rate": self.fillability_rate,
            "no_fill_rate": self.no_fill_rate,
            "refusal_rate": self.refusal_rate,
            "refusals_by_reason": dict(self.refusals_by_reason),
            "median_spread_bps": self.median_spread_bps,
            "median_slippage": self.median_slippage,
            "mean_fill_ratio": self.mean_fill_ratio,
            "depth_sufficient": self.depth_sufficient,
            "depth_unknown": self.depth_unknown,
            "margin_unknown": self.margin_unknown,
            "protection_infeasible": self.protection_infeasible,
            "protection_unknown": self.protection_unknown,
            "contracts_unavailable": self.contracts_unavailable,
            "median_selection_drift_seconds": self.median_selection_drift_seconds,
            "max_selection_drift_seconds": self.max_selection_drift_seconds,
        }


def _median_or_none(values: Sequence[float]) -> float | None:
    return median(values) if values else None


def summarize_lane(lane_key: str, records: Iterable[ShadowRecord]) -> ShadowMetrics:
    """Aggregate one lane's shadow records into the §9.2 metric set."""
    rows = [r for r in records if r.lane_key == lane_key]

    counts: dict[ShadowOutcome, int] = defaultdict(int)
    refusals: dict[str, int] = defaultdict(int)
    spreads: list[float] = []
    slippages: list[float] = []
    fill_ratios: list[float] = []
    drifts: list[float] = []
    depth_ok = depth_unknown = margin_unknown = 0
    protection_bad = protection_unknown = contracts_missing = 0

    for row in rows:
        counts[row.outcome] += 1

        if row.refusal_reason is not None:
            refusals[str(row.refusal_reason)] += 1
            if row.refusal_reason is RefusalReason.NO_LISTED_CONTRACT:
                contracts_missing += 1
            if row.refusal_reason is RefusalReason.PROTECTION_INFEASIBLE:
                protection_bad += 1

        book = row.observed_at_selection
        if book is not None and book.spread_bps is not None:
            spreads.append(book.spread_bps)

        if row.slippage is not None:
            slippages.append(row.slippage)

        ratio = row.fill_ratio
        if ratio is not None:
            fill_ratios.append(ratio)

        depth = row.depth_sufficient
        if depth is None:
            depth_unknown += 1
        elif depth:
            depth_ok += 1

        if row.broker_margin is None:
            margin_unknown += 1

        if row.protection_feasible is None:
            protection_unknown += 1
        elif not row.protection_feasible:
            protection_bad += 1

        drift = row.selection_drift_seconds
        if drift is not None:
            drifts.append(drift)

    measured = sum(counts[o] for o in _MEASURED)

    return ShadowMetrics(
        lane_key=lane_key,
        intents=len(rows),
        measured=measured,
        filled=counts[ShadowOutcome.FILLED],
        partial=counts[ShadowOutcome.PARTIAL],
        no_fill=counts[ShadowOutcome.NO_FILL],
        refused=counts[ShadowOutcome.REFUSED],
        unobserved=counts[ShadowOutcome.UNOBSERVED],
        refusals_by_reason=dict(refusals),
        median_spread_bps=_median_or_none(spreads),
        median_slippage=_median_or_none(slippages),
        mean_fill_ratio=mean(fill_ratios) if fill_ratios else None,
        depth_sufficient=depth_ok,
        depth_unknown=depth_unknown,
        margin_unknown=margin_unknown,
        protection_infeasible=protection_bad,
        protection_unknown=protection_unknown,
        contracts_unavailable=contracts_missing,
        median_selection_drift_seconds=_median_or_none(drifts),
        max_selection_drift_seconds=max(drifts) if drifts else None,
    )


def summarize_all(records: Iterable[ShadowRecord]) -> dict[str, ShadowMetrics]:
    """One metric set per lane that produced at least one shadow record."""
    rows = list(records)
    lanes = sorted({row.lane_key for row in rows})
    return {lane: summarize_lane(lane, rows) for lane in lanes}


def render_shadow_metrics(metrics: Mapping[str, ShadowMetrics]) -> str:
    """Operator-readable shadow table. Unknowns render as ``?``, never as 0."""

    def pct(value: float | None) -> str:
        return "?" if value is None else f"{value * 100:.0f}%"

    def num(value: float | None, digits: int = 1) -> str:
        return "?" if value is None else f"{value:.{digits}f}"

    lines = [
        f"{'Lane':<28}{'Intents':>8}{'Fillable':>10}{'No-fill':>9}"
        f"{'Spread bp':>11}{'Slip':>8}{'Drift s':>9}{'Refused':>9}"
    ]
    for lane_key in sorted(metrics):
        m = metrics[lane_key]
        lines.append(
            f"{lane_key:<28}{m.intents:>8}{pct(m.fillability_rate):>10}"
            f"{pct(m.no_fill_rate):>9}{num(m.median_spread_bps, 0):>11}"
            f"{num(m.median_slippage, 2):>8}"
            f"{num(m.median_selection_drift_seconds, 1):>9}{m.refused:>9}"
        )
    return "\n".join(lines)


__all__ = [
    "BookObservation",
    "RefusalReason",
    "ShadowMetrics",
    "ShadowOutcome",
    "ShadowRecord",
    "render_shadow_metrics",
    "shadow_lane_key",
    "summarize_all",
    "summarize_lane",
]
