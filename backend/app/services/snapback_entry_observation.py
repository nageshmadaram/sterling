"""Proof that the T+1 opening window was actually observed.

"First executable quote" must mean the first one Sterling provably saw, not the first
one it happened to see after starting late. A backend that comes up at 09:40 cannot
know whether a qualifying quote existed at 09:18, so it must not fill — the honest
answer is INCONCLUSIVE_MISSED_OPEN_OBSERVATION.

Monitor state lives in `prospective_sessions` so it survives a restart, and every
entry evaluation is appended to `entry_attempts` whether it filled or refused.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

_IST = timezone(timedelta(hours=5, minutes=30))

from app.engines.snapback.policy import EXECUTION_POLICY

ENTRY_OBSERVATION_MAX_GAP_MS = EXECUTION_POLICY.entry_observation_max_gap_ms

MISSED_OPEN = "INCONCLUSIVE_MISSED_OPEN_OBSERVATION"
OBSERVATION_GAP = "INCONCLUSIVE_ENTRY_OBSERVATION_GAP"

# The monitor must be up within this long after the open to count as "from the open".
OPEN_TOLERANCE_S = 60


@dataclass
class ContinuityVerdict:
    continuous: bool
    reasons: List[str] = field(default_factory=list)


@dataclass
class EntryObservation:
    opportunity_id: str
    session_date: str
    attempt_sequence: int
    continuous: bool
    decision: str
    reason_codes: List[str] = field(default_factory=list)


def _dt(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=_IST)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=_IST)


def continuity_verdict(
    *,
    session_open: datetime,
    monitor_started_at: Optional[datetime],
    last_heartbeat_at: Optional[datetime],
    max_gap_ms: int,
    now: datetime,
) -> ContinuityVerdict:
    """Was the opening window observed continuously up to now?"""
    reasons: List[str] = []

    started = _dt(monitor_started_at)
    if started is None or (started - session_open).total_seconds() > OPEN_TOLERANCE_S:
        reasons.append(MISSED_OPEN)

    if int(max_gap_ms or 0) >= ENTRY_OBSERVATION_MAX_GAP_MS:
        reasons.append(OBSERVATION_GAP)

    beat = _dt(last_heartbeat_at)
    if beat is None:
        reasons.append(OBSERVATION_GAP)
    elif (now - beat).total_seconds() * 1000 >= ENTRY_OBSERVATION_MAX_GAP_MS:
        reasons.append(OBSERVATION_GAP)

    deduped: List[str] = []
    for reason in reasons:
        if reason not in deduped:
            deduped.append(reason)

    return ContinuityVerdict(continuous=not deduped, reasons=deduped)


# --------------------------------------------------------------- monitor state


def start_entry_monitor(warehouse, *, session_date: str, at: Optional[datetime] = None) -> None:
    """Record that observation began. A later start never overwrites the first."""
    at = at or datetime.now(_IST)
    conn = warehouse._get_connection()
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO prospective_sessions (session_date, entry_window_open_at,
                    entry_monitor_started_at, entry_last_heartbeat_at, scanner_status)
                VALUES (?, ?, ?, ?, 'PENDING')
                ON CONFLICT(session_date) DO UPDATE SET
                    entry_monitor_started_at = COALESCE(
                        prospective_sessions.entry_monitor_started_at, excluded.entry_monitor_started_at
                    ),
                    entry_window_open_at = COALESCE(
                        prospective_sessions.entry_window_open_at, excluded.entry_window_open_at
                    ),
                    entry_last_heartbeat_at = excluded.entry_last_heartbeat_at
                """,
                (session_date, at.isoformat(), at.isoformat(), at.isoformat()),
            )
    finally:
        conn.close()


def heartbeat_entry_monitor(warehouse, *, session_date: str, at: Optional[datetime] = None) -> None:
    """Record that observation is still running, and measure any gap since the last."""
    at = at or datetime.now(_IST)
    conn = warehouse._get_connection()
    try:
        with conn:
            row = conn.execute(
                "SELECT entry_last_heartbeat_at, entry_gap_count, entry_max_gap_ms "
                "FROM prospective_sessions WHERE session_date = ?",
                (session_date,),
            ).fetchone()

            gap_count = int(row["entry_gap_count"]) if row else 0
            max_gap = int(row["entry_max_gap_ms"]) if row else 0
            previous = _dt(row["entry_last_heartbeat_at"]) if row else None

            if previous is not None:
                gap_ms = int((at - previous).total_seconds() * 1000)
                if gap_ms >= ENTRY_OBSERVATION_MAX_GAP_MS:
                    gap_count += 1
                max_gap = max(max_gap, gap_ms)

            conn.execute(
                """
                INSERT INTO prospective_sessions (session_date, entry_last_heartbeat_at,
                    entry_gap_count, entry_max_gap_ms, scanner_status)
                VALUES (?, ?, ?, ?, 'PENDING')
                ON CONFLICT(session_date) DO UPDATE SET
                    entry_last_heartbeat_at = excluded.entry_last_heartbeat_at,
                    entry_gap_count = excluded.entry_gap_count,
                    entry_max_gap_ms = excluded.entry_max_gap_ms
                """,
                (session_date, at.isoformat(), gap_count, max_gap),
            )
    finally:
        conn.close()


def entry_monitor_state(warehouse, session_date: str) -> Dict[str, Any]:
    conn = warehouse._get_connection()
    try:
        with conn:
            row = conn.execute(
                "SELECT * FROM prospective_sessions WHERE session_date = ?",
                (session_date,),
            ).fetchone()
            return dict(row) if row else {}
    finally:
        conn.close()


def session_continuity(
    warehouse, *, session_date: str, session_open: datetime, now: Optional[datetime] = None
) -> ContinuityVerdict:
    """Continuity for this session, read from durable state."""
    state = entry_monitor_state(warehouse, session_date)
    return continuity_verdict(
        session_open=session_open,
        monitor_started_at=state.get("entry_monitor_started_at"),
        last_heartbeat_at=state.get("entry_last_heartbeat_at"),
        max_gap_ms=int(state.get("entry_max_gap_ms") or 0),
        now=now or datetime.now(_IST),
    )


def record_attempt(
    warehouse,
    *,
    opportunity_id: str,
    session_date: str,
    continuous: bool,
    decision: str,
    reason_codes: Optional[List[str]] = None,
    at: Optional[datetime] = None,
    **ids: str,
) -> EntryObservation:
    """Append one entry evaluation to the attempt ledger."""
    at = at or datetime.now(_IST)
    sequence = warehouse.next_attempt_sequence(opportunity_id)
    warehouse.record_entry_attempt(
        attempt_id=f"ATT-{opportunity_id}-{sequence}",
        opportunity_id=opportunity_id,
        session_date=session_date,
        attempt_sequence=sequence,
        attempted_at=at.isoformat(),
        monitoring_continuous_from_open=continuous,
        decision=decision,
        reason_codes=reason_codes or [],
        underlying_quote_event_id=ids.get("underlying_quote_event_id", ""),
        option_quote_event_id=ids.get("option_quote_event_id", ""),
        futures_quote_event_id=ids.get("futures_quote_event_id", ""),
        candidate_set_id=ids.get("candidate_set_id", ""),
    )
    return EntryObservation(
        opportunity_id=opportunity_id,
        session_date=session_date,
        attempt_sequence=sequence,
        continuous=continuous,
        decision=decision,
        reason_codes=list(reason_codes or []),
    )
