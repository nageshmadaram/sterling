"""Lane attribution on evidence rows, and the additive migration that adds it.

Two things must hold at once: new rows can name their lane, and old rows are
not retroactively given one. A back-filled lane label is a fabricated
attribution, and it would silently inflate the first lane anybody reported.
"""
from __future__ import annotations

import sqlite3

import pytest

from app.core.evidence import (
    PROMOTABLE_CLASSES,
    UNATTRIBUTED_LANE,
    EvidenceClass,
    eligible_for_lane,
    is_promotable_class,
    partition_by_lane,
    row_lane,
)
from app.services.snapback_observation_warehouse import (
    EVIDENCE_SCHEMA_VERSION,
    SnapbackObservationWarehouse,
)

LANE = "snapback:swing"


def _row(**over):
    row = {
        "lane_key": LANE,
        "authoritative": 1,
        "evidence_class": EvidenceClass.PAPER.value,
    }
    row.update(over)
    return row


# ── evidence classes ──────────────────────────────────────────────────────


def test_only_observed_classes_are_promotable():
    assert PROMOTABLE_CLASSES == {
        EvidenceClass.PAPER,
        EvidenceClass.SHADOW,
        EvidenceClass.BROKER,
    }


@pytest.mark.parametrize("value", ["modelled", "replay", "acceptance"])
def test_research_and_certification_classes_are_not_promotable(value):
    """A modelled fill measures the model; acceptance certifies the runtime."""
    assert is_promotable_class(value) is False


@pytest.mark.parametrize("value", [None, "", "paper_ish", "BROKER "])
def test_an_unknown_class_is_not_promotable(value):
    assert is_promotable_class(value) is False


# ── lane eligibility ──────────────────────────────────────────────────────


def test_a_matching_authoritative_paper_row_is_eligible():
    assert eligible_for_lane(_row(), LANE) is True


def test_a_row_from_another_lane_never_counts():
    assert eligible_for_lane(_row(lane_key="snapback:scalping"), LANE) is False
    assert eligible_for_lane(_row(lane_key="supertrend:swing"), LANE) is False


def test_a_legacy_row_with_no_lane_never_counts():
    """The whole point of the empty default."""
    assert eligible_for_lane(_row(lane_key=UNATTRIBUTED_LANE), LANE) is False
    assert eligible_for_lane(_row(lane_key=None), LANE) is False
    assert row_lane(_row(lane_key="")) is None


def test_a_non_authoritative_row_never_counts():
    assert eligible_for_lane(_row(authoritative=0), LANE) is False


def test_a_modelled_row_never_counts_toward_a_forward_gate():
    assert eligible_for_lane(_row(evidence_class="modelled"), LANE) is False


def test_partition_reports_unattributed_rows_rather_than_dropping_them():
    """A silent drop makes an incomplete sample look complete."""
    rows = [
        _row(),
        _row(lane_key="supertrend:swing"),
        _row(lane_key=""),
        _row(lane_key=""),
    ]
    lanes, unattributed = partition_by_lane(rows)
    assert set(lanes) == {LANE, "supertrend:swing"}
    assert len(unattributed) == 2


# ── migration ─────────────────────────────────────────────────────────────


def _legacy_db(path) -> None:
    """A database as it existed before the taxonomy, holding one real row."""
    conn = sqlite3.connect(path)
    with conn:
        conn.execute(
            """
            CREATE TABLE outcomes (
                outcome_id TEXT PRIMARY KEY,
                opportunity_id TEXT NOT NULL UNIQUE,
                exit_reason TEXT NOT NULL,
                entry_ts TEXT NOT NULL,
                exit_ts TEXT NOT NULL,
                actual_total_pnl REAL NOT NULL DEFAULT 0.0,
                observed_at TEXT NOT NULL,
                received_at TEXT NOT NULL,
                symbol TEXT NOT NULL
            )
            """
        )
        conn.execute(
            "INSERT INTO outcomes VALUES "
            "('O1','OPP1','target','2026-09-01T10:00','2026-09-04T10:00',"
            "1234.5,'2026-09-04T10:00','2026-09-04T10:00','NIFTY')"
        )
    conn.close()


