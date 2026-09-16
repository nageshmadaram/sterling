"""Stale EXIT_PENDING and PROCESSING_ENTRY must actually alert.

derive_operational_alerts() has supported both since it was written, but the scheduler
hardcoded exit_pending_stale=False and processing_entry_stale=False, so neither alert
could ever fire in production.
"""

from __future__ import annotations

import os
import tempfile
import time
from datetime import datetime, timedelta, timezone

import pytest

from app.services.snapback_observation_warehouse import SnapbackObservationWarehouse
from app.services.snapback_stale import (
    PROCESSING_ENTRY_STALE_SECONDS,
    EXIT_PENDING_STALE_SECONDS,
    stale_lifecycle_state,
)


@pytest.fixture
def warehouse():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as fh:
        path = fh.name
    wh = SnapbackObservationWarehouse(db_path=path)
    yield wh
    if os.path.exists(path):
        os.remove(path)


def _position(wh, opp, *, status="OPEN", pending_ts=None):
    wh.save_paper_position(
        opportunity_id=opp, symbol="NIFTY", option_symbol="OPT", option_qty=25,
        option_entry_price=100.0, option_expiry="2026-10-29", option_strike=25000.0,
        futures_symbol="FUT", futures_lot_size=25, current_futures_lots=1,
        avg_futures_entry_price=24500.0, realized_futures_pnl=0.0, entry_spot=24500.0,
        entry_timestamp=datetime.now(timezone.utc).isoformat(), entry_dte=45,
        entry_iv=0.2, causal_beta=1.0,
    )
    if status == "EXIT_PENDING":
        wh.set_paper_position_pending_exit(
            opportunity_id=opp, pending_exit_reason="PREMIUM_STOP",
            pending_exit_option_bid=60.0,
            pending_exit_ts=pending_ts or datetime.now(timezone.utc).isoformat(),
        )


def test_clean_book_is_not_stale(warehouse):
    _position(warehouse, "OPP-1")

    state = stale_lifecycle_state(warehouse)

    assert state["exit_pending_stale"] is False
    assert state["processing_entry_stale"] is False
    assert state["exit_pending_count"] == 0


def test_fresh_exit_pending_is_not_stale(warehouse):
    _position(warehouse, "OPP-1", status="EXIT_PENDING")

    state = stale_lifecycle_state(warehouse)

    assert state["exit_pending_count"] == 1
    assert state["exit_pending_stale"] is False


def test_old_exit_pending_is_stale(warehouse):
    old = (datetime.now(timezone.utc) - timedelta(seconds=EXIT_PENDING_STALE_SECONDS + 60)).isoformat()
    _position(warehouse, "OPP-1", status="EXIT_PENDING", pending_ts=old)

    state = stale_lifecycle_state(warehouse)

    assert state["exit_pending_stale"] is True
    assert state["oldest_exit_pending_age_s"] > EXIT_PENDING_STALE_SECONDS


def test_stuck_processing_entry_is_stale(warehouse):
    warehouse.record_opportunity(
        opportunity_id="OPP-2", symbol="NIFTY", signal_type="SNAPBACK_FADE_UP",
        spot_price=24500.0, trend="BEARISH",
    )
    old_ms = int((time.time() - PROCESSING_ENTRY_STALE_SECONDS - 60) * 1000)
    import sqlite3

    conn = sqlite3.connect(warehouse.db_path)
    try:
        conn.execute(
            "UPDATE opportunities SET status='PROCESSING_ENTRY', processing_started_at_ms=? ",
            (old_ms,),
        )
        conn.commit()
    finally:
        conn.close()

    state = stale_lifecycle_state(warehouse)

    assert state["processing_entry_stale"] is True
    assert state["processing_entry_count"] == 1
    assert state["oldest_processing_entry_age_s"] > PROCESSING_ENTRY_STALE_SECONDS


def test_scheduler_passes_real_stale_flags_to_the_alerts(monkeypatch, tmp_path):
    """The scheduler must compute these, not hardcode False."""
    import inspect

    from app.services import snapback_ops_scheduler as mod

    source = inspect.getsource(mod.SnapbackOpsScheduler._dispatch)

    assert "exit_pending_stale=False" not in source
    assert "processing_entry_stale=False" not in source
    assert "stale_lifecycle_state" in inspect.getsource(mod)


def test_alerts_fire_for_both_stale_conditions():
    from app.services.snapback_alerts import derive_operational_alerts

    health = {
        "status": "HEALTHY", "healthy": True, "runner_alive": True,
        "broker_connected": True, "market_data_fresh": True, "calendar_ok": True,
        "database_ok": True, "manifest_ok": True, "exit_pending": 1,
        "processing_entries": 1, "unresolved_errors": [],
    }

    alerts = derive_operational_alerts(
        health=health, backup_ok=True,
        exit_pending_stale=True, processing_entry_stale=True,
    )

    codes = {a.code for a in alerts}
    assert "exit_pending_unresolved" in codes
    assert "pending_entry_stuck" in codes


def test_health_exposes_stale_counts_and_ages():
    from app.services.snapback_health import build_prospective_health

    class W:
        def opportunity_status_counts(self):
            return {"PROCESSING_ENTRY": 1}

        def paper_position_status_counts(self):
            return {"EXIT_PENDING": 2}

    now = datetime(2026, 9, 16, 6, 0, tzinfo=timezone.utc)
    body = build_prospective_health(
        warehouse=W(), runtime_sha="sha", strategy_manifest="m", manifest_ok=True,
        mode="PAPER", broker_connected=True, market_data_fresh=True, calendar_ok=True,
        database_ok=True, runner_alive=True, last_runner_tick=now,
        stale_state={"exit_pending_stale": True, "processing_entry_stale": False,
                     "oldest_exit_pending_age_s": 5000, "oldest_processing_entry_age_s": None},
        now=now,
    )

    assert body["exit_pending_stale"] is True
    assert body["oldest_exit_pending_age_s"] == 5000
    assert body["healthy"] is False
    assert "exit_pending_unresolved" in body["unresolved_errors"]
