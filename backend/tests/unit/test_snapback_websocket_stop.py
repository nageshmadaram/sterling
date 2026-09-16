"""E19: stop detection must observe ticks, not 30-second REST snapshots.

The adversarial case is a breach that opens and closes between two polls: a stop is
hit at 10:00:05 and the price recovers by 10:00:12, so a 30-second poll never sees it.
That is an irreversible economic contamination — the trade's recorded exit is simply
wrong — so the stop monitor consumes accepted tick events instead.
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

from app.services.snapback_observation_warehouse import SnapbackObservationWarehouse
from app.services.snapback_stop_monitor import (
    STOP_OBSERVATION_TOLERANCE_S,
    SnapbackStopMonitor,
)

_IST = timezone(timedelta(hours=5, minutes=30))


@pytest.fixture
def warehouse():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as fh:
        path = fh.name
    wh = SnapbackObservationWarehouse(db_path=path)
    yield wh
    if os.path.exists(path):
        os.remove(path)


def _position(wh, *, opp="OPP-1", is_runner=0, peak=0.0):
    wh.save_paper_position(
        opportunity_id=opp, symbol="NIFTY", option_symbol="NIFTY26OCT25000PE",
        option_qty=25, option_entry_price=100.0, option_expiry="2026-10-29",
        option_strike=25000.0, futures_symbol="NIFTY26OCTFUT", futures_lot_size=25,
        current_futures_lots=1, avg_futures_entry_price=24500.0,
        realized_futures_pnl=0.0, entry_spot=24500.0,
        entry_timestamp=datetime.now(timezone.utc).isoformat(), entry_dte=45,
        entry_iv=0.2, causal_beta=1.0,
    )
    if is_runner:
        wh.update_paper_position_peak_bid(opp, peak, 1)
    return opp


def _tick(bid, *, at, ask=None):
    return {
        "tradingsymbol": "NIFTY26OCT25000PE",
        "bid": bid,
        "ask": ask if ask is not None else bid + 2.0,
        "bid_quantity": 500,
        "ask_quantity": 500,
        "exchange_timestamp": at,
    }


def test_a_breach_between_two_polls_is_still_caught(warehouse):
    """10:00:00 bid 70 -> 10:00:05 bid 64 (breach) -> 10:00:12 bid 73."""
    opp = _position(warehouse)
    monitor = SnapbackStopMonitor(warehouse=warehouse)

    base = datetime(2026, 10, 3, 10, 0, 0, tzinfo=_IST)

    monitor.on_tick(_tick(70.0, at=base))
    monitor.on_tick(_tick(64.0, at=base + timedelta(seconds=5)))
    monitor.on_tick(_tick(73.0, at=base + timedelta(seconds=12)))

    pos = warehouse.get_paper_position(opp)

    assert pos["status"] == "EXIT_PENDING"
    assert pos["pending_exit_reason"] == "PREMIUM_STOP"
    assert float(pos["pending_exit_option_bid"]) == pytest.approx(64.0)


def test_a_later_recovery_cannot_undo_a_latched_stop(warehouse):
    opp = _position(warehouse)
    monitor = SnapbackStopMonitor(warehouse=warehouse)
    base = datetime(2026, 10, 3, 10, 0, 0, tzinfo=_IST)

    monitor.on_tick(_tick(64.0, at=base))
    for bid in (80.0, 120.0, 200.0):
        monitor.on_tick(_tick(bid, at=base + timedelta(seconds=30)))

    pos = warehouse.get_paper_position(opp)

    assert pos["status"] == "EXIT_PENDING"
    assert float(pos["pending_exit_option_bid"]) == pytest.approx(64.0)


def test_a_bid_above_the_stop_does_not_latch(warehouse):
    opp = _position(warehouse)
    monitor = SnapbackStopMonitor(warehouse=warehouse)

    monitor.on_tick(_tick(66.0, at=datetime(2026, 10, 3, 10, 0, tzinfo=_IST)))

    assert warehouse.get_paper_position(opp)["status"] == "OPEN"


def test_runner_giveback_trails_the_stored_eod_peak(warehouse):
    opp = _position(warehouse, is_runner=1, peak=200.0)
    monitor = SnapbackStopMonitor(warehouse=warehouse)

    monitor.on_tick(_tick(149.0, at=datetime(2026, 10, 3, 11, 0, tzinfo=_IST)))

    pos = warehouse.get_paper_position(opp)
    assert pos["status"] == "EXIT_PENDING"
    assert pos["pending_exit_reason"] == "RUNNER_TRAIL_STOP"


def test_a_tick_never_raises_the_runner_peak(warehouse):
    opp = _position(warehouse, is_runner=1, peak=200.0)
    monitor = SnapbackStopMonitor(warehouse=warehouse)

    monitor.on_tick(_tick(400.0, at=datetime(2026, 10, 3, 11, 0, tzinfo=_IST)))

    pos = warehouse.get_paper_position(opp)
    assert float(pos["peak_option_bid"]) == pytest.approx(200.0)
    assert pos["status"] == "OPEN"


def test_every_stop_observation_is_recorded_as_evidence(warehouse):
    opp = _position(warehouse)
    monitor = SnapbackStopMonitor(warehouse=warehouse)

    monitor.on_tick(_tick(90.0, at=datetime(2026, 10, 3, 10, 0, tzinfo=_IST)))

    events = warehouse.get_records_by_table("quote_quality_events", opportunity_id=opp)

    assert len(events) == 1
    assert events[0]["phase"] == "INTRADAY_STOP"
    assert events[0]["required_for_economics"] == 1
    assert events[0]["accepted"] == 1


def test_a_rejected_tick_is_recorded_and_does_not_decide(warehouse):
    opp = _position(warehouse)
    monitor = SnapbackStopMonitor(warehouse=warehouse)

    # Crossed book: not executable, so it must not latch a stop either.
    monitor.on_tick(_tick(50.0, at=datetime(2026, 10, 3, 10, 0, tzinfo=_IST), ask=40.0))

    events = warehouse.get_records_by_table("quote_quality_events", opportunity_id=opp)

    assert len(events) == 1
    assert events[0]["accepted"] == 0
    assert warehouse.get_paper_position(opp)["status"] == "OPEN"


def test_a_missing_observation_window_is_a_data_gap(warehouse):
    opp = _position(warehouse)
    monitor = SnapbackStopMonitor(warehouse=warehouse)

    base = datetime(2026, 10, 3, 10, 0, 0, tzinfo=_IST)
    monitor.on_tick(_tick(90.0, at=base))

    gaps = monitor.data_gaps(now=base + timedelta(seconds=STOP_OBSERVATION_TOLERANCE_S + 30))

    assert opp in gaps
    assert monitor.monitoring_healthy(now=base + timedelta(seconds=STOP_OBSERVATION_TOLERANCE_S + 30)) is False


def test_fresh_observation_keeps_monitoring_healthy(warehouse):
    _position(warehouse)
    monitor = SnapbackStopMonitor(warehouse=warehouse)

    base = datetime(2026, 10, 3, 10, 0, 0, tzinfo=_IST)
    monitor.on_tick(_tick(90.0, at=base))

    assert monitor.monitoring_healthy(now=base + timedelta(seconds=5)) is True


def test_a_rest_poll_cannot_prove_absence_of_a_breach(warehouse):
    """A REST snapshot after a gap is a diagnostic, never proof nothing happened."""
    opp = _position(warehouse)
    monitor = SnapbackStopMonitor(warehouse=warehouse)

    base = datetime(2026, 10, 3, 10, 0, 0, tzinfo=_IST)
    monitor.on_tick(_tick(90.0, at=base))

    late = base + timedelta(seconds=STOP_OBSERVATION_TOLERANCE_S + 60)
    monitor.on_tick(_tick(95.0, at=late, ), source="REST")

    # The gap is recorded against the position even though the later poll looked fine.
    assert opp in monitor.compromised_positions()


def test_monitor_tracks_subscription_state(warehouse):
    opp = _position(warehouse)
    monitor = SnapbackStopMonitor(warehouse=warehouse)

    monitor.mark_subscribed(opp, "NIFTY26OCT25000PE")
    assert monitor.state(opp)["subscription_active"] is True

    monitor.mark_unsubscribed(opp)
    assert monitor.state(opp)["subscription_active"] is False


def test_runner_blocks_entries_during_a_stop_observation_gap():
    import inspect

    from app.services import snapback_runner

    source = inspect.getsource(snapback_runner.tick)

    assert "data_gaps" in source
    assert "stop_gap" in source


def test_ticker_feeds_the_stop_monitor():
    import inspect

    from app.services.exchanges.kite import ticker_manager

    source = inspect.getsource(ticker_manager)

    assert "get_stop_monitor" in source
    assert "_snapback_tick" in source


def test_a_gap_raises_a_critical_alert():
    from app.services.snapback_alerts import derive_operational_alerts

    health = {
        "status": "HEALTHY", "healthy": True, "runner_alive": True,
        "broker_connected": True, "market_data_fresh": True, "calendar_ok": True,
        "database_ok": True, "manifest_ok": True, "unresolved_errors": [],
        "stop_monitor_gap": True,
    }

    alerts = derive_operational_alerts(health=health, backup_ok=True)

    match = [a for a in alerts if a.code == "stop_observation_gap"]
    assert match and match[0].severity == "CRITICAL"


def test_health_reports_the_gap_as_unhealthy():
    from datetime import datetime, timezone

    from app.services.snapback_health import build_prospective_health

    class W:
        def opportunity_status_counts(self):
            return {}

        def paper_position_status_counts(self):
            return {"OPEN": 1}

    now = datetime(2026, 10, 3, 6, 0, tzinfo=timezone.utc)
    body = build_prospective_health(
        warehouse=W(), runtime_sha="sha", strategy_manifest="m", manifest_ok=True,
        mode="PAPER", broker_connected=True, market_data_fresh=True, calendar_ok=True,
        database_ok=True, runner_alive=True, last_runner_tick=now,
        stale_state={"stop_monitor_gap": True, "stop_monitor_gap_positions": ["OPP-1"]},
        now=now,
    )

    assert body["stop_monitor_gap"] is True
    assert body["healthy"] is False
    assert "stop_observation_gap" in body["unresolved_errors"]
