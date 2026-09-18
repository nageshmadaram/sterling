"""Shadow production: the real decision, the real book, no order.

Stage 1 of going live is running every market day against live data, live
contracts, live depth and live margin with entry submission switched off. The
question it answers is not "was there a signal?" — paper already answers that —
but "could this actually have been executed, at what price, and how often not
at all?".

Two properties make the answer trustworthy, and both are enforced here rather
than documented:

* **The service cannot place an order.** It accepts a market *reader* and
  refuses, at construction, any object that exposes an order-placing method.
  There is therefore no code path from a shadow intent to a broker order, and
  no future edit can add one without deleting :func:`assert_read_only`.
* **A no-fill stays a no-fill.** Every refusal produces a record with no price
  and no quantity. :class:`~app.core.shadow_record.ShadowRecord` already
  refuses to hold a fill it did not have; this module never tries.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence

from app.core.execution_vehicle import ExecutionVehicle, canonical_vehicle
from app.core.shadow_record import (
    BookObservation,
    RefusalReason,
    ShadowMetrics,
    ShadowOutcome,
    ShadowRecord,
    render_shadow_metrics,
    summarize_all,
)

__all__ = [
    "ShadowExecutionService",
    "ShadowIntent",
    "ShadowStore",
    "OrderCapabilityError",
    "assert_read_only",
    "shadow_report",
]

#: Method names that mean "this object can move money". A reader that has one
#: is not a reader.
_ORDERING_METHODS: tuple[str, ...] = (
    "place_order",
    "place_order_async",
    "modify_order",
    "cancel_order",
    "submit",
    "submit_order",
    "execute",
    "place_gtt",
)


class OrderCapabilityError(TypeError):
    """Something able to place orders was handed to the shadow path."""


def assert_read_only(client: Any, *, what: str = "market reader") -> Any:
    """Refuse any object that can place, modify or cancel an order.

    This is the structural half of "shadow never sends". The policy half — a
    disabled switch — can be flipped by configuration; this cannot.
    """
    if client is None:
        return None
    offenders = [name for name in _ORDERING_METHODS if callable(getattr(client, name, None))]
    if offenders:
        raise OrderCapabilityError(
            f"{what} {type(client).__name__} exposes {', '.join(offenders)}; "
            "the shadow path must never hold an object that can place orders"
        )
    return client


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class ShadowIntent:
    """What the strategy wanted to do, before the market was asked."""

    lane_key: str
    session_date: str
    signal_at: str
    contract: str | None
    quantity: int
    reference_price: float | None
    execution_vehicle: ExecutionVehicle
    selected_at: str | None = None
    #: Free-form lane context carried into the record's notes.
    notes: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "execution_vehicle", canonical_vehicle(self.execution_vehicle)
        )
        if self.quantity <= 0:
            raise ValueError(f"{self.lane_key}: shadow intent needs a positive quantity")


@dataclass(frozen=True)
class MarketFacts:
    """Everything observed about the market for one intent. ``None`` is unknown."""

    book: BookObservation | None = None
    contract_listed: bool | None = None
    quote_age_seconds: float | None = None
    broker_margin: float | None = None
    margin_available: float | None = None
    protection_feasible: bool | None = None
    book_after: BookObservation | None = None


#: How stale a quote may be before an intent priced against it is meaningless.
DEFAULT_MAX_QUOTE_AGE_SECONDS: float = 5.0


class ShadowStore:
    """Append-only JSONL, one file per session date. Boring on purpose."""

    def __init__(self, directory: Path | str | None = None) -> None:
        if directory:
            self.directory = Path(directory)
        else:
            root = Path(os.environ.get("STERLING_ROOT") or Path(__file__).resolve().parents[3])
            self.directory = Path(
                os.environ.get("STERLING_SHADOW_DIR") or root / "data" / "shadow"
            )

    def _path(self, session_date: str) -> Path:
        return self.directory / f"{session_date}.jsonl"

    def append(self, record: ShadowRecord) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self._path(record.session_date)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record.as_dict(), sort_keys=True) + "\n")
        return path

    def read(self, session_date: str) -> list[dict[str, Any]]:
        path = self._path(session_date)
        if not path.exists():
            return []
        rows: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                rows.append(json.loads(line))
        return rows

    def sessions(self) -> list[str]:
        if not self.directory.exists():
            return []
        return sorted(p.stem for p in self.directory.glob("*.jsonl"))


class ShadowExecutionService:
    """Turn one intent plus one set of market facts into one shadow record.

    Shared by Snapback and SuperTrend: the point of a common service is that
    "would this have filled?" is answered the same way for both, so their
    execution evidence is comparable.
    """

    def __init__(
        self,
        *,
        market_reader: Any = None,
        store: ShadowStore | None = None,
        max_quote_age_seconds: float = DEFAULT_MAX_QUOTE_AGE_SECONDS,
        clock: Callable[[], str] = _now,
    ) -> None:
        self.market_reader = assert_read_only(market_reader)
        self.store = store if store is not None else ShadowStore()
        self.max_quote_age_seconds = max_quote_age_seconds
        self._clock = clock

    # -- the decision ------------------------------------------------------
    def evaluate(self, intent: ShadowIntent, facts: MarketFacts) -> ShadowRecord:
        """Price the intent against the observed book. Never sends anything."""
        refusal = self._refusal(intent, facts)
        common: dict[str, Any] = {
            "lane_key": intent.lane_key,
            "session_date": intent.session_date,
            "signal_at": intent.signal_at,
            "contract": intent.contract,
            "selected_at": intent.selected_at,
            "intended_quantity": intent.quantity,
            "observed_at_selection": facts.book,
            "broker_margin": facts.broker_margin,
            "margin_available": facts.margin_available,
            "protection_feasible": facts.protection_feasible,
            "reference_price": intent.reference_price,
            "observed_after": facts.book_after,
            "notes": intent.notes,
        }

        if refusal is not None:
            return ShadowRecord(
                **common, outcome=ShadowOutcome.REFUSED, refusal_reason=refusal
            )

        book = facts.book
        assert book is not None  # guaranteed by _refusal
        ask, ask_qty = book.ask, book.ask_qty

        if ask is None:
            # No offer to lift. Unobserved, not a no-fill: the market did not
            # decline the order, the measurement failed.
            return ShadowRecord(**common, outcome=ShadowOutcome.UNOBSERVED)

        if ask_qty is None:
            # A quoted price with unknown size cannot demonstrate a fill, and
            # assuming the full size is exactly the synthetic fill this module
            # exists to refuse.
            return ShadowRecord(**common, outcome=ShadowOutcome.UNOBSERVED)

        if ask_qty <= 0:
            return ShadowRecord(**common, outcome=ShadowOutcome.NO_FILL)

        fillable = min(ask_qty, intent.quantity)
        outcome = (
            ShadowOutcome.FILLED if fillable >= intent.quantity else ShadowOutcome.PARTIAL
        )
        return ShadowRecord(
            **common,
            outcome=outcome,
            filled_quantity=fillable,
            hypothetical_fill_price=ask,
        )

    def _refusal(self, intent: ShadowIntent, facts: MarketFacts) -> RefusalReason | None:
        """The first reason the intent never reached the book, in severity order."""
        if facts.contract_listed is False:
            return RefusalReason.NO_LISTED_CONTRACT
        if facts.book is None:
            return RefusalReason.STALE_QUOTE
        if (
            facts.quote_age_seconds is not None
            and facts.quote_age_seconds > self.max_quote_age_seconds
        ):
            return RefusalReason.STALE_QUOTE
        if facts.broker_margin is None:
            # Unknown margin blocks. Treating it as affordable is how a lane
            # discovers at the broker what it should have discovered here.
            return RefusalReason.UNKNOWN_MARGIN
        if (
            facts.margin_available is not None
            and facts.broker_margin > facts.margin_available
        ):
            return RefusalReason.INSUFFICIENT_MARGIN
        if facts.protection_feasible is False:
            return RefusalReason.PROTECTION_INFEASIBLE
        book = facts.book
        if book.ask_qty is not None and 0 < book.ask_qty < intent.quantity:
            # Partial is a fill outcome, not a refusal; only a book that cannot
            # serve any of it is a depth refusal. Fall through.
            return None
        return None

    def record(self, intent: ShadowIntent, facts: MarketFacts) -> ShadowRecord:
        """Evaluate and persist. The only write this service performs."""
        shadow = self.evaluate(intent, facts)
        self.store.append(shadow)
        return shadow

    # -- reporting ---------------------------------------------------------
    def session_metrics(self, session_date: str) -> dict[str, ShadowMetrics]:
        return summarize_all(_records_from_rows(self.store.read(session_date)))


def _records_from_rows(rows: Iterable[Mapping[str, Any]]) -> Iterator[ShadowRecord]:
    for row in rows:
        yield ShadowRecord(
            lane_key=str(row["lane_key"]),
            session_date=str(row["session_date"]),
            signal_at=str(row["signal_at"]),
            contract=row.get("contract"),
            selected_at=row.get("selected_at"),
            intended_quantity=int(row.get("intended_quantity") or 0),
            observed_at_selection=_book(row.get("observed_at_selection")),
            broker_margin=row.get("broker_margin"),
            margin_available=row.get("margin_available"),
            protection_feasible=row.get("protection_feasible"),
            outcome=ShadowOutcome(str(row.get("outcome") or ShadowOutcome.UNOBSERVED)),
            refusal_reason=(
                RefusalReason(str(row["refusal_reason"]))
                if row.get("refusal_reason")
                else None
            ),
            filled_quantity=int(row.get("filled_quantity") or 0),
            hypothetical_fill_price=row.get("hypothetical_fill_price"),
            reference_price=row.get("reference_price"),
            observed_after=_book(row.get("observed_after")),
            exit_outcome=row.get("exit_outcome"),
            notes=str(row.get("notes") or ""),
        )


def _book(payload: Mapping[str, Any] | None) -> BookObservation | None:
    if not payload:
        return None
    return BookObservation(
        observed_at=str(payload.get("observed_at") or ""),
        bid=payload.get("bid"),
        ask=payload.get("ask"),
        bid_qty=payload.get("bid_qty"),
        ask_qty=payload.get("ask_qty"),
        last=payload.get("last"),
    )


def shadow_report(session_dates: Sequence[str] | None = None, *, store: ShadowStore | None = None) -> str:
    """Per-lane shadow execution report an operator can read without JSON."""
    shadow_store = store or ShadowStore()
    dates = list(session_dates or shadow_store.sessions())
    rows: list[Mapping[str, Any]] = []
    for date in dates:
        rows.extend(shadow_store.read(date))
    if not rows:
        return "No shadow records. Shadow production has not run, or wrote nowhere."
    metrics = summarize_all(_records_from_rows(rows))
    header = f"SHADOW EXECUTION — {len(dates)} session(s), {len(rows)} record(s)"
    return f"{header}\n\n{render_shadow_metrics(metrics)}"
