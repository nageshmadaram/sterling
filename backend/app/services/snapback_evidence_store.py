"""Durable evidence writing, separated from the recorder that produces it.

The recorder knows nothing about Parquet or directories. That separation is what
makes a writer failure testable and what will let the storage format change
without touching the lifecycle rules.

Two behaviours matter more than the format.

**Atomicity.** Every file is serialized into ``_staging`` and moved into place
only after fsync. A crash may leave an orphan staging file; it must never leave a
truncated final evidence part.

**Asymmetric failure.** A writer that cannot persist must block *new* entries. It
must not block managing existing exposure. Restart hydration also validates the
causal lifecycle chain: a missing part is an evidence gap, not a shorter valid
history.
"""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

__all__ = [
    "EVIDENCE_STORE_SCHEMA_VERSION",
    "EvidenceStoreError",
    "WriterHealth",
    "SnapbackEvidenceStore",
]

EVIDENCE_STORE_SCHEMA_VERSION = "1"


class EvidenceStoreError(RuntimeError):
    """The evidence could not be made durable or reconstructed truthfully."""


@dataclass
class WriterHealth:
    """Whether evidence is durable, and what that permits."""

    healthy: bool = True
    last_error: Optional[str] = None
    failed_writes: int = 0

    @property
    def may_open_new_exposure(self) -> bool:
        return self.healthy

    @property
    def may_manage_existing_exposure(self) -> bool:
        return True


def _as_row(obj: Any) -> dict[str, Any]:
    if hasattr(obj, "as_dict"):
        return obj.as_dict()
    if isinstance(obj, dict):
        return dict(obj)
    raise EvidenceStoreError(f"cannot serialise {type(obj).__name__}; it has no as_dict()")


def _default(value: Any) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


