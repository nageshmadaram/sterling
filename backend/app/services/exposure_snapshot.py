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
    """What is actually open — in Sterling's stores AND at the broker."""

    #: None means the stores could not be read. Never zero in that case.
    unresolved_intents: int | None
    open_positions: int | None
    #: Symbols still held, for a message a person can act on.
    held: tuple[str, ...] = ()
    uids: tuple[str, ...] = ()
    detail: str = ""
    #: Broker positions with no Sterling intent behind them. None when the
    #: broker could not be asked — which is not the same as none being held.
    #: Sterling's own stores once said "flat" while the account held 16625 of a
    #: CDSL call; that is the reading this field exists to prevent.
    external_positions: int | None = None
    external_detail: str = ""
    external_instruments: tuple[str, ...] = ()
    #: The full broker answer, so a caller can render it without asking twice.
    #: Two reads can disagree — one can time out — and a report that shows both
    #: is worse than one that shows either.
    external: Any = None

    @property
    def total(self) -> int | None:
        parts = (self.unresolved_intents, self.open_positions, self.external_positions)
        if any(p is None for p in parts):
            return None
        return sum(p for p in parts if p is not None)

    def as_dict(self) -> dict[str, Any]:
        return {
            "unresolved_intents": self.unresolved_intents,
            "open_positions": self.open_positions,
            "external_positions": self.external_positions,
            "external_instruments": list(self.external_instruments),
            "external_detail": self.external_detail,
            "total": self.total,
            "held": list(self.held),
            "uids": list(self.uids),
            "detail": self.detail,
        }


def _external() -> tuple[int | None, str, tuple[str, ...], Any]:
    """Ask the broker what it holds that Sterling did not open.

    Returns ``(count, detail, instruments)`` with ``None`` when the broker could
    not be asked. Run outside an event loop; inside one, the caller is the async
    path and should await `external_exposure` directly.
    """
    import asyncio

    from app.services.external_positions import external_exposure

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        return None, "broker not queried from inside a running event loop", (), None

    try:
        exposure = asyncio.run(external_exposure())
    except Exception as exc:  # noqa: BLE001
        return None, f"broker could not be asked: {type(exc).__name__}: {exc}", (), None

    # Whatever the broker said — including that it could not be asked — is
    # recorded here, because admission reads the record rather than the broker.
    try:
        from app.services.external_positions import record_observation

        record_observation(exposure)
    except Exception:  # noqa: BLE001 - recording must never break the read
        pass

    if not exposure.readable:
        return None, exposure.detail, (), exposure
    return (len(exposure.positions), exposure.detail,
            tuple(p.instrument for p in exposure.positions), exposure)


def _external_recorded() -> tuple[int | None, str, tuple[str, ...], Any]:
    """The last recorded broker answer, with no network call.

    A dashboard refreshing every ten seconds, and a certification report, must
    not each open a broker connection. They read what the last reconciliation
    saw. A missing record is UNKNOWN — the same answer an unreachable broker
    gives, because in both cases nobody can say what the account holds.
    """
    from app.services.external_positions import ExternalExposure, last_observation

    record = last_observation()
    if record is None:
        return None, "no reconciliation has recorded what the broker holds", (), None
    if not record.get("readable", False) or record.get("count") is None:
        return (None, str(record.get("detail") or "the broker could not be read"),
                (), ExternalExposure(readable=False,
                                     detail=str(record.get("detail") or "")))

    instruments = tuple(str(i) for i in (record.get("instruments") or []))
    return int(record.get("count") or 0), "", instruments, None


def exposure_snapshot(*, include_broker: bool = True) -> ExposureSnapshot:
    """Read every operator's durable exposure, and the broker's. Never raises.

    ``include_broker=True`` asks the broker and records the answer.
    ``include_broker=False`` reads the last recorded answer instead, for callers
    that must not make a network call — the dashboard poll and the certification
    report. Either way a missing or unreadable answer is UNKNOWN, never flat.
    """
    try:
        from app.services import db

        db.init()
    except Exception as exc:  # noqa: BLE001
        return ExposureSnapshot(None, None, detail=f"database unavailable: {exc}")  # noqa: E501

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

    external_count, external_detail, external_instruments, external = (
        _external() if include_broker else _external_recorded()
    )

    return ExposureSnapshot(
        unresolved_intents=intents,
        open_positions=open_count,
        held=tuple(sorted(set(held))),
        uids=uids,
        external_positions=external_count,
        external_detail=external_detail,
        external_instruments=external_instruments,
        external=external,
    )


def unresolved_exposure_count() -> int | None:
    """Everything still open, as one number, or None when it could not be read."""
    return exposure_snapshot().total
