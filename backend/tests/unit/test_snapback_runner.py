"""Operational unit tests for Snapback unattended prospective runner & two-phase lifecycle."""

from __future__ import annotations

import os
import time
import tempfile
from datetime import date, datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app.engines.snapback import SnapbackConfig, SnapbackSignal
from app.engines.snapback.intraday_models import RawQuoteEvent
from app.services.snapback_prospective_collector import SnapbackProspectiveCollector, SnapbackObservationWarehouse
from app.services.snapback import process_prospective_pending_entries, process_prospective_daily_mtm_and_exits
from app.services.snapback_runner import tick, _is_nse_trading_day


def _nifty_identity():
    """The provider identity a scan would have captured for NIFTY."""
    from app.services.snapback_instrument_identity import identity_from_instrument

    return identity_from_instrument(
        {"tradingsymbol": "NIFTY 50", "instrument_token": 256265,
         "exchange": "NSE", "name": "NIFTY"},
        canonical_symbol="NIFTY",
    )



@pytest.fixture(autouse=True)
def _observed_opening_window(monkeypatch):
    """These tests exercise fill mechanics, not observation continuity.

    The continuity proof is covered by test_snapback_entry_observation_window.py; here
    the opening window is presented as continuously observed so the invariant under
    test is the one that decides.
    """
    from app.services.snapback_entry_observation import ContinuityVerdict

    monkeypatch.setattr(
        "app.services.snapback.session_continuity",
        lambda *a, **k: ContinuityVerdict(continuous=True, reasons=[]),
        raising=False,
    )



_IST = timezone(timedelta(hours=5, minutes=30))


@pytest.fixture
def temp_warehouse():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    warehouse = SnapbackObservationWarehouse(db_path=db_path)
    yield warehouse
    if os.path.exists(db_path):
        os.remove(db_path)


@pytest.fixture
def sample_config():
    return SnapbackConfig(enabled=True)


def build_mock_client():
    mock_client = AsyncMock()
    mock_client.get_quote = AsyncMock(return_value={
        "NSE:NIFTY": {"last_price": 24510.0},
        "NSE:NIFTY 50": {"last_price": 24510.0},
        "NFO:NIFTY26OCTFUT": {
            "last_price": 24511.0,
            "buy_price": 24510.0,
            "sell_price": 24512.0,
            "buy_quantity": 100,
            "sell_quantity": 100,
            "depth": {"buy": [{"price": 24510.0, "quantity": 100}], "sell": [{"price": 24512.0, "quantity": 100}]},
            "timestamp": "2026-09-16T09:19:59+05:30",
            "oi": 500000,
        },
        "NFO:NIFTY26OCT25000PE": {
            "last_price": 101.0,
            "buy_price": 100.0,
            "sell_price": 102.0,
            "buy_quantity": 50,
            "sell_quantity": 50,
            "depth": {"buy": [{"price": 100.0, "quantity": 50}], "sell": [{"price": 102.0, "quantity": 50}]},
            "timestamp": "2026-09-16T09:19:59+05:30",
            "oi": 60000,
        },
    })
    mock_client.search_instruments = AsyncMock(return_value=[
        {
            "name": "NIFTY",
            "tradingsymbol": "NIFTY26OCTFUT",
            "instrument_name": "NIFTY26OCTFUT",
            "segment": "NFO-FUT",
            "instrument_type": "FUT",
            "strike": 0.0,
            "expiry": "2026-10-29",
            "expiry_date": "2026-10-29",
            "instrument_token": 67890,
            "token": 67890,
            "lot_size": 15,
        },
        {
            "name": "NIFTY",
            "tradingsymbol": "NIFTY26OCT25000PE",
            "instrument_name": "NIFTY26OCT25000PE",
            "segment": "NFO-OPT",
            "instrument_type": "PE",
            "option_type": "PE",
            "strike": 25000.0,
            "dte": 45,
            "expiry_date": "2026-10-29",
            "expiry": "2026-10-29",
            "instrument_token": 12345,
            "token": 12345,
            "lot_size": 25,
        },
    ])
    return mock_client


