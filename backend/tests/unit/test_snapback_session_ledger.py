"""A quiet session and a broken scanner must be distinguishable.

Zero opportunities currently means any of: no signal, scanner never ran, config
disabled, broker disconnected, or a partly failed 200-symbol scan. Only the first is
evidence.
"""

from __future__ import annotations

import os
import tempfile
from datetime import date

import pytest

from app.services.snapback_observation_warehouse import SnapbackObservationWarehouse
from app.services.snapback_session_ledger import (
    SessionStatus,
    record_session_scan,
    session_is_complete,
    session_record,
)


@pytest.fixture
def warehouse():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as fh:
        path = fh.name
    wh = SnapbackObservationWarehouse(db_path=path)
    yield wh
    if os.path.exists(path):
        os.remove(path)


def _scan(wh, **over):
    payload = dict(
        session_date="2026-09-17",
        experiment_id="EXP-1",
        calendar_version="nse_calendar_v2025_2026a",
        universe_expected=200,
        universe_scanned=200,
        symbol_failures=0,
        market_gate_status="BEARISH_OK",
        signals_authoritative=0,
        status=SessionStatus.COMPLETE,
    )
    payload.update(over)
    record_session_scan(wh, **payload)


def test_a_real_zero_signal_session_is_valid_evidence(warehouse):
    _scan(warehouse)

    row = session_record(warehouse, "2026-09-17")

    assert row["universe_scanned"] == 200
    assert row["universe_expected"] == 200
    assert row["symbol_failures"] == 0
    assert row["signals_authoritative"] == 0
    assert session_is_complete(warehouse, "2026-09-17") is True


def test_a_session_that_never_ran_is_not_complete(warehouse):
    assert session_record(warehouse, "2026-09-17") is None
    assert session_is_complete(warehouse, "2026-09-17") is False


def test_a_partly_failed_scan_is_not_complete(warehouse):
    _scan(warehouse, universe_scanned=173, symbol_failures=27)

    assert session_is_complete(warehouse, "2026-09-17") is False
    row = session_record(warehouse, "2026-09-17")
    assert row["symbol_failures"] == 27


def test_a_missing_market_gate_is_not_complete(warehouse):
    _scan(warehouse, market_gate_status="")

    assert session_is_complete(warehouse, "2026-09-17") is False


def test_an_aborted_scan_is_recorded_with_its_reason(warehouse):
    _scan(warehouse, status=SessionStatus.FAILED, universe_scanned=0,
          market_gate_status="", evidence_gap_codes=["broker_disconnected"])

    row = session_record(warehouse, "2026-09-17")

    assert row["scanner_status"] == SessionStatus.FAILED
    assert "broker_disconnected" in row["evidence_gap_codes_json"]
    assert session_is_complete(warehouse, "2026-09-17") is False


def test_session_rows_carry_build_provenance(warehouse):
    _scan(warehouse)

    row = session_record(warehouse, "2026-09-17")

    assert row["runtime_build_sha"]
    assert row["runtime_build_sha"] != "UNKNOWN"


def test_rescanning_a_session_updates_rather_than_duplicates(warehouse):
    _scan(warehouse, universe_scanned=100, symbol_failures=100,
          status=SessionStatus.FAILED)
    _scan(warehouse, universe_scanned=200, symbol_failures=0,
          status=SessionStatus.COMPLETE)

    rows = warehouse.get_records_by_table("prospective_sessions")

    assert len(rows) == 1
    assert session_is_complete(warehouse, "2026-09-17") is True


def test_gate_counts_only_fully_observed_sessions(warehouse):
    _scan(warehouse, session_date="2026-09-17", status=SessionStatus.COMPLETE)
    _scan(warehouse, session_date="2026-09-18", universe_scanned=10,
          symbol_failures=190, status=SessionStatus.FAILED)

    from app.services.snapback_session_ledger import observed_session_count

    assert observed_session_count(warehouse) == 1
