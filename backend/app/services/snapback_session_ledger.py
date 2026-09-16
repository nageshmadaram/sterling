"""Durable per-session scan record.

Zero opportunities is only evidence when the scanner can prove it looked. Without
this record, "no signal today" and "the scanner never ran", "the broker was
disconnected" or "27 of 200 symbols failed" are indistinguishable — and three of those
four are broken collection masquerading as a quiet market.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

log = logging.getLogger(__name__)


class SessionStatus:
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"


def _build_sha() -> str:
    try:
        from app.services.snapback_identity import build_sha

        return build_sha()
    except Exception:
        return "UNKNOWN"


def record_session_scan(
    warehouse,
    *,
    session_date: str,
    experiment_id: str,
    calendar_version: str,
    universe_expected: int,
    universe_scanned: int,
    symbol_failures: int,
    market_gate_status: str,
    signals_authoritative: int,
    status: str = SessionStatus.COMPLETE,
    scanner_started_at: Optional[str] = None,
    evidence_gap_codes: Optional[Iterable[str]] = None,
) -> None:
    """Upsert the session's scan record. Re-scanning a session updates it."""
    now = datetime.now(timezone.utc).isoformat()
    conn = warehouse._get_connection()
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO prospective_sessions (
                    session_date, experiment_id, runtime_build_sha, calendar_version,
                    universe_expected, universe_scanned, symbol_failures,
                    scanner_started_at, scanner_completed_at, scanner_status,
                    market_gate_status, signals_authoritative, evidence_gap_codes_json,
                    observed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_date) DO UPDATE SET
                    experiment_id = excluded.experiment_id,
                    runtime_build_sha = excluded.runtime_build_sha,
                    calendar_version = excluded.calendar_version,
                    universe_expected = excluded.universe_expected,
                    universe_scanned = excluded.universe_scanned,
                    symbol_failures = excluded.symbol_failures,
                    scanner_started_at = excluded.scanner_started_at,
                    scanner_completed_at = excluded.scanner_completed_at,
                    scanner_status = excluded.scanner_status,
                    market_gate_status = excluded.market_gate_status,
                    signals_authoritative = excluded.signals_authoritative,
                    evidence_gap_codes_json = excluded.evidence_gap_codes_json,
                    observed_at = excluded.observed_at
                """,
                (
                    session_date, experiment_id, _build_sha(), calendar_version,
                    int(universe_expected), int(universe_scanned), int(symbol_failures),
                    scanner_started_at or now, now, status,
                    market_gate_status, int(signals_authoritative),
                    json.dumps(list(evidence_gap_codes or [])), now,
                ),
            )
    finally:
        conn.close()


def update_session_phase(warehouse, session_date: str, **phases: str) -> None:
    """Advance entry/eod/package phase status for a session."""
    allowed = {"entry_phase_status", "eod_phase_status", "package_status"}
    sets = {k: v for k, v in phases.items() if k in allowed}
    if not sets:
        return
    conn = warehouse._get_connection()
    try:
        with conn:
            clause = ", ".join(f"{k} = ?" for k in sets)
            conn.execute(
                f"UPDATE prospective_sessions SET {clause} WHERE session_date = ?",
                (*sets.values(), session_date),
            )
    finally:
        conn.close()


def session_record(warehouse, session_date: str) -> Optional[Dict[str, Any]]:
    conn = warehouse._get_connection()
    try:
        with conn:
            row = conn.execute(
                "SELECT * FROM prospective_sessions WHERE session_date = ?",
                (session_date,),
            ).fetchone()
            return dict(row) if row else None
    finally:
        conn.close()


def evidence_gap_codes(row: Dict[str, Any]) -> List[str]:
    try:
        return list(json.loads(row.get("evidence_gap_codes_json") or "[]"))
    except Exception:
        # An unreadable gap list is itself a gap.
        return ["unreadable_evidence_gap_codes"]


def session_scan_complete(row: Dict[str, Any]) -> bool:
    """Did the SCANNER observe the whole session?

    This answers "must I rescan?" only. It says nothing about whether the trading
    day's entry, end-of-day and packaging phases finished, so it must never be used
    as the economic evidence denominator.
    """
    if not row:
        return False
    if str(row.get("scanner_status")) != SessionStatus.COMPLETE:
        return False
    if int(row.get("symbol_failures") or 0) > 0:
        return False
    expected = int(row.get("universe_expected") or 0)
    scanned = int(row.get("universe_scanned") or 0)
    if not expected or scanned < expected:
        return False
    if not str(row.get("market_gate_status") or ""):
        return False
    return True


def session_evidence_complete(row: Dict[str, Any]) -> bool:
    """Is this session admissible as economic evidence?

    Every phase must have finished and no gap may be recorded. A session whose
    end-of-day mark is missing is not a held-out session, however well it scanned.
    """
    if not session_scan_complete(row):
        return False
    for phase in ("entry_phase_status", "eod_phase_status", "package_status"):
        if str(row.get(phase) or "") != SessionStatus.COMPLETE:
            return False
    return not evidence_gap_codes(row)


def session_is_complete(warehouse, session_date: str) -> bool:
    """Scanner completeness, used by the runner to avoid a duplicate scan."""
    return session_scan_complete(session_record(warehouse, session_date) or {})


def observed_session_count(warehouse) -> int:
    """Fully observed sessions — the denominator the 60-session rule actually means.

    Counted from the session ledger, not from unique trade entry dates: a session
    that produced no signal is still a held-out session if it was fully observed.
    """
    try:
        rows = warehouse.get_records_by_table("prospective_sessions")
    except Exception as exc:
        log.warning("Snapback session ledger: unreadable (%s); counting zero", exc)
        return 0
    return sum(1 for r in rows if session_evidence_complete(dict(r)))


def incomplete_sessions(warehouse) -> List[str]:
    try:
        rows = warehouse.get_records_by_table("prospective_sessions")
    except Exception:
        return []
    return [
        str(dict(r)["session_date"])
        for r in rows
        if not session_evidence_complete(dict(r))
    ]
