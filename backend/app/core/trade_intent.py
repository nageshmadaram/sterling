"""The intent a strategy emits, and the durable store that makes it idempotent.

Strategies never call the broker. They emit a :class:`TradeIntent`, which is
persisted before anything is sent, and the execution layer works from the
stored record. That ordering is what makes a retry safe: the second attempt
finds the first intent instead of creating a second order.

The uniqueness constraint is the mechanism. One
``(strategy_id, mode, opportunity_id, instrument_token, action)`` may exist
once, ever. Two lanes may act on the same instrument in the same session — that
is the exposure coordinator's problem, not this one — but one lane cannot act
twice on one opportunity because a process restarted mid-submit.

Two states exist specifically because "I do not know" is a real answer:

* ``SUBMITTED_UNKNOWN`` — the order was sent and the outcome was never read.
  It is never resubmitted. The broker is asked first.
* ``UNPROTECTED`` — the entry filled and the protective order is not confirmed.
  An API call that returned without raising is not confirmation.
"""
from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, Iterable

from app.core.horizon import HorizonMode, canonical_mode
from app.core.strategy_identity import stable_hash


class IntentState(StrEnum):
    """Where a stored intent stands with the broker."""

    #: Persisted, nothing sent. Safe to submit.
    NOT_SUBMITTED = "not_submitted"
    #: Sent; the outcome was never read. NEVER resubmit — query the broker.
    SUBMITTED_UNKNOWN = "submitted_unknown"
    OPEN = "open"
    PARTIAL = "partial"
    FILLED = "filled"
    CANCELLED = "cancelled"
    REJECTED = "rejected"


#: States from which a fresh submission is safe.
_SUBMITTABLE: Final[frozenset[IntentState]] = frozenset({IntentState.NOT_SUBMITTED})

#: States that are finished: nothing further will happen on their own.
TERMINAL_STATES: Final[frozenset[IntentState]] = frozenset(
    {IntentState.FILLED, IntentState.CANCELLED, IntentState.REJECTED}
)


class ProtectionState(StrEnum):
    """Whether a filled position actually has a stop at the broker."""

    #: No protection requested yet, or requested and not confirmed.
    UNPROTECTED = "unprotected"
    #: The request was accepted and the order is visible in the broker book.
    CONFIRMED = "confirmed"
    #: The request failed, or the confirmed order has disappeared.
    FAILED = "failed"


class IntentError(RuntimeError):
    """The intent is malformed, or the transition asked for is not allowed."""


class DuplicateIntent(IntentError):
    """A different intent already occupies this uniqueness slot."""


@dataclass(frozen=True)
class TradeIntent:
    """What a strategy asks the execution layer to do.

    ``instrument_token``, ``exchange`` and ``tradingsymbol`` are all carried,
    because the later MTM, risk, hedge, exit and reconciliation steps must use
    the identity recorded here rather than rebuild ``"NFO:" + symbol`` from a
    canonical name.
    """

    intent_id: str

    strategy_id: str
    strategy_version: str

    mode: HorizonMode
    mode_version: str

    opportunity_id: str

    instrument_token: int
    exchange: str
    tradingsymbol: str

    side: str
    quantity: int

    order_type: str
    limit_price: float | None

    protection_required: bool
    max_loss_budget: float

    horizon_plan_id: str

    config_hash: str
    runtime_sha: str

    #: "entry" or "exit". Part of the uniqueness slot, and never inferred from
    #: price: a sell that is an exit and a sell that opens a short are
    #: different acts.
    action: str = "entry"

    def __post_init__(self) -> None:
        if not isinstance(self.mode, HorizonMode):
            raise IntentError("mode must be a HorizonMode; canonicalise first")
        if self.quantity <= 0:
            raise IntentError("quantity must be positive")
        if self.action not in ("entry", "exit"):
            raise IntentError(f"action must be entry or exit, got {self.action!r}")
        if self.side.upper() not in ("BUY", "SELL"):
            raise IntentError(f"side must be BUY or SELL, got {self.side!r}")
        if self.order_type.upper() == "LIMIT" and self.limit_price is None:
            raise IntentError("a LIMIT order needs a limit_price")
        if self.max_loss_budget <= 0:
            raise IntentError(
                "max_loss_budget must be positive: an intent with no loss "
                "budget cannot be checked against any risk limit"
            )
        if not self.horizon_plan_id:
            raise IntentError(
                "horizon_plan_id is required: a position with no time budget "
                "has no hard exit"
            )
        if not self.instrument_token:
            raise IntentError("instrument_token is required")

    @property
    def lane_key(self) -> str:
        return f"{self.strategy_id}:{self.mode.value}"

    @property
    def uniqueness_slot(self) -> tuple:
        """The tuple that may exist only once."""
        return (
            self.strategy_id,
            self.mode.value,
            self.opportunity_id,
            int(self.instrument_token),
            self.action,
        )

    @property
    def payload_hash(self) -> str:
        """Hash of everything that would change what the broker is told."""
        payload = asdict(self)
        payload.pop("intent_id", None)
        payload["mode"] = self.mode.value
        return stable_hash(payload)

    def as_row(self) -> dict[str, Any]:
        row = asdict(self)
        row["mode"] = self.mode.value
        row["lane_key"] = self.lane_key
        row["payload_hash"] = self.payload_hash
        return row