class SnapbackEvidenceStore:
    """Append-only evidence, one immutable part file per write."""

    KINDS = (
        "opportunities", "candidate_universes", "selections",
        "execution_contracts", "hedge_selections", "broker_events", "lifecycle",
        "market_events", "protection",
    )

    def __init__(self, root: Path | str, *, session_date: Optional[date] = None,
                 run_id: Optional[str] = None) -> None:
        self._root = Path(root)
        self._date = session_date or date.today()
        self._run_id = run_id or uuid.uuid4().hex[:12]
        self._counters: dict[str, int] = {kind: 0 for kind in self.KINDS}
        self.health = WriterHealth()

    @property
    def run_id(self) -> str:
        return self._run_id

    def _dir(self, kind: str) -> Path:
        return self._root / "evidence" / f"date={self._date.isoformat()}" / kind

    def _write(self, kind: str, row: dict[str, Any]) -> Optional[Path]:
        """Serialise one row atomically. Records the failure rather than raising."""
        try:
            target_dir = self._dir(kind)
            staging_dir = self._root / "evidence" / "_staging"
            target_dir.mkdir(parents=True, exist_ok=True)
            staging_dir.mkdir(parents=True, exist_ok=True)

            self._counters[kind] += 1
            name = f"part-{self._run_id}-{self._counters[kind]:06d}.json"
            staging = staging_dir / name
            final = target_dir / name

            # Reusing a run id after restart must never overwrite a final part.
            # An overwrite would turn append-only evidence into mutable evidence.
            if final.exists():
                raise OSError(f"evidence part already exists and is immutable: {final}")

            blob = json.dumps(row, sort_keys=True, separators=(",", ":"), default=_default)
            with open(staging, "w", encoding="utf-8") as fh:
                fh.write(blob)
                fh.flush()
                os.fsync(fh.fileno())

            os.replace(staging, final)

            self.health.healthy = True
            self.health.last_error = None
            return final
        except OSError as exc:
            self.health.healthy = False
            self.health.last_error = f"{type(exc).__name__}: {exc}"
            self.health.failed_writes += 1
            return None

    def append_opportunity(self, envelope: Any) -> Optional[Path]:
        return self._write("opportunities", _as_row(envelope))

    def append_candidate_universe(self, universe: Any) -> Optional[Path]:
        return self._write("candidate_universes", _as_row(universe))

    def append_selection(self, selection: Any) -> Optional[Path]:
        return self._write("selections", _as_row(selection))

    def append_execution_contract(self, record: Any) -> Optional[Path]:
        return self._write("execution_contracts", _as_row(record))

    def append_hedge_selection(self, hedge: Any) -> Optional[Path]:
        return self._write("hedge_selections", _as_row(hedge))

    def append_broker_event(self, event: Any) -> Optional[Path]:
        return self._write("broker_events", _as_row(event))

    def append_lifecycle_event(self, event: Any) -> Optional[Path]:
        return self._write("lifecycle", _as_row(event))

    def append_market_event(self, row: Any) -> Optional[Path]:
        return self._write("market_events", row if isinstance(row, dict) else _as_row(row))

    def append_protection_event(self, event: Any) -> Optional[Path]:
        return self._write("protection", _as_row(event))

    def read(self, kind: str) -> list[dict[str, Any]]:
        """Every persisted row of one kind, ordered by part filename."""
        if kind not in self.KINDS:
            raise EvidenceStoreError(f"unknown evidence kind {kind!r}")
        directory = self._dir(kind)
        if not directory.exists():
            return []
        rows = []
        for path in sorted(directory.glob("part-*.json")):
            try:
                rows.append(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError) as exc:
                raise EvidenceStoreError(f"unreadable evidence part {path}: {exc}") from exc
        return rows

    def read_lifecycle_events(self) -> list[Any]:
        """Rebuild lifecycle events and reject a broken causal chain.

        Sorting is not validation. A failed write can leave sequence 0,2 on disk;
        accepting that as a two-event history would erase the fact that sequence 1
        was lost. The same applies to duplicated ids and impossible transitions.
        """
        # The recorder calls this class State. The other name this used to
        # import belongs to app.engines.adaptive_edge.contracts and is an
        # unrelated enum, so the import raised at call time and this validator
        # could never run.
        from app.services.snapback_evidence_recorder import (
            LEGAL_TRANSITIONS,
            EvidenceLifecycleEvent,
            State,
        )

        events = [EvidenceLifecycleEvent(**row) for row in self.read("lifecycle")]
        events.sort(key=lambda e: (e.opportunity_id, e.sequence))

        seen_ids: set[str] = set()
        by_opp: dict[str, list[Any]] = {}
        for event in events:
            if event.event_id in seen_ids:
                raise EvidenceStoreError(f"duplicate lifecycle event_id {event.event_id}")
            seen_ids.add(event.event_id)
            by_opp.setdefault(event.opportunity_id, []).append(event)

        for opportunity_id, chain in by_opp.items():
            prior_state: Optional[str] = None
            for expected_sequence, event in enumerate(chain):
                if int(event.sequence) != expected_sequence:
                    raise EvidenceStoreError(
                        f"lifecycle sequence gap for {opportunity_id}: expected "
                        f"{expected_sequence}, got {event.sequence}"
                    )
                if expected_sequence == 0:
                    if event.previous_state is not None:
                        raise EvidenceStoreError(
                            f"first lifecycle event for {opportunity_id} has previous_state "
                            f"{event.previous_state!r}"
                        )
                    if event.state != State.OPPORTUNITY_CREATED:
                        raise EvidenceStoreError(
                            f"first lifecycle event for {opportunity_id} is {event.state!r}, "
                            "not OPPORTUNITY_CREATED"
                        )
                else:
                    if event.previous_state != prior_state:
                        raise EvidenceStoreError(
                            f"lifecycle previous_state mismatch for {opportunity_id} at "
                            f"sequence {event.sequence}: stored={event.previous_state!r}, "
                            f"expected={prior_state!r}"
                        )
                    if event.state not in LEGAL_TRANSITIONS.get(str(prior_state), frozenset()):
                        raise EvidenceStoreError(
                            f"illegal persisted lifecycle transition for {opportunity_id}: "
                            f"{prior_state} -> {event.state}"
                        )
                prior_state = event.state

        return events
