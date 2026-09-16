from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.snapback_alerts import (
    AlertDispatcher,
    LoggingAlertSink,
)
from app.services.snapback_ops_scheduler import (
    POST_MARKET_START,
    SnapbackOpsScheduler,
)


_IST = timezone(timedelta(hours=5, minutes=30))


class FakeDispatcher:
    def __init__(self):
        self.calls = []

    def dispatch(self, alerts, *, now=None):
        alerts = list(alerts)
        self.calls.append((alerts, now))
        return SimpleNamespace(
            sent=len(alerts),
            suppressed=0,
            failed=0,
            codes_sent=[a.code for a in alerts],
        )


def _dt(hour: int, minute: int = 0):
    return datetime(
        2026, 9, 16,
        hour, minute,
        tzinfo=_IST,
    )


def _healthy():
    return {
        "status": "HEALTHY",
        "healthy": True,
        "runner_alive": True,
        "broker_connected": True,
        "market_data_fresh": True,
        "market_open": False,
        "calendar_ok": True,
        "database_ok": True,
        "manifest_ok": True,
        "pending_entries": 0,
        "processing_entries": 0,
        "open_positions": 0,
        "exit_pending": 0,
        "unresolved_errors": [],
        "runtime_sha":
            "9e989dd910995deb5e77385b983e5992c58883c0",
        "strategy_manifest": "manifest-test",
    }


def _scheduler(tmp_path, calls, **overrides):
    backup_artifact = SimpleNamespace(
        db_path=tmp_path / "backup.db",
        checksum_path=tmp_path / "backup.db.sha256",
    )

    def backup_fn(**kwargs):
        calls.append("backup")
        return backup_artifact

    def verify_fn(db, checksum):
        calls.append("verify")
        return True

    def report_fn(**kwargs):
        calls.append("report")
        return SimpleNamespace(
            directory=tmp_path / "report",
        )

    def health_fn():
        calls.append("health")
        return _healthy()

    defaults = dict(
        source_db=tmp_path / "prospective.db",
        backup_root=tmp_path / "backups",
        freeze_record=tmp_path / "freeze.md",
        report_root=tmp_path / "reports",
        state_path=tmp_path / "ops-state.json",
        backup_fn=backup_fn,
        verify_backup_fn=verify_fn,
        report_fn=report_fn,
        health_fn=health_fn,
        dispatcher=FakeDispatcher(),
        is_trading_day_fn=lambda d: d == date(2026, 9, 16),
    )

    defaults.update(overrides)

    return SnapbackOpsScheduler(**defaults)


def test_before_post_market_window_does_nothing(tmp_path):
    calls = []
    scheduler = _scheduler(tmp_path, calls)

    result = scheduler.run_due(now_ist=_dt(15, 39))

    assert result.status == "NOT_DUE"
    assert calls == []


def test_post_market_cycle_runs_backup_verify_report_then_alerts(tmp_path):
    calls = []
    dispatcher = FakeDispatcher()

    scheduler = _scheduler(
        tmp_path,
        calls,
        dispatcher=dispatcher,
    )

    result = scheduler.run_due(now_ist=_dt(15, 40))

    assert result.status == "COMPLETE"
    assert result.backup_ok is True
    assert result.report_ok is True

    assert calls == [
        "backup",
        "verify",
        "report",
        "health",
    ]

    assert len(dispatcher.calls) == 1


def test_same_session_runs_only_once(tmp_path):
    calls = []
    scheduler = _scheduler(tmp_path, calls)

    first = scheduler.run_due(now_ist=_dt(15, 40))
    second = scheduler.run_due(now_ist=_dt(16, 10))

    assert first.status == "COMPLETE"
    assert second.status == "ALREADY_COMPLETE"

    assert calls.count("backup") == 1
    assert calls.count("report") == 1


def test_completed_session_survives_process_restart(tmp_path):
    calls_a = []
    scheduler_a = _scheduler(tmp_path, calls_a)

    assert scheduler_a.run_due(
        now_ist=_dt(15, 40)
    ).status == "COMPLETE"

    calls_b = []
    scheduler_b = _scheduler(tmp_path, calls_b)

    result = scheduler_b.run_due(
        now_ist=_dt(17, 0)
    )

    assert result.status == "ALREADY_COMPLETE"
    assert calls_b == []


