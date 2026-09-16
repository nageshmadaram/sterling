"""Decision history is an append-only audit trail.

Recovery and re-evaluation must add an event, never destroy the previous one. The
shadow replay hit the old UNIQUE(opportunity_id) constraint, which forced the choice
between deleting history and failing the entry.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from app.services.snapback_observation_warehouse import SnapbackObservationWarehouse


@pytest.fixture
def warehouse():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as fh:
        path = fh.name
    wh = SnapbackObservationWarehouse(db_path=path)
    yield wh
    if os.path.exists(path):
        os.remove(path)


def _record(wh, decision_id, opp, decision, reason, **kw):
    wh.record_decision(
        decision_id=decision_id,
        opportunity_id=opp,
        symbol="NIFTY",
        decision=decision,
        reason=reason,
        provider_timestamp="2026-09-16T09:20:00+05:30",
        **kw,
    )


def test_repeat_decisions_append_rather_than_fail(warehouse):
    _record(warehouse, "D-1", "OPP-1", "INCONCLUSIVE", "missed window")
    _record(warehouse, "D-2", "OPP-1", "FILLED", "entered on retry")

    rows = warehouse.get_records_by_table("decisions", opportunity_id="OPP-1")

    assert len(rows) == 2
    assert {r["decision"] for r in rows} == {"INCONCLUSIVE", "FILLED"}


def test_history_is_never_destroyed_by_a_later_decision(warehouse):
    _record(warehouse, "D-1", "OPP-1", "INCONCLUSIVE", "stale futures quote")
    _record(warehouse, "D-2", "OPP-1", "NO_FILL", "spread too wide")
    _record(warehouse, "D-3", "OPP-1", "FILLED", "filled at ask")

    rows = warehouse.get_records_by_table("decisions", opportunity_id="OPP-1")
    reasons = [r["reason"] for r in rows]

    assert len(rows) == 3
    assert "stale futures quote" in reasons


def test_decision_id_remains_the_primary_key(warehouse):
    _record(warehouse, "D-1", "OPP-1", "INCONCLUSIVE", "first")
    # Re-recording the same decision id is an idempotent replay, not a new event.
    _record(warehouse, "D-1", "OPP-1", "INCONCLUSIVE", "first")

    rows = warehouse.get_records_by_table("decisions", opportunity_id="OPP-1")
    assert len(rows) == 1


def test_decisions_are_queryable_per_opportunity(warehouse):
    _record(warehouse, "D-1", "OPP-1", "FILLED", "a")
    _record(warehouse, "D-2", "OPP-2", "NO_FILL", "b")

    assert len(warehouse.get_records_by_table("decisions", opportunity_id="OPP-1")) == 1
    assert len(warehouse.get_records_by_table("decisions", opportunity_id="OPP-2")) == 1


def test_decision_rows_carry_identity_and_authority_columns(warehouse):
    _record(warehouse, "D-1", "OPP-1", "FILLED", "a")

    row = warehouse.get_records_by_table("decisions", opportunity_id="OPP-1")[0]

    assert "authoritative" in row.keys() if hasattr(row, "keys") else "authoritative" in row
    assert "runtime_build_sha" in (row.keys() if hasattr(row, "keys") else row)


def test_legacy_unique_constraint_is_migrated_away(warehouse):
    import sqlite3

    conn = sqlite3.connect(warehouse.db_path)
    try:
        sql = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='decisions'"
        ).fetchone()[0]
    finally:
        conn.close()

    assert "UNIQUE" not in sql.upper().replace("UNIQUE(DECISION_ID)", "")
