"""Gaps found auditing the implementation against the specification.

Each test pins something the spec requires that the code did not have: a
declared event vocabulary, universe and track in the identity, a registry that
can explain a recorded hash, depth-aware fills, and the reporting/operator
endpoints.
"""
from __future__ import annotations

import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.core.auth import get_current_user
from app.core.evidence_events import (
    POSITION_EVENTS,
    TERMINAL_WITHOUT_POSITION,
    EvidenceEvent,
    UnknownEvidenceEvent,
    canonical_event,
    may_follow,
    sequence_defects,
)
from app.core.fill_model import (
    NO_FILL_INSUFFICIENT_DEPTH,
    NO_FILL_NO_BOOK,
    NO_FILL_SPREAD_TOO_WIDE,
    simulate_fill,
)
from app.core.strategy_identity import (
    VALID_TRACKS,
    IdentityError,
    build_identity,
    challenger_id,
)

RUNTIME = "e" * 40
TAG = "snapback-prospective-runtime-1.6"
HEALTHY_SEQUENCE = [
    "OPPORTUNITY_OBSERVED", "SIGNAL_EVALUATED", "SIGNAL_FOUND",
    "CONTRACT_SELECTION", "CAPACITY_EVALUATION", "EXECUTION_EVALUATION",
    "PAPER_ENTRY", "POSITION_OPEN", "POSITION_MARK", "PROTECTION_STATUS",
    "EXIT_DECISION", "EXIT_OBSERVED", "RECONCILED",
]


# ── §25 evidence vocabulary ───────────────────────────────────────────────


def test_the_vocabulary_covers_the_whole_declared_lifecycle():
    names = {e.value for e in EvidenceEvent}
    assert set(HEALTHY_SEQUENCE) <= names
    for extra in ("NO_SIGNAL", "SIGNAL_REJECTED", "NO_FILL", "SHADOW_INTENT",
                  "BROKER_INTENT", "EVIDENCE_ERROR"):
        assert extra in names


def test_failures_are_first_class_events():
    assert TERMINAL_WITHOUT_POSITION == {
        EvidenceEvent.NO_SIGNAL, EvidenceEvent.SIGNAL_REJECTED, EvidenceEvent.NO_FILL
    }
    assert EvidenceEvent.EVIDENCE_ERROR not in TERMINAL_WITHOUT_POSITION
    assert EvidenceEvent.RECONCILED in POSITION_EVENTS


def test_a_misspelled_event_is_refused_not_recorded():
    """A misspelling vanishes from every report that filters correctly."""
    for bad in ("no_fill_", "NOFILL", "", "position opened"):
        with pytest.raises(UnknownEvidenceEvent):
            canonical_event(bad)
    assert canonical_event("no_fill") is EvidenceEvent.NO_FILL


def test_a_complete_lifecycle_has_no_defects():
    assert sequence_defects(HEALTHY_SEQUENCE) == []


def test_a_position_cannot_appear_without_execution():
    assert sequence_defects(
        ["OPPORTUNITY_OBSERVED", "POSITION_OPEN", "RECONCILED"]
    ) == ["OPPORTUNITY_OBSERVED -> POSITION_OPEN is not a legal transition"]


def test_every_illegal_transition_is_reported_not_just_the_first():
    defects = sequence_defects(
        ["OPPORTUNITY_OBSERVED", "POSITION_OPEN", "SIGNAL_FOUND", "RECONCILED"]
    )
    assert len(defects) == 3


def test_a_sequence_that_does_not_start_at_observation_is_a_defect():
    assert any(
        "starts at" in d for d in sequence_defects(["SIGNAL_EVALUATED", "NO_SIGNAL"])
    )


def test_no_fill_is_reachable_from_every_execution_stage():
    for stage in ("CONTRACT_SELECTION", "CAPACITY_EVALUATION", "EXECUTION_EVALUATION"):
        assert may_follow(stage, "NO_FILL")


# ── §11 universe and track ────────────────────────────────────────────────


def _identity(**over):
    kwargs = dict(
        strategy_id="snapback", strategy_version="snapback_core_v1", mode="swing",
        mode_version="snapback_swing_v1", runtime_sha=RUNTIME, release_tag=TAG,
        config={}, rules={}, universe=["NIFTY", "BANKNIFTY"],
    )
    kwargs.update(over)
    return build_identity(**kwargs)


