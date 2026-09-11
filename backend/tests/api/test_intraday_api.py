"""The intraday config routes.

Mostly about what the server *publishes*. The recurring bug class here is a UI
that claims backend behaviour the backend does not honour, so defaults, enums
and the uncalibrated flag all come from the engine rather than being typed a
second time in the client.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.endpoints.config import router


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("STERLING_DB_PATH", str(tmp_path / "test.db"))
    from app.services import db
    monkeypatch.setattr(db, "_DB_PATH", str(tmp_path / "test.db"), raising=False)
    db.init()
    from app.services.simulation import SimState, simulation_runner
    simulation_runner._state = SimState.IDLE
    simulation_runner._session_complete = False
    simulation_runner._stats.events = []
    simulation_runner._stats.trades = []
    from app.services import intraday
    intraday._state.clear()
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_it_publishes_all_three_strategies_with_their_own_identity(client):
    body = client.get("/config/intraday").json()
    s = body["strategy"]
    assert s["id"] == "intraday"
    assert [x["id"] for x in s["strategies"]] == [
        "pivot_break", "ma_ribbon", "vwap_supertrend"]
    assert [x["tag"] for x in s["strategies"]] == ["PB", "MR", "VS"]
    assert all(x["how_it_works"] for x in s["strategies"])


def test_nothing_is_marked_calibrated_because_nothing_is(client):
    s = client.get("/config/intraday").json()["strategy"]
    assert s["calibrated_fields"] == []
    assert s["validated"] is False


def test_defaults_are_the_operator_s_numbers(client):
    d = client.get("/config/intraday").json()["defaults"]
    assert d["timeframe"] == "5m"
    assert d["pb_ema_length"] == 9 and d["pb_pivot_type"] == "fibonacci"
    assert [d["rb_ema_fast"], d["rb_ema_1"], d["rb_ema_2"], d["rb_ema_slow"]] == [8, 13, 21, 55]
    assert d["vs_atr_length"] == 18 and d["vs_factor"] == 1.46
    assert d["vs_target_points"] == 20.0
    assert d["pb_target_r"] == 2.0 and d["pb_target2_r"] == 3.0


def test_vocabularies_come_from_the_engine_not_the_client(client):
    v = client.get("/config/intraday").json()["vocabularies"]
    assert v["strategy_key"] == ["pivot_break", "ma_ribbon", "vwap_supertrend"]
    assert "5m" in v["timeframe"] and "fibonacci" in v["pb_pivot_type"]
    assert set(v["vs_stop_source"]) == {"vwap", "supertrend", "wider"}
    assert v["scan_stocks"], "the eligible universe is published, not typed"


def test_a_partial_update_takes_and_is_read_back(client):
    r = client.put("/config/intraday", json={"vs_target_points": 30.0})
    assert r.status_code == 200
    assert r.json()["config"]["vs_target_points"] == 30.0
    assert client.get("/config/intraday").json()["config"]["vs_target_points"] == 30.0


def test_an_unknown_key_is_a_422_not_a_silent_drop(client):
    r = client.put("/config/intraday", json={"pb_ema_lenght": 9})
    assert r.status_code == 422 and "Unknown" in r.json()["detail"]


def test_an_invalid_value_is_refused_with_the_reason(client):
    r = client.put("/config/intraday", json={"rb_ema_slow": 5})
    assert r.status_code == 422
    assert "increase" in r.json()["detail"]


def test_an_empty_body_is_refused(client):
    assert client.put("/config/intraday", json={}).status_code == 422


def test_turning_a_strategy_off_removes_it_from_enabled_strategies(client):
    client.put("/config/intraday", json={"rb_enabled": False})
    body = client.get("/config/intraday").json()
    assert body["enabled_strategies"] == ["pivot_break", "vwap_supertrend"]


def test_risky_choices_are_published_as_warnings_not_errors(client):
    r = client.put("/config/intraday", json={"rb_require_full_cross": False})
    assert r.status_code == 200
    assert any("ONE line" in w for w in r.json()["warnings"])


def test_the_snapshot_is_well_formed_before_any_scan(client):
    body = client.get("/config/intraday/snapshot").json()
    assert body["rows"] == [] and body["armed"] == 0
    assert body["enabled_strategies"]


# ------------------------------------------------------------------ the live routes

def test_arm_requires_a_signal_id(client):
    assert client.post("/config/intraday/arm", json={}).status_code == 422


def test_adopt_refuses_a_nonsense_body(client):
    for body in ({}, {"symbol": "X"}, {"symbol": "X", "quantity": 0, "entry_price": 1},
                 {"symbol": "X", "quantity": 1, "entry_price": 0}):
        assert client.post("/config/intraday/adopt", json=body).status_code == 422


def test_exit_requires_a_symbol(client):
    assert client.post("/config/intraday/exit", json={}).status_code == 422


def test_positions_is_well_formed_before_anything_is_held(client):
    body = client.get("/config/intraday/positions").json()
    assert body["positions"] == []
    assert body["realised_pnl_today"] == 0.0
    assert body["record"]["trades"] == 0


def test_the_snapshot_publishes_the_live_view(client):
    """The board reads holdings, the day record and the mode from here, so a
    missing key is a board that silently renders nothing rather than an error."""
    body = client.get("/config/intraday/snapshot").json()
    for key in ("positions", "open_positions", "record", "mode", "notes",
                "entry_blocker"):
        assert key in body, key


def test_the_snapshot_says_why_the_next_entry_would_be_refused(client):
    client.put("/config/intraday", json={"enabled": False})
    body = client.get("/config/intraday/snapshot").json()
    assert body["entry_blocker"] and "switched off" in body["entry_blocker"]


def test_sizing_and_protection_defaults_are_published(client):
    d = client.get("/config/intraday").json()["defaults"]
    assert d["sizing_mode"] == "RISK_PCT"
    assert d["stop_mode"] == "both"
    # Auto-execution stays off until something has been walk-forward tested.
    assert d["auto_execute"] is False
    assert d["close_at_session_end"] is True
    assert d["dynamic_stops"] is True and d["dynamic_targets"] is True


def test_the_new_vocabularies_come_from_the_engine(client):
    v = client.get("/config/intraday").json()["vocabularies"]
    assert set(v["sizing_mode"]) == {"RISK_PCT", "LOTS"}
    assert set(v["stop_mode"]) == {"broker", "monitor", "both"}


def test_monitor_only_protection_is_published_as_a_warning(client):
    r = client.put("/config/intraday", json={"stop_mode": "monitor"})
    assert r.status_code == 200
    assert any("nothing at the broker" in w for w in r.json()["warnings"])


def test_lots_above_the_ceiling_is_refused(client):
    r = client.put("/config/intraday", json={"lots": 99, "max_lots": 5})
    assert r.status_code == 422 and "max_lots" in r.json()["detail"]
