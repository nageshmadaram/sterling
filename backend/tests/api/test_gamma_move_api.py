"""The Gamma Move config routes.

The recurring bug class in this codebase is a UI that claims backend behaviour
the backend does not honour, so these tests are mostly about what the server
*publishes*: defaults, vocabularies, and the research-only list the UI greys out.
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
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_descriptor_publishes_identity_and_calibration(client):
    body = client.get("/config/gamma-move").json()
    s = body["strategy"]
    assert s["id"] == "gamma_move"
    # Not proven, so the UI must never offer live.
    # Not validated — and deliberately NOT a lock. Paper/live has its own switch.
    assert s["validated"] is False
    assert "live_ready" not in s
    assert s["headline_finding"]
    assert set(s["calibrated_fields"]) >= {"level_proximity_pct", "min_oi_drop_pct",
                                           "volume_spike_mult", "min_price_gain_pct",
                                           "regime_multiplier"}
    # Each measured default carries what the measurement was, so the UI can show
    # provenance beside the control rather than asking the reader to trust it.
    for field in s["calibrated_fields"]:
        assert s["calibration"][field]


def test_source_gates_are_published_as_occupancy_not_edge(client):
    """Snapshot occupancy, not a measured edge. Must not sit in calibrated_fields."""
    s = client.get("/config/gamma-move").json()["strategy"]
    gates = s["source_gates"]
    assert "require_chain_max_oi" in gates
    assert "require_spot_through_strike" in gates
    assert "require_chain_max_oi" not in s["calibrated_fields"]
    assert "require_spot_through_strike" not in s["calibrated_fields"]


def test_defaults_are_the_calibrated_values(client):
    d = client.get("/config/gamma-move").json()["defaults"]
    assert d["level_proximity_pct"] == 1.0
    assert d["min_oi_drop_pct"] == 3.0
    assert d["volume_spike_mult"] == 2.5
    assert d["min_price_gain_pct"] == 2.0
    assert d["regime_multiplier"] == 2.0      # not the conventional 3.0
    assert d["enabled"] is True
    assert d["stop_mode"] == "both"
    assert "execution_mode" not in d


def test_vocabularies_cover_every_choice_field(client):
    v = client.get("/config/gamma-move").json()["vocabularies"]
    for key in ("level_timeframe", "trigger_timeframe", "exit_policy", "stop_basis",
                "sizing_mode", "stop_mode", "scan_stocks"):
        assert v[key], f"{key} has no published vocabulary"


def test_the_eligible_universe_is_published(client):
    """So the UI's selectable set cannot drift from what the scanner accepts."""
    from app.services.kite_engine.stock_registry import HIGH_LIQUIDITY_STOCK_NAMES
    v = client.get("/config/gamma-move").json()["vocabularies"]
    assert set(v["scan_stocks"]) == set(HIGH_LIQUIDITY_STOCK_NAMES)


def test_an_off_registry_stock_is_refused(client):
    r = client.put("/config/gamma-move", json={"scan_stocks": ["SOMEPENNYCO"]})
    assert r.status_code == 422
    assert "registry" in r.json()["detail"]


def test_research_only_exits_are_published(client):
    """The source gives no exit rule, so only the time stop may run live."""
    r = client.get("/config/gamma-move").json()["research_only"]
    assert set(r["exit_policy"]) == {"PERCENT_TARGET", "TRAILING_STOP"}


def test_partial_update_changes_only_what_was_sent(client):
    before = client.get("/config/gamma-move").json()["config"]
    after = client.put("/config/gamma-move",
                       json={"min_oi_drop_pct": 4.5}).json()["config"]
    assert after["min_oi_drop_pct"] == 4.5
    assert after["volume_spike_mult"] == before["volume_spike_mult"]


def test_source_flags_round_trip_through_put(client):
    """Settings PUT must store the two source gates, not drop them via getattr."""
    defaults = client.get("/config/gamma-move").json()["defaults"]
    assert defaults["require_chain_max_oi"] is True
    assert defaults["require_spot_through_strike"] is True
    r = client.put("/config/gamma-move", json={
        "require_chain_max_oi": False,
        "require_spot_through_strike": False,
    })
    assert r.status_code == 200, r.text
    cfg = r.json()["config"]
    assert cfg["require_chain_max_oi"] is False
    assert cfg["require_spot_through_strike"] is False
    again = client.get("/config/gamma-move").json()["config"]
    assert again["require_chain_max_oi"] is False
    assert again["require_spot_through_strike"] is False


def test_unknown_key_is_refused_not_dropped(client):
    """A silently ignored setting is worse than a 422: the UI cannot tell."""
    r = client.put("/config/gamma-move", json={"min_oi_drp_pct": 4.5})
    assert r.status_code == 422
    assert "min_oi_drp_pct" in r.json()["detail"]


def test_empty_body_is_refused(client):
    assert client.put("/config/gamma-move", json={}).status_code == 422


@pytest.mark.parametrize("payload,fragment", [
    ({"level_proximity_pct": 0}, "level_proximity_pct"),
    ({"min_oi_drop_pct": 0}, "min_oi_drop_pct"),
    ({"volume_spike_mult": 1.0}, "volume_spike_mult"),
    ({"expiry_dte_min": 5, "expiry_dte_max": 2}, "expiry_dte_max"),
    ({"stop_percent": 100}, "100% stop"),
    ({"confirm_bars": 9}, "confirm_bars"),
])
def test_invalid_values_are_refused_with_a_reason(client, payload, fragment):
    r = client.put("/config/gamma-move", json=payload)
    assert r.status_code == 422
    assert fragment in r.json()["detail"]


