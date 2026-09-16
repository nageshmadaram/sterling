"""Operational unit tests for Snapback unattended prospective runner & two-phase lifecycle."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app.engines.snapback import SnapbackConfig, SnapbackSignal
from app.services.snapback_prospective_collector import SnapbackProspectiveCollector, SnapbackObservationWarehouse
from app.services.snapback import process_prospective_pending_entries, process_prospective_daily_mtm_and_exits
from app.services.snapback_runner import tick, _is_nse_trading_day

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
    rec_res = collector.record_signal_at_close(signal=sig, cfg=sample_config)
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
    rec_res = collector.record_signal_at_close(signal=sig, cfg=sample_config)
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
    rec_res = collector.record_signal_at_close(signal=sig, cfg=sample_config)
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
    rec_res = collector.record_signal_at_close(signal=sig, cfg=sample_config)
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

    # Step 2: 15:28 IST tick (price = 125.0, inside closing window 15:25-15:30 IST)
    dt_1528 = datetime(2026, 9, 16, 9, 58, 0, tzinfo=timezone.utc)  # 15:28 IST

    class FixedDateTime1528(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is not None:
                return dt_1528.astimezone(tz)
            return dt_1528

    monkeypatch.setattr("app.services.snapback_runner.datetime", FixedDateTime1528)
    monkeypatch.setattr("app.services.snapback.datetime", FixedDateTime1528)

    mock_client_1528 = AsyncMock()
    mock_client_1528.get_quote = AsyncMock(return_value={
        "NSE:NIFTY": {"last_price": 24500.0},
        "NFO:NIFTY26OCTFUT": {
            "last_price": 24510.0, "buy_price": 24510.0, "sell_price": 24512.0,
            "buy_quantity": 100, "sell_quantity": 100,
            "depth": {"buy": [{"price": 24510.0, "quantity": 100}], "sell": [{"price": 24512.0, "quantity": 100}]},
            "timestamp": "2026-09-16T15:28:00+05:30", "oi": 500000,
        },
        "NFO:NIFTY26OCT25000PE": {
            "last_price": 125.0, "buy_price": 125.0, "sell_price": 126.0,
            "buy_quantity": 100, "sell_quantity": 100,
            "depth": {"buy": [{"price": 125.0, "quantity": 100}], "sell": [{"price": 126.0, "quantity": 100}]},
            "timestamp": "2026-09-16T15:28:00+05:30", "oi": 50000,
        },
    })
    monkeypatch.setattr("app.services.exchanges.kite.accounts.acquire_client", AsyncMock(return_value=mock_client_1528))

    res_1528 = await tick(uid="default")
    assert res_1528["status"] == "ok"
    assert res_1528["mtm_processed"] == 1

    # Exactly 1 daily MTM record created with closing-window option bid = 125.0
    mtms_1528 = temp_warehouse.get_records_by_table("daily_mtm")
    assert len(mtms_1528) == 1
    assert float(mtms_1528[0]["option_liquidation_bid"]) == 125.0