@pytest.mark.asyncio
async def test_unattended_paper_entry_without_ui(temp_warehouse, sample_config, monkeypatch):
    """Requirement 1: Backend runner starts with pending T+1 opportunity and no UI client connected -> paper entry occurs during opening window."""
    monkeypatch.setattr(
        "app.services.snapback_prospective_collector.SnapbackObservationWarehouse",
        lambda *a, **k: temp_warehouse
    )

    collector = SnapbackProspectiveCollector(warehouse=temp_warehouse)
    dt_t = datetime(2026, 9, 15, 10, 0, 0, tzinfo=timezone.utc)
    sig = SnapbackSignal(
        symbol="NIFTY",
        side="fade_up",
        direction="BEARISH",
        option_type="PE",
        timestamp_ms=int(dt_t.timestamp() * 1000),
        entry=24500.0,
        mean_target=24800.0,
        stretch=1.8,
        atr=180.0,
        realized_vol=0.15,
        assumed_iv=0.18,
        level=24400.0,
        strength="MODERATE",
    )
    rec_res = collector.record_signal_at_close(signal=sig, cfg=sample_config, identity=_nifty_identity())
    opp_id = rec_res["opportunity_id"]

    # Target T+1 time: 2026-09-16 09:20 IST (03:50 UTC)
    t1_dt = datetime(2026, 9, 16, 3, 50, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("time.time", lambda: t1_dt.timestamp())

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is not None:
                return t1_dt.astimezone(tz)
            return t1_dt

    monkeypatch.setattr("app.services.snapback.datetime", FixedDateTime)
    monkeypatch.setattr("app.services.snapback_runner.datetime", FixedDateTime)

    mock_client = build_mock_client()

    mock_acct = MagicMock()
    mock_acct.connected = True
    monkeypatch.setattr("app.services.exchanges.kite.accounts.get_active", lambda uid: mock_acct)
    monkeypatch.setattr("app.services.exchanges.kite.accounts.all_accounts", lambda: [mock_acct])
    monkeypatch.setattr("app.services.exchanges.kite.accounts.acquire_client", AsyncMock(return_value=mock_client))

    # Execute unattended runner tick without UI connection
    res = await tick(uid="default")

    assert res["status"] == "ok"
    assert res["entries_processed"] == 1

    pos = temp_warehouse.get_paper_position(opp_id)
    assert pos is not None
    assert pos["status"] == "OPEN"
    assert pos["option_qty"] == 25


@pytest.mark.asyncio
async def test_restart_resilience_at_0925(temp_warehouse, sample_config, monkeypatch):
    """Requirement 2: Restart at 09:25 -> unresolved PENDING_ENTRY opportunity is discovered and resumed."""
    monkeypatch.setattr(
        "app.services.snapback_prospective_collector.SnapbackObservationWarehouse",
        lambda *a, **k: temp_warehouse
    )

    collector = SnapbackProspectiveCollector(warehouse=temp_warehouse)
    dt_t = datetime(2026, 9, 15, 10, 0, 0, tzinfo=timezone.utc)
    sig = SnapbackSignal(
        symbol="NIFTY",
        side="fade_up",
        direction="BEARISH",
        option_type="PE",
        timestamp_ms=int(dt_t.timestamp() * 1000),
        entry=24500.0,
        mean_target=24800.0,
        stretch=1.8,
        atr=180.0,
        realized_vol=0.15,
        assumed_iv=0.18,
        level=24400.0,
        strength="MODERATE",
    )
    rec_res = collector.record_signal_at_close(signal=sig, cfg=sample_config, identity=_nifty_identity())
    opp_id = rec_res["opportunity_id"]

    # Simulating backend restart at 09:25:00 IST (03:55:00 UTC)
    restart_dt = datetime(2026, 9, 16, 3, 55, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("time.time", lambda: restart_dt.timestamp())

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is not None:
                return restart_dt.astimezone(tz)
            return restart_dt

    monkeypatch.setattr("app.services.snapback.datetime", FixedDateTime)
    monkeypatch.setattr("app.services.snapback_runner.datetime", FixedDateTime)

    mock_client = build_mock_client()
    # Align quote timestamp with 09:25:00 IST (09:24:59 IST)
    mock_client.get_quote = AsyncMock(return_value={
        "NSE:NIFTY": {"last_price": 24510.0},
        "NSE:NIFTY 50": {"last_price": 24510.0},
        "NFO:NIFTY26OCTFUT": {
            "last_price": 24511.0,
            "buy_price": 24510.0,
            "sell_price": 24512.0,
            "buy_quantity": 100,
            "sell_quantity": 100,
            "depth": {"buy": [{"price": 24510.0, "quantity": 100}], "sell": [{"price": 24512.0, "quantity": 100}]},
            "timestamp": "2026-09-16T09:24:59+05:30",
            "oi": 500000,
        },
        "NFO:NIFTY26OCT25000PE": {
            "last_price": 101.0,
            "buy_price": 100.0,
            "sell_price": 102.0,
            "buy_quantity": 50,
            "sell_quantity": 50,
            "depth": {"buy": [{"price": 100.0, "quantity": 50}], "sell": [{"price": 102.0, "quantity": 50}]},
            "timestamp": "2026-09-16T09:24:59+05:30",
            "oi": 60000,
        },
    })

    mock_acct = MagicMock()
    mock_acct.connected = True
    monkeypatch.setattr("app.services.exchanges.kite.accounts.get_active", lambda uid: mock_acct)
    monkeypatch.setattr("app.services.exchanges.kite.accounts.all_accounts", lambda: [mock_acct])
    monkeypatch.setattr("app.services.exchanges.kite.accounts.acquire_client", AsyncMock(return_value=mock_client))

    # Runner tick after restart at 09:25 IST
    res = await tick(uid="default")

    assert res["status"] == "ok"
    pos = temp_warehouse.get_paper_position(opp_id)
    assert pos is not None
    assert pos["status"] == "OPEN"


@pytest.mark.asyncio
async def test_double_tick_opening_window_idempotency(temp_warehouse, sample_config, monkeypatch):
    """Requirement 3: Two runner ticks during entry window -> exactly ONE paper position / fill created."""
    monkeypatch.setattr(
        "app.services.snapback_prospective_collector.SnapbackObservationWarehouse",
        lambda *a, **k: temp_warehouse
    )

    collector = SnapbackProspectiveCollector(warehouse=temp_warehouse)
    dt_t = datetime(2026, 9, 15, 10, 0, 0, tzinfo=timezone.utc)
    sig = SnapbackSignal(
        symbol="NIFTY",
        side="fade_up",
        direction="BEARISH",
        option_type="PE",
        timestamp_ms=int(dt_t.timestamp() * 1000),
        entry=24500.0,
        mean_target=24800.0,
        stretch=1.8,
        atr=180.0,
        realized_vol=0.15,
        assumed_iv=0.18,
        level=24400.0,
        strength="MODERATE",
    )
    rec_res = collector.record_signal_at_close(signal=sig, cfg=sample_config, identity=_nifty_identity())
    opp_id = rec_res["opportunity_id"]

    t1_dt = datetime(2026, 9, 16, 3, 50, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("time.time", lambda: t1_dt.timestamp())

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is not None:
                return t1_dt.astimezone(tz)
            return t1_dt

    monkeypatch.setattr("app.services.snapback.datetime", FixedDateTime)

    mock_client = build_mock_client()

    # Tick 1: Executes entry
    count1 = await process_prospective_pending_entries(mock_client, sample_config)
    assert count1 == 1

    # Tick 2: Runs again immediately
    count2 = await process_prospective_pending_entries(mock_client, sample_config)
    assert count2 == 0

    # Verify exactly 1 paper fill in warehouse
    fills = temp_warehouse.get_records_by_table("paper_fills", opportunity_id=opp_id)
    assert len(fills) == 1


@pytest.mark.asyncio
async def test_eod_position_phase_double_execution_idempotency(temp_warehouse, sample_config, monkeypatch):
    """Requirement 4: EOD position phase invoked twice in same session -> exactly ONE MTM/rebalance decision recorded."""
    monkeypatch.setattr(
        "app.services.snapback_prospective_collector.SnapbackObservationWarehouse",
        lambda *a, **k: temp_warehouse
    )

    collector = SnapbackProspectiveCollector(warehouse=temp_warehouse)
    dt_t = datetime(2026, 9, 15, 10, 0, 0, tzinfo=timezone.utc)
    sig = SnapbackSignal(
        symbol="NIFTY",
        side="fade_up",
        direction="BEARISH",
        option_type="PE",
        timestamp_ms=int(dt_t.timestamp() * 1000),
        entry=24500.0,
        mean_target=24800.0,
        stretch=1.8,
        atr=180.0,
        realized_vol=0.15,
        assumed_iv=0.18,
        level=24400.0,
        strength="MODERATE",
    )
    rec_res = collector.record_signal_at_close(signal=sig, cfg=sample_config, identity=_nifty_identity())
    opp_id = rec_res["opportunity_id"]

    t1_dt = datetime(2026, 9, 16, 3, 50, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("time.time", lambda: t1_dt.timestamp())

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is not None:
                return t1_dt.astimezone(tz)
            return t1_dt

    monkeypatch.setattr("app.services.snapback.datetime", FixedDateTime)

    mock_client = build_mock_client()

    # Open paper position
    await process_prospective_pending_entries(mock_client, sample_config)
    pos = temp_warehouse.get_paper_position(opp_id)
    assert pos is not None

    # EOD MTM Invocation 1 (15:15 IST)
    mtm_dt = datetime(2026, 9, 16, 9, 45, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("time.time", lambda: mtm_dt.timestamp())

    class FixedDateTimeMTM(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is not None:
                return mtm_dt.astimezone(tz)
            return mtm_dt

    monkeypatch.setattr("app.services.snapback.datetime", FixedDateTimeMTM)

    # Align mock quote timestamps with 15:15:00 IST (09:44:59 UTC / 15:14:59 IST)
    mock_client.get_quote = AsyncMock(return_value={
        "NSE:NIFTY": {"last_price": 24510.0},
        "NSE:NIFTY 50": {"last_price": 24510.0},
        "NFO:NIFTY26OCTFUT": {
            "last_price": 24511.0,
            "buy_price": 24510.0,
            "sell_price": 24512.0,
            "buy_quantity": 100,
            "sell_quantity": 100,
            "depth": {"buy": [{"price": 24510.0, "quantity": 100}], "sell": [{"price": 24512.0, "quantity": 100}]},
            "timestamp": "2026-09-16T15:14:59+05:30",
            "oi": 500000,
        },
        "NFO:NIFTY26OCT25000PE": {
            "last_price": 101.0,
            "buy_price": 100.0,
            "sell_price": 102.0,
            "buy_quantity": 50,
            "sell_quantity": 50,
            "depth": {"buy": [{"price": 100.0, "quantity": 50}], "sell": [{"price": 102.0, "quantity": 50}]},
            "timestamp": "2026-09-16T15:14:59+05:30",
            "oi": 60000,
        },
    })

    c1 = await process_prospective_daily_mtm_and_exits(mock_client, sample_config)
    assert c1 == 1

    # EOD MTM Invocation 2 (Second tick during 15:15 IST)
    c2 = await process_prospective_daily_mtm_and_exits(mock_client, sample_config)
    assert c2 == 0

    # Verify exactly 1 daily MTM record for 2026-09-16
    mtm_records = temp_warehouse.get_records_by_table("daily_mtm", opportunity_id=opp_id)
    assert len(mtm_records) == 1
    assert mtm_records[0]["session_date"] == "2026-09-16"


@pytest.mark.asyncio
async def test_weekend_holiday_suppression(monkeypatch):
    """Requirement 5: Weekend / NSE holiday -> no cycle executed."""
    # 2026-09-19 is a Saturday
    saturday_dt = datetime(2026, 9, 19, 4, 0, 0, tzinfo=timezone.utc)

    class FixedDateTimeSat(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is not None:
                return saturday_dt.astimezone(tz)
            return saturday_dt

    monkeypatch.setattr("app.services.snapback_runner.datetime", FixedDateTimeSat)

    res = await tick(uid="default")
    assert res["status"] == "market_closed_weekend_or_holiday"
    assert res.get("entries_processed") is None


@pytest.mark.asyncio
async def test_disconnected_kite_fail_closed(temp_warehouse, sample_config, monkeypatch):
    """Requirement 6: Stale/disconnected Kite -> INCONCLUSIVE/gap, never synthetic evidence or crash."""
    monkeypatch.setattr(
        "app.services.snapback_prospective_collector.SnapbackObservationWarehouse",
        lambda *a, **k: temp_warehouse
    )

    t1_dt = datetime(2026, 9, 16, 3, 50, 0, tzinfo=timezone.utc)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is not None:
                return t1_dt.astimezone(tz)
            return t1_dt

    monkeypatch.setattr("app.services.snapback_runner.datetime", FixedDateTime)

    # Disconnected Kite account
    mock_acct = MagicMock()
    mock_acct.connected = False
    monkeypatch.setattr("app.services.exchanges.kite.accounts.get_active", lambda uid: mock_acct)
    monkeypatch.setattr("app.services.exchanges.kite.accounts.all_accounts", lambda: [mock_acct])

    res = await tick(uid="default")

    assert res["status"] == "kite_disconnected_or_unavailable"
    # No crash, no synthetic fills written
    fills = temp_warehouse.get_records_by_table("paper_fills")
    assert len(fills) == 0


@pytest.mark.asyncio
async def test_runner_never_invokes_broker_order_execution():
    """Requirement 7: Verify runner code contains zero invocations of broker order placement methods."""
    import ast
    import app.services.snapback_runner as runner_mod
    import inspect

    source = inspect.getsource(runner_mod)
    tree = ast.parse(source)

    forbidden = {"place_order", "modify_order", "cancel_order"}
    found_calls = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute):
                if node.func.attr in forbidden:
                    found_calls.add(node.func.attr)
            elif isinstance(node.func, ast.Name):
                if node.func.id in forbidden:
                    found_calls.add(node.func.id)

    assert len(found_calls) == 0, f"Runner unlawfully contains broker order calls: {found_calls}"


@pytest.mark.asyncio
async def test_crash_after_processing_entry_recovers_deterministically(temp_warehouse, sample_config, monkeypatch):
    """Operational Test 1: Crash after PROCESSING_ENTRY -> restart recovers deterministically."""
    collector = SnapbackProspectiveCollector(warehouse=temp_warehouse)
    monkeypatch.setattr(
        "app.services.snapback_prospective_collector.SnapbackProspectiveCollector",
        lambda *a, **k: collector
    )

    t1_dt = datetime(2026, 9, 16, 3, 50, 0, tzinfo=timezone.utc)
    sig_dt = datetime(2026, 9, 15, 10, 0, 0, tzinfo=timezone.utc)

    opp_id = "OPP-CRASH-TEST-001"
    temp_warehouse.record_opportunity(
        identity=_nifty_identity(),
        opportunity_id=opp_id,
        symbol="NIFTY",
        signal_type="SNAPBACK_FADE_UP",
        spot_price=24500.0,
        signal_timestamp=sig_dt.isoformat(),
        signal_side="fade_up",
    )

    # Simulate crash mid-flight: status left as PROCESSING_ENTRY
    temp_warehouse.update_opportunity_status(opp_id, "PROCESSING_ENTRY")

    mock_client = build_mock_client()
    mock_acct = MagicMock()
    mock_acct.connected = True
    monkeypatch.setattr("app.services.exchanges.kite.accounts.get_active", lambda uid: mock_acct)
    monkeypatch.setattr("app.services.exchanges.kite.accounts.acquire_client", AsyncMock(return_value=mock_client))

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is not None:
                return t1_dt.astimezone(tz)
            return t1_dt

    monkeypatch.setattr("app.services.snapback_runner.get_config", lambda uid="default": sample_config)
    monkeypatch.setattr("app.services.snapback.get_config", lambda uid="default": sample_config)

    monkeypatch.setattr("time.time", lambda: t1_dt.timestamp())
    monkeypatch.setattr("app.services.snapback_runner.datetime", FixedDateTime)
    monkeypatch.setattr("app.services.snapback.datetime", FixedDateTime)
    monkeypatch.setattr("app.services.snapback_prospective_collector.datetime", FixedDateTime)

    # Run pending entries (recovers stranded PROCESSING_ENTRY)
    processed = await process_prospective_pending_entries(mock_client, sample_config)

    print("DECISIONS:", temp_warehouse.get_records_by_table("decisions"))
    assert processed == 1
    opp_after = temp_warehouse.get_opportunity_by_id(opp_id)
    assert opp_after["status"] == "OPEN_POSITION"

    pos = temp_warehouse.get_paper_position(opp_id)
    assert pos is not None
    assert pos["symbol"] == "NIFTY"


@pytest.mark.asyncio
async def test_option_crosses_premium_stop_intraday_then_recovers(temp_warehouse, sample_config, monkeypatch):
    """Operational Test 2: Option crosses premium stop intraday then recovers -> stop remains triggered."""
    collector = SnapbackProspectiveCollector(warehouse=temp_warehouse)
    monkeypatch.setattr(
        "app.services.snapback_prospective_collector.SnapbackProspectiveCollector",
        lambda *a, **k: collector
    )

    opp_id = "OPP-STOP-RECOVER-001"
    temp_warehouse.record_opportunity(
        identity=_nifty_identity(),
        opportunity_id=opp_id,
        symbol="NIFTY",
        signal_type="SNAPBACK_FADE_UP",
        spot_price=24500.0,
        status="OPEN_POSITION",
    )

    # Save active paper position (entry_price = 100.0, stop_pct = 50.0 -> stop_price = 50.0)
    temp_warehouse.save_paper_position(
        opportunity_id=opp_id,
        symbol="NIFTY",
        option_symbol="NIFTY26OCT25000PE",
        option_qty=75,
        option_entry_price=100.0,
        option_expiry="2026-10-26",
        option_strike=25000.0,
        futures_symbol="NIFTY26OCTFUT",
        futures_lot_size=65,
        current_futures_lots=1,
        avg_futures_entry_price=24510.0,
        realized_futures_pnl=0.0,
        entry_spot=24500.0,
        entry_timestamp="2026-09-15T09:20:00+05:30",
        entry_dte=45,
        entry_iv=0.20,
        causal_beta=1.0,
        peak_option_bid=100.0,
        status="OPEN",
    )

    monkeypatch.setattr("app.services.snapback_runner.get_config", lambda uid="default": sample_config)
    monkeypatch.setattr("app.services.snapback.get_config", lambda uid="default": sample_config)

    now_dt = datetime(2026, 9, 16, 6, 0, 0, tzinfo=timezone.utc)
    q_time = "2026-09-16T11:30:00+05:30"
    monkeypatch.setattr("time.time", lambda: now_dt.timestamp())

    # Step 1: Intraday price drops to 40.0 (crosses 50.0 stop)
    mock_client_stop = AsyncMock()
    mock_client_stop.get_quote = AsyncMock(return_value={
        "NSE:NIFTY": {"last_price": 24500.0},
        "NFO:NIFTY26OCT25000PE": {
            "last_price": 40.0, "buy_price": 40.0, "sell_price": 41.0,
            "buy_quantity": 100, "sell_quantity": 100,
            "depth": {"buy": [{"price": 40.0, "quantity": 100}], "sell": [{"price": 41.0, "quantity": 100}]},
            "timestamp": q_time, "oi": 50000,
        },
        "NFO:NIFTY26OCTFUT": {
            "last_price": 24510.0, "buy_price": 24510.0, "sell_price": 24512.0,
            "buy_quantity": 100, "sell_quantity": 100,
            "depth": {"buy": [{"price": 24510.0, "quantity": 100}], "sell": [{"price": 24512.0, "quantity": 100}]},
            "timestamp": q_time, "oi": 500000,
        },
    })

    from app.services.snapback import process_prospective_intraday_risk
    processed = await process_prospective_intraday_risk(mock_client_stop, sample_config)
    assert processed == 1

    opp_stopped = temp_warehouse.get_opportunity_by_id(opp_id)
    assert opp_stopped["status"] == "CLOSED"

    outcomes_stopped = temp_warehouse.get_records_by_table("outcomes")
    assert len(outcomes_stopped) > 0
    assert outcomes_stopped[0]["exit_reason"] == "PREMIUM_STOP"

    # Step 2: Price recovers to 120.0 later in session
    mock_client_recover = AsyncMock()
    mock_client_recover.get_quote = AsyncMock(return_value={
        "NSE:NIFTY": {"last_price": 24500.0},
        "NFO:NIFTY26OCT25000PE": {
            "last_price": 120.0, "buy_price": 120.0, "sell_price": 121.0,
            "buy_quantity": 100, "sell_quantity": 100,
            "depth": {"buy": [{"price": 120.0, "quantity": 100}], "sell": [{"price": 121.0, "quantity": 100}]},
            "timestamp": q_time, "oi": 50000,
        },
    })

    # Run intraday risk & daily MTM again
    await process_prospective_intraday_risk(mock_client_recover, sample_config)
    await process_prospective_daily_mtm_and_exits(mock_client_recover, sample_config)

    # Position MUST remain CLOSED with PREMIUM_STOP
    opp_final = temp_warehouse.get_opportunity_by_id(opp_id)
    assert opp_final["status"] == "CLOSED"
    outcomes_final = temp_warehouse.get_records_by_table("outcomes")
    assert len(outcomes_final) == 1
    assert outcomes_final[0]["exit_reason"] == "PREMIUM_STOP"


@pytest.mark.asyncio
async def test_1500_price_differs_from_closing_window_price(temp_warehouse, sample_config, monkeypatch):
    """Operational Test 3: 15:00 price != closing-window price -> only closing-window observation becomes daily MTM."""
    collector = SnapbackProspectiveCollector(warehouse=temp_warehouse)
    monkeypatch.setattr(
        "app.services.snapback_prospective_collector.SnapbackObservationWarehouse",
        lambda *a, **k: temp_warehouse
    )

    opp_id = "OPP-EOD-TIMING-001"
    temp_warehouse.record_opportunity(
        identity=_nifty_identity(),
        opportunity_id=opp_id,
        symbol="NIFTY",
        signal_type="SNAPBACK_FADE_UP",
        spot_price=24500.0,
        status="OPEN_POSITION",
    )

    temp_warehouse.save_paper_position(
        opportunity_id=opp_id,
        symbol="NIFTY",
        option_symbol="NIFTY26OCT25000PE",
        option_qty=75,
        option_entry_price=100.0,
        option_expiry="2026-10-26",
        option_strike=25000.0,
        futures_symbol="NIFTY26OCTFUT",
        futures_lot_size=65,
        current_futures_lots=1,
        avg_futures_entry_price=24510.0,
        realized_futures_pnl=0.0,
        entry_spot=24500.0,
        entry_timestamp="2026-09-15T09:20:00+05:30",
        entry_dte=45,
        entry_iv=0.20,
        causal_beta=1.0,
        peak_option_bid=100.0,
        status="OPEN",
    )

    # Step 1: 15:00 IST tick (price = 110.0, outside closing window 15:25-15:30 IST)
    dt_1500 = datetime(2026, 9, 16, 9, 30, 0, tzinfo=timezone.utc)  # 15:00 IST
    monkeypatch.setattr("time.time", lambda: dt_1500.timestamp())

    class FixedDateTime1500(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is not None:
                return dt_1500.astimezone(tz)
            return dt_1500

    monkeypatch.setattr("app.services.snapback_runner.get_config", lambda uid="default": sample_config)
    monkeypatch.setattr("app.services.snapback.get_config", lambda uid="default": sample_config)

    monkeypatch.setattr("app.services.snapback_runner.datetime", FixedDateTime1500)
    monkeypatch.setattr("app.services.snapback.datetime", FixedDateTime1500)

    mock_client_1500 = AsyncMock()
    mock_client_1500.get_quote = AsyncMock(return_value={
        "NSE:NIFTY": {"last_price": 24500.0},
        "NFO:NIFTY26OCTFUT": {
            "last_price": 24510.0, "buy_price": 24510.0, "sell_price": 24512.0,
            "buy_quantity": 100, "sell_quantity": 100,
            "depth": {"buy": [{"price": 24510.0, "quantity": 100}], "sell": [{"price": 24512.0, "quantity": 100}]},
            "timestamp": "2026-09-16T15:00:00+05:30", "oi": 500000,
        },
        "NFO:NIFTY26OCT25000PE": {
            "last_price": 110.0, "buy_price": 110.0, "sell_price": 111.0,
            "buy_quantity": 100, "sell_quantity": 100,
            "depth": {"buy": [{"price": 110.0, "quantity": 100}], "sell": [{"price": 111.0, "quantity": 100}]},
            "timestamp": "2026-09-16T15:00:00+05:30", "oi": 50000,
        },
    })

    mock_acct = MagicMock()
    mock_acct.connected = True
    monkeypatch.setattr("app.services.exchanges.kite.accounts.get_active", lambda uid: mock_acct)
    monkeypatch.setattr("app.services.exchanges.kite.accounts.acquire_client", AsyncMock(return_value=mock_client_1500))

    res_1500 = await tick(uid="default")
    assert res_1500["status"] == "ok"
    assert res_1500["mtm_processed"] == 0
    # No daily MTM recorded at 15:00 IST
    mtms_1500 = temp_warehouse.get_records_by_table("daily_mtm")
    assert len(mtms_1500) == 0

    # Step 2: 15:29 IST tick (price = 125.0, inside closing window 15:29-15:30 IST)
    dt_1529 = datetime(2026, 9, 16, 9, 59, 0, tzinfo=timezone.utc)  # 15:29 IST
    monkeypatch.setattr("time.time", lambda: dt_1529.timestamp())

    class FixedDateTime1529(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is not None:
                return dt_1529.astimezone(tz)
            return dt_1529

    monkeypatch.setattr("app.services.snapback_runner.datetime", FixedDateTime1529)
    monkeypatch.setattr("app.services.snapback.datetime", FixedDateTime1529)

    mock_client_1529 = AsyncMock()
    mock_client_1529.get_quote = AsyncMock(return_value={
        "NSE:NIFTY": {"last_price": 24500.0},
        "NFO:NIFTY26OCTFUT": {
            "last_price": 24510.0, "buy_price": 24510.0, "sell_price": 24512.0,
            "buy_quantity": 100, "sell_quantity": 100,
            "depth": {"buy": [{"price": 24510.0, "quantity": 100}], "sell": [{"price": 24512.0, "quantity": 100}]},
            "timestamp": "2026-09-16T15:29:00+05:30", "oi": 500000,
        },
        "NFO:NIFTY26OCT25000PE": {
            "last_price": 125.0, "buy_price": 125.0, "sell_price": 126.0,
            "buy_quantity": 100, "sell_quantity": 100,
            "depth": {"buy": [{"price": 125.0, "quantity": 100}], "sell": [{"price": 126.0, "quantity": 100}]},
            "timestamp": "2026-09-16T15:29:00+05:30", "oi": 50000,
        },
    })
    monkeypatch.setattr("app.services.exchanges.kite.accounts.acquire_client", AsyncMock(return_value=mock_client_1529))

    res_1529 = await tick(uid="default")
    assert res_1529["status"] == "ok"
    assert res_1529["mtm_processed"] == 1

    # Exactly 1 daily MTM record created with closing-window option bid = 125.0
    mtms_1528 = temp_warehouse.get_records_by_table("daily_mtm")
    assert len(mtms_1528) == 1
    assert float(mtms_1528[0]["option_liquidation_bid"]) == 125.0


@pytest.mark.asyncio
async def test_crash_after_partial_option_fill_recovers_cleanly(temp_warehouse, sample_config, monkeypatch):
    """Adversarial Test 1: Crash after partial records exist -> process restart recovers cleanly without duplicate key errors."""
    monkeypatch.setattr(
        "app.services.snapback_prospective_collector.SnapbackObservationWarehouse",
        lambda *a, **k: temp_warehouse
    )

    collector = SnapbackProspectiveCollector(warehouse=temp_warehouse)
    opp_id = "OPP-CRASH-PARTIAL-001"
    temp_warehouse.record_opportunity(
        identity=_nifty_identity(),
        opportunity_id=opp_id,
        symbol="NIFTY",
        signal_type="SNAPBACK_FADE_UP",
        spot_price=24500.0,
        status="PROCESSING_ENTRY",
        provider_timestamp="2026-09-15T09:20:00+05:30",
    )

    # Simulate partial records already inserted prior to hard process crash
    temp_warehouse.record_decision(
        decision_id=f"DECISION-{opp_id}",
        opportunity_id=opp_id,
        symbol="NIFTY",
        decision="EXECUTE_PAPER",
        chosen_option_symbol="NIFTY26OCT25000PE",
        chosen_strike=25000.0,
        chosen_delta=-0.50,
        causal_beta=1.0,
        target_hedge_lots=1,
    )
    temp_warehouse.record_paper_fill(
        fill_id=f"FILL-OPT-{opp_id}",
        opportunity_id=opp_id,
        symbol="NIFTY26OCT25000PE",
        order_side="BUY",
        fill_price=100.0,
        fill_quantity=25,
    )

    t1_dt = datetime(2026, 9, 16, 3, 50, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("time.time", lambda: t1_dt.timestamp())

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is not None:
                return t1_dt.astimezone(tz)
            return t1_dt

    monkeypatch.setattr("app.services.snapback.datetime", FixedDateTime)

    mock_client = build_mock_client()

    # Re-run entry processing on recovered opportunity (relocks expired/stranded lease)
    processed = await process_prospective_pending_entries(mock_client, sample_config)

    assert processed == 1
    opp_after = temp_warehouse.get_opportunity_by_id(opp_id)
    assert opp_after["status"] == "OPEN_POSITION"

    pos = temp_warehouse.get_paper_position(opp_id)
    assert pos is not None
    assert pos["status"] == "OPEN"
    assert pos["option_qty"] == 25


@pytest.mark.asyncio
async def test_concurrent_process_blocked_by_active_lease(temp_warehouse):
    """Adversarial Test 2: Two concurrent processes -> active lease blocks second process until TTL expires."""
    opp_id = "OPP-CONCURRENT-LEASE-001"
    temp_warehouse.record_opportunity(
        identity=_nifty_identity(),
        opportunity_id=opp_id,
        symbol="NIFTY",
        signal_type="SNAPBACK_FADE_UP",
        spot_price=24500.0,
        status="PENDING_ENTRY",
    )

    # Process 1 acquires lock
    locked1, token1 = temp_warehouse.try_lock_pending_opportunity(opp_id, lease_ttl_ms=60000)
    assert locked1 is True
    assert token1.startswith("LEASE-")

    # Process 2 attempts lock while lease is active -> BLOCKED
    locked2, token2 = temp_warehouse.try_lock_pending_opportunity(opp_id, lease_ttl_ms=60000)
    assert locked2 is False
    assert token2 == ""

    # Simulate lease expiration by advancing system time in try_lock by > 60s
    with patch("time.time", return_value=datetime.now(timezone.utc).timestamp() + 100.0):
        locked3, token3 = temp_warehouse.try_lock_pending_opportunity(opp_id, lease_ttl_ms=60000)
        assert locked3 is True
        assert token3.startswith("LEASE-")


@pytest.mark.asyncio
async def test_intraday_price_hits_runner_mult_before_session_15_not_promoted(temp_warehouse, sample_config, monkeypatch):
    """Adversarial Test 3: Premium hits 1.5x intraday before session 15 -> position is NOT promoted to runner intraday."""
    monkeypatch.setattr(
        "app.services.snapback_prospective_collector.SnapbackObservationWarehouse",
        lambda *a, **k: temp_warehouse
    )

    collector = SnapbackProspectiveCollector(warehouse=temp_warehouse)

    opp_id = "OPP-RUNNER-MULT-INTRADAY-001"
    temp_warehouse.record_opportunity(
        identity=_nifty_identity(),
        opportunity_id=opp_id,
        symbol="NIFTY",
        signal_type="SNAPBACK_FADE_UP",
        spot_price=24500.0,
        status="OPEN_POSITION",
    )

    # Save position at session 2 (entry_price = 100.0, runner_mult = 1.5 -> 150.0)
    temp_warehouse.save_paper_position(
        opportunity_id=opp_id,
        symbol="NIFTY",
        option_symbol="NIFTY26OCT25000PE",
        option_qty=75,
        option_entry_price=100.0,
        option_expiry="2026-10-26",
        option_strike=25000.0,
        futures_symbol="NIFTY26OCTFUT",
        futures_lot_size=65,
        current_futures_lots=1,
        avg_futures_entry_price=24510.0,
        realized_futures_pnl=0.0,
        entry_spot=24500.0,
        entry_timestamp="2026-09-15T09:20:00+05:30",
        entry_dte=45,
        entry_iv=0.20,
        causal_beta=1.0,
        peak_option_bid=100.0,
        sessions_held=2,
        is_runner=0,
        status="OPEN",
    )

    now_dt = datetime(2026, 9, 16, 6, 0, 0, tzinfo=timezone.utc)
    q_time = "2026-09-16T11:30:00+05:30"
    monkeypatch.setattr("time.time", lambda: now_dt.timestamp())

    # Option price surges to 160.0 (> 150.0 1.5x runner threshold) intraday
    mock_client = AsyncMock()
    mock_client.get_quote = AsyncMock(return_value={
        "NSE:NIFTY": {"last_price": 24500.0},
        "NFO:NIFTY26OCT25000PE": {
            "last_price": 160.0, "buy_price": 160.0, "sell_price": 161.0,
            "buy_quantity": 100, "sell_quantity": 100,
            "depth": {"buy": [{"price": 160.0, "quantity": 100}], "sell": [{"price": 161.0, "quantity": 100}]},
            "timestamp": q_time, "oi": 50000,
        },
    })

    from app.services.snapback import process_prospective_intraday_risk
    processed = await process_prospective_intraday_risk(mock_client, sample_config)
    assert processed == 1

    pos = temp_warehouse.get_paper_position(opp_id)
    assert pos["status"] == "OPEN"
    # MUST remain non-runner (is_runner == 0); promotion ONLY occurs at 15-session horizon
    assert pos["is_runner"] == 0
    # Non-runner positions MUST NOT update peak_option_bid during sessions 1-14
    assert pos["peak_option_bid"] == 100.0


@pytest.mark.asyncio
async def test_intraday_stop_calculates_real_futures_pnl_when_futures_moved(temp_warehouse, sample_config, monkeypatch):
    """Adversarial Test 4: Intraday stop with futures moving significantly -> produces non-zero futures hedge PnL."""
    monkeypatch.setattr(
        "app.services.snapback_prospective_collector.SnapbackObservationWarehouse",
        lambda *a, **k: temp_warehouse
    )

    collector = SnapbackProspectiveCollector(warehouse=temp_warehouse)

    opp_id = "OPP-INTRADAY-FUT-PNL-001"
    temp_warehouse.record_opportunity(
        identity=_nifty_identity(),
        opportunity_id=opp_id,
        symbol="NIFTY",
        signal_type="SNAPBACK_FADE_UP",
        spot_price=24500.0,
        status="OPEN_POSITION",
    )

    # Long 1 NIFTY futures lot (lot_size=65) hedged at avg entry = 24500.0
    temp_warehouse.save_paper_position(
        opportunity_id=opp_id,
        symbol="NIFTY",
        option_symbol="NIFTY26OCT25000PE",
        option_qty=75,
        option_entry_price=100.0,
        option_expiry="2026-10-26",
        option_strike=25000.0,
        futures_symbol="NIFTY26OCTFUT",
        futures_lot_size=65,
        current_futures_lots=1,
        avg_futures_entry_price=24500.0,
        realized_futures_pnl=0.0,
        entry_spot=24500.0,
        entry_timestamp="2026-09-15T09:20:00+05:30",
        entry_dte=45,
        entry_iv=0.20,
        causal_beta=1.0,
        peak_option_bid=100.0,
        status="OPEN",
    )

    now_dt = datetime(2026, 9, 16, 6, 0, 0, tzinfo=timezone.utc)
    q_time = "2026-09-16T11:30:00+05:30"
    monkeypatch.setattr("time.time", lambda: now_dt.timestamp())

    # Option drops to 40.0 (triggers PREMIUM_STOP), while Futures rises to 24650.0 (+150 pts gain!)
    mock_client = AsyncMock()
    mock_client.get_quote = AsyncMock(return_value={
        "NSE:NIFTY": {"last_price": 24650.0},
        "NFO:NIFTY26OCT25000PE": {
            "last_price": 40.0, "buy_price": 40.0, "sell_price": 41.0,
            "buy_quantity": 100, "sell_quantity": 100,
            "depth": {"buy": [{"price": 40.0, "quantity": 100}], "sell": [{"price": 41.0, "quantity": 100}]},
            "timestamp": q_time, "oi": 50000,
        },
        "NFO:NIFTY26OCTFUT": {
            "last_price": 24650.0, "buy_price": 24650.0, "sell_price": 24652.0,
            "buy_quantity": 100, "sell_quantity": 100,
            "depth": {"buy": [{"price": 24650.0, "quantity": 100}], "sell": [{"price": 24652.0, "quantity": 100}]},
            "timestamp": q_time, "oi": 500000,
        },
    })

    from app.services.snapback import process_prospective_intraday_risk
    processed = await process_prospective_intraday_risk(mock_client, sample_config)
    assert processed == 1

    outcomes = temp_warehouse.get_records_by_table("outcomes")
    assert len(outcomes) == 1
    outcome = outcomes[0]
    assert outcome["exit_reason"] == "PREMIUM_STOP"

    # Actual futures PnL must be (24650.0 - 24500.0) * 65 = +9750.0
    expected_futures_pnl = (24650.0 - 24500.0) * 65
    assert float(outcome["actual_futures_pnl"]) == pytest.approx(expected_futures_pnl)
    assert float(outcome["actual_futures_pnl"]) > 0.0


@pytest.mark.asyncio
async def test_stalled_worker_lease_loss_fails_commit(temp_warehouse, sample_config):
    """Adversarial Test 5: Stalled worker loses lease -> final commit raises RuntimeError and rolls back."""
    collector = SnapbackProspectiveCollector(warehouse=temp_warehouse)
    opp_id = "OPP-LEASE-LOSS-COMMIT-001"
    temp_warehouse.record_opportunity(
        identity=_nifty_identity(),
        opportunity_id=opp_id,
        symbol="NIFTY",
        signal_type="SNAPBACK_FADE_UP",
        spot_price=24500.0,
        status="PENDING_ENTRY",
    )

    # Worker A acquires lease A
    locked_A, token_A = temp_warehouse.try_lock_pending_opportunity(opp_id, lease_ttl_ms=60000)
    assert locked_A is True

    # Worker B acquires lease B (simulating lease A expiration)
    with patch("time.time", return_value=time.time() + 100.0):
        locked_B, token_B = temp_warehouse.try_lock_pending_opportunity(opp_id, lease_ttl_ms=60000)
        assert locked_B is True
        assert token_B != token_A

    # Worker A attempts to commit using expired token_A -> MUST FAIL with RuntimeError
    with pytest.raises(RuntimeError, match="Lease lost"):
        temp_warehouse.commit_paper_entry_transaction(
            opportunity_id=opp_id,
            decision_data={
                "decision_id": f"DECISION-{opp_id}", "symbol": "NIFTY", "decision": "EXECUTE_PAPER",
                "chosen_option_symbol": "NIFTY26OCT25000PE", "chosen_strike": 25000.0, "chosen_delta": -0.5,
                "causal_beta": 1.0, "target_hedge_lots": 1, "reason": "Test worker A commit",
            },
            paper_fill_data={
                "fill_id": f"FILL-{opp_id}", "symbol": "NIFTY26OCT25000PE", "order_side": "BUY",
                "fill_price": 100.0, "fill_quantity": 75,
            },
            hedge_rebalance_data={
                "rebalance_id": f"REB-{opp_id}", "symbol": "NIFTY", "prior_hedge_lots": 0,
                "new_hedge_lots": 1, "futures_fill_price": 24510.0,
            },
            cost_data={"cost_id": f"COST-{opp_id}", "symbol": "NIFTY", "total_statutory_costs": 50.0},
            margin_snapshot_data={
                "snapshot_id": f"MARGIN-{opp_id}", "symbol": "NIFTY", "option_margin_required": 10000.0,
                "futures_margin_required": 120000.0, "total_margin": 130000.0, "available_capital": 1000000.0,
            },
            paper_position_data={
                "symbol": "NIFTY", "option_symbol": "NIFTY26OCT25000PE", "option_qty": 75,
                "option_entry_price": 100.0, "option_expiry": "2026-10-26", "option_strike": 25000.0,
                "futures_symbol": "NIFTY26OCTFUT", "futures_lot_size": 65, "current_futures_lots": 1,
                "avg_futures_entry_price": 24510.0, "entry_spot": 24500.0, "entry_timestamp": "2026-09-15T09:20:00+05:30",
                "entry_dte": 45, "entry_iv": 0.20, "causal_beta": 1.0, "status": "OPEN",
            },
            processing_token=token_A,
        )

    # Opportunity status MUST remain PROCESSING_ENTRY (held by Worker B)
    opp = temp_warehouse.get_opportunity_by_id(opp_id)
    assert opp["status"] == "PROCESSING_ENTRY"
    assert opp["processing_token"] == token_B


@pytest.mark.asyncio
async def test_session_15_runner_peak_initialization_not_contaminated_by_prerunner_highs(temp_warehouse, sample_config, monkeypatch):
    """Adversarial Test 6: Pre-runner highs (sessions 1-14) do NOT contaminate runner peak initialized at session 15."""
    monkeypatch.setattr(
        "app.services.snapback_prospective_collector.SnapbackObservationWarehouse",
        lambda *a, **k: temp_warehouse
    )
    collector = SnapbackProspectiveCollector(warehouse=temp_warehouse)

    opp_id = "OPP-RUNNER-PEAK-CLEAN-001"
    temp_warehouse.record_opportunity(
        identity=_nifty_identity(),
        opportunity_id=opp_id,
        symbol="NIFTY",
        signal_type="SNAPBACK_FADE_UP",
        spot_price=24500.0,
        status="OPEN_POSITION",
    )

    # Position at session 14 with initial entry = 100.0 and peak_option_bid = 100.0
    temp_warehouse.save_paper_position(
        opportunity_id=opp_id,
        symbol="NIFTY",
        option_symbol="NIFTY26OCT25000PE",
        option_qty=75,
        option_entry_price=100.0,
        option_expiry="2026-10-26",
        option_strike=25000.0,
        futures_symbol="NIFTY26OCTFUT",
        futures_lot_size=65,
        current_futures_lots=1,
        avg_futures_entry_price=24510.0,
        realized_futures_pnl=0.0,
        entry_spot=24500.0,
        entry_timestamp="2026-08-25T09:20:00+05:30",
        entry_dte=45,
        entry_iv=0.20,
        causal_beta=1.0,
        peak_option_bid=100.0,
        sessions_held=14,
        is_runner=0,
        status="OPEN",
    )

    # At Session 15 horizon MTM tick, closing-window option bid is 155.0 (qualifies as runner >= 1.5x)
    now_ms = 1789552680000
    fut_event = RawQuoteEvent("NIFTY26OCTFUT", now_ms, now_ms, 24510.0, 24512.0, 100, 100, 24510.0, 500000)
    opt_event = RawQuoteEvent("NIFTY26OCT25000PE", now_ms, now_ms, 155.0, 156.0, 50, 50, 155.0, 60000)

    mtm_res = collector.rebalance_and_mtm(
        opportunity_id=opp_id,
        session_date="2026-09-16",
        symbol="NIFTY",
        current_spot=24500.0,
        current_option_delta=-0.5,
        option_bid=155.0,
        futures_quote_event=fut_event,
        option_entry_price=100.0,
        option_quantity=75,
        current_futures_lots=1,
        futures_lot_size=65,
        causal_beta=1.0,
        option_quote_event=opt_event,
        cfg=sample_config,
    )

    assert mtm_res["is_runner"] is True
    pos = temp_warehouse.get_paper_position(opp_id)
    assert pos["is_runner"] == 1
    # Runner peak MUST be initialized to the session 15 horizon bid (155.0)
    assert pos["peak_option_bid"] == 155.0


@pytest.mark.asyncio
async def test_calendar_next_trading_day_failure_marks_calendar_error(temp_warehouse, sample_config, monkeypatch):
    """Adversarial Test 7: Calendar failure during entry execution -> marks CALENDAR_ERROR (fail closed)."""
    opp_id = "OPP-CAL-FAIL-001"
    temp_warehouse.record_opportunity(
        identity=_nifty_identity(),
        opportunity_id=opp_id,
        symbol="NIFTY",
        signal_type="SNAPBACK_FADE_UP",
        spot_price=24500.0,
        signal_timestamp="2026-09-15T10:00:00+05:30",
        status="PENDING_ENTRY",
    )

    collector = SnapbackProspectiveCollector(warehouse=temp_warehouse)

    # Force next_trading_day to raise RuntimeError
    def bad_calendar(d):
        raise RuntimeError("Calendar system offline")

    monkeypatch.setattr("app.services.snapback_prospective_collector.next_trading_day", bad_calendar)

    res = collector.execute_pending_entry(
        opportunity_id=opp_id,
        cfg=sample_config,
        t1_spot_price=24500.0,
        futures_quote_event=None,
        futures_symbol="NIFTY26OCTFUT",
        option_candidates=[],
        option_quote_events={},
        causal_beta=1.0,
        futures_lot_size=65,
        execution_timestamp_ms=1789530000000, # 2026-09-16 09:20 IST
    )

    assert res["status"] == "CALENDAR_ERROR"
    opp = temp_warehouse.get_opportunity_by_id(opp_id)
    assert opp["status"] == "CALENDAR_ERROR"


@pytest.mark.asyncio
async def test_intraday_exit_latches_pending_exit_and_closes_on_next_tick_even_if_option_recovers(temp_warehouse, sample_config, monkeypatch):
    """Adversarial Test 8: Intraday stop triggered but futures exit quote is invalid -> latches EXIT_PENDING.
    On next tick (11:00:30), option recovers above stop but fresh futures quote arrives -> trade STILL closes with original stop reason.
    """
    monkeypatch.setattr(
        "app.services.snapback_prospective_collector.SnapbackObservationWarehouse",
        lambda *a, **k: temp_warehouse
    )

    opp_id = "OPP-INVALID-FUT-EXIT-001"
    temp_warehouse.record_opportunity(
        identity=_nifty_identity(),
        opportunity_id=opp_id,
        symbol="NIFTY",
        signal_type="SNAPBACK_FADE_UP",
        spot_price=24500.0,
        status="OPEN_POSITION",
    )

    temp_warehouse.save_paper_position(
        opportunity_id=opp_id,
        symbol="NIFTY",
        option_symbol="NIFTY26OCT25000PE",
        option_qty=75,
        option_entry_price=100.0,
        option_expiry="2026-10-26",
        option_strike=25000.0,
        futures_symbol="NIFTY26OCTFUT",
        futures_lot_size=65,
        current_futures_lots=1,
        avg_futures_entry_price=24500.0,
        realized_futures_pnl=0.0,
        entry_spot=24500.0,
        entry_timestamp="2026-09-15T09:20:00+05:30",
        entry_dte=45,
        entry_iv=0.20,
        causal_beta=1.0,
        peak_option_bid=100.0,
        status="OPEN",
    )

    now_dt = datetime(2026, 9, 16, 5, 30, 0, tzinfo=timezone.utc) # 11:00:00 IST
    monkeypatch.setattr("time.time", lambda: now_dt.timestamp())

    # Tick 1 (11:00:00 IST): Option drops to 40.0 (triggers PREMIUM_STOP), but Futures returns invalid quote (bid=0.0)
    mock_client1 = AsyncMock()
    mock_client1.get_quote = AsyncMock(return_value={
        "NSE:NIFTY": {"last_price": 24650.0},
        "NFO:NIFTY26OCT25000PE": {
            "last_price": 40.0, "buy_price": 40.0, "sell_price": 41.0,
            "buy_quantity": 100, "sell_quantity": 100,
            "depth": {"buy": [{"price": 40.0, "quantity": 100}], "sell": [{"price": 41.0, "quantity": 100}]},
            "timestamp": "2026-09-16T11:00:00+05:30", "oi": 50000,
        },
        "NFO:NIFTY26OCTFUT": {
            "last_price": 0.0, "buy_price": 0.0, "sell_price": 0.0, # INVALID QUOTE!
            "timestamp": "2026-09-16T11:00:00+05:30",
        },
    })

    from app.services.snapback import process_prospective_intraday_risk
    processed1 = await process_prospective_intraday_risk(mock_client1, sample_config)
    assert processed1 == 1

    # Position MUST transition to EXIT_PENDING with pending_exit_reason = "PREMIUM_STOP"
    pos1 = temp_warehouse.get_paper_position(opp_id)
    assert pos1["status"] == "EXIT_PENDING"
    assert pos1["pending_exit_reason"] == "PREMIUM_STOP"
    assert pos1["pending_exit_option_bid"] == 40.0
    outcomes1 = temp_warehouse.get_records_by_table("outcomes")
    assert len(outcomes1) == 0 # Not closed yet because futures quote was invalid

    # Tick 2 (11:00:30 IST): Option bid recovers to 200.0 (well above stop!), but Futures quote is now VALID!
    tick2_dt = datetime(2026, 9, 16, 5, 30, 30, tzinfo=timezone.utc)
    monkeypatch.setattr("time.time", lambda: tick2_dt.timestamp())

    mock_client2 = AsyncMock()
    mock_client2.get_quote = AsyncMock(return_value={
        "NSE:NIFTY": {"last_price": 24650.0},
        "NFO:NIFTY26OCT25000PE": {
            "last_price": 200.0, "buy_price": 200.0, "sell_price": 201.0,
            "buy_quantity": 100, "sell_quantity": 100,
            "depth": {"buy": [{"price": 200.0, "quantity": 100}], "sell": [{"price": 201.0, "quantity": 100}]},
            "timestamp": "2026-09-16T11:00:30+05:30", "oi": 50000,
        },
        "NFO:NIFTY26OCTFUT": {
            "last_price": 24650.0, "buy_price": 24650.0, "sell_price": 24652.0, # VALID QUOTE!
            "buy_quantity": 100, "sell_quantity": 100,
            "depth": {"buy": [{"price": 24650.0, "quantity": 100}], "sell": [{"price": 24652.0, "quantity": 100}]},
            "timestamp": "2026-09-16T11:00:30+05:30", "oi": 500000,
        },
    })

    processed2 = await process_prospective_intraday_risk(mock_client2, sample_config)
    assert processed2 == 1

    # Position MUST now be CLOSED and outcome recorded with ORIGINAL stop reason ("PREMIUM_STOP")
    pos2 = temp_warehouse.get_paper_position(opp_id)
    assert pos2["status"] == "CLOSED"
    outcomes2 = temp_warehouse.get_records_by_table("outcomes")
    assert len(outcomes2) == 1
    assert outcomes2[0]["exit_reason"] == "PREMIUM_STOP"





# ---------------------------------------------------------------------------
# Freeze patch regression tests: EXIT_PENDING isolation + actual intraday exit DTE
# ---------------------------------------------------------------------------


async def _open_position_for_freeze_tests(temp_warehouse, sample_config, monkeypatch):
    """Helper: record a signal and execute the T+1 paper entry, returning the opportunity id."""
    monkeypatch.setattr(
        "app.services.snapback_prospective_collector.SnapbackObservationWarehouse",
        lambda *a, **k: temp_warehouse
    )

    collector = SnapbackProspectiveCollector(warehouse=temp_warehouse)
    dt_t = datetime(2026, 9, 15, 10, 0, 0, tzinfo=timezone.utc)
    sig = SnapbackSignal(
        symbol="NIFTY",
        side="fade_up",
        direction="BEARISH",
        option_type="PE",
        timestamp_ms=int(dt_t.timestamp() * 1000),
        entry=24500.0,
        mean_target=24800.0,
        stretch=1.8,
        atr=180.0,
        realized_vol=0.15,
        assumed_iv=0.18,
        level=24400.0,
        strength="MODERATE",
    )
    opp_id = collector.record_signal_at_close(signal=sig, cfg=sample_config, identity=_nifty_identity())["opportunity_id"]

    t1_dt = datetime(2026, 9, 16, 3, 50, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("time.time", lambda: t1_dt.timestamp())

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return t1_dt.astimezone(tz) if tz is not None else t1_dt

    monkeypatch.setattr("app.services.snapback.datetime", FixedDateTime)

    mock_client = build_mock_client()
    await process_prospective_pending_entries(mock_client, sample_config)
    assert temp_warehouse.get_paper_position(opp_id) is not None
    return opp_id, mock_client


def _quotes_at(ist_stamp: str, option_bid: float = 100.0, option_ask: float = 102.0):
    return {
        "NSE:NIFTY": {"last_price": 24510.0},
        "NSE:NIFTY 50": {"last_price": 24510.0},
        "NFO:NIFTY26OCTFUT": {
            "last_price": 24511.0,
            "buy_price": 24510.0,
            "sell_price": 24512.0,
            "buy_quantity": 100,
            "sell_quantity": 100,
            "depth": {"buy": [{"price": 24510.0, "quantity": 100}], "sell": [{"price": 24512.0, "quantity": 100}]},
            "timestamp": ist_stamp,
            "oi": 500000,
        },
        "NFO:NIFTY26OCT25000PE": {
            "last_price": option_bid + 1.0,
            "buy_price": option_bid,
            "sell_price": option_ask,
            "buy_quantity": 50,
            "sell_quantity": 50,
            "depth": {"buy": [{"price": option_bid, "quantity": 50}], "sell": [{"price": option_ask, "quantity": 50}]},
            "timestamp": ist_stamp,
            "oi": 60000,
        },
    }


@pytest.mark.asyncio
async def test_exit_pending_isolated_from_eod_cycle(temp_warehouse, sample_config, monkeypatch):
    """TEST A: an EXIT_PENDING position must never be touched by the EOD MTM/rebalance phase."""
    opp_id, mock_client = await _open_position_for_freeze_tests(temp_warehouse, sample_config, monkeypatch)

    # Latch EXIT_PENDING exactly as the intraday risk monitor does
    temp_warehouse.set_paper_position_pending_exit(
        opportunity_id=opp_id,
        pending_exit_reason="PREMIUM_STOP",
        pending_exit_option_bid=40.0,
        pending_exit_ts=datetime.now(timezone.utc).isoformat(),
    )
    before = temp_warehouse.get_paper_position(opp_id)
    assert before["status"] == "EXIT_PENDING"
    hedges_before = len(temp_warehouse.get_records_by_table("hedge_rebalances", opportunity_id=opp_id))

    # Run the 15:29 EOD phase
    eod_dt = datetime(2026, 9, 16, 9, 59, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("time.time", lambda: eod_dt.timestamp())

    class FixedDateTimeEOD(datetime):
        @classmethod
        def now(cls, tz=None):
            return eod_dt.astimezone(tz) if tz is not None else eod_dt

    monkeypatch.setattr("app.services.snapback.datetime", FixedDateTimeEOD)
    mock_client.get_quote = AsyncMock(return_value=_quotes_at("2026-09-16T15:28:59+05:30"))

    processed = await process_prospective_daily_mtm_and_exits(mock_client, sample_config)
    assert processed == 0

    assert temp_warehouse.get_records_by_table("daily_mtm", opportunity_id=opp_id) == []
    assert len(temp_warehouse.get_records_by_table("hedge_rebalances", opportunity_id=opp_id)) == hedges_before

    after = temp_warehouse.get_paper_position(opp_id)
    assert after["status"] == "EXIT_PENDING"
    assert after["pending_exit_reason"] == "PREMIUM_STOP"
    assert after["sessions_held"] == before["sessions_held"]
    assert after["is_runner"] == before["is_runner"]


@pytest.mark.asyncio
async def test_intraday_exit_uses_remaining_dte(temp_warehouse, sample_config, monkeypatch):
    """TEST B: an actual intraday exit must report remaining DTE, not the original entry DTE."""
    opp_id, mock_client = await _open_position_for_freeze_tests(temp_warehouse, sample_config, monkeypatch)

    pos = temp_warehouse.get_paper_position(opp_id)
    entry_dte = int(pos["entry_dte"])
    assert entry_dte > 0

    # Advance several calendar days into the holding period
    later_dt = datetime(2026, 9, 26, 6, 0, 0, tzinfo=timezone.utc)
    monkeypatch.setattr("time.time", lambda: later_dt.timestamp())

    class FixedDateTimeLater(datetime):
        @classmethod
        def now(cls, tz=None):
            return later_dt.astimezone(tz) if tz is not None else later_dt

    monkeypatch.setattr("app.services.snapback.datetime", FixedDateTimeLater)

    # Collapse the option bid far below the premium stop
    stop_bid = float(pos["option_entry_price"]) * 0.30
    mock_client.get_quote = AsyncMock(
        return_value=_quotes_at("2026-09-26T11:29:59+05:30", option_bid=stop_bid, option_ask=stop_bid + 2.0)
    )

    captured = {}
    real_close = SnapbackProspectiveCollector.close_opportunity

    def spy_close(self, **kwargs):
        captured.update(kwargs)
        return real_close(self, **kwargs)

    monkeypatch.setattr(SnapbackProspectiveCollector, "close_opportunity", spy_close)

    from app.services.snapback import process_prospective_intraday_risk
    await process_prospective_intraday_risk(mock_client, sample_config)

    assert captured, "close_opportunity was not invoked by the intraday risk monitor"
    expiry = date.fromisoformat(str(pos["option_expiry"])[:10])
    expected_dte = max(0, (expiry - later_dt.astimezone(_IST).date()).days)
    assert captured["exit_dte"] == expected_dte
    assert captured["exit_dte"] != entry_dte
