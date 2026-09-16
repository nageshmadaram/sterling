"""Durable alert outbox.

Creating an alert and delivering it are different facts. Scheduling an async send and
returning is not delivery: a network failure after that point is invisible, so the
dispatcher counts a notification nobody received — and one silently lost CRITICAL is
enough to make every other alert untrustworthy.

Every alert is persisted first, claimed by a worker, and only marked DELIVERED when
the transport actually said so.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

log = logging.getLogger(__name__)

_IST = timezone(timedelta(hours=5, minutes=30))


class OutboxStatus:
    PENDING = "PENDING"
    DELIVERING = "DELIVERING"
    DELIVERED = "DELIVERED"
    RETRY = "RETRY"
    DEAD = "DEAD"


# Bounded retry: a transport that has failed this many times needs a human, not
# another attempt.
BACKOFF_SECONDS = (0, 30, 120, 300, 900, 1800)
MAX_ATTEMPTS = len(BACKOFF_SECONDS)


@dataclass
class OutboxResult:
    delivered: int = 0
    failed: int = 0
    skipped: int = 0
    dead: int = 0


def dedupe_key_for(*, code: str, trading_session: Optional[str] = None,
                   entity: Optional[str] = None) -> str:
    """One fault, one session, one entity: one alert."""
    parts = [code]
    if trading_session:
        parts.append(trading_session)
    if entity:
        parts.append(entity)
    return ":".join(parts)


def _iso(value: datetime) -> str:
    return value.isoformat()


class AlertOutbox:
    def __init__(self, warehouse=None) -> None:
        if warehouse is None:
            from app.services.snapback_prospective_collector import SnapbackObservationWarehouse

            warehouse = SnapbackObservationWarehouse()
        self.warehouse = warehouse

    # ---------------------------------------------------------------- enqueue

    def enqueue(
        self,
        alert: Any,
        *,
        trading_session: Optional[str] = None,
        opportunity_id: Optional[str] = None,
        now: Optional[datetime] = None,
    ) -> Optional[str]:
        """Persist one alert for delivery. A duplicate in the same session is dropped."""
        now = now or datetime.now(timezone.utc)
        code = str(getattr(alert, "code", "") or "")
        dedupe = dedupe_key_for(
            code=code, trading_session=trading_session, entity=opportunity_id,
        )

        payload = {
            "code": code,
            "severity": str(getattr(alert, "severity", "WARNING") or "WARNING"),
            "title": str(getattr(alert, "title", "") or ""),
            "message": str(getattr(alert, "message", "") or ""),
            "trading_session": trading_session,
            "opportunity_id": opportunity_id,
        }
        payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        payload_hash = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
        alert_id = f"ALERT-{uuid.uuid4().hex}"

        conn = self.warehouse._get_connection()
        try:
            with conn:
                existing = conn.execute(
                    "SELECT alert_id FROM operational_alert_outbox WHERE dedupe_key = ?",
                    (dedupe,),
                ).fetchone()
                if existing is not None:
                    return None

                conn.execute(
                    """
                    INSERT INTO operational_alert_outbox (
                        alert_id, dedupe_key, code, severity, trading_session,
                        opportunity_id, payload_json, created_at, available_after,
                        status, payload_hash
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        alert_id, dedupe, code, payload["severity"], trading_session,
                        opportunity_id, payload_json, _iso(now), _iso(now),
                        OutboxStatus.PENDING, payload_hash,
                    ),
                )
        finally:
            conn.close()

        return alert_id

    # ---------------------------------------------------------------- process

    async def process(
        self,
        *,
        send: Callable[[Dict[str, Any]], Awaitable[Any]],
        now: Optional[datetime] = None,
        limit: int = 20,
    ) -> OutboxResult:
        """Attempt delivery of every due item, recording what actually happened."""
        now = now or datetime.now(timezone.utc)
        result = OutboxResult()

        due = self._claim_due(now=now, limit=limit)
        for row in due:
            payload = json.loads(row["payload_json"])
            attempt = int(row["attempt_count"] or 0) + 1
            try:
                message_id = await send(payload)
            except Exception as exc:
                # The transport failed: this is recorded as a failure, never as a
                # delivery, and retried with a bounded backoff.
                status = OutboxStatus.RETRY if attempt < MAX_ATTEMPTS else OutboxStatus.DEAD
                if status == OutboxStatus.DEAD:
                    result.dead += 1
                    log.error(
                        "Alert %s exhausted delivery attempts and is DEAD: %s",
                        row["code"], exc,
                    )
                self._record_failure(row["alert_id"], attempt, str(exc), status, now)
                result.failed += 1
                continue

            self._record_success(row["alert_id"], attempt, str(message_id or ""), now)
            result.delivered += 1

        return result

    def _claim_due(self, *, now: datetime, limit: int) -> List[Dict[str, Any]]:
        conn = self.warehouse._get_connection()
        try:
            with conn:
                rows = conn.execute(
                    """
                    SELECT * FROM operational_alert_outbox
                    WHERE status IN (?, ?) AND available_after <= ?
                    ORDER BY created_at ASC LIMIT ?
                    """,
                    (OutboxStatus.PENDING, OutboxStatus.RETRY, _iso(now), int(limit)),
                ).fetchall()
                claimed = [dict(r) for r in rows]
                for row in claimed:
                    conn.execute(
                        "UPDATE operational_alert_outbox SET status = ? WHERE alert_id = ?",
                        (OutboxStatus.DELIVERING, row["alert_id"]),
                    )
                return claimed
        finally:
            conn.close()

    def _record_success(self, alert_id: str, attempt: int, message_id: str,
                        now: datetime) -> None:
        conn = self.warehouse._get_connection()
        try:
            with conn:
                conn.execute(
                    """
                    UPDATE operational_alert_outbox
                    SET status = ?, delivered_at = ?, delivery_message_id = ?,
                        attempt_count = ?, last_attempt_at = ?, last_error = NULL
                    WHERE alert_id = ?
                    """,
                    (OutboxStatus.DELIVERED, _iso(now), message_id, attempt,
                     _iso(now), alert_id),
                )
        finally:
            conn.close()

    def _record_failure(self, alert_id: str, attempt: int, error: str, status: str,
                        now: datetime) -> None:
        backoff = BACKOFF_SECONDS[min(attempt, len(BACKOFF_SECONDS) - 1)]
        available_after = now + timedelta(seconds=backoff)
        conn = self.warehouse._get_connection()
        try:
            with conn:
                conn.execute(
                    """
                    UPDATE operational_alert_outbox
                    SET status = ?, attempt_count = ?, last_attempt_at = ?,
                        last_error = ?, available_after = ?
                    WHERE alert_id = ?
                    """,
                    (status, attempt, _iso(now), error, _iso(available_after), alert_id),
                )
        finally:
            conn.close()

    # ----------------------------------------------------------------- health

    def health(self) -> Dict[str, Any]:
        conn = self.warehouse._get_connection()
        try:
            with conn:
                rows = conn.execute(
                    "SELECT status, COUNT(*) AS n FROM operational_alert_outbox GROUP BY status"
                ).fetchall()
                counts = {str(r["status"]): int(r["n"]) for r in rows}

                last_ok = conn.execute(
                    "SELECT delivered_at FROM operational_alert_outbox "
                    "WHERE delivered_at IS NOT NULL ORDER BY delivered_at DESC LIMIT 1"
                ).fetchone()
                last_err = conn.execute(
                    "SELECT last_error, last_attempt_at FROM operational_alert_outbox "
                    "WHERE last_error IS NOT NULL ORDER BY last_attempt_at DESC LIMIT 1"
                ).fetchone()
        finally:
            conn.close()

        return {
            "alert_outbox_pending": counts.get(OutboxStatus.PENDING, 0)
            + counts.get(OutboxStatus.RETRY, 0)
            + counts.get(OutboxStatus.DELIVERING, 0),
            "alert_outbox_dead": counts.get(OutboxStatus.DEAD, 0),
            "last_alert_delivery_success": last_ok["delivered_at"] if last_ok else None,
            "last_alert_delivery_error": last_err["last_error"] if last_err else None,
            "last_alert_delivery_at": last_err["last_attempt_at"] if last_err else None,
        }


def morning_login_alert(
    outbox: AlertOutbox,
    *,
    broker_connected: bool,
    now: datetime,
    trading_session: str,
) -> None:
    """Warn before the open, escalate if the session is about to start regardless.

    Keyed by session so yesterday's alert cannot suppress this morning's.
    """
    from app.services.snapback_alerts import CRITICAL, WARNING, OperationalAlert

    if broker_connected:
        return

    local = now.astimezone(_IST) if now.tzinfo else now
    minutes = local.hour * 60 + local.minute

    if minutes >= 9 * 60 + 10:
        outbox.enqueue(
            OperationalAlert(
                code="broker_login_required_critical", severity=CRITICAL,
                title="Broker login required NOW",
                message="The session is about to start and Sterling is not connected "
                        "to the broker. No entry can be observed until you log in.",
            ),
            trading_session=trading_session, now=now,
        )
    elif minutes >= 8 * 60 + 45:
        outbox.enqueue(
            OperationalAlert(
                code="broker_login_required", severity=WARNING,
                title="Broker login required before today's session",
                message="Log in to the broker before 09:15 IST.",
            ),
            trading_session=trading_session, now=now,
        )
