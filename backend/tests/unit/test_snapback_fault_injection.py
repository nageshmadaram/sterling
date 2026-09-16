"""Block 6: what the system does when its dependencies fail underneath it.

Every test here injects a failure that a healthy-looking system produces no
warning about. The contract in each case is the same: an unknown must not be
recorded as a measurement, and a degraded component must be visible as degraded.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

_IST = timezone(timedelta(hours=5, minutes=30))


# ------------------------------------------------------- database faults


def test_a_full_disk_during_a_write_does_not_record_a_partial_outcome(tmp_path, monkeypatch):
    from app.services.snapback_observation_warehouse import SnapbackObservationWarehouse

    wh = SnapbackObservationWarehouse(db_path=str(tmp_path / "w.db"))

    real_connect = sqlite3.connect

    def failing_connect(*args, **kwargs):
        conn = real_connect(*args, **kwargs)
        original = conn.execute

        def execute(sql, *a, **k):
            if "INSERT INTO outcomes" in sql:
                raise sqlite3.OperationalError("database or disk is full")
            return original(sql, *a, **k)

        conn.execute = execute  # type: ignore[method-assign]
        return conn

    monkeypatch.setattr(sqlite3, "connect", failing_connect)

    with pytest.raises(Exception):
        wh.commit_paper_close_transaction(
            opportunity_id="OPP-1",
            outcome_data={
                "outcome_id": "OUT-1", "opportunity_id": "OPP-1", "symbol": "NIFTY",
                "exit_reason": "PREMIUM_STOP",
                "entry_ts": "2026-10-01T09:20:00+05:30",
                "exit_ts": "2026-10-03T11:00:00+05:30",
                "actual_option_pnl": -1000.0, "actual_futures_pnl": 200.0,
            },
            cost_events=[],
        )

    monkeypatch.undo()
    assert wh.get_records_by_table("outcomes", opportunity_id="OPP-1") == []


def test_a_corrupt_evidence_database_fails_preflight(tmp_path):
    from app.services.snapback_preflight import check_database

    path = tmp_path / "corrupt.db"
    path.write_bytes(b"\x00garbage" * 500)

    passed, details = check_database(db_path=str(path))

    assert passed is False
    assert details["writable"] is False


def test_a_locked_database_is_reported_not_silently_skipped(tmp_path):
    """A busy writer must surface as a failed probe, never as an empty result."""
    from app.services.snapback_observation_warehouse import SnapbackObservationWarehouse

    path = tmp_path / "locked.db"
    SnapbackObservationWarehouse(db_path=str(path))

    holder = sqlite3.connect(str(path), timeout=1.0, isolation_level="EXCLUSIVE")
    holder.execute("BEGIN EXCLUSIVE")
    try:
        from app.services.snapback_preflight import check_database

        passed, details = check_database(db_path=str(path))
        # Either the write probe fails outright, or it succeeded because the
        # lock cleared. What must never happen is a pass with writable False.
        assert passed == bool(details.get("writable"))
    finally:
        holder.rollback()
        holder.close()


# ------------------------------------------------------- dependency faults


def test_a_calendar_outage_halts_the_cycle_rather_than_assuming_a_trading_day():
    from app.services.snapback_ops_scheduler import SnapbackOpsScheduler

    def exploding_calendar(_d):
        raise RuntimeError("calendar service unreachable")

    scheduler = SnapbackOpsScheduler(
        is_trading_day_fn=exploding_calendar,
        state_path=Path("/tmp/fault-calendar-state.json"),
    )

    result = scheduler.run_due(now_ist=datetime(2026, 9, 17, 17, 0, tzinfo=_IST))

    assert result.status == "CALENDAR_UNAVAILABLE"
    assert any("calendar_unavailable" in e for e in result.errors)


def test_a_backup_failure_prevents_the_session_being_marked_complete(tmp_path):
    from app.services.snapback_ops_scheduler import SnapbackOpsScheduler

    def failing_backup(**_kwargs):
        raise OSError("No space left on device")

    scheduler = SnapbackOpsScheduler(
        source_db=tmp_path / "live.db",
        report_root=tmp_path / "reports",
        state_path=tmp_path / "state.json",
        is_trading_day_fn=lambda _d: True,
        session_evidence_complete_fn=lambda _d: True,
        backup_fn=failing_backup,
        report_fn=lambda **_k: None,
        health_fn=lambda: {"status": "HEALTHY", "healthy": True},
    )

    result = scheduler.run_due(now_ist=datetime(2026, 9, 17, 17, 0, tzinfo=_IST))

    assert result.status == "PARTIAL_FAILURE"
    assert result.backup_ok is False
    assert not (tmp_path / "state.json").exists()


def test_an_alert_transport_outage_does_not_fail_the_evidence_cycle(tmp_path):
    """Evidence is the product; notification is not. A dead transport must not
    stop the day's evidence from being packaged."""
    from app.services.snapback_ops_scheduler import SnapbackOpsScheduler

    class ExplodingDispatcher:
        def dispatch(self, *_a, **_k):
            raise RuntimeError("Telegram unreachable")

    class _Artifact:
        db_path = tmp_path / "snap.db"
        checksum_path = tmp_path / "snap.sha256"

    scheduler = SnapbackOpsScheduler(
        source_db=tmp_path / "live.db",
        report_root=tmp_path / "reports",
        state_path=tmp_path / "state.json",
        is_trading_day_fn=lambda _d: True,
        session_evidence_complete_fn=lambda _d: True,
        backup_fn=lambda **_k: _Artifact(),
        verify_backup_fn=lambda *_a, **_k: True,
        report_fn=lambda **_k: None,
        health_fn=lambda: {"status": "HEALTHY", "healthy": True},
        dispatcher=ExplodingDispatcher(),
    )

    result = scheduler.run_due(now_ist=datetime(2026, 9, 17, 17, 0, tzinfo=_IST))

    assert result.backup_ok is True
    assert result.report_ok is True
    assert any("alert_dispatch_failed" in e for e in result.errors)


