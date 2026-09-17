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


# ── the horizon is enforced at admission and frozen onto the position ─────

IST_ENTRY = "2026-09-18T11:00:00+05:30"          # a trading Friday
SATURDAY = "2026-09-19T11:00:00+05:30"
LATE_DECEMBER = "2026-12-28T11:00:00+05:30"      # +15 sessions leaves the calendar
AFTER_SQUARE_OFF = "2026-09-18T15:30:00+05:30"


def test_a_computable_horizon_passes_through_to_the_funding_check():
    """Not allowed here, but refused for funding — so the horizon check passed."""
    decision = evaluate_capacity(**FUNDABLE, lane_key="snapback:swing", entry_at=IST_ENTRY)
    assert decision.status != "TIMELINE_UNAVAILABLE"


def test_an_entry_on_a_non_session_is_refused():
    decision = evaluate_capacity(**FUNDABLE, lane_key="snapback:swing", entry_at=SATURDAY)
    assert decision.allowed is False
    assert decision.status == "TIMELINE_UNAVAILABLE"


def test_a_swing_that_outruns_the_verified_calendar_is_refused():
    """+15 sessions from late December leaves the verified holiday list."""
    decision = evaluate_capacity(
        **FUNDABLE, lane_key="snapback:swing", entry_at=LATE_DECEMBER
    )
    assert decision.allowed is False
    assert "TIMELINE_CALENDAR_UNKNOWN" in decision.reasons


def test_a_session_bound_entry_after_the_square_off_is_refused():
    decision = evaluate_capacity(
        **FUNDABLE, lane_key="snapback:scalping", entry_at=AFTER_SQUARE_OFF
    )
    assert decision.allowed is False
    assert decision.status == "TIMELINE_UNAVAILABLE"


def test_an_unparseable_entry_timestamp_refuses_rather_than_defaulting_to_now():
    decision = evaluate_capacity(**FUNDABLE, lane_key="snapback:swing", entry_at="yesterday")
    assert decision.allowed is False
    assert "entry_timestamp_unparseable" in decision.reasons


def test_the_production_call_site_passes_the_entry_time():
    assert "entry_at=provider_ts" in inspect.getsource(collector)


def test_the_entry_commit_freezes_a_horizon_plan():
    """Written inside the same transaction as the position it belongs to."""
    source = inspect.getsource(collector)
    assert "horizon_plan=_entry_horizon(cfg, provider_ts)" in source
    assert "lane_identity=current_lane_identity(cfg)" in source


def test_the_collector_builds_a_swing_horizon_for_a_trading_day():
    from app.engines.snapback.config import SnapbackConfig

    plan = collector._entry_horizon(SnapbackConfig(), IST_ENTRY)
    assert plan is not None
    assert plan.mode.value == "swing"
    assert plan.hard_max_sessions == 15
    assert plan.hard_exit_session is not None


def test_an_unusable_entry_timestamp_yields_no_plan_rather_than_a_wrong_one():
    from app.engines.snapback.config import SnapbackConfig

    assert collector._entry_horizon(SnapbackConfig(), "not-a-timestamp") is None


def test_the_position_row_carries_the_frozen_horizon(tmp_path, monkeypatch):
    from app.engines.snapback.config import SnapbackConfig

    monkeypatch.setenv("STERLING_RELEASE_TAG", "snapback-prospective-runtime-1.6")
    db = tmp_path / "obs.db"
    warehouse = SnapbackObservationWarehouse(db_path=str(db))
    warehouse.init_db()

    cfg = SnapbackConfig()
    plan = collector._entry_horizon(cfg, IST_ENTRY)
    warehouse.save_paper_position(
        opportunity_id="OPP-H-1", symbol="NIFTY", option_symbol="NIFTY26SEP24000CE",
        option_qty=75, option_entry_price=120.0, option_expiry="2026-09-24",
        option_strike=24000.0, futures_symbol="NIFTY26SEPFUT", futures_lot_size=75,
        current_futures_lots=1, avg_futures_entry_price=24010.0,
        realized_futures_pnl=0.0, entry_spot=24000.0, entry_timestamp=IST_ENTRY,
        entry_dte=45, entry_iv=14.0, causal_beta=1.0,
        horizon_plan=plan, lane_identity=collector.current_lane_identity(cfg),
    )

    conn = sqlite3.connect(db)
    try:
        conn.row_factory = sqlite3.Row
        row = dict(conn.execute("SELECT * FROM paper_positions").fetchone())
    finally:
        conn.close()

    assert row["horizon_plan_id"] == plan.plan_id
    assert row["hard_exit_session"] == plan.hard_exit_session.isoformat()
    assert row["hard_exit_at"] is None          # session-based, not wall-clock
    assert row["calendar_version"]
    assert row["mode_config_hash"]
    assert row["lane_key"] == "snapback:swing"


def test_a_position_written_without_a_plan_stores_empties_not_guesses(tmp_path):
    db = tmp_path / "obs.db"
    warehouse = SnapbackObservationWarehouse(db_path=str(db))
    warehouse.init_db()
    warehouse.save_paper_position(
        opportunity_id="OPP-H-2", symbol="NIFTY", option_symbol="X", option_qty=75,
        option_entry_price=1.0, option_expiry="2026-09-24", option_strike=1.0,
        futures_symbol="F", futures_lot_size=75, current_futures_lots=0,
        avg_futures_entry_price=0.0, realized_futures_pnl=0.0, entry_spot=1.0,
        entry_timestamp=IST_ENTRY, entry_dte=1, entry_iv=1.0, causal_beta=1.0,
    )
    conn = sqlite3.connect(db)
    try:
        conn.row_factory = sqlite3.Row
        row = dict(conn.execute("SELECT * FROM paper_positions").fetchone())
    finally:
        conn.close()
    assert row["horizon_plan_id"] == ""
    assert row["hard_exit_at"] is None
    assert row["hard_exit_session"] is None
    assert row["lane_key"] == ""
