from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints.snapback_ops import router
from app.services.snapback_health import build_prospective_health


FREEZE_SHA = "9e989dd910995deb5e77385b983e5992c58883c0"
MANIFEST_HASH = "602d28f804e840d046e7b51d020d5718dfd38a08d27d5324ec9d81bfbc4e53e4"


class FakeWarehouse:
    def __init__(
        self,
        *,
        pending=0,
        processing=0,
        open_positions=0,
        exit_pending=0,
    ):
        self.pending = pending
        self.processing = processing
        self.open_positions = open_positions
        self.exit_pending = exit_pending

    def opportunity_status_counts(self):
        return {
            "PENDING_ENTRY": self.pending,
            "PROCESSING_ENTRY": self.processing,
        }

    def paper_position_status_counts(self):
        return {
            "OPEN": self.open_positions,
            "EXIT_PENDING": self.exit_pending,
        }


def _healthy_kwargs(now):
    return dict(
        warehouse=FakeWarehouse(
            pending=2,
            processing=0,
            open_positions=1,
            exit_pending=0,
        ),
        runtime_sha=FREEZE_SHA,
        strategy_manifest=MANIFEST_HASH,
        manifest_ok=True,
        mode="PAPER",
        broker_connected=True,
        market_data_fresh=True,
        calendar_ok=True,
        database_ok=True,
        runner_alive=True,
        last_runner_tick=now - timedelta(seconds=10),
        last_signal_scan=now - timedelta(minutes=5),
        last_entry_cycle=now - timedelta(minutes=3),
        last_intraday_risk_cycle=now - timedelta(seconds=30),
        last_eod_cycle=now - timedelta(hours=20),
        unresolved_errors=[],
        now=now,
    )


def test_health_snapshot_reports_exact_frozen_identity_and_server_truth():
    now = datetime(2026, 9, 16, 6, 0, tzinfo=timezone.utc)

    body = build_prospective_health(**_healthy_kwargs(now))

    assert body["status"] == "HEALTHY"
    assert body["healthy"] is True

    assert body["runtime_sha"] == FREEZE_SHA
    assert body["strategy_manifest"] == MANIFEST_HASH
    assert body["manifest_ok"] is True

    assert body["mode"] == "PAPER"

    assert body["runner_alive"] is True
    assert body["broker_connected"] is True
    assert body["market_data_fresh"] is True
    assert body["calendar_ok"] is True
    assert body["database_ok"] is True

    assert body["pending_entries"] == 2
    assert body["processing_entries"] == 0
    assert body["open_positions"] == 1
    assert body["exit_pending"] == 0

    assert body["unresolved_errors"] == []


def test_health_never_reports_healthy_when_manifest_mismatches():
    now = datetime(2026, 9, 16, 6, 0, tzinfo=timezone.utc)
    kwargs = _healthy_kwargs(now)
    kwargs["manifest_ok"] = False

    body = build_prospective_health(**kwargs)

    assert body["healthy"] is False
    assert body["status"] in {"DEGRADED", "HALTED"}
    assert "manifest_mismatch" in body["unresolved_errors"]


def test_health_never_reports_healthy_when_database_is_unavailable():
    now = datetime(2026, 9, 16, 6, 0, tzinfo=timezone.utc)
    kwargs = _healthy_kwargs(now)
    kwargs["database_ok"] = False

    body = build_prospective_health(**kwargs)

    assert body["healthy"] is False
    assert body["status"] == "HALTED"
    assert "database_unavailable" in body["unresolved_errors"]


def test_health_reports_stale_runner_heartbeat():
    now = datetime(2026, 9, 16, 6, 0, tzinfo=timezone.utc)
    kwargs = _healthy_kwargs(now)

    # Runner polls every 30 sec. >90 sec means it has missed 3+ cycles.
    kwargs["last_runner_tick"] = now - timedelta(seconds=91)

    body = build_prospective_health(**kwargs)

    assert body["healthy"] is False
    assert body["runner_alive"] is False
    assert body["status"] in {"DEGRADED", "HALTED"}
    assert "runner_stale" in body["unresolved_errors"]


def test_health_reports_stale_market_data_fail_closed():
    now = datetime(2026, 9, 16, 6, 0, tzinfo=timezone.utc)
    kwargs = _healthy_kwargs(now)
    kwargs["market_data_fresh"] = False

    body = build_prospective_health(**kwargs)

    assert body["healthy"] is False
    assert body["status"] in {"DEGRADED", "HALTED"}
    assert "market_data_stale" in body["unresolved_errors"]


def test_health_reports_broker_disconnect_truthfully():
    now = datetime(2026, 9, 16, 6, 0, tzinfo=timezone.utc)
    kwargs = _healthy_kwargs(now)
    kwargs["broker_connected"] = False

    body = build_prospective_health(**kwargs)

    assert body["healthy"] is False
    assert body["broker_connected"] is False
    assert "broker_disconnected" in body["unresolved_errors"]


def test_health_calendar_unknown_is_not_green():
    now = datetime(2026, 9, 16, 6, 0, tzinfo=timezone.utc)
    kwargs = _healthy_kwargs(now)
    kwargs["calendar_ok"] = False

    body = build_prospective_health(**kwargs)

    assert body["healthy"] is False
    assert body["status"] in {"DEGRADED", "HALTED"}
    assert "calendar_unavailable" in body["unresolved_errors"]


def test_health_surfaces_processing_and_exit_pending_states():
    now = datetime(2026, 9, 16, 6, 0, tzinfo=timezone.utc)
    kwargs = _healthy_kwargs(now)

    kwargs["warehouse"] = FakeWarehouse(
        pending=1,
        processing=2,
        open_positions=3,
        exit_pending=4,
    )

    body = build_prospective_health(**kwargs)

    assert body["pending_entries"] == 1
    assert body["processing_entries"] == 2
    assert body["open_positions"] == 3
    assert body["exit_pending"] == 4


def test_health_preserves_existing_unresolved_errors():
    now = datetime(2026, 9, 16, 6, 0, tzinfo=timezone.utc)
    kwargs = _healthy_kwargs(now)
    kwargs["unresolved_errors"] = [
        "position_reconciliation_mismatch",
    ]

    body = build_prospective_health(**kwargs)

    assert body["healthy"] is False
    assert "position_reconciliation_mismatch" in body["unresolved_errors"]


def test_health_endpoint_contract(monkeypatch):
    now = datetime(2026, 9, 16, 6, 0, tzinfo=timezone.utc)
    expected = build_prospective_health(**_healthy_kwargs(now))

    monkeypatch.setattr(
        "app.api.v1.endpoints.snapback_ops.get_prospective_health",
        lambda: expected,
    )

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    client = TestClient(app)

    response = client.get("/api/v1/snapback/prospective/health")

    assert response.status_code == 200
    assert response.json() == expected


def test_health_endpoint_never_converts_probe_exception_to_green(monkeypatch):
    def broken_health():
        raise RuntimeError("database locked")

    monkeypatch.setattr(
        "app.api.v1.endpoints.snapback_ops.get_prospective_health",
        broken_health,
    )

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")

    client = TestClient(app)

    response = client.get("/api/v1/snapback/prospective/health")

    assert response.status_code == 503

    body = response.json()

    assert body["detail"]["healthy"] is False
    assert body["detail"]["status"] == "HALTED"
    assert "health_probe_failed" in body["detail"]["unresolved_errors"]
