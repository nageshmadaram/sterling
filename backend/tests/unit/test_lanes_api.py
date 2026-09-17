"""The read-only lane API. Reporting only: no endpoint here changes anything."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.auth import get_current_user


@pytest.fixture()
def client() -> TestClient:
    from main import create_app

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: type(
        "U", (), {"user_id": "t", "username": "t"}
    )()
    return TestClient(app)


def test_focus_status_reports_the_originators(client):
    body = client.get("/api/v1/focus/status").json()
    assert body["focusable"] == ["snapback", "supertrend"]
    assert body["originators"] == ["snapback", "supertrend"]
    assert "STERLING_FOCUSED_STRATEGIES" in body["source"]


def test_focus_status_follows_a_narrowed_setting(client, monkeypatch):
    monkeypatch.setenv("STERLING_FOCUSED_STRATEGIES", "snapback")
    assert client.get("/api/v1/focus/status").json()["originators"] == ["snapback"]


def test_a_misconfigured_focus_setting_is_an_error_not_the_default(client, monkeypatch):
    """The operator meant to restrict something; silence would widen risk."""
    monkeypatch.setenv("STERLING_FOCUSED_STRATEGIES", "snapbak")
    assert client.get("/api/v1/focus/status").status_code == 500


def test_strategies_lists_both_with_their_five_modes(client):
    body = client.get("/api/v1/strategies").json()
    assert [s["strategy"] for s in body["strategies"]] == ["snapback", "supertrend"]
    for entry in body["strategies"]:
        assert len(entry["modes"]) == 5


def test_modes_report_state_and_rule_definedness(client):
    body = client.get("/api/v1/strategies/snapback/modes").json()
    by_mode = {m["mode"]: m for m in body["modes"]}
    assert by_mode["swing"]["state"] == "paper"
    assert by_mode["overnight"]["rules_defined"] is False
    assert by_mode["overnight"]["note"]


def test_unknown_strategy_is_404(client):
    assert client.get("/api/v1/strategies/gamma_move/modes").status_code == 404


def test_an_ambiguous_legacy_mode_is_a_400_not_a_guess(client):
    """positional predates the overnight/swing split."""
    assert client.get("/api/v1/strategies/snapback/positional/status").status_code == 400


def test_a_legacy_alias_resolves_to_its_canonical_lane(client):
    body = client.get("/api/v1/strategies/snapback/scalp/status").json()
    assert body["mode"] == "scalping"
    assert body["lane_key"] == "snapback:scalping"


def test_lane_status_carries_identity_and_timeline(client):
    body = client.get("/api/v1/strategies/snapback/swing/status").json()
    assert body["may_originate"] is True
    assert body["refusal"] is None
    assert body["identity"]["rule_hash"]
    assert body["timeline"]["hard_max_sessions"] == 15
    assert body["timeline"]["allow_overnight"] is True


def test_a_blocked_lane_states_its_refusal(client):
    body = client.get("/api/v1/strategies/snapback/overnight/status").json()
    assert body["may_originate"] is False
    assert body["refusal"]["reason"] == "LANE_RULES_UNDEFINED"


def test_a_session_bound_lane_reports_its_square_off(client):
    body = client.get("/api/v1/strategies/supertrend/intraday/status").json()
    assert body["timeline"]["allow_overnight"] is False
    assert body["timeline"]["force_close_time"] == "15:20"


def test_the_supertrend_core_reports_as_frozen(client):
    body = client.get("/api/v1/strategies/supertrend/swing/status").json()
    assert body["identity"]["strategy_version"] == "supertrend_core_v1"
    assert body["identity"]["core_frozen"] is True


def test_promotion_says_plainly_that_evidence_is_not_wired(client):
    """Zeros presented as a measurement would be worse than saying so."""
    body = client.get("/api/v1/strategies/snapback/swing/promotion").json()
    assert body["evidence_wired"] is False
    assert body["required"] == {"sessions": 60, "completed_trades": 300}
    assert body["verdict"] == "INCONCLUSIVE"


def test_the_dashboard_serves_all_ten_lanes(client):
    body = client.get("/api/v1/lanes/dashboard").json()
    assert len(body["lanes"]) == 10
    assert body["system"]["may_open_new_exposure"] is False


def test_the_api_exposes_no_way_to_promote_a_lane(client):
    """Promotion is an evidence decision, never an HTTP call."""
    from main import create_app

    app = create_app()
    lane_routes = [
        r for r in app.routes
        if getattr(r, "path", "").startswith(("/api/v1/strategies", "/api/v1/lanes", "/api/v1/focus"))
    ]
    assert lane_routes
    for route in lane_routes:
        assert set(getattr(route, "methods", set())) <= {"GET", "HEAD", "OPTIONS"}
