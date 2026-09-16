"""Day-T scanning must be unattended, and interactive scanning must not move money.

A browser must never be required for a signal to exist, and an interactive /scan must
never advance an open position's lifecycle.
"""

from __future__ import annotations

import inspect
from datetime import date, datetime, time, timedelta, timezone

import pytest


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



_IST = timezone(timedelta(hours=5, minutes=30))


def test_interactive_scan_cannot_mutate_position_lifecycle():
    from app.services import snapback as sb

    source = inspect.getsource(sb.scan_once)

    # The interactive path may fetch and evaluate; it may not run the lifecycle.
    assert "process_prospective_pending_entries_and_mtm" not in source
    assert "process_prospective_daily_mtm_and_exits" not in source
    assert "process_prospective_intraday_risk" not in source


def test_combined_lifecycle_helper_is_gone_from_the_scan_path():
    from app.services import snapback as sb

    helper = getattr(sb, "process_prospective_pending_entries_and_mtm", None)
    if helper is not None:
        assert getattr(helper, "__deprecated__", False) is True


def test_scanner_module_exposes_an_unattended_entry_point():
    from app.services import snapback_prospective_scanner as scanner

    assert hasattr(scanner, "finalize_session_signals")
    assert inspect.iscoroutinefunction(scanner.finalize_session_signals)


def test_scanner_runs_after_the_official_close():
    from app.services.snapback_prospective_scanner import SCAN_AFTER_CLOSE

    # The daily bar is only final after 15:30 IST.
    assert SCAN_AFTER_CLOSE >= time(15, 30)


def test_runner_owns_the_session_scan():
    from app.services import snapback_runner

    source = inspect.getsource(snapback_runner)

    assert "finalize_session_signals" in source


@pytest.mark.asyncio
async def test_scanner_records_a_session_even_with_zero_signals(tmp_path, monkeypatch):
    from app.services import snapback_prospective_scanner as scanner
    from app.services.snapback_observation_warehouse import SnapbackObservationWarehouse
    from app.services.snapback_session_ledger import session_is_complete

    wh = SnapbackObservationWarehouse(db_path=str(tmp_path / "w.db"))
    monkeypatch.setattr(
        "app.services.snapback_prospective_collector.SnapbackObservationWarehouse",
        lambda *a, **k: wh,
    )

    monkeypatch.setattr(
        "app.services.snapback.get_config",
        lambda uid=None: __import__("app.engines.snapback", fromlist=["SnapbackConfig"]).SnapbackConfig(enabled=True),
    )

    async def fake_scan(**kwargs):
        return {
            "universe_expected": 3,
            "universe_scanned": 3,
            "symbol_failures": 0,
            "market_gate_status": "BEARISH_OK",
            "signals": [],
        }

    monkeypatch.setattr(scanner, "_scan_universe", fake_scan)

    result = await scanner.finalize_session_signals(
        session_date=date(2026, 9, 17), client=object(), warehouse=wh,
    )

    assert result.signals_authoritative == 0
    assert result.status == "COMPLETE"
    assert session_is_complete(wh, "2026-09-17") is True


@pytest.mark.asyncio
async def test_partly_failed_scan_is_not_a_complete_session(tmp_path, monkeypatch):
    from app.services import snapback_prospective_scanner as scanner
    from app.services.snapback_observation_warehouse import SnapbackObservationWarehouse
    from app.services.snapback_session_ledger import session_is_complete

    wh = SnapbackObservationWarehouse(db_path=str(tmp_path / "w.db"))

    monkeypatch.setattr(
        "app.services.snapback.get_config",
        lambda uid=None: __import__("app.engines.snapback", fromlist=["SnapbackConfig"]).SnapbackConfig(enabled=True),
    )

    async def failing_scan(**kwargs):
        return {
            "universe_expected": 200,
            "universe_scanned": 173,
            "symbol_failures": 27,
            "market_gate_status": "BEARISH_OK",
            "signals": [],
        }

    monkeypatch.setattr(scanner, "_scan_universe", failing_scan)

    result = await scanner.finalize_session_signals(
        session_date=date(2026, 9, 17), client=object(), warehouse=wh,
    )

    assert result.status == "FAILED"
    assert session_is_complete(wh, "2026-09-17") is False


@pytest.mark.asyncio
async def test_scanner_without_a_broker_records_the_gap(tmp_path, monkeypatch):
    from app.services import snapback_prospective_scanner as scanner
    from app.services.snapback_observation_warehouse import SnapbackObservationWarehouse
    from app.services.snapback_session_ledger import session_record

    wh = SnapbackObservationWarehouse(db_path=str(tmp_path / "w.db"))

    result = await scanner.finalize_session_signals(
        session_date=date(2026, 9, 17), client=None, warehouse=wh,
    )

    assert result.status == "FAILED"
    row = session_record(wh, "2026-09-17")
    assert "broker_unavailable" in row["evidence_gap_codes_json"]