def test_a_widened_universe_is_a_different_experiment():
    """Adding instruments mid-sample adds opportunities earlier trades never had."""
    narrow = _identity(universe=["NIFTY"])
    wide = _identity(universe=["NIFTY", "BANKNIFTY"])
    assert narrow.universe_hash != wide.universe_hash
    assert narrow.identity_hash != wide.identity_hash


def test_universe_order_does_not_matter():
    assert _identity(universe=["BANKNIFTY", "NIFTY"]).universe_hash == (
        _identity(universe=["NIFTY", "BANKNIFTY"]).universe_hash
    )


def test_tracks_are_declared_and_default_to_the_frozen_one():
    assert VALID_TRACKS == {"A", "B", "C"}
    assert _identity().track == "A"
    assert _identity().is_challenger is False


def test_a_challenger_never_collides_with_the_lane_it_challenges():
    frozen = _identity()
    challenger = _identity(track="C", challenger="c01_dte30_45")
    assert challenger.is_challenger is True
    assert challenger.mode_version == "snapback_swing_v1_c01_dte30_45"
    assert challenger.identity_hash != frozen.identity_hash
    assert challenger.lane_key == frozen.lane_key  # same lane, different identity


def test_an_unknown_track_is_refused():
    with pytest.raises(IdentityError):
        _identity(track="D")


def test_a_challenger_slug_must_say_what_changed():
    """"c02" is unreadable six months later, which is when it gets asked about."""
    assert challenger_id("snapback_swing_v1", "c01_dte30_45") == (
        "snapback_swing_v1_c01_dte30_45"
    )
    for vague in ("c02", "", "dte30"):
        with pytest.raises(IdentityError):
            challenger_id("snapback_swing_v1", vague)


# ── §26 registry ──────────────────────────────────────────────────────────


@pytest.fixture()
def warehouse(tmp_path):
    from app.services.snapback_observation_warehouse import SnapbackObservationWarehouse

    w = SnapbackObservationWarehouse(db_path=str(tmp_path / "obs.db"))
    w.init_db()
    return w


def test_the_registry_tables_exist(warehouse):
    conn = sqlite3.connect(warehouse.db_path)
    try:
        tables = {r[0] for r in conn.execute(
            "select name from sqlite_master where type='table'"
        )}
    finally:
        conn.close()
    assert {"strategy_versions", "mode_versions", "operator_actions",
            "health_events"} <= tables


def test_a_recorded_hash_can_be_explained_later(warehouse):
    """Otherwise a report names rule_hash abc123 and nobody knows what it was."""
    identity = _identity()
    warehouse.register_identity(identity, timeline={"hard_max_sessions": 15})
    found = warehouse.known_identity(identity.identity_hash)
    assert found is not None
    assert found["mode_version"] == "snapback_swing_v1"
    assert found["lane_key"] == "snapback:swing"
    assert found["track"] == "A"


def test_registering_the_same_identity_twice_is_not_a_conflict(warehouse):
    identity = _identity()
    warehouse.register_identity(identity)
    warehouse.register_identity(identity)
    conn = sqlite3.connect(warehouse.db_path)
    try:
        assert conn.execute("select count(*) from mode_versions").fetchone()[0] == 1
    finally:
        conn.close()


def test_a_challenger_registers_beside_its_frozen_lane(warehouse):
    warehouse.register_identity(_identity())
    warehouse.register_identity(_identity(track="C", challenger="c01_dte30_45"))
    conn = sqlite3.connect(warehouse.db_path)
    try:
        rows = conn.execute(
            "select mode_version, track from mode_versions order by track"
        ).fetchall()
    finally:
        conn.close()
    assert [r[1] for r in rows] == ["A", "C"]


def test_operator_actions_and_health_events_are_recorded(warehouse):
    warehouse.record_operator_action(action="SAFE_MODE_ON", actor="u", detail="test")
    warehouse.record_health_event(component="broker", status="broker_error")
    conn = sqlite3.connect(warehouse.db_path)
    try:
        assert conn.execute("select count(*) from operator_actions").fetchone()[0] == 1
        assert conn.execute("select count(*) from health_events").fetchone()[0] == 1
    finally:
        conn.close()


# ── §35 depth-aware fill ──────────────────────────────────────────────────


BOOK = [(100.0, 50), (100.5, 50), (101.0, 25)]