def test_migration_is_additive_and_preserves_the_old_row(tmp_path):
    db = tmp_path / "legacy.db"
    _legacy_db(db)

    SnapbackObservationWarehouse(db_path=str(db)).init_db()

    conn = sqlite3.connect(db)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM outcomes").fetchall()
        assert len(rows) == 1
        row = dict(rows[0])
        # Untouched.
        assert row["outcome_id"] == "O1"
        assert row["actual_total_pnl"] == 1234.5
        assert row["exit_reason"] == "target"
        # Newly present, and empty — not guessed.
        assert row["lane_key"] == ""
        assert row["strategy_id"] == ""
        assert row["mode"] == ""
        assert row["evidence_class"] == ""
        assert row["legacy_mode"] is None
        assert row["horizon_plan_id"] == ""
        assert row["hard_exit_at"] is None
        assert row["sessions_held_at_exit"] is None
        assert row["timeline_state_at_exit"] is None
    finally:
        conn.close()


def test_the_preserved_legacy_row_is_ineligible_for_every_lane(tmp_path):
    db = tmp_path / "legacy.db"
    _legacy_db(db)
    SnapbackObservationWarehouse(db_path=str(db)).init_db()

    conn = sqlite3.connect(db)
    try:
        conn.row_factory = sqlite3.Row
        row = dict(conn.execute("SELECT * FROM outcomes").fetchone())
    finally:
        conn.close()

    for lane in ("snapback:swing", "snapback:scalping", "supertrend:swing"):
        assert eligible_for_lane(row, lane) is False


def test_migration_is_idempotent(tmp_path):
    db = tmp_path / "legacy.db"
    _legacy_db(db)
    warehouse = SnapbackObservationWarehouse(db_path=str(db))
    warehouse.init_db()
    warehouse.init_db()

    conn = sqlite3.connect(db)
    try:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(outcomes)")]
        assert len(cols) == len(set(cols)), "a column was added twice"
        assert conn.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0] == 1
        assert conn.execute("PRAGMA user_version").fetchone()[0] == (
            EVIDENCE_SCHEMA_VERSION
        )
    finally:
        conn.close()


def test_a_fresh_database_carries_lane_and_horizon_columns(tmp_path):
    db = tmp_path / "fresh.db"
    SnapbackObservationWarehouse(db_path=str(db)).init_db()

    conn = sqlite3.connect(db)
    try:
        for table in ("opportunities", "decisions", "costs", "outcomes"):
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            assert {"lane_key", "mode", "identity_hash", "evidence_class"} <= cols, table
        position_cols = {
            r[1] for r in conn.execute("PRAGMA table_info(paper_positions)")
        }
        assert {"horizon_plan_id", "hard_exit_at", "hard_exit_session"} <= position_cols
        outcome_cols = {r[1] for r in conn.execute("PRAGMA table_info(outcomes)")}
        assert {
            "age_at_exit_seconds",
            "sessions_held_at_exit",
            "timeline_state_at_exit",
        } <= outcome_cols
    finally:
        conn.close()


def test_sessions_held_at_exit_does_not_collide_with_the_legacy_counter(tmp_path):
    """Two different numbers, so two different columns.

    ``paper_positions.sessions_held`` is inclusive of both ends with a floor of
    1; ``sessions_held_at_exit`` is sessions elapsed since entry, 0 on the
    entry day. Reusing one name would make a one-day trade read as both 0 and 1.
    """
    db = tmp_path / "fresh.db"
    SnapbackObservationWarehouse(db_path=str(db)).init_db()
    conn = sqlite3.connect(db)
    try:
        position_cols = {
            r[1] for r in conn.execute("PRAGMA table_info(paper_positions)")
        }
        assert "sessions_held" in position_cols
        outcome_cols = {r[1] for r in conn.execute("PRAGMA table_info(outcomes)")}
        assert "sessions_held_at_exit" in outcome_cols
    finally:
        conn.close()
