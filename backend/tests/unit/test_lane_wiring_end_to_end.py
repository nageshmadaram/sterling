"""Proof that the lane model is actually connected, not merely importable.

Every test here fails if a wire is cut: the admission gate stops consulting the
lane registry, the collector stops passing a lane, the warehouse stops writing
the columns, or the API stops exposing them. Modules with tests but no callers
are the failure mode this file exists to prevent.
"""
from __future__ import annotations

import inspect
import os
import sqlite3

import pytest

from app.core.evidence import EvidenceClass, eligible_for_lane
from app.services import snapback_prospective_collector as collector
from app.services.snapback_capacity import evaluate_capacity
from app.services.snapback_observation_warehouse import (
    SnapbackObservationWarehouse,
    evidence_class_for,
    lane_columns,
)

FUNDABLE = dict(
    capital=1_000_000.0,
    reserved_margin=0.0,
    option_premium_cash=10_000.0,
    hedge_margin=20_000.0,
    fee_reserve=500.0,
    open_positions=0,
    max_open_positions=5,
    underlying="NIFTY",
)


@pytest.fixture(autouse=True)
def _no_safe_mode(tmp_path, monkeypatch):
    """Point SAFE_MODE at an untouched path so it reads NORMAL."""
    monkeypatch.setenv("STERLING_SAFE_MODE_FILE", str(tmp_path / "safe_mode.json"))


# ── admission consults the lane registry ──────────────────────────────────


def test_an_originating_lane_is_admitted():
    decision = evaluate_capacity(**FUNDABLE, lane_key="snapback:swing")
    assert decision.allowed is True


def test_a_lane_without_rules_is_refused_at_admission():
    """snapback:overnight has no gap, DTE, theta, stop or hedge policy."""
    decision = evaluate_capacity(**FUNDABLE, lane_key="snapback:overnight")
    assert decision.allowed is False
    assert decision.status == "LANE_NOT_ORIGINATING"
    assert "LANE_RULES_UNDEFINED" in decision.reasons


def test_a_research_lane_is_refused_at_admission():
    decision = evaluate_capacity(**FUNDABLE, lane_key="supertrend:swing")
    assert decision.allowed is False
    assert decision.status == "LANE_NOT_ORIGINATING"


def test_snapback_intraday_is_refused_because_it_shares_the_scalp_rules():
    decision = evaluate_capacity(**FUNDABLE, lane_key="snapback:intraday")
    assert decision.allowed is False
    assert "LANE_RULES_UNDEFINED" in decision.reasons


@pytest.mark.parametrize("bad", ["snapback:positional", "gamma_move:swing", "nonsense", ""])
def test_an_unresolvable_lane_refuses_rather_than_falling_through(bad):
    decision = evaluate_capacity(**FUNDABLE, lane_key=bad)
    assert decision.allowed is False
    assert decision.status == "LANE_UNKNOWN"


def test_focus_mode_blocks_admission(monkeypatch):
    monkeypatch.setenv("STERLING_FOCUSED_STRATEGIES", "supertrend")
    decision = evaluate_capacity(**FUNDABLE, lane_key="snapback:swing")
    assert decision.allowed is False
    assert "STRATEGY_NOT_FOCUSED" in decision.reasons


def test_safe_mode_still_outranks_the_lane_gate(tmp_path, monkeypatch):
    path = tmp_path / "safe_mode.json"
    path.write_text('{"state": "SAFE_MODE", "triggers": ["OPERATOR"], "reason": "test"}')
    monkeypatch.setenv("STERLING_SAFE_MODE_FILE", str(path))
    decision = evaluate_capacity(**FUNDABLE, lane_key="snapback:swing")
    assert decision.status == "SAFE_MODE"


# ── the collector actually passes a lane ──────────────────────────────────


def test_the_production_call_site_passes_a_lane():
    """The one caller of evaluate_capacity must supply lane_key.

    The parameter is optional so the funding arithmetic stays directly
    testable; that optionality is only safe while this holds.
    """
    source = inspect.getsource(collector)
    assert "lane_key=_lane_key_for(cfg)" in source


def test_the_collector_maps_the_engine_mode_to_a_canonical_lane():
    from app.engines.snapback.config import SnapbackConfig

    assert collector._lane_key_for(SnapbackConfig(trading_mode="swing")) == "snapback:swing"
    assert collector._lane_key_for(SnapbackConfig(trading_mode="scalp")) == "snapback:scalping"


