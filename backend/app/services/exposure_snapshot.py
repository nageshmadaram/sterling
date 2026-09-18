"""How much exposure is open, read from the durable store by any process.

Three callers need this number — the release certification gate, the
authoritative-start report and the stop sequence — and all three were getting
None from a command line, because the durable stores answer nothing until
`db.init()` has run in that process. The in-process default is "not available",
which is the right default for a library and the wrong answer for a question
whose UNKNOWN blocks a release: the gate could never pass, not because there
was exposure but because nobody had opened the database.

So the initialisation happens here, once, and a genuine failure still returns
None. The distinction this module has to protect is between "no exposure" and
"could not tell", and it is the second one that must never quietly become zero.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)

__all__ = ["ExposureSnapshot", "exposure_snapshot", "unresolved_exposure_count"]


@dataclass(frozen=True)
class ExposureSnapshot:
    """What the durable store says is still open, per operator."""

    #: None means the stores could not be read. Never zero in that case.
    unresolved_intents: int | None
    open_positions: int | None
    #: Symbols still held, for a message a person can act on.
    held: tuple[str, ...] = ()
    uids: tuple[str, ...] = ()
    detail: str = ""

    @property
    def total(self) -> int | None:
        if self.unresolved_intents is None or self.open_positions is None:
            return None
        return self.unresolved_intents + self.open_positions

    def as_dict(self) -> dict[str, Any]:
        return {
            "unresolved_intents": self.unresolved_intents,
            "open_positions": self.open_positions,
            "total": self.total,
            "held": list(self.held),
            "uids": list(self.uids),
            "detail": self.detail,
        }


def exposure_snapshot() -> ExposureSnapshot:
    """Read every operator's durable exposure. Never raises."""
    try:
        from app.services import db

        db.init()
    except Exception as exc:  # noqa: BLE001
        return ExposureSnapshot(None, None, detail=f"database unavailable: {exc}")

    try:
        from app.services.kite_engine import order_journal, positions

        uids = tuple(positions.known_uids())
    except Exception as exc:  # noqa: BLE001
        return ExposureSnapshot(None, None, detail=f"position registry unreadable: {exc}")

    intents = 0
    open_count = 0
    held: list[str] = []
    for uid in uids:
        try:
            unresolved = order_journal.unresolved(uid)
            positions_open = positions.open_positions(uid)
        except Exception as exc:  # noqa: BLE001
            return ExposureSnapshot(None, None, uids=uids,
                                    detail=f"state for {uid} unreadable: {exc}")
        intents += len(unresolved)
        open_count += len(positions_open)
        held.extend(p.symbol for p in positions_open)

    return ExposureSnapshot(
        unresolved_intents=intents,
        open_positions=open_count,
        held=tuple(sorted(set(held))),
        uids=uids,
    )


def unresolved_exposure_count() -> int | None:
    """Everything still open, as one number, or None when it could not be read."""
    return exposure_snapshot().total
