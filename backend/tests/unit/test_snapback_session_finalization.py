"""E1: three distinct notions of "complete", so promotion cannot deadlock on packaging.

Requiring package_status for the 60-session denominator is circular: the report needs
the session count, the package needs the report. Market evidence and packaging are
separate facts.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from app.services.snapback_observation_warehouse import SnapbackObservationWarehouse
from app.services.snapback_session_ledger import (



    SessionStatus,
    append_session_evidence_gap,
    evidence_gap_codes,
    observed_session_count,
    record_session_scan,
    session_market_evidence_complete,
    session_package_complete,
    session_record,
    session_scan_complete,
    update_session_phase,
)
@pytest.fixture(autouse=True)
def _decision_artifacts_present(monkeypatch):
    """These tests exercise session phase logic, not artifact reconciliation.

    Scan completeness now also requires one durable decision per scanned symbol; that
    rule is proved in test_snapback_scan_decisions.py. Here the artifacts are presented
    as present so the phase invariant under test is the one that decides.
    """
    monkeypatch.setattr(
        "app.services.snapback_session_ledger._decisions_recorded",
        lambda warehouse, session_date: 10**6,
        raising=False,
    )


@pytest.fixture
def warehouse():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as fh:
        path = fh.name
    wh = SnapbackObservationWarehouse(db_path=path)
    yield wh
    if os.path.exists(path):
        os.remove(path)


def _row(**over):
    base = {
        "session_date": "2026-09-17", "scanner_status": "COMPLETE",
        "universe_expected": 200, "universe_scanned": 200, "symbol_failures": 0,
        "decisions_recorded": 200,
        # One durable decision per scanned symbol; the counter alone is not evidence.
        "decisions_recorded": 200,
        "market_gate_status": "EVALUATED", "entry_phase_status": "COMPLETE",
        "eod_phase_status": "COMPLETE", "package_status": "COMPLETE",
        "evidence_gap_codes_json": "[]",
    }
    base.update(over)
    return base


def test_scan_completeness_ignores_the_trading_phases():
    assert session_scan_complete(_row(entry_phase_status="PENDING",
                                      eod_phase_status="PENDING",
                                      package_status="PENDING")) is True


def test_market_evidence_needs_entry_and_eod_but_not_the_package():
    row = _row(package_status="PENDING")

    assert session_market_evidence_complete(row) is True
    assert session_package_complete(row) is False


@pytest.mark.parametrize("field", ["entry_phase_status", "eod_phase_status"])
def test_a_missing_trading_phase_breaks_market_evidence(field):
    assert session_market_evidence_complete(_row(**{field: "FAILED"})) is False


def test_an_evidence_gap_breaks_market_evidence():
    row = _row(evidence_gap_codes_json='["EOD_EVIDENCE_GAP"]')

    assert session_market_evidence_complete(row) is False


def test_package_completeness_requires_market_evidence_first():
    row = _row(eod_phase_status="FAILED", package_status="COMPLETE")

    assert session_package_complete(row) is False


def test_promotion_counts_market_evidence_not_packaging(warehouse):
    """The gate must be able to count today before today's package exists."""
    record_session_scan(
        warehouse, session_date="2026-09-17", experiment_id="E", calendar_version="v",
        universe_expected=200, universe_scanned=200, symbol_failures=0,
        market_gate_status="EVALUATED", signals_authoritative=0,
        status=SessionStatus.COMPLETE,
    )
    update_session_phase(
        warehouse, "2026-09-17",
        entry_phase_status="COMPLETE", eod_phase_status="COMPLETE",
    )

    # Package has not run yet, and must not block the count.
    assert observed_session_count(warehouse) == 1
    assert session_package_complete(session_record(warehouse, "2026-09-17")) is False


def test_gap_codes_are_appended_not_replaced(warehouse):
    record_session_scan(
        warehouse, session_date="2026-09-17", experiment_id="E", calendar_version="v",
        universe_expected=200, universe_scanned=200, symbol_failures=0,
        market_gate_status="EVALUATED", signals_authoritative=0,
        status=SessionStatus.COMPLETE,
    )

    append_session_evidence_gap(warehouse, "2026-09-17", "EOD_EVIDENCE_GAP")
    append_session_evidence_gap(warehouse, "2026-09-17", "entry_gap")

    codes = evidence_gap_codes(session_record(warehouse, "2026-09-17"))

    assert "EOD_EVIDENCE_GAP" in codes
    assert "entry_gap" in codes


def test_duplicate_gap_codes_are_not_repeated(warehouse):
    record_session_scan(
        warehouse, session_date="2026-09-17", experiment_id="E", calendar_version="v",
        universe_expected=200, universe_scanned=200, symbol_failures=0,
        market_gate_status="EVALUATED", signals_authoritative=0,
        status=SessionStatus.COMPLETE,
    )

    append_session_evidence_gap(warehouse, "2026-09-17", "EOD_EVIDENCE_GAP")
    append_session_evidence_gap(warehouse, "2026-09-17", "EOD_EVIDENCE_GAP")

    assert evidence_gap_codes(session_record(warehouse, "2026-09-17")).count(
        "EOD_EVIDENCE_GAP"
    ) == 1


def test_a_rescan_cannot_erase_an_earlier_gap(warehouse):
    record_session_scan(
        warehouse, session_date="2026-09-17", experiment_id="E", calendar_version="v",
        universe_expected=200, universe_scanned=200, symbol_failures=0,
        market_gate_status="EVALUATED", signals_authoritative=0,
        status=SessionStatus.COMPLETE,
    )
    append_session_evidence_gap(warehouse, "2026-09-17", "EOD_EVIDENCE_GAP")

    # The scanner runs again later in the day.
    record_session_scan(
        warehouse, session_date="2026-09-17", experiment_id="E", calendar_version="v",
        universe_expected=200, universe_scanned=200, symbol_failures=0,
        market_gate_status="EVALUATED", signals_authoritative=1,
        status=SessionStatus.COMPLETE,
    )

    assert "EOD_EVIDENCE_GAP" in evidence_gap_codes(session_record(warehouse, "2026-09-17"))
