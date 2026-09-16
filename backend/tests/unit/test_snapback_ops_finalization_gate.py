"""E23: the post-market package must refuse a session whose market evidence is missing.

Backing up and reporting a session whose end-of-day mark never happened produces a
complete-looking package around a hole.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

_IST = timezone(timedelta(hours=5, minutes=30))


def _dt(h, m):
    return datetime(2026, 10, 15, h, m, tzinfo=_IST)


def _scheduler(tmp_path, calls, *, evidence_complete=True, finalize_result=None):
    from app.services.snapback_ops_scheduler import SnapbackOpsScheduler

    artifact = SimpleNamespace(db_path=tmp_path / "b.db", checksum_path=tmp_path / "b.sha")

    def backup_fn(**kw):
        calls.append("backup")
        return artifact

    def verify_fn(db, checksum):
        calls.append("verify")
        return True

    def report_fn(**kw):
        calls.append("report")
        return SimpleNamespace(directory=tmp_path / "r")

    def health_fn():
        calls.append("health")
        return {"status": "HEALTHY", "healthy": True, "unresolved_errors": []}

    def evidence_fn(session_date):
        calls.append("evidence")
        return evidence_complete

    def finalize_fn(session_date):
        calls.append("finalize")
        return finalize_result if finalize_result is not None else evidence_complete

    return SnapbackOpsScheduler(
        source_db=tmp_path / "src.db", backup_root=tmp_path / "backups",
        freeze_record=tmp_path / "f.md", report_root=tmp_path / "reports",
        state_path=tmp_path / "state.json",
        backup_fn=backup_fn, verify_backup_fn=verify_fn, report_fn=report_fn,
        health_fn=health_fn, dispatcher=SimpleNamespace(
            dispatch=lambda alerts, now=None: SimpleNamespace(
                sent=len(list(alerts)), suppressed=0, failed=0,
                codes_sent=[a.code for a in alerts],
            )
        ),
        is_trading_day_fn=lambda d: True,
        session_evidence_complete_fn=evidence_fn,
        finalize_session_fn=finalize_fn,
    )


def test_a_complete_session_is_packaged(tmp_path):
    calls = []
    scheduler = _scheduler(tmp_path, calls)

    result = scheduler.run_due(now_ist=_dt(15, 45))

    assert result.status == "COMPLETE"
    assert "backup" in calls and "report" in calls


def test_an_incomplete_session_is_not_packaged(tmp_path):
    calls = []
    scheduler = _scheduler(tmp_path, calls, evidence_complete=False)

    result = scheduler.run_due(now_ist=_dt(15, 45))

    assert result.status == "PARTIAL_FAILURE"
    assert "backup" not in calls
    assert "report" not in calls
    assert any("evidence" in e for e in result.errors)


def test_finalization_is_attempted_before_refusing(tmp_path):
    calls = []
    # Evidence is incomplete now, but deterministic finalization fixes it.
    scheduler = _scheduler(tmp_path, calls, evidence_complete=False, finalize_result=True)

    result = scheduler.run_due(now_ist=_dt(15, 45))

    assert "finalize" in calls
    assert result.status == "COMPLETE"


def test_an_incomplete_session_is_not_marked_complete_in_state(tmp_path):
    calls = []
    scheduler = _scheduler(tmp_path, calls, evidence_complete=False)

    scheduler.run_due(now_ist=_dt(15, 45))

    state_file = tmp_path / "state.json"
    assert not state_file.exists() or "last_completed_session" not in json.loads(
        state_file.read_text(encoding="utf-8")
    )


def test_the_state_file_records_every_phase(tmp_path):
    calls = []
    scheduler = _scheduler(tmp_path, calls)

    scheduler.run_due(now_ist=_dt(15, 45))

    state = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))

    for field in ("scanner_complete", "entry_phase_complete", "eod_complete",
                  "backup_verified", "report_generated", "package_complete"):
        assert field in state, field
    assert state["package_complete"] is True


def test_a_legacy_state_file_does_not_skip_the_new_cycle(tmp_path):
    calls = []
    (tmp_path / "state.json").write_text(json.dumps({
        "last_completed_session": "2026-10-15",
        "backup_verified": True, "report_generated": True,
    }), encoding="utf-8")

    scheduler = _scheduler(tmp_path, calls)
    result = scheduler.run_due(now_ist=_dt(15, 45))

    # The old shape cannot prove the new phases ran.
    assert result.status == "COMPLETE"
    assert "backup" in calls


def test_an_incomplete_session_raises_a_critical_alert():
    from app.services.snapback_alerts import derive_operational_alerts

    alerts = derive_operational_alerts(
        health={"status": "HEALTHY", "healthy": True, "unresolved_errors": []},
        backup_ok=True,
        session_evidence_incomplete=True,
        session_evidence_gaps=["missing_futures_close_mark:OPP-1"],
    )

    match = [a for a in alerts if a.code == "session_evidence_incomplete"]
    assert match and match[0].severity == "CRITICAL"
    assert "OPP-1" in match[0].message


def test_a_quiet_session_does_not_raise_the_alert():
    from app.services.snapback_alerts import derive_operational_alerts

    alerts = derive_operational_alerts(
        health={"status": "HEALTHY", "healthy": True, "unresolved_errors": []},
        backup_ok=True, session_evidence_incomplete=False,
    )

    assert not [a for a in alerts if a.code == "session_evidence_incomplete"]
