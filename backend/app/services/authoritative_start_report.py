"""Read the live stores and answer: has the new forward sample started at zero?

`app.core.authoritative_start` holds the rule. This holds the reading of it —
which table each number comes from, and what to do when one cannot be read.
The split matters because the rule is testable without a database, and every
"could not be read" here becomes an UNKNOWN there rather than a zero.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path
from typing import Any, Mapping

from app.core.authoritative_start import (
    AuthoritativeStart,
    evaluate_authoritative_start,
)

log = logging.getLogger(__name__)

__all__ = ["authoritative_start_state", "DEFAULT_DB"]

DEFAULT_DB = os.environ.get("STERLING_OBSERVATIONS_DB_PATH", "snapback_observations.db")


def _count(conn: sqlite3.Connection, sql: str, args: tuple = ()) -> int | None:
    try:
        row = conn.execute(sql, args).fetchone()
    except Exception as exc:  # noqa: BLE001
        log.warning("authoritative start: %s", exc)
        return None
    return int(row[0]) if row else None


def _sample_rows(conn: sqlite3.Connection, limit: int = 5000) -> list[Mapping[str, Any]]:
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM outcomes WHERE authoritative = 1 LIMIT ?", (limit,)
        ).fetchall()
    except Exception as exc:  # noqa: BLE001
        log.warning("authoritative start: outcomes unreadable: %s", exc)
        return []
    finally:
        conn.row_factory = None
    return [dict(r) for r in rows]


def _unresolved_exposure() -> int | None:
    """Unresolved order intents across every operator the registry knows.

    Unreadable is None, not zero: an exposure count nobody could read is the
    one number that must never be reported as "none".
    """
    from app.services.exposure_snapshot import unresolved_exposure_count

    return unresolved_exposure_count()


def _identity_drift() -> int | None:
    try:
        from app.core.release_manifest import read_manifest, verify_manifest

        stored = read_manifest()
        if stored is None:
            return None
        return len(verify_manifest(stored).identity_drift)
    except Exception as exc:  # noqa: BLE001
        log.warning("authoritative start: manifest unreadable: %s", exc)
        return None


def authoritative_start_state(db_path: str | Path | None = None) -> AuthoritativeStart:
    """Read the stores and judge them against the authoritative-start contract."""
    from app.core.release_manifest import release_tag, runtime_sha

    path = Path(db_path or DEFAULT_DB)
    lane_sessions: int | None = None
    trades: int | None = None
    rows: list[Mapping[str, Any]] = []

    if path.exists():
        try:
            with sqlite3.connect(str(path)) as conn:
                lane_sessions = _count(conn, "SELECT COUNT(*) FROM prospective_sessions")
                trades = _count(
                    conn, "SELECT COUNT(*) FROM outcomes WHERE authoritative = 1")
                rows = _sample_rows(conn)
        except Exception as exc:  # noqa: BLE001
            log.warning("authoritative start: %s unreadable: %s", path, exc)
    # A missing file is not an empty sample: it may be the wrong path. Both
    # counts stay None, which reads as UNKNOWN.

    try:
        tag = release_tag()
    except Exception:  # noqa: BLE001
        tag = ""
    try:
        sha = runtime_sha()
    except Exception:  # noqa: BLE001
        sha = ""

    return evaluate_authoritative_start(
        lane_sessions=lane_sessions,
        authoritative_trades=trades,
        unresolved_exposure=_unresolved_exposure(),
        identity_drift=_identity_drift(),
        release_tag=tag,
        runtime_sha=sha,
        rows=rows,
    )