def test_a_request_inside_the_touch_fills_at_the_touch():
    result = simulate_fill(side="BUY", quantity=50, levels=BOOK)
    assert result.filled is True
    assert result.average_price == 100.0
    assert result.slippage_vs_touch == 0.0


def test_walking_the_book_costs_slippage():
    """The whole reason LTP is not a fill price."""
    result = simulate_fill(side="BUY", quantity=120, levels=BOOK)
    assert result.filled_quantity == 120
    assert result.average_price > 100.0
    assert result.slippage_vs_touch > 0
    assert result.consumed_levels == 3


def test_more_than_the_visible_book_does_not_fill():
    result = simulate_fill(side="BUY", quantity=200, levels=BOOK)
    assert result.filled is False
    assert result.reason == NO_FILL_INSUFFICIENT_DEPTH
    assert result.visible_quantity == 125


def test_an_empty_book_is_its_own_refusal():
    assert simulate_fill(side="BUY", quantity=10, levels=[]).reason == NO_FILL_NO_BOOK


def test_a_partial_fill_must_be_asked_for():
    """A differently sized trade is a different trade."""
    assert simulate_fill(side="BUY", quantity=200, levels=BOOK).filled_quantity == 0
    allowed = simulate_fill(
        side="BUY", quantity=200, levels=BOOK, partial_allowed=True
    )
    assert allowed.filled_quantity == 125


def test_a_wide_spread_refuses_before_any_fill():
    result = simulate_fill(
        side="BUY", quantity=10, levels=BOOK, max_spread_pct=1.0, opposite_touch=90.0
    )
    assert result.reason == NO_FILL_SPREAD_TOO_WIDE


def test_slippage_is_positive_for_a_seller_walking_down():
    sell_book = [(100.0, 50), (99.5, 50)]
    result = simulate_fill(side="SELL", quantity=100, levels=sell_book)
    assert result.average_price < 100.0
    assert result.slippage_vs_touch > 0


def test_a_zero_quantity_is_a_programming_error_not_a_no_fill():
    with pytest.raises(ValueError):
        simulate_fill(side="BUY", quantity=0, levels=BOOK)


# ── §59 the endpoints that were missing ───────────────────────────────────


@pytest.fixture()
def client() -> TestClient:
    from main import create_app

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: type(
        "U", (), {"user_id": "t", "username": "tester"}
    )()
    return TestClient(app)


def test_lane_evidence_reports_exclusions_not_only_the_count(client):
    body = client.get("/api/v1/strategies/snapback/swing/evidence").json()
    assert body["lane_key"] == "snapback:swing"
    assert "excluded" in body["evidence"]
    assert "unattributed_rows" in body["store_coverage"]


def test_positions_reports_horizon_and_attribution_not_only_pnl(client):
    body = client.get("/api/v1/positions").json()
    assert set(body) >= {
        "positions", "open_count", "hard_exit_due_or_unknown", "unattributed_count"
    }


def test_the_doctor_endpoint_returns_the_same_verdict_as_the_cli(client):
    body = client.post("/api/v1/operator/doctor").json()
    assert set(body) >= {"safe", "exit_code", "checks"}
    assert body["exit_code"] in (0, 1, 2)


def test_leaving_safe_mode_needs_an_acknowledgement_and_a_reason(client):
    assert client.post(
        "/api/v1/operator/safe-mode", json={"engage": False}
    ).status_code == 400
    assert client.post(
        "/api/v1/operator/safe-mode", json={"engage": False, "acknowledge": True}
    ).status_code == 400


def test_engaging_safe_mode_needs_a_reason(client):
    assert client.post(
        "/api/v1/operator/safe-mode", json={"engage": True}
    ).status_code == 400


def test_every_lane_route_is_read_only_except_the_operator_actions(client):
    """Same fix as above: read the fixture's app, and count what was checked so
    an empty route list cannot pass silently."""
    seen = 0
    app = client.app
    for route in app.routes:
        path = getattr(route, "path", "")
        methods = set(getattr(route, "methods", set()))
        if path.startswith(("/api/v1/strategies", "/api/v1/lanes", "/api/v1/focus")):
            assert methods <= {"GET", "HEAD", "OPTIONS"}, path
            seen += 1
        if path.startswith("/api/v1/operator/"):
            assert methods <= {"POST", "HEAD", "OPTIONS"}, path
            seen += 1
    assert seen >= 10