def test_a_stop_is_required_regardless_of_mode(client):
    r = client.put("/config/gamma-move", json={"stop_percent": 0, "stop_points": 0})
    assert r.status_code == 422
    assert "a stop is required" in r.json()["detail"]


def test_warnings_are_published_for_risky_but_legal_choices(client):
    client.put("/config/gamma-move", json={"stop_mode": "monitor"})
    body = client.get("/config/gamma-move").json()
    assert any("unprotected" in w for w in body["warnings"])
    client.put("/config/gamma-move", json={"stop_mode": "both"})


def test_scan_and_arm_are_refused_while_replay_owns_the_board(client, monkeypatch):
    from app.services.simulation import SimulationRunner
    monkeypatch.setattr(SimulationRunner, "has_session_view",
                        property(lambda self: True))
    scan = client.post("/config/gamma-move/scan")
    assert scan.status_code == 409
    assert "replay" in scan.json()["detail"]
    arm = client.post("/config/gamma-move/arm", json={"signal_id": "x"})
    assert arm.status_code == 409
    assert "replay" in arm.json()["detail"]


def test_a_rejected_change_does_not_persist(client):
    client.put("/config/gamma-move", json={"min_oi_drop_pct": 3.0})
    client.put("/config/gamma-move", json={"min_oi_drop_pct": 0})
    assert client.get("/config/gamma-move").json()["config"]["min_oi_drop_pct"] == 3.0


def test_snapshot_states_that_the_strategy_is_unvalidated(client):
    """The finding belongs where the operator decides whether to switch it on,
    not only in a document."""
    body = client.get("/config/gamma-move/snapshot").json()
    assert any("not validated" in w for w in body["warnings"])


def test_snapshot_reports_mode_read_from_its_real_home(client):
    mode = client.get("/config/gamma-move/snapshot").json()["mode"]
    assert set(mode) >= {"is_paper", "auto_execute"}
    assert "Trading Mode" in mode["note"]


def test_arm_requires_a_signal_id(client):
    assert client.post("/config/gamma-move/arm", json={}).status_code == 422


def test_simulate_validates_its_arguments(client):
    assert client.post("/config/gamma-move/simulate", json={}).status_code == 422
    assert client.post("/config/gamma-move/simulate",
                       json={"symbols": ["X"], "days": 999}).status_code == 422


def test_contract_vocabulary_matches_the_other_engines(client):
    """One vocabulary for one idea. A value that means something on the ORB page
    must mean the same here, so both read it from the same definition."""
    from app.engines.option_contracts import EXPIRY_SELECTIONS
    v = client.get("/config/gamma-move").json()["vocabularies"]
    assert set(v["expiry_selection"]) == set(EXPIRY_SELECTIONS)
    assert set(v["scan_expiries_indices"]) == {"weekly", "monthly"}
    # NSE lists no weekly single-stock options, and offering one would be a
    # control that cannot be honoured.
    assert v["scan_expiries_stocks"] == ["monthly"]


def test_contract_settings_round_trip(client):
    body = {"expiry_selection": "monthly", "expiry_dte_min": 2,
            "expiry_dte_max": 21, "avoid_expiry_day": False,
            "scan_expiries_indices": ["monthly"]}
    got = client.put("/config/gamma-move", json=body).json()["config"]
    assert got["expiry_selection"] == "monthly"
    assert (got["expiry_dte_min"], got["expiry_dte_max"]) == (2, 21)
    assert got["avoid_expiry_day"] is False
    assert got["scan_expiries_indices"] == ["monthly"]
    client.put("/config/gamma-move", json={"expiry_dte_min": 0, "expiry_dte_max": 14,
                                           "avoid_expiry_day": True,
                                           "scan_expiries_indices": ["weekly", "monthly"],
                                           "expiry_selection": "nearest"})


def test_the_finding_is_split_from_its_evidence(client):
    """A board banner is not the place for confidence intervals.

    The claim, the action it implies, and the measurement behind it are three
    separate fields so the UI can lead with what to do and keep the statistics
    one gesture away — rather than putting a paper abstract across the top of a
    trading screen.
    """
    s = client.get("/config/gamma-move").json()["strategy"]
    assert s["headline_finding"] and s["what_to_do"] and s["evidence"]
    # The claim carries no measurements; the evidence carries all of them.
    assert "[" not in s["headline_finding"], "confidence intervals belong in evidence"
    # And no configurable value either: `level_proximity_pct` can be changed, so
    # a claim naming today's setting silently goes stale the moment it is.
    assert "1%" not in s["headline_finding"]
    assert "46.2" not in s["headline_finding"]
    assert "46.2%" in s["evidence"]
    assert "21.7%" in s["evidence"]
    # And it says where to check it.
    assert "VALIDATION_REPORT" in s["evidence"]


def test_the_finding_still_says_the_trigger_alone_showed_nothing(client):
    """Readability must not sand off the conclusion."""
    s = client.get("/config/gamma-move").json()["strategy"]
    combined = f"{s['headline_finding']} {s['what_to_do']}".lower()
    assert "level filter" in combined
    assert "random" in combined or "no edge" in combined
    assert s["validated"] is False
