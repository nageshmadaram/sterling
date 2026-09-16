"""Item 4a: plans are durable artifacts, not in-flight messages.

A plan held only in memory cannot be armed safely. A restart between publishing
and arming loses the thing the operator approved, and a browser retry can arm the
same plan twice — two authorizations for one decision. Here a plan is a row with
a revision, and arming is a transition against that revision: a stale client
loses, and a retry with the same idempotency key replays rather than repeats.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)

DEFAULT_PLAN_DB_PATH = os.path.join("data", "snapback", "plans.db")

STATUS_PUBLISHED = "PUBLISHED"
STATUS_ARMED = "ARMED"
STATUS_CANCELLED = "CANCELLED"
STATUS_CONSUMED = "CONSUMED"


class PlanStoreError(RuntimeError):
    """Base class, so a caller can refuse everything at once."""


class PlanConflictError(PlanStoreError):
    """The plan is not in the state the caller believed it was."""


class PlanExpiredError(PlanStoreError):
    """The plan's executable window has passed.

    Separate from a conflict: an expired plan was valid and is now stale, which
    is a different thing for an operator to read than a revision mismatch.
    """


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class PlanStore:
    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = db_path or os.environ.get(
            "STERLING_PLAN_DB_PATH", DEFAULT_PLAN_DB_PATH
        )
        parent = os.path.dirname(self.db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=15.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=15000")
        return conn

    def _init_db(self) -> None:
        conn = self._connect()
        try:
            with conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS snapback_plans (
                        plan_id                 TEXT PRIMARY KEY,
                        opportunity_id          TEXT NOT NULL,
                        account_id              TEXT NOT NULL,
                        status                  TEXT NOT NULL,
                        revision                INTEGER NOT NULL,
                        option_symbol           TEXT NOT NULL,
                        option_quantity         INTEGER NOT NULL,
                        futures_symbol          TEXT NOT NULL DEFAULT '',
                        target_futures_quantity INTEGER NOT NULL DEFAULT 0,
                        max_option_price        REAL NOT NULL DEFAULT 0.0,
                        hedge_price_limit       REAL NOT NULL DEFAULT 0.0,
                        risk_amount             REAL NOT NULL DEFAULT 0.0,
                        cash_required           REAL NOT NULL DEFAULT 0.0,
                        margin_required         REAL NOT NULL DEFAULT 0.0,
                        policy_snapshot_hash    TEXT NOT NULL DEFAULT '',
                        runtime_build_sha       TEXT NOT NULL DEFAULT '',
                        mode                    TEXT NOT NULL DEFAULT 'PAPER',
                        expires_at              TEXT,
                        armed_at                TEXT,
                        armed_by                TEXT,
                        arm_idempotency_key     TEXT,
                        cancelled_at            TEXT,
                        cancel_reason           TEXT,
                        published_at            TEXT NOT NULL,
                        updated_at              TEXT NOT NULL,
                        extra_json              TEXT NOT NULL DEFAULT '{}'
                    )
                """)
                # Every transition, kept. An armed plan that was later cancelled
                # must not read as though it was never armed.
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS snapback_plan_events (
                        event_id     INTEGER PRIMARY KEY AUTOINCREMENT,
                        plan_id      TEXT NOT NULL,
                        event        TEXT NOT NULL,
                        revision     INTEGER NOT NULL,
                        actor        TEXT,
                        detail_json  TEXT NOT NULL DEFAULT '{}',
                        recorded_at  TEXT NOT NULL
                    )
                """)
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS ix_plan_events_plan "
                    "ON snapback_plan_events(plan_id)"
                )
        finally:
            conn.close()

    # ------------------------------------------------------------- reading

    def get(self, plan_id: str) -> Optional[Dict[str, Any]]:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT * FROM snapback_plans WHERE plan_id = ?", (plan_id,)
            ).fetchone()
        finally:
            conn.close()
        return dict(row) if row is not None else None

    def events(self, plan_id: str) -> list:
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT * FROM snapback_plan_events WHERE plan_id = ? ORDER BY event_id",
                (plan_id,),
            ).fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------- writing

    def publish(
        self,
        *,
        plan_id: str,
        opportunity_id: str,
        account_id: str,
        option_symbol: str,
        option_quantity: int,
        futures_symbol: str = "",
        target_futures_quantity: int = 0,
        max_option_price: float = 0.0,
        hedge_price_limit: float = 0.0,
        risk_amount: float = 0.0,
        cash_required: float = 0.0,
        margin_required: float = 0.0,
        policy_snapshot_hash: str = "",
        runtime_build_sha: str = "",
        mode: str = "PAPER",
        expires_at: Optional[str] = None,
        **extra: Any,
    ) -> Dict[str, Any]:
        """Record a plan at revision 1. Republishing the same id is refused:
        a plan is a specific proposal, and silently replacing one an operator may
        already be looking at would change what they are about to approve."""
        now = _now_iso()
        conn = self._connect()
        try:
            with conn:
                try:
                    conn.execute(
                        """
                        INSERT INTO snapback_plans (
                            plan_id, opportunity_id, account_id, status, revision,
                            option_symbol, option_quantity, futures_symbol,
                            target_futures_quantity, max_option_price, hedge_price_limit,
                            risk_amount, cash_required, margin_required,
                            policy_snapshot_hash, runtime_build_sha, mode, expires_at,
                            published_at, updated_at, extra_json
                        ) VALUES (?,?,?,?,1,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            plan_id, opportunity_id, account_id, STATUS_PUBLISHED,
                            option_symbol, int(option_quantity), futures_symbol,
                            int(target_futures_quantity), float(max_option_price),
                            float(hedge_price_limit), float(risk_amount),
                            float(cash_required), float(margin_required),
                            policy_snapshot_hash, runtime_build_sha, mode, expires_at,
                            now, now, json.dumps(extra, default=str),
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    raise PlanConflictError(
                        f"plan {plan_id} already exists"
                    ) from exc
                self._record_event(conn, plan_id, "PUBLISHED", 1, None, {})
        finally:
            conn.close()
        return self.get(plan_id) or {}

    def arm(
        self,
        plan_id: str,
        *,
        expected_revision: int,
        idempotency_key: str,
        armed_by: str,
        mode: str = "PAPER",
        now: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Authorize this plan for execution.

        The revision check is what stops a stale client arming a plan it has not
        seen the current version of. The idempotency key is what stops a browser
        retry becoming a second authorization.
        """
        plan = self.get(plan_id)
        if plan is None:
            raise PlanConflictError(f"plan {plan_id} does not exist")

        # A retry of the arm that already succeeded is a replay, not a repeat.
        if (
            plan["status"] == STATUS_ARMED
            and plan.get("arm_idempotency_key") == idempotency_key
        ):
            replay = dict(plan)
            replay["idempotent_replay"] = True
            return replay

        if plan["status"] != STATUS_PUBLISHED:
            raise PlanConflictError(
                f"plan {plan_id} is {plan['status']}, not {STATUS_PUBLISHED}"
            )

        if int(plan["revision"]) != int(expected_revision):
            raise PlanConflictError(
                f"plan {plan_id} is at revision {plan['revision']}, "
                f"not {expected_revision}"
            )

        expires_at = plan.get("expires_at")
        if expires_at:
            moment = now or datetime.now(timezone.utc)
            try:
                deadline = datetime.fromisoformat(expires_at)
            except ValueError:
                deadline = None
            if deadline is not None and moment >= deadline:
                raise PlanExpiredError(
                    f"plan {plan_id} expired at {expires_at}"
                )

        revision = int(plan["revision"]) + 1
        armed_at = _now_iso()
        conn = self._connect()
        try:
            with conn:
                changed = conn.execute(
                    """
                    UPDATE snapback_plans
                       SET status = ?, revision = ?, armed_at = ?, armed_by = ?,
                           arm_idempotency_key = ?, mode = ?, updated_at = ?
                     WHERE plan_id = ? AND revision = ?
                    """,
                    (
                        STATUS_ARMED, revision, armed_at, armed_by, idempotency_key,
                        mode, armed_at, plan_id, int(expected_revision),
                    ),
                ).rowcount
                if changed != 1:
                    # Somebody else moved it between the read and the write.
                    raise PlanConflictError(f"plan {plan_id} changed during arming")
                self._record_event(
                    conn, plan_id, "ARMED", revision, armed_by,
                    {"mode": mode, "idempotency_key": idempotency_key},
                )
        finally:
            conn.close()

        log.warning("Snapback plan %s ARMED by %s (mode=%s)", plan_id, armed_by, mode)
        armed = self.get(plan_id) or {}
        armed["idempotent_replay"] = False
        return armed

    def consume(self, plan_id: str, *, actor: str = "executor") -> Dict[str, Any]:
        """Mark an armed plan as spent.

        Consumption is what stops a retry opening a second position, so it is
        recorded durably rather than held in the executor's memory.
        """
        plan = self.get(plan_id)
        if plan is None:
            raise PlanConflictError(f"plan {plan_id} does not exist")

        revision = int(plan["revision"]) + 1
        now = _now_iso()
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    "UPDATE snapback_plans SET status = ?, revision = ?, "
                    "updated_at = ? WHERE plan_id = ?",
                    (STATUS_CONSUMED, revision, now, plan_id),
                )
                self._record_event(conn, plan_id, "CONSUMED", revision, actor, {})
        finally:
            conn.close()
        return self.get(plan_id) or {}

    def cancel(self, plan_id: str, *, reason: str, actor: str = "system") -> Dict[str, Any]:
        plan = self.get(plan_id)
        if plan is None:
            raise PlanConflictError(f"plan {plan_id} does not exist")

        revision = int(plan["revision"]) + 1
        now = _now_iso()
        conn = self._connect()
        try:
            with conn:
                conn.execute(
                    """
                    UPDATE snapback_plans
                       SET status = ?, revision = ?, cancelled_at = ?,
                           cancel_reason = ?, updated_at = ?
                     WHERE plan_id = ?
                    """,
                    (STATUS_CANCELLED, revision, now, reason, now, plan_id),
                )
                self._record_event(
                    conn, plan_id, "CANCELLED", revision, actor, {"reason": reason},
                )
        finally:
            conn.close()
        return self.get(plan_id) or {}

    def _record_event(
        self, conn: sqlite3.Connection, plan_id: str, event: str,
        revision: int, actor: Optional[str], detail: Dict[str, Any],
    ) -> None:
        conn.execute(
            """
            INSERT INTO snapback_plan_events
                (plan_id, event, revision, actor, detail_json, recorded_at)
            VALUES (?,?,?,?,?,?)
            """,
            (plan_id, event, revision, actor, json.dumps(detail, default=str), _now_iso()),
        )