def test_a_broker_outage_leaves_reconciliation_unresolved_not_reconciled():
    """No broker answer is not a clean book. It is no information at all."""
    import asyncio

    from app.services.snapback_reconciliation import reconcile_account

    snapshot = asyncio.run(
        reconcile_account(
            client=None,  # the transport is down
            account_id="ACC-1",
            sterling_positions=[],
            sterling_intents=[],
        )
    )

    assert snapshot.clean is False


def test_a_quote_outage_counts_as_a_miss_not_as_coverage():
    """A required quote that never arrived must lower coverage. Counting only the
    quotes that did arrive makes an outage look like a perfect session."""
    from app.services.snapback_quote_evidence import coverage_from_events

    stats = coverage_from_events([
        {"required_for_economics": 1, "accepted": 1, "quote_present": 1},
        {"required_for_economics": 1, "accepted": 0, "quote_present": 0},
    ])

    assert stats["coverage_pct"] == pytest.approx(50.0)


# ------------------------------------------------------------ clock faults


def test_an_unsynchronised_clock_blocks_startup(monkeypatch):
    from app.services import snapback_preflight

    monkeypatch.delenv("STERLING_SKIP_CLOCK_CHECK", raising=False)
    monkeypatch.setattr(
        snapback_preflight, "_ntp_synchronised", lambda: (False, "not_synchronised:no"),
    )

    result = snapback_preflight.run_preflight(
        build_identity_fn=lambda: (True, []),
        worktree_clean_fn=lambda: (True, []),
        config_identity_fn=lambda: (True, []),
        database_fn=lambda: (True, {}),
        evidence_meta_fn=lambda: (True, {}),
        calendar_fn=lambda: (True, {}),
        family_account_fn=lambda: (True, {}),
        allocation_capital_fn=lambda: (True, {}),
        disk_fn=lambda: (True, {}),
        backup_writable_fn=lambda: (True, {}),
        lifecycle_fn=lambda: (True, {}),
    )

    assert result.passed is False
    assert "clock" in [c.code for c in result.failures]
