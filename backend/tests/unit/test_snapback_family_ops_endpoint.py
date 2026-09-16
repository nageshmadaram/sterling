"""Family Operations surface: health, mode, evidence, exposure and a stop switch.
No strategy controls, and unknown economics are null rather than fabricated zeros.
"""

from __future__ import annotations

from fastapi import FastAPI
from types import SimpleNamespace

from fastapi.testclient import TestClient

from app.api.v1.endpoints.snapback_ops import router


def _app():
    from app.core.auth import get_current_user

    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    # Authentication itself is covered by test_snapback_family_controls_auth.py.
    app.dependency_overrides[get_current_user] = lambda: SimpleNamespace(uid="operator")
    return TestClient(app)


_HEALTH = {
    "status": "DEGRADED",
    "healthy": False,
    "runtime_sha": "9e989dd910995deb5e77385b983e5992c58883c0",
    "strategy_manifest": "manifest-hash",
    "manifest_ok": True,
    "mode": "PAPER",
    "runner_alive": True,
    "broker_connected": False,
    "market_data_fresh": False,
    "calendar_ok": True,
    "database_ok": True,
    "open_positions": 0,
    "exit_pending": 0,
    "unresolved_errors": ["broker_disconnected"],
}


def test_family_view_exposes_only_operational_truth(monkeypatch):
    monkeypatch.setattr(
        "app.api.v1.endpoints.snapback_ops.get_prospective_health", lambda: _HEALTH
    )
    monkeypatch.setattr(
        "app.api.v1.endpoints.snapback_ops.get_family_evidence_verdict",
        lambda: {"verdict": "INCONCLUSIVE", "net_pnl": None},
    )

    body = _app().get("/api/v1/snapback/family/operations").json()

    assert body["system_status"] == "DEGRADED"
    assert body["mode"] == "PAPER"
    assert body["strategy"] == "Snapback 1.0.5"
    assert body["runtime_sha"] == "9e989dd910995deb5e77385b983e5992c58883c0"
    assert body["evidence"] == "INCONCLUSIVE"
    assert body["broker_connected"] is False
    assert body["runner_alive"] is True
    assert body["open_positions_count"] == 0
    assert body["exit_pending"] == 0

    # Unknown economics stay unknown.
    assert body["cumulative_net_pnl"] is None
    assert body["drawdown_pct"] is None
    assert body["current_exposure_inr"] is None

    # Live must be blocked tonight, and the screen must say so.
    assert body["live_blocked"] is True

    # No strategy controls on this surface.
    for forbidden in ("target_delta", "hold_days", "min_stretch_atr", "auto_execute"):
        assert forbidden not in body


def test_family_view_reports_halted_when_probe_fails(monkeypatch):
    def broken():
        raise RuntimeError("database locked")

    monkeypatch.setattr(
        "app.api.v1.endpoints.snapback_ops.get_prospective_health", broken
    )

    response = _app().get("/api/v1/snapback/family/operations")

    assert response.status_code == 503
    assert response.json()["detail"]["system_status"] == "HALTED"


def test_stop_all_new_trades_blocks_entries_without_touching_state(monkeypatch):
    stopped = {}

    monkeypatch.setattr(
        "app.api.v1.endpoints.snapback_ops.set_new_trades_halted",
        lambda halted, reason="": stopped.update(halted=halted, reason=reason),
    )

    body = _app().post("/api/v1/snapback/family/stop-new-trades").json()

    assert stopped["halted"] is True
    assert body["new_trades_halted"] is True
    # Existing positions and reconciliation continue.
    assert body["exits_still_processed"] is True