def test_backup_verification_failure_does_not_suppress_alerts(tmp_path):
    calls = []
    dispatcher = FakeDispatcher()

    scheduler = _scheduler(
        tmp_path,
        calls,
        dispatcher=dispatcher,
        verify_backup_fn=lambda *_a, **_k: False,
    )

    result = scheduler.run_due(
        now_ist=_dt(15, 40)
    )

    assert result.status == "PARTIAL_FAILURE"
    assert result.backup_ok is False

    # Health/alert phase must still execute.
    assert "health" in calls
    assert len(dispatcher.calls) == 1

    sent_codes = {
        alert.code
        for alert in dispatcher.calls[0][0]
    }

    assert "backup_failed" in sent_codes


def test_report_failure_does_not_suppress_alerts(tmp_path):
    calls = []
    dispatcher = FakeDispatcher()

    def broken_report(**kwargs):
        calls.append("report")
        raise RuntimeError("report generation failed")

    scheduler = _scheduler(
        tmp_path,
        calls,
        dispatcher=dispatcher,
        report_fn=broken_report,
    )

    result = scheduler.run_due(
        now_ist=_dt(15, 40)
    )

    assert result.status == "PARTIAL_FAILURE"
    assert result.report_ok is False

    assert len(dispatcher.calls) == 1

    sent_codes = {
        alert.code
        for alert in dispatcher.calls[0][0]
    }

    assert "evidence_report_failed" in sent_codes


def test_failed_core_cycle_is_not_marked_complete(tmp_path):
    calls = []

    scheduler = _scheduler(
        tmp_path,
        calls,
        verify_backup_fn=lambda *_a, **_k: False,
    )

    first = scheduler.run_due(
        now_ist=_dt(15, 40)
    )

    second = scheduler.run_due(
        now_ist=_dt(15, 45)
    )

    assert first.status == "PARTIAL_FAILURE"

    # Retry, rather than silently considering the day complete.
    assert calls.count("backup") == 2


def test_alert_transport_failure_does_not_make_core_cycle_fail(tmp_path):
    calls = []

    class BrokenDispatcher:
        def dispatch(self, alerts, *, now=None):
            return SimpleNamespace(
                sent=0,
                suppressed=0,
                failed=1,
                codes_sent=[],
            )

    scheduler = _scheduler(
        tmp_path,
        calls,
        dispatcher=BrokenDispatcher(),
    )

    result = scheduler.run_due(
        now_ist=_dt(15, 40)
    )

    # Evidence is backed up and reported.
    assert result.backup_ok is True
    assert result.report_ok is True
    assert result.status == "COMPLETE"

    # Transport failure is separately visible.
    assert result.alert_failed == 1


def test_non_trading_day_does_not_create_fake_session(tmp_path):
    calls = []

    scheduler = _scheduler(
        tmp_path,
        calls,
        is_trading_day_fn=lambda _d: False,
    )

    result = scheduler.run_due(
        now_ist=_dt(16, 0)
    )

    assert result.status == "NON_TRADING_DAY"
    assert calls == []


def test_corrupt_state_file_fails_safe_by_rerunning_idempotent_cycle(
    tmp_path,
):
    calls = []

    state = tmp_path / "ops-state.json"
    state.write_text(
        "{this-is-corrupt-json",
        encoding="utf-8",
    )

    scheduler = _scheduler(tmp_path, calls)

    result = scheduler.run_due(
        now_ist=_dt(15, 40)
    )

    # Safer to rerun idempotent backup/report than silently skip.
    assert result.status == "COMPLETE"
    assert calls.count("backup") == 1


def test_health_poll_dispatches_operational_fault_without_waiting_for_close(
    tmp_path,
):
    calls = []
    dispatcher = FakeDispatcher()

    health = _healthy()
    health["healthy"] = False
    health["status"] = "DEGRADED"
    health["broker_connected"] = False
    health["unresolved_errors"] = [
        "broker_disconnected",
    ]

    scheduler = _scheduler(
        tmp_path,
        calls,
        dispatcher=dispatcher,
        health_fn=lambda: health,
    )

    result = scheduler.poll_health(
        now_ist=_dt(11, 0)
    )

    assert result is not None
    assert len(dispatcher.calls) == 1

    codes = {
        a.code
        for a in dispatcher.calls[0][0]
    }

    assert "broker_disconnected" in codes
