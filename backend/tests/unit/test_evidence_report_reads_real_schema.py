"""The report must read the schema that exists, and say so when it cannot.

The failure this guards against already happened: the loader named tables that
do not exist and filtered on a column the real tables do not have, so a
populated evidence store produced a report of zeros — indistinguishable from a
quiet day.
"""
from __future__ import annotations

import sqlite3

import pytest

from app.core.evidence_completeness import (
    SOURCE_TABLES,
    build_daily_report,
    load_session_rows,
    normalise_row,
    verify_source_tables,
)
from app.services.snapback_observation_warehouse import SnapbackObservationWarehouse


@pytest.fixture()
def warehouse(tmp_path):
    return SnapbackObservationWarehouse(str(tmp_path / "evidence.db"))


class TestSchemaAgreement:
    def test_every_declared_source_table_exists(self, warehouse):
        # If this fails, the report is reading tables nobody writes.
        assert verify_source_tables(warehouse) == ()

    def test_every_declared_date_column_exists(self, warehouse):
        conn = warehouse._get_connection()
        try:
            for table, spec in SOURCE_TABLES.items():
                columns = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
                for column in spec["date_columns"]:
                    assert column in columns, f"{table}.{column} does not exist"
                for key in ("refusal_column", "pnl_column", "status_column"):
                    named = spec.get(key)
                    if named:
                        assert named in columns, f"{table}.{named} does not exist"
        finally:
            conn.close()

    def test_every_lane_attributed_table_carries_lane_key(self, warehouse):
        conn = warehouse._get_connection()
        try:
            for table in SOURCE_TABLES:
                columns = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
                assert "lane_key" in columns, f"{table} cannot be attributed to a lane"
        finally:
            conn.close()

    def test_a_renamed_table_is_reported_as_a_gap(self, warehouse):
        conn = warehouse._get_connection()
        try:
            conn.execute("ALTER TABLE outcomes RENAME TO outcomes_old")
            conn.commit()
        finally:
            conn.close()

        gaps = verify_source_tables(warehouse)

        assert gaps == ("evidence_table_missing:outcomes",)


class TestNormalisation:
    def test_an_outcome_belongs_to_the_session_it_exited_in(self):
        row = normalise_row(
            {
                "lane_key": "snapback:swing",
                "entry_ts": "2026-09-01T10:00:00+05:30",
                "exit_ts": "2026-09-08T14:30:00+05:30",
                "actual_total_pnl": 120.0,
                "authoritative": 1,
                "evidence_class": "paper",
            },
            table="outcomes",
            spec=SOURCE_TABLES["outcomes"],
        )

        assert row["session_date"] == "2026-09-08"
        assert row["trade_pnl"] == 120.0
        assert row["row_type"] == "trade"

    def test_the_modelled_pnl_is_never_read_as_a_trade_result(self):
        # A lane could otherwise reach 300 "trades" without one observed fill.
        row = normalise_row(
            {
                "lane_key": "snapback:swing",
                "exit_ts": "2026-09-08T14:30:00+05:30",
                "modeled_total_pnl": 999.0,
                "actual_total_pnl": None,
                "authoritative": 1,
                "evidence_class": "paper",
            },
            table="outcomes",
            spec=SOURCE_TABLES["outcomes"],
        )
        assert row["trade_pnl"] is None

    def test_a_rejected_opportunity_becomes_a_reason_coded_refusal(self):
        row = normalise_row(
            {
                "lane_key": "snapback:scalping",
                "signal_timestamp": "2026-09-08T09:20:00+05:30",
                "rejection_reason": "NON_FROZEN_CONFIG",
            },
            table="opportunities",
            spec=SOURCE_TABLES["opportunities"],
        )
        assert row["refusal_reason"] == "NON_FROZEN_CONFIG"
        assert row["row_type"] == "signal"

    def test_an_accepted_opportunity_has_no_refusal_reason(self):
        row = normalise_row(
            {
                "lane_key": "snapback:scalping",
                "signal_timestamp": "2026-09-08T09:20:00+05:30",
                "rejection_reason": "",
            },
            table="opportunities",
            spec=SOURCE_TABLES["opportunities"],
        )
        assert row["refusal_reason"] is None

    def test_an_exit_pending_position_is_unresolved(self):
        row = normalise_row(
            {
                "lane_key": "snapback:swing",
                "entry_timestamp": "2026-09-08T09:30:00+05:30",
                "status": "EXIT_PENDING",
            },
            table="paper_positions",
            spec=SOURCE_TABLES["paper_positions"],
        )
        assert row["opened"] is True
        assert row["unresolved"] is True
        assert row["closed"] is False

    def test_a_row_with_no_usable_timestamp_has_no_session(self):
        row = normalise_row(
            {"lane_key": "snapback:swing"},
            table="outcomes",
            spec=SOURCE_TABLES["outcomes"],
        )
        assert row["session_date"] is None