def make_intent_id(
    *, strategy_id: str, mode: str | HorizonMode, opportunity_id: str,
    instrument_token: int, action: str = "entry",
) -> str:
    """Deterministic id from the uniqueness slot.

    Deterministic on purpose: a retry that regenerates the intent computes the
    same id and collides with the stored one, instead of minting a fresh id and
    a second order.
    """
    return stable_hash(
        {
            "strategy_id": strategy_id.strip().lower(),
            "mode": canonical_mode(mode).value,
            "opportunity_id": opportunity_id,
            "instrument_token": int(instrument_token),
            "action": action,
        }
    )


@dataclass(frozen=True)
class StoredIntent:
    """An intent plus everything the execution layer has learned about it."""

    intent: TradeIntent
    state: IntentState
    protection: ProtectionState
    broker_order_id: str
    filled_quantity: int
    created_at: str
    updated_at: str
    note: str = ""

    @property
    def safe_to_submit(self) -> bool:
        return self.state in _SUBMITTABLE

    @property
    def needs_broker_query(self) -> bool:
        """Must the broker be asked before anything else happens?"""
        return self.state is IntentState.SUBMITTED_UNKNOWN

    @property
    def position_is_unprotected(self) -> bool:
        """A fill with no confirmed stop is live, naked risk."""
        return (
            self.state in (IntentState.FILLED, IntentState.PARTIAL)
            and self.intent.protection_required
            and self.protection is not ProtectionState.CONFIRMED
        )


