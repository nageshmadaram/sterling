"""One switch that means "take no new risk", and that survives a restart.

Sterling already fails closed in many individual places. What it lacked was a
single durable state an operator can read and set without understanding any of
them — and, more importantly, one that a process restart cannot clear. A safety
condition that evaporates when the service is restarted is worse than none,
because restarting is the first thing anyone does when something looks wrong.

The distinction that matters is between opening risk and managing it. SAFE_MODE
blocks new entries and blocks nothing else: reconciliation, protection repair,
hedge repair, position reduction, exit and evidence capture all continue. A
system that refused to close a position because it was in safe mode would have
turned a safety feature into a way to lose money.

Leaving SAFE_MODE is deliberately manual. Automatic recovery would clear the
state the moment a transient check passed, which is exactly when a human should
be looking at it.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

__all__ = [
    "NORMAL",
    "SAFE_MODE",
    "SafeModeTrigger",
    "SafeModeError",
    "SafeModeState",
    "SafeModeService",
]

NORMAL = "NORMAL"
SAFE_MODE = "SAFE_MODE"


class SafeModeTrigger:
    """Conditions that force safe mode. Each names a specific uncertainty."""

    UNKNOWN_BROKER_EXPOSURE = "UNKNOWN_BROKER_EXPOSURE"
    POSITION_MISMATCH = "POSITION_MISMATCH"
    RECONCILIATION_FAILURE = "RECONCILIATION_FAILURE"
    PROTECTION_MISSING = "PROTECTION_MISSING"
    PROTECTION_FAILURE = "PROTECTION_FAILURE"
    EVIDENCE_WRITER_FAILURE = "EVIDENCE_WRITER_FAILURE"
    MARKET_DATA_INTEGRITY = "MARKET_DATA_INTEGRITY"
    RUNTIME_IDENTITY_MISMATCH = "RUNTIME_IDENTITY_MISMATCH"
    UNRESOLVED_ORDER = "UNRESOLVED_ORDER"
    EXECUTION_DISCREPANCY = "EXECUTION_DISCREPANCY"
    OPERATOR = "OPERATOR"

    ALL = frozenset({
        UNKNOWN_BROKER_EXPOSURE, POSITION_MISMATCH, RECONCILIATION_FAILURE,
        PROTECTION_MISSING, PROTECTION_FAILURE, EVIDENCE_WRITER_FAILURE,
        MARKET_DATA_INTEGRITY, RUNTIME_IDENTITY_MISMATCH, UNRESOLVED_ORDER,
        EXECUTION_DISCREPANCY, OPERATOR,
    })

    #: Only an operator may clear these; they indicate capital is at stake and
    #: someone has to look. OPERATOR-triggered safe mode is the exception: a
    #: person turned it on, so a person turning it off needs no investigation.
    REQUIRES_INVESTIGATION = ALL - {OPERATOR}


class SafeModeError(RuntimeError):
    """Refusing to leave safe mode, or to record an unknown trigger."""


@dataclass(frozen=True)
class SafeModeState:
    """The persisted state. Plain enough for a person to read the file."""

    state: str
    triggers: tuple[str, ...]
    reason: str
    entered_at: Optional[str]
    updated_at: str
    runtime_sha: str

    @property
    def active(self) -> bool:
        return self.state == SAFE_MODE

    @property
    def may_open_new_exposure(self) -> bool:
        return not self.active

    @property
    def may_manage_existing_exposure(self) -> bool:
        # Always. Exiting, hedging and reconciling must never be blocked.
        return True

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class SafeModeService:
    """Reads and writes the safe-mode file. Deliberately boring and synchronous.

    The state lives in one small JSON file rather than a database, so an operator
    can read it with ``cat`` when the application will not start — which is
    precisely when they most need to know whether trading is blocked.
    """

    def __init__(self, path: Path | str, *, runtime_sha: str = "") -> None:
        self._path = Path(path)
        self._runtime_sha = runtime_sha

    @property
    def path(self) -> Path:
        return self._path

    def read(self) -> SafeModeState:
        """Current state. A missing or unreadable file is NOT treated as NORMAL.

        An unreadable safety file is itself an unknown, and the safe answer to an
        unknown is to block new risk. The only thing that reads as NORMAL is a
        file that says NORMAL.
        """
        if not self._path.exists():
            return SafeModeState(
                state=NORMAL, triggers=(), reason="no safe-mode file; never engaged",
                entered_at=None, updated_at=datetime.now(timezone.utc).isoformat(),
                runtime_sha=self._runtime_sha,
            )
        try:
            blob = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return SafeModeState(
                state=SAFE_MODE,
                triggers=(SafeModeTrigger.EVIDENCE_WRITER_FAILURE,),
                reason=f"safe-mode file unreadable ({type(exc).__name__}); refusing to assume NORMAL",
                entered_at=None, updated_at=datetime.now(timezone.utc).isoformat(),
                runtime_sha=self._runtime_sha,
            )

        state = blob.get("state")
        if state not in (NORMAL, SAFE_MODE):
            return SafeModeState(
                state=SAFE_MODE, triggers=(SafeModeTrigger.OPERATOR,),
                reason=f"unrecognised state {state!r}; refusing to assume NORMAL",
                entered_at=blob.get("entered_at"),
                updated_at=datetime.now(timezone.utc).isoformat(),
                runtime_sha=self._runtime_sha,
            )

        return SafeModeState(
            state=state,
            triggers=tuple(blob.get("triggers") or ()),
            reason=blob.get("reason", ""),
            entered_at=blob.get("entered_at"),
            updated_at=blob.get("updated_at", ""),
            runtime_sha=blob.get("runtime_sha", ""),
        )

    def _write(self, state: SafeModeState) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        staging = self._path.with_suffix(".staging")
        blob = json.dumps(state.as_dict(), indent=1, sort_keys=True)
        with open(staging, "w", encoding="utf-8") as fh:
            fh.write(blob)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(staging, self._path)

    def engage(self, *, trigger: str, reason: str = "") -> SafeModeState:
        """Enter safe mode, or add a trigger to an existing one.

        Engaging twice does not reset ``entered_at``: how long Sterling has been
        blocked is information an operator needs, and a second trigger must not
        erase it.
        """
        if trigger not in SafeModeTrigger.ALL:
            raise SafeModeError(f"unknown safe-mode trigger {trigger!r}")

        now = datetime.now(timezone.utc).isoformat()
        current = self.read()
        triggers = tuple(sorted(set(current.triggers) | {trigger}))

        state = SafeModeState(
            state=SAFE_MODE, triggers=triggers,
            reason=reason or current.reason or trigger,
            entered_at=current.entered_at if current.active else now,
            updated_at=now, runtime_sha=self._runtime_sha,
        )
        self._write(state)
        return state

    def release(self, *, operator_ack: bool = False, note: str = "") -> SafeModeState:
        """Leave safe mode. Refuses while an investigated trigger stands.

        ``operator_ack`` is the explicit statement that a human looked at the
        condition and resolved it. Without it this refuses, so that safe mode
        cannot be cleared by a script that simply wants to trade.
        """
        current = self.read()
        if not current.active:
            return current

        blocking = set(current.triggers) & SafeModeTrigger.REQUIRES_INVESTIGATION
        if blocking and not operator_ack:
            raise SafeModeError(
                "refusing to leave SAFE_MODE while "
                f"{', '.join(sorted(blocking))} stands; resolve the condition and "
                "acknowledge explicitly (sterling-safe-mode off --ack)"
            )

        now = datetime.now(timezone.utc).isoformat()
        state = SafeModeState(
            state=NORMAL, triggers=(), reason=note or "released by operator",
            entered_at=None, updated_at=now, runtime_sha=self._runtime_sha,
        )
        self._write(state)
        return state

    def may_open_new_exposure(self) -> bool:
        return self.read().may_open_new_exposure