class TestLoading:
    def _insert(self, warehouse, table: str, values: dict):
        conn = warehouse._get_connection()
        try:
            columns = ", ".join(values)
            marks = ", ".join("?" for _ in values)
            conn.execute(
                f"INSERT INTO {table} ({columns}) VALUES ({marks})",
                list(values.values()),
            )
            conn.commit()
        finally:
            conn.close()

    def test_a_stored_outcome_reaches_its_lane(self, warehouse):
        self._insert(
            warehouse,
            "outcomes",
            {
                "outcome_id": "o1",
                "opportunity_id": "p1",
                "exit_reason": "target",
                "entry_ts": "2026-09-08T09:30:00+05:30",
                "exit_ts": "2026-09-08T14:30:00+05:30",
                "actual_total_pnl": 42.0,
                "observed_at": "2026-09-08T14:30:00+05:30",
                "received_at": "2026-09-08T14:30:00+05:30",
                "symbol": "NIFTY",
                "provider_symbol": "NIFTY",
                "lane_key": "snapback:swing",
                "evidence_class": "paper",
                "authoritative": 1,
            },
        )

        _, rows, gaps = load_session_rows(warehouse, "2026-09-08")

        assert gaps == []
        assert len(rows) == 1
        report = build_daily_report(
            session_date="2026-09-08",
            session_row=None,
            evidence_rows=rows,
            release_tag="t",
            runtime_sha="a" * 40,
        )
        lane = next(r for r in report.lanes if r.lane_key == "snapback:swing")
        assert lane.trades_completed == 1

    def test_a_row_from_another_day_is_not_counted(self, warehouse):
        self._insert(
            warehouse,
            "outcomes",
            {
                "outcome_id": "o2",
                "opportunity_id": "p2",
                "exit_reason": "target",
                "entry_ts": "2026-09-07T09:30:00+05:30",
                "exit_ts": "2026-09-07T14:30:00+05:30",
                "actual_total_pnl": 42.0,
                "observed_at": "2026-09-07T14:30:00+05:30",
                "received_at": "2026-09-07T14:30:00+05:30",
                "symbol": "NIFTY",
                "provider_symbol": "NIFTY",
                "lane_key": "snapback:swing",
                "evidence_class": "paper",
                "authoritative": 1,
            },
        )

        _, rows, _ = load_session_rows(warehouse, "2026-09-08")

        assert rows == []

    def test_an_unreadable_table_becomes_a_gap_not_a_zero(self, warehouse):
        conn = warehouse._get_connection()
        try:
            conn.execute("DROP TABLE outcomes")
            conn.commit()
        finally:
            conn.close()

        _, _, gaps = load_session_rows(warehouse, "2026-09-08")

        assert any(g.startswith("evidence_table_unreadable:outcomes") for g in gaps)

    def test_a_read_gap_makes_the_day_unusable(self):
        report = build_daily_report(
            session_date="2026-09-08",
            session_row={
                "scanner_status": "COMPLETE",
                "market_gate_status": "OPEN",
                "universe_expected": 1,
                "universe_scanned": 1,
                "decisions_recorded": 1,
                "symbol_failures": 0,
                "entry_phase_status": "COMPLETE",
                "eod_phase_status": "COMPLETE",
                "evidence_gap_codes_json": "[]",
                "quotes_required": 10,
                "quotes_observed": 10,
            },
            extra_gap_codes=["evidence_table_missing:outcomes"],
            release_tag="t",
            runtime_sha="a" * 40,
        )

        # Every other signal says the day was clean. The read failure is what
        # makes it unusable, and it must survive into the verdict.
        assert "evidence_table_missing:outcomes" in report.evidence_gap_codes
        assert not report.usable_as_evidence


def test_a_completed_trade_with_no_observed_result_does_not_vanish():
    from app.core.evidence_completeness import build_lane_rows

    rows, _ = build_lane_rows(
        [
            {
                "lane_key": "snapback:swing",
                "session_date": "2026-09-08",
                "row_type": "trade",
                "trade_pnl": None,
                "authoritative": 1,
                "evidence_class": "paper",
            }
        ]
    )
    lane = next(r for r in rows if r.lane_key == "snapback:swing")

    assert lane.trades_completed == 0
    assert lane.non_promotable_rows == 1