_SCHEMA = """
CREATE TABLE IF NOT EXISTS trade_intents (
    intent_id         TEXT PRIMARY KEY,
    strategy_id       TEXT NOT NULL,
    strategy_version  TEXT NOT NULL,
    mode              TEXT NOT NULL,
    mode_version      TEXT NOT NULL,
    lane_key          TEXT NOT NULL,
    opportunity_id    TEXT NOT NULL,
    instrument_token  INTEGER NOT NULL,
    exchange          TEXT NOT NULL,
    tradingsymbol     TEXT NOT NULL,
    side              TEXT NOT NULL,
    quantity          INTEGER NOT NULL,
    order_type        TEXT NOT NULL,
    limit_price       REAL,
    protection_required INTEGER NOT NULL,
    max_loss_budget   REAL NOT NULL,
    horizon_plan_id   TEXT NOT NULL,
    config_hash       TEXT NOT NULL,
    runtime_sha       TEXT NOT NULL,
    action            TEXT NOT NULL,
    payload_hash      TEXT NOT NULL,
    state             TEXT NOT NULL,
    protection        TEXT NOT NULL,
    broker_order_id   TEXT NOT NULL DEFAULT '',
    filled_quantity   INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    note              TEXT NOT NULL DEFAULT '',
    UNIQUE (strategy_id, mode, opportunity_id, instrument_token, action)
);
CREATE INDEX IF NOT EXISTS ix_trade_intents_lane ON trade_intents(lane_key);
CREATE INDEX IF NOT EXISTS ix_trade_intents_state ON trade_intents(state);
CREATE UNIQUE INDEX IF NOT EXISTS ux_trade_intents_broker_order
    ON trade_intents(broker_order_id) WHERE broker_order_id != '';
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class IntentStore:
    """Durable intent record. Written before submission, always."""

    def __init__(self, db_path: str | Path) -> None:
        self._path = str(db_path)
        self._init()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init(self) -> None:
        conn = self._connect()
        try:
            with conn:
                conn.executescript(_SCHEMA)
        finally:
            conn.close()

    # ── writing ───────────────────────────────────────────────────────────

    def reserve(self, intent: TradeIntent) -> StoredIntent:
        """Persist an intent, or return the one already holding its slot.

        A retry with identical content is not an error — it is the retry
        working. A retry with *different* content under the same slot is an
        error, because one of the two is wrong and guessing which would send
        the wrong order.
        """
        existing = self.get_by_slot(intent.uniqueness_slot)
        if existing is not None:
            if existing.intent.payload_hash != intent.payload_hash:
                raise DuplicateIntent(
                    f"slot {intent.uniqueness_slot} already holds intent "
                    f"{existing.intent.intent_id} with different content"
                )
            return existing

        row = intent.as_row()
        row.update(
            state=IntentState.NOT_SUBMITTED.value,
            protection=ProtectionState.UNPROTECTED.value,
            broker_order_id="",
            filled_quantity=0,
            created_at=_now(),
            updated_at=_now(),
            note="",
        )
        row["protection_required"] = int(intent.protection_required)
        columns = ", ".join(row)
        placeholders = ", ".join(f":{c}" for c in row)
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    f"INSERT INTO trade_intents ({columns}) VALUES ({placeholders})",
                    row,
                )
        except sqlite3.IntegrityError as exc:  # pragma: no cover - race path
            conn.close()
            found = self.get_by_slot(intent.uniqueness_slot)
            if found is not None:
                return found
            raise DuplicateIntent(str(exc)) from exc
        else:
            conn.close()
        stored = self.get(intent.intent_id)
        assert stored is not None
        return stored

    def record_submission(self, intent_id: str) -> StoredIntent:
        """Mark an intent as sent with an unknown outcome.

        Called immediately BEFORE the broker call, not after. If the process
        dies during the call, recovery must find SUBMITTED_UNKNOWN; a record
        written afterwards would leave a real order looking un-submitted.
        """
        return self._transition(
            intent_id,
            IntentState.SUBMITTED_UNKNOWN,
            allowed_from={IntentState.NOT_SUBMITTED, IntentState.SUBMITTED_UNKNOWN},
        )

    def record_broker_state(
        self,
        intent_id: str,
        state: IntentState,
        *,
        broker_order_id: str = "",
        filled_quantity: int | None = None,
        note: str = "",
    ) -> StoredIntent:
        """Record what the broker actually said."""
        if state is IntentState.NOT_SUBMITTED:
            raise IntentError(
                "cannot move an intent back to NOT_SUBMITTED: a sent order does "
                "not become unsent, and treating it as such would resubmit it"
            )
        return self._transition(
            intent_id,
            state,
            allowed_from=None,
            broker_order_id=broker_order_id,
            filled_quantity=filled_quantity,
            note=note,
        )

    def record_protection(
        self, intent_id: str, protection: ProtectionState, *, note: str = ""
    ) -> StoredIntent:
        conn = self._connect()
        try:
            with conn:
                cur = conn.execute(
                    "UPDATE trade_intents SET protection = ?, note = ?, updated_at = ? "
                    "WHERE intent_id = ?",
                    (protection.value, note, _now(), intent_id),
                )
            if cur.rowcount == 0:
                raise IntentError(f"unknown intent {intent_id}")
        finally:
            conn.close()
        stored = self.get(intent_id)
        assert stored is not None
        return stored

    def _transition(
        self,
        intent_id: str,
        state: IntentState,
        *,
        allowed_from: frozenset[IntentState] | set[IntentState] | None,
        broker_order_id: str = "",
        filled_quantity: int | None = None,
        note: str = "",
    ) -> StoredIntent:
        current = self.get(intent_id)
        if current is None:
            raise IntentError(f"unknown intent {intent_id}")
        if allowed_from is not None and current.state not in allowed_from:
            raise IntentError(
                f"{intent_id}: cannot move from {current.state} to {state}"
            )
        if current.state in TERMINAL_STATES and state is not current.state:
            raise IntentError(
                f"{intent_id}: {current.state} is terminal; a finished order "
                "does not change outcome"
            )

        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    "UPDATE trade_intents SET state = ?, broker_order_id = ?, "
                    "filled_quantity = ?, note = ?, updated_at = ? WHERE intent_id = ?",
                    (
                        state.value,
                        broker_order_id or current.broker_order_id,
                        current.filled_quantity
                        if filled_quantity is None
                        else int(filled_quantity),
                        note or current.note,
                        _now(),
                        intent_id,
                    ),
                )
        finally:
            conn.close()
        stored = self.get(intent_id)
        assert stored is not None
        return stored

    # ── reading ───────────────────────────────────────────────────────────

    def get(self, intent_id: str) -> StoredIntent | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM trade_intents WHERE intent_id = ?", (intent_id,)
            ).fetchone()
        finally:
            conn.close()
        return _hydrate(row) if row else None

    def get_by_slot(self, slot: tuple) -> StoredIntent | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM trade_intents WHERE strategy_id = ? AND mode = ? "
                "AND opportunity_id = ? AND instrument_token = ? AND action = ?",
                slot,
            ).fetchone()
        finally:
            conn.close()
        return _hydrate(row) if row else None

    def pending_recovery(self) -> list[StoredIntent]:
        """Intents a restart must resolve before anything else happens."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM trade_intents WHERE state NOT IN (?, ?, ?) "
                "ORDER BY created_at",
                tuple(s.value for s in TERMINAL_STATES),
            ).fetchall()
        finally:
            conn.close()
        return [_hydrate(row) for row in rows]

    def unprotected_positions(self) -> list[StoredIntent]:
        """Filled or partly filled intents with no confirmed protection."""
        return [
            stored
            for stored in self._all()
            if stored.position_is_unprotected
        ]

    def for_lane(self, lane_key: str) -> list[StoredIntent]:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM trade_intents WHERE lane_key = ? ORDER BY created_at",
                (lane_key,),
            ).fetchall()
        finally:
            conn.close()
        return [_hydrate(row) for row in rows]

    def _all(self) -> Iterable[StoredIntent]:
        conn = self._connect()
        try:
            rows = conn.execute("SELECT * FROM trade_intents").fetchall()
        finally:
            conn.close()
        return [_hydrate(row) for row in rows]


def _hydrate(row: sqlite3.Row) -> StoredIntent:
    intent = TradeIntent(
        intent_id=row["intent_id"],
        strategy_id=row["strategy_id"],
        strategy_version=row["strategy_version"],
        mode=HorizonMode(row["mode"]),
        mode_version=row["mode_version"],
        opportunity_id=row["opportunity_id"],
        instrument_token=int(row["instrument_token"]),
        exchange=row["exchange"],
        tradingsymbol=row["tradingsymbol"],
        side=row["side"],
        quantity=int(row["quantity"]),
        order_type=row["order_type"],
        limit_price=row["limit_price"],
        protection_required=bool(row["protection_required"]),
        max_loss_budget=float(row["max_loss_budget"]),
        horizon_plan_id=row["horizon_plan_id"],
        config_hash=row["config_hash"],
        runtime_sha=row["runtime_sha"],
        action=row["action"],
    )
    return StoredIntent(
        intent=intent,
        state=IntentState(row["state"]),
        protection=ProtectionState(row["protection"]),
        broker_order_id=row["broker_order_id"],
        filled_quantity=int(row["filled_quantity"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        note=row["note"],
    )