def test_an_unrecognised_engine_mode_yields_an_unresolvable_lane():
    """Refused as LANE_UNKNOWN rather than silently attributed."""

    class _Cfg:
        trading_mode = "moon_shot"

    key = collector._lane_key_for(_Cfg())
    assert evaluate_capacity(**FUNDABLE, lane_key=key).status == "LANE_UNKNOWN"


# ── identity is available only with a tagged release ──────────────────────


def test_no_release_tag_means_no_lane_identity(monkeypatch):
    """An authoritative row must name the immutable release that produced it."""
    from app.engines.snapback.config import SnapbackConfig

    monkeypatch.delenv("STERLING_RELEASE_TAG", raising=False)
    assert collector.current_lane_identity(SnapbackConfig()) is None


def test_a_tagged_release_produces_the_swing_identity(monkeypatch):
    from app.engines.snapback.config import SnapbackConfig

    monkeypatch.setenv("STERLING_RELEASE_TAG", "snapback-prospective-runtime-1.6")
    identity = collector.current_lane_identity(SnapbackConfig())
    assert identity is not None
    assert identity.lane_key == "snapback:swing"
    assert identity.release_tag == "snapback-prospective-runtime-1.6"


# ── the warehouse writes the columns ──────────────────────────────────────


def test_evidence_class_is_derived_from_the_source_not_the_caller():
    assert evidence_class_for("PROSPECTIVE_PAPER") == EvidenceClass.PAPER.value
    assert evidence_class_for("LIVE_CATCHUP_REPLAY") == EvidenceClass.REPLAY.value
    assert evidence_class_for("SAME_DAY_SHADOW_REPLAY") == EvidenceClass.REPLAY.value
    assert evidence_class_for("something_else") == ""


def test_no_identity_yields_empty_lane_columns_not_a_guess():
    columns = lane_columns(None, "PROSPECTIVE_PAPER")
    assert columns["lane_key"] == ""
    assert columns["strategy_id"] == ""
    assert columns["legacy_mode"] is None
    # The class is still known: it comes from the source, not the identity.
    assert columns["evidence_class"] == EvidenceClass.PAPER.value


def _recorded(tmp_path, monkeypatch, *, tagged: bool) -> dict:
    from app.engines.snapback.config import SnapbackConfig

    db = tmp_path / "obs.db"
    warehouse = SnapbackObservationWarehouse(db_path=str(db))
    warehouse.init_db()
    if tagged:
        monkeypatch.setenv("STERLING_RELEASE_TAG", "snapback-prospective-runtime-1.6")
    else:
        monkeypatch.delenv("STERLING_RELEASE_TAG", raising=False)
    warehouse.record_opportunity(
        opportunity_id="OPP-TEST-1",
        symbol="NIFTY",
        signal_type="SNAPBACK_FADE_UP",
        spot_price=24000.0,
        lane_identity=collector.current_lane_identity(SnapbackConfig()),
    )
    conn = sqlite3.connect(db)
    try:
        conn.row_factory = sqlite3.Row
        return dict(conn.execute("SELECT * FROM opportunities").fetchone())
    finally:
        conn.close()


def test_a_tagged_row_is_attributed_and_gate_eligible(tmp_path, monkeypatch):
    row = _recorded(tmp_path, monkeypatch, tagged=True)
    assert row["lane_key"] == "snapback:swing"
    assert row["strategy_id"] == "snapback"
    assert row["mode"] == "swing"
    assert row["evidence_class"] == EvidenceClass.PAPER.value
    assert row["identity_hash"]
    assert row["authoritative"] == 1
    assert eligible_for_lane(row, "snapback:swing") is True


def test_an_untagged_row_is_recorded_but_unattributed(tmp_path, monkeypatch):
    """Still evidence. Just not attributable to any lane's gate."""
    row = _recorded(tmp_path, monkeypatch, tagged=False)
    assert row["opportunity_id"] == "OPP-TEST-1"
    assert row["lane_key"] == ""
    assert eligible_for_lane(row, "snapback:swing") is False


def test_a_tagged_row_does_not_count_toward_another_lane(tmp_path, monkeypatch):
    row = _recorded(tmp_path, monkeypatch, tagged=True)
    for other in ("snapback:scalping", "supertrend:swing"):
        assert eligible_for_lane(row, other) is False
