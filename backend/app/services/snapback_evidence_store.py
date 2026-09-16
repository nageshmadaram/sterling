"""Durable evidence writing, separated from the recorder that produces it.

The recorder knows nothing about Parquet or directories. That separation is what
makes a writer failure testable and what will let the storage format change
without touching the lifecycle rules.

Two behaviours matter more than the format.

**Atomicity.** Every file is serialized into ``_staging`` and moved into place
with ``os.replace``. A crash may leave an orphan staging file, which is
recoverable noise; a crash must never leave a truncated file that reads as valid
evidence, which is unrecoverable and silent.

**Asymmetric failure.** A writer that cannot persist must block *new* entries —
evidence that is not durable cannot support a capital decision. It must not block
exiting an *existing* position. Capital safety outranks evidence completeness once
exposure exists, and a system that refuses to close a position because its disk
died has turned a storage fault into a market loss.
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
    """The evidence could not be made durable."""


@dataclass
class WriterHealth:
    """Whether evidence is durable, and what that permits.

    ``may_open_new_exposure`` and ``may_manage_existing_exposure`` are separate on
    purpose: they answer different questions and must be allowed to disagree.
    """

    healthy: bool = True
    last_error: Optional[str] = None
    failed_writes: int = 0

    @property
    def may_open_new_exposure(self) -> bool:
        return self.healthy

    @property
    def may_manage_existing_exposure(self) -> bool:
        # Always. Losing the disk must not strand an open position.
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
    """Append-only evidence, one immutable part file per write.

    Part files rather than one rewritten daily file: rewriting means reading the
    whole of yesterday into memory and writing it back on every append, which
    turns a power cut or a pulled drive into the loss of a whole session rather
    than of one record.
    """

    KINDS = (
        "opportunities", "candidate_universes", "selections",
        "hedge_selections", "broker_events", "lifecycle",
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
        """Serialise one row atomically. Records the failure rather than raising.

        Raising here would propagate a storage fault into whichever trading path
        happened to be recording, which is exactly the coupling this module
        exists to avoid. The caller consults ``health`` instead.
        """
        try:
            target_dir = self._dir(kind)
            staging_dir = self._root / "evidence" / "_staging"
            target_dir.mkdir(parents=True, exist_ok=True)
            staging_dir.mkdir(parents=True, exist_ok=True)

            self._counters[kind] += 1
            name = f"part-{self._run_id}-{self._counters[kind]:06d}.json"
            staging = staging_dir / name
            final = target_dir / name

            blob = json.dumps(row, sort_keys=True, separators=(",", ":"), default=_default)
            with open(staging, "w", encoding="utf-8") as fh:
                fh.write(blob)
                fh.flush()
                os.fsync(fh.fileno())

            # Only now does it become visible under its final name.
            os.replace(staging, final)

            self.health.healthy = True
            self.health.last_error = None
            return final
        except OSError as exc:
            self.health.healthy = False
            self.health.last_error = f"{type(exc).__name__}: {exc}"
            self.health.failed_writes += 1
            return None

    # ─── append methods ──────────────────────────────────────────────────────

    def append_opportunity(self, envelope: Any) -> Optional[Path]:
        return self._write("opportunities", _as_row(envelope))

    def append_candidate_universe(self, universe: Any) -> Optional[Path]:
        return self._write("candidate_universes", _as_row(universe))

    def append_selection(self, selection: Any) -> Optional[Path]:
        return self._write("selections", _as_row(selection))

    def append_hedge_selection(self, hedge: Any) -> Optional[Path]:
        return self._write("hedge_selections", _as_row(hedge))

    def append_broker_event(self, event: Any) -> Optional[Path]:
        return self._write("broker_events", _as_row(event))

    def append_lifecycle_event(self, event: Any) -> Optional[Path]:
        return self._write("lifecycle", _as_row(event))

    # ─── reading back ────────────────────────────────────────────────────────

    def read(self, kind: str) -> list[dict[str, Any]]:
        """Every persisted row of one kind, ordered by part number.

        Staging files are deliberately not read: a file that never reached its
        final name is a crash artefact, not evidence.
        """
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
        """Rebuild lifecycle events for restart, ordered causally per opportunity."""
        from app.services.snapback_evidence_recorder import EvidenceLifecycleEvent

        events = [EvidenceLifecycleEvent(**row) for row in self.read("lifecycle")]
        events.sort(key=lambda e: (e.opportunity_id, e.sequence))
        return events
