from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.services.snapback_alerts import (
    AlertDispatcher,
    derive_operational_alerts,
)


class FakeSink:
    def __init__(self):
        self.sent = []

    def send(self, alert):
        self.sent.append(alert)


def _healthy():
    return {
        "status": "HEALTHY",
        "healthy": True,
        "runner_alive": True,
        "broker_connected": True,
        "market_data_fresh": True,
        "calendar_ok": True,
        "database_ok": True,
        "manifest_ok": True,
        "pending_entries": 0,
        "processing_entries": 0,
        "open_positions": 0,
        "exit_pending": 0,
        "unresolved_errors": [],
    }


def test_healthy_system_emits_no_alerts():
    alerts = derive_operational_alerts(
        health=_healthy(),
        backup_ok=True,
    )

    assert alerts == []


def test_database_failure_is_critical():
    health = _healthy()
    health["healthy"] = False
    health["status"] = "HALTED"
    health["database_ok"] = False
    health["unresolved_errors"] = ["database_unavailable"]

    alerts = derive_operational_alerts(
        health=health,
        backup_ok=True,
    )

    codes = {a.code: a for a in alerts}

    assert "database_unavailable" in codes
    assert codes["database_unavailable"].severity == "CRITICAL"


def test_manifest_mismatch_is_critical():
    health = _healthy()
    health["healthy"] = False
    health["status"] = "HALTED"
    health["manifest_ok"] = False
    health["unresolved_errors"] = ["manifest_mismatch"]

    alerts = derive_operational_alerts(
        health=health,
        backup_ok=True,
    )

    assert any(
        a.code == "manifest_mismatch"
        and a.severity == "CRITICAL"
        for a in alerts
    )


def test_broker_disconnect_generates_actionable_alert():
    health = _healthy()
    health["healthy"] = False
    health["status"] = "DEGRADED"
    health["broker_connected"] = False
    health["unresolved_errors"] = ["broker_disconnected"]

    alerts = derive_operational_alerts(
        health=health,
        backup_ok=True,
    )

    assert any(a.code == "broker_disconnected" for a in alerts)


def test_market_data_stale_generates_alert():
    health = _healthy()
    health["healthy"] = False
    health["market_data_fresh"] = False
    health["unresolved_errors"] = ["market_data_stale"]

    alerts = derive_operational_alerts(
        health=health,
        backup_ok=True,
    )

    assert any(a.code == "market_data_stale" for a in alerts)


def test_backup_failure_generates_critical_alert():
    alerts = derive_operational_alerts(
        health=_healthy(),
        backup_ok=False,
    )

    assert any(
        a.code == "backup_failed"
        and a.severity == "CRITICAL"
        for a in alerts
    )


def test_exit_pending_unresolved_generates_alert():
    health = _healthy()
    health["exit_pending"] = 1

    alerts = derive_operational_alerts(
        health=health,
        backup_ok=True,
        exit_pending_stale=True,
    )

    assert any(
        a.code == "exit_pending_unresolved"
        for a in alerts
    )


def test_processing_entry_stuck_generates_alert():
    health = _healthy()
    health["processing_entries"] = 1

    alerts = derive_operational_alerts(
        health=health,
        backup_ok=True,
        processing_entry_stale=True,
    )

    assert any(
        a.code == "pending_entry_stuck"
        for a in alerts
    )


def test_same_alert_is_not_spammed_every_poll():
    sink = FakeSink()
    dispatcher = AlertDispatcher(
        sink=sink,
        repeat_after=timedelta(hours=4),
    )

    now = datetime(2026, 9, 16, 6, 0, tzinfo=timezone.utc)

    health = _healthy()
    health["healthy"] = False
    health["broker_connected"] = False
    health["unresolved_errors"] = ["broker_disconnected"]

    alerts = derive_operational_alerts(
        health=health,
        backup_ok=True,
    )

    dispatcher.dispatch(alerts, now=now)
    dispatcher.dispatch(alerts, now=now + timedelta(minutes=1))
    dispatcher.dispatch(alerts, now=now + timedelta(minutes=30))

    assert len(sink.sent) == 1


def test_recovered_then_failed_again_sends_new_alert():
    sink = FakeSink()
    dispatcher = AlertDispatcher(
        sink=sink,
        repeat_after=timedelta(hours=4),
    )

    now = datetime(2026, 9, 16, 6, 0, tzinfo=timezone.utc)

    bad = _healthy()
    bad["healthy"] = False
    bad["broker_connected"] = False
    bad["unresolved_errors"] = ["broker_disconnected"]

    first = derive_operational_alerts(
        health=bad,
        backup_ok=True,
    )

    dispatcher.dispatch(first, now=now)

    # Healthy poll clears active state.
    dispatcher.dispatch(
        derive_operational_alerts(
            health=_healthy(),
            backup_ok=True,
        ),
        now=now + timedelta(minutes=5),
    )

    # Same fault occurs again.
    dispatcher.dispatch(
        first,
        now=now + timedelta(minutes=10),
    )

    assert len(sink.sent) == 2


def test_normal_trade_loss_does_not_generate_operational_alert():
    # Economic P&L is deliberately not an input to this function.
    alerts = derive_operational_alerts(
        health=_healthy(),
        backup_ok=True,
    )

    assert alerts == []


def test_breaker_breach_is_alerted():
    alerts = derive_operational_alerts(
        health=_healthy(),
        backup_ok=True,
        daily_loss_breached=True,
        drawdown_breached=True,
    )

    codes = {a.code for a in alerts}

    assert "daily_loss_breaker" in codes
    assert "drawdown_breaker" in codes


def test_system_halted_is_critical():
    health = _healthy()
    health["healthy"] = False
    health["status"] = "HALTED"

    alerts = derive_operational_alerts(
        health=health,
        backup_ok=True,
    )

    assert any(
        a.code == "system_halted"
        and a.severity == "CRITICAL"
        for a in alerts
    )


def test_alert_transport_failure_does_not_crash_monitoring():
    class BrokenSink:
        def send(self, alert):
            raise RuntimeError("telegram unavailable")

    dispatcher = AlertDispatcher(
        sink=BrokenSink(),
        repeat_after=timedelta(hours=4),
    )

    health = _healthy()
    health["healthy"] = False
    health["broker_connected"] = False
    health["unresolved_errors"] = ["broker_disconnected"]

    alerts = derive_operational_alerts(
        health=health,
        backup_ok=True,
    )

    result = dispatcher.dispatch(
        alerts,
        now=datetime(2026, 9, 16, 6, 0, tzinfo=timezone.utc),
    )

    assert result.failed == 1
    assert result.sent == 0


def test_evidence_report_failure_is_critical():
    alerts = derive_operational_alerts(
        health=_healthy(),
        backup_ok=True,
        report_ok=False,
    )

    assert any(
        a.code == "evidence_report_failed"
        and a.severity == "CRITICAL"
        for a in alerts
    )
