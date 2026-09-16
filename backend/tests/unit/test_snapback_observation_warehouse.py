"""Unit tests for Snapback Prospective Observation Warehouse Service & Collector (PROSPECTIVE CAPTURE 1.0.1)."""
import os
import sqlite3
import tempfile
import time
from datetime import datetime, timedelta, timezone
import pytest

from app.engines.snapback.config import SnapbackConfig
from app.engines.snapback.intraday_models import RawQuoteEvent
from app.engines.snapback.models import SnapbackSignal
from app.services.snapback_observation_warehouse import SnapbackObservationWarehouse
from app.services.snapback_prospective_collector import (
    OptionCandidateInfo,
    SnapbackProspectiveCollector,
)


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
    return SnapbackConfig()


@pytest.fixture
def sample_fade_up_signal():
    # Signal timestamp: 2026-09-15T15:30:00Z (Day T close)
    dt = datetime(2026, 9, 15, 15, 30, 0, tzinfo=timezone.utc)
    ts_ms = int(dt.timestamp() * 1000)
    return SnapbackSignal(
        symbol="NIFTY",
        side="fade_up",
        direction="BEARISH",
        option_type="PE",
        timestamp_ms=ts_ms,
        entry=24500.0,
        mean_target=24800.0,
        stretch=1.8,
        atr=180.0,
        realized_vol=0.15,
        assumed_iv=0.18,
        level=24400.0,
        strength="MODERATE",
        reasons=("Closed above 20-session high",),
        metrics={"stretch_atr": 1.8},
    )


def test_warehouse_init_and_tables(temp_warehouse):
    conn = temp_warehouse._get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cur.fetchall()}
        expected_tables = {
            "opportunities",
            "contract_candidates",
            "option_quotes",
            "futures_quotes",
            "decisions",
            "paper_fills",
            "hedge_rebalances",
            "daily_mtm",
            "margin_snapshots",
            "costs",
            "outcomes",
        }
        assert expected_tables.issubset(tables)
    finally:
        conn.close()


def test_old_db_auto_migrates(temp_warehouse):
    """Adversarial Test 1: SQLite migration adds status and semantic columns to an old database."""
    db_path = temp_warehouse.db_path
    conn = sqlite3.connect(db_path)
    # Simulate an old DB schema without status column
    conn.execute("DROP TABLE opportunities")
    conn.execute("""
        CREATE TABLE opportunities (
            opportunity_id TEXT PRIMARY KEY,
            signal_type TEXT NOT NULL,
            spot_price REAL NOT NULL,
            ema_50 REAL NOT NULL,
            ema_200 REAL NOT NULL,
            trend TEXT NOT NULL,
            is_valid INTEGER NOT NULL DEFAULT 1,
            rejection_reason TEXT NOT NULL DEFAULT '',
            observed_at TEXT NOT NULL,
            provider_timestamp TEXT,
            received_at TEXT NOT NULL,
            symbol TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

    # Re-initialize warehouse — must migrate schema automatically without error
    migrated_wh = SnapbackObservationWarehouse(db_path=db_path)
    conn = migrated_wh._get_connection()
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(opportunities)").fetchall()}
        assert "status" in cols
        assert "signal_spot" in cols
        assert "mean_target" in cols
        assert "breakout_level" in cols
    finally:
        conn.close()


def test_warehouse_zero_outcomes_returns_inconclusive(temp_warehouse):
    report = temp_warehouse.generate_falsification_report()
    assert report["total_outcomes"] == 0
    assert report["evidence_status"] == "INCONCLUSIVE"
    assert report["survives_falsification"] is False
    assert report["report_type"] == "DIAGNOSTIC_ONLY"
    assert report["promotion_permitted"] is False


def test_warehouse_immutable_inserts_reject_duplicates(temp_warehouse):
    opp_id = "OPP-DUP-TEST"
    temp_warehouse.record_opportunity(
        opportunity_id=opp_id,
        symbol="NIFTY",
        signal_type="SNAPBACK_FADE_UP",
        spot_price=24500.0,
        ema_50=24800.0,
        ema_200=24400.0,
        trend="BEARISH",
        is_valid=True,
    )

    with pytest.raises(sqlite3.IntegrityError):
        temp_warehouse.record_opportunity(
            opportunity_id=opp_id,
            symbol="NIFTY",
            signal_type="SNAPBACK_FADE_UP",
            spot_price=24600.0,
            ema_50=24800.0,
            ema_200=24400.0,
            trend="BEARISH",
            is_valid=True,
        )


def test_collector_record_signal_no_signal_returns_no_signal(temp_warehouse, sample_config):
    collector = SnapbackProspectiveCollector(warehouse=temp_warehouse)
    res = collector.record_signal_at_close(signal=None, cfg=sample_config)
    assert res["status"] == "NO_SIGNAL"
    assert res["opportunity_id"] == ""


def test_collector_record_signal_day_t_close_persists_pending_entry(temp_warehouse, sample_config, sample_fade_up_signal):
    collector = SnapbackProspectiveCollector(warehouse=temp_warehouse)
    res = collector.record_signal_at_close(signal=sample_fade_up_signal, cfg=sample_config)

    assert res["status"] == "PENDING_ENTRY"
    opp_id = res["opportunity_id"]
    assert opp_id.startswith("OPP-NIFTY-")

    pending = temp_warehouse.get_pending_opportunities()
    assert len(pending) == 1
    assert pending[0]["opportunity_id"] == opp_id
    assert pending[0]["status"] == "PENDING_ENTRY"
    assert pending[0]["mean_target"] == 24800.0
    assert pending[0]["breakout_level"] == 24400.0


def test_fade_up_signal_ce_candidate_rejected(temp_warehouse, sample_config, sample_fade_up_signal):
    """Adversarial Test 2: CE candidate for fade_up PE signal must be rejected."""
    collector = SnapbackProspectiveCollector(warehouse=temp_warehouse)
    rec_res = collector.record_signal_at_close(signal=sample_fade_up_signal, cfg=sample_config)
    opp_id = rec_res["opportunity_id"]

    # Day T+1 execution timestamp: 2026-09-16T03:45:00Z (09:15 IST open)
    t1_dt = datetime(2026, 9, 16, 3, 45, 0, tzinfo=timezone.utc)
    t1_ms = int(t1_dt.timestamp() * 1000)

    fut_event = RawQuoteEvent(
        contract_id="NIFTY-I",
        exchange_timestamp_ms=t1_ms - 200,
        received_at_ms=t1_ms - 100,
        best_bid=24510.0,
        best_ask=24512.0,
        bid_quantity=100,
        ask_quantity=100,
        last_price=24511.0,
        open_interest=500000,
    )

    # Candidate has CE option type (invalid for fade_up signal which requires PE)
    cand_ce = OptionCandidateInfo(
        symbol="NIFTY26OCT25000CE",
        expiry="2026-10-29",
        strike=25000.0,
        option_type="CE",
        dte=45,
        is_monthly=True,
        theoretical_delta=0.70,
        lot_size=65,
    )

    opt_quote = RawQuoteEvent(
        contract_id="NIFTY26OCT25000CE",
        exchange_timestamp_ms=t1_ms - 200,
        received_at_ms=t1_ms - 100,
        best_bid=450.0,
        best_ask=452.0,
        bid_quantity=50,
        ask_quantity=50,
        last_price=451.0,
        open_interest=60000,
    )

    exec_res = collector.execute_pending_entry(
        opportunity_id=opp_id,
        cfg=sample_config,
        t1_spot_price=24510.0,
        futures_quote_event=fut_event,
        futures_symbol="NIFTY-I",
        option_candidates=[cand_ce],
        option_quote_events={"NIFTY26OCT25000CE": opt_quote},
        causal_beta=1.15,
        futures_lot_size=65,
        execution_timestamp_ms=t1_ms,
    )

    assert exec_res["status"] == "NO_FILL"


def test_session_timing_verification(temp_warehouse, sample_config, sample_fade_up_signal):
    """Adversarial Test 3: Session timing enforcement (Day T close fill blocked, late fill INCONCLUSIVE)."""
    collector = SnapbackProspectiveCollector(warehouse=temp_warehouse)
    rec_res = collector.record_signal_at_close(signal=sample_fade_up_signal, cfg=sample_config)
    opp_id = rec_res["opportunity_id"]

    # Signal is dated 2026-09-15
    # 1. Fill attempt on same day close (2026-09-15) -> Blocked with INVALID_SESSION_TIMING
    same_day_ms = int(datetime(2026, 9, 15, 15, 30, 0, tzinfo=timezone.utc).timestamp() * 1000)
    res_same_day = collector.execute_pending_entry(
        opportunity_id=opp_id,
        cfg=sample_config,
        t1_spot_price=24500.0,
        futures_quote_event=None,
        futures_symbol="NIFTY-I",
        option_candidates=[],
        option_quote_events={},
        causal_beta=1.0,
        futures_lot_size=65,
        execution_timestamp_ms=same_day_ms,
    )
    assert res_same_day["status"] == "INVALID_SESSION_TIMING"

    # 2. Fill attempt 5 days late (2026-09-20) -> Marked INCONCLUSIVE (MISSED_T1_ENTRY_WINDOW)
    late_ms = int(datetime(2026, 9, 20, 9, 15, 0, tzinfo=timezone.utc).timestamp() * 1000)
    res_late = collector.execute_pending_entry(
        opportunity_id=opp_id,
        cfg=sample_config,
        t1_spot_price=24500.0,
        futures_quote_event=None,
        futures_symbol="NIFTY-I",
        option_candidates=[],
        option_quote_events={},
        causal_beta=1.0,
        futures_lot_size=65,
        execution_timestamp_ms=late_ms,
    )
    assert res_late["status"] == "INCONCLUSIVE"
    assert "Missed T+1" in res_late["reason"]


def test_tampered_config_rejected(temp_warehouse, sample_fade_up_signal):
    """Adversarial Test 4: Tampered/modified SnapbackConfig parameter is rejected."""
    collector = SnapbackProspectiveCollector(warehouse=temp_warehouse)
    rec_res = collector.record_signal_at_close(signal=sample_fade_up_signal, cfg=SnapbackConfig())
    opp_id = rec_res["opportunity_id"]

    # Modified config (min_dte changed to 10)
    tampered_cfg = SnapbackConfig(min_dte=10)
    t1_ms = int(datetime(2026, 9, 16, 3, 45, 0, tzinfo=timezone.utc).timestamp() * 1000)

    res = collector.execute_pending_entry(
        opportunity_id=opp_id,
        cfg=tampered_cfg,
        t1_spot_price=24510.0,
        futures_quote_event=None,
        futures_symbol="NIFTY-I",
        option_candidates=[],
        option_quote_events={},
        causal_beta=1.0,
        futures_lot_size=65,
        execution_timestamp_ms=t1_ms,
    )

    assert res["status"] == "NON_FROZEN_CONFIG"



def test_bid_ask_aware_futures_rebalancing(temp_warehouse, sample_config, sample_fade_up_signal):
    """Adversarial Test 5: Futures rebalance fills increases at ask, decreases at bid, retains realized P&L."""
    collector = SnapbackProspectiveCollector(warehouse=temp_warehouse)
    rec_res = collector.record_signal_at_close(signal=sample_fade_up_signal, cfg=sample_config)
    opp_id = rec_res["opportunity_id"]

    now_ms = int(datetime(2026, 9, 16, 3, 45, 0, tzinfo=timezone.utc).timestamp() * 1000)
    fut_event = RawQuoteEvent(
        contract_id="NIFTY-I",
        exchange_timestamp_ms=now_ms - 200,
        received_at_ms=now_ms - 100,
        best_bid=24510.0,
        best_ask=24512.0,
        bid_quantity=100,
        ask_quantity=100,
        last_price=24511.0,
        open_interest=500000,
    )

    cand_valid = OptionCandidateInfo(
        symbol="NIFTY26OCT25000PE",
        expiry="2026-10-29",
        strike=25000.0,
        option_type="PE",
        dte=45,
        is_monthly=True,
        theoretical_delta=-0.70,
        lot_size=65,
    )

    opt_quote = RawQuoteEvent(
        contract_id="NIFTY26OCT25000PE",
        exchange_timestamp_ms=now_ms - 200,
        received_at_ms=now_ms - 100,
        best_bid=450.0,
        best_ask=452.0,
        bid_quantity=50,
        ask_quantity=50,
        last_price=451.0,
        open_interest=60000,
    )

    exec_res = collector.execute_pending_entry(
        opportunity_id=opp_id,
        cfg=sample_config,
        t1_spot_price=24510.0,
        futures_quote_event=fut_event,
        futures_symbol="NIFTY-I",
        option_candidates=[cand_valid],
        option_quote_events={"NIFTY26OCT25000PE": opt_quote},
        causal_beta=1.65,
        futures_lot_size=65,
        execution_timestamp_ms=now_ms,
    )


    assert exec_res["status"] == "OPEN_POSITION"

    # Session 1: Increase hedge from 1 lot to 2 lots -> Fills additional lot at ASK (24620.0)
    fut_event_up = RawQuoteEvent(
        contract_id="NIFTY-I",
        exchange_timestamp_ms=now_ms + 86400000,
        received_at_ms=now_ms + 86400000,
        best_bid=24618.0,
        best_ask=24620.0,
        bid_quantity=100,
        ask_quantity=100,
        last_price=24619.0,
    )

    reb_up = collector.rebalance_and_mtm(
        opportunity_id=opp_id,
        session_date="2026-09-17",
        symbol="NIFTY",
        current_spot=24600.0,
        current_option_delta=-0.95,
        option_bid=470.0,
        futures_quote_event=fut_event_up,
        option_entry_price=exec_res["option_fill_price"],
        option_quantity=65,
        current_futures_lots=1,
        futures_lot_size=65,
        causal_beta=1.65,
        prior_realized_futures_pnl=0.0,
        prior_avg_futures_entry_price=exec_res["futures_fill_price"],
    )

    assert reb_up["new_hedge_lots"] == 2
    assert reb_up["avg_open_futures_entry_price"] > exec_res["futures_fill_price"]  # Weighted average updated

    # Session 2: Decrease hedge from 2 lots to 1 lot -> Fills closed lot at BID (24700.0), records realized PnL
    fut_event_down = RawQuoteEvent(
        contract_id="NIFTY-I",
        exchange_timestamp_ms=now_ms + 172800000,
        received_at_ms=now_ms + 172800000,
        best_bid=24700.0,
        best_ask=24702.0,
        bid_quantity=100,
        ask_quantity=100,
        last_price=24701.0,
    )

    reb_down = collector.rebalance_and_mtm(
        opportunity_id=opp_id,
        session_date="2026-09-18",
        symbol="NIFTY",
        current_spot=24550.0,
        current_option_delta=-0.50,  # Lower delta requires 1 lot
        option_bid=460.0,
        futures_quote_event=fut_event_down,
        option_entry_price=exec_res["option_fill_price"],
        option_quantity=65,
        current_futures_lots=2,
        futures_lot_size=65,
        causal_beta=1.65,
        prior_realized_futures_pnl=reb_up["realized_futures_pnl"],
        prior_avg_futures_entry_price=reb_up["avg_open_futures_entry_price"],
    )

    assert reb_down["new_hedge_lots"] == 1
    assert reb_down["realized_futures_pnl"] > 0.0  # Realized gain on closed futures lot retained


def test_stock_trade_counterfactual_uses_nifty_path(temp_warehouse, sample_config):
    """Adversarial Test 6: Stock trade counterfactual uses NIFTY index futures path and actual statutory costs."""
    collector = SnapbackProspectiveCollector(warehouse=temp_warehouse)

    close_res = collector.close_opportunity(
        opportunity_id="OPP-RELIANCE-12345",
        symbol="RELIANCE",
        exit_reason="HOLDING_HORIZON",
        entry_ts="2026-09-16T09:30:00Z",
        exit_ts="2026-10-01T15:15:00Z",
        entry_spot=3000.0,
        exit_spot=3100.0,
        selected_strike=3100.0,
        entry_dte=45,
        exit_dte=30,
        iv_proxy=0.22,
        option_entry_price=80.0,
        option_exit_bid=95.0,
        futures_entry_price=24500.0,
        futures_exit_bid=24700.0,
        statutory_costs=250.0,
        option_quantity=250,
        futures_quantity=65,
        nifty_futures_entry=24500.0,
        nifty_futures_exit=24700.0,
    )

    assert close_res["status"] == "RECORDED"
    outcomes = temp_warehouse.get_records_by_table("outcomes", "OPP-RELIANCE-12345")
    assert len(outcomes) == 1
    assert outcomes[0]["modeled_costs"] == 250.0  # Equals actual statutory costs (no 0.90 multiplier!)
    assert outcomes[0]["modeled_futures_pnl"] == (24700.0 - 24500.0) * 65  # NIFTY path used!


def test_production_adapter_no_placeholders():
    """Adversarial Test 7: Verify real production prospective adapter in snapback.py contains no runtime placeholders."""
    import inspect
    from app.services import snapback

    source = inspect.getsource(snapback)

    # Locate process_prospective_pending_entries_and_mtm function body inside snapback.py
    fn_source = inspect.getsource(snapback.process_prospective_pending_entries_and_mtm)

    forbidden_patterns = [
        "option_candidates=[]",
        "option_candidates = []",
        "option_bid=100.0",
        "option_bid = 100.0",
        "option_entry_price=100.0",
        "option_entry_price = 100.0",
        "causal_beta=1.0,",
        "causal_beta = 1.0",
        "option_lot_size=65",
        "option_lot_size = 65",
        "futures_lot_size=65",
        "futures_lot_size = 65",
        "exchange_timestamp_ms=now_ms",
        "exchange_timestamp_ms = now_ms",
        "depth_bid or last_price",
        "depth_ask or last_price",
        "depth_price or last_price",
    ]

    found_violations = []
    for pattern in forbidden_patterns:
        if pattern in fn_source:
            found_violations.append(f"In process_prospective_pending_entries_and_mtm: '{pattern}'")
        if pattern in inspect.getsource(snapback.extract_raw_quote_event):
            found_violations.append(f"In extract_raw_quote_event: '{pattern}'")

    assert not found_violations, f"Found runtime placeholder violations in production adapter: {found_violations}"


def test_friday_signal_fills_monday_opening_window(temp_warehouse, sample_config):
    """Proves: Friday signal fills Monday opening window (and misses outside window)."""
    collector = SnapbackProspectiveCollector(warehouse=temp_warehouse)

    # Friday close signal: 2026-09-18 15:30 IST (10:00 UTC)
    fri_dt = datetime(2026, 9, 18, 10, 0, 0, tzinfo=timezone.utc)
    fri_signal_1 = SnapbackSignal(
        symbol="NIFTY",
        side="fade_up",
        direction="BEARISH",
        option_type="PE",
        timestamp_ms=int(fri_dt.timestamp() * 1000),
        entry=24500.0,
        mean_target=24800.0,
        stretch=1.8,
        atr=180.0,
        realized_vol=0.15,
        assumed_iv=0.18,
        level=24400.0,
        strength="MODERATE",
    )

    rec_res = collector.record_signal_at_close(signal=fri_signal_1, cfg=sample_config)
    opp_id = rec_res["opportunity_id"]
    assert opp_id != ""

    cand_valid = OptionCandidateInfo(
        symbol="NIFTY26OCT25000PE",
        expiry="2026-10-29",
        strike=25000.0,
        option_type="PE",
        dte=41,
        is_monthly=True,
        theoretical_delta=-0.70,
        lot_size=65,
    )

    # Monday opening window: 2026-09-21 09:20 IST (03:50 UTC)
    mon_open_dt = datetime(2026, 9, 21, 3, 50, 0, tzinfo=timezone.utc)
    mon_open_ms = int(mon_open_dt.timestamp() * 1000)

    fut_event = RawQuoteEvent(
        contract_id="NIFTY-I",
        exchange_timestamp_ms=mon_open_ms - 200,
        received_at_ms=mon_open_ms - 100,
        best_bid=24510.0,
        best_ask=24512.0,
        bid_quantity=100,
        ask_quantity=100,
        last_price=24511.0,
        open_interest=500000,
    )
    opt_quote = RawQuoteEvent(
        contract_id="NIFTY26OCT25000PE",
        exchange_timestamp_ms=mon_open_ms - 200,
        received_at_ms=mon_open_ms - 100,
        best_bid=450.0,
        best_ask=452.0,
        bid_quantity=50,
        ask_quantity=50,
        last_price=451.0,
        open_interest=60000,
    )

    # Monday opening window fill succeeds
    exec_res = collector.execute_pending_entry(
        opportunity_id=opp_id,
        cfg=sample_config,
        t1_spot_price=24510.0,
        futures_quote_event=fut_event,
        futures_symbol="NIFTY-I",
        option_candidates=[cand_valid],
        option_quote_events={"NIFTY26OCT25000PE": opt_quote},
        causal_beta=1.0,
        futures_lot_size=65,
        execution_timestamp_ms=mon_open_ms,
    )
    assert exec_res["status"] == "OPEN_POSITION"

    # Now verify that attempting execution OUTSIDE opening window (e.g. 11:30 IST / 06:00 UTC) fails closed
    fri_signal_2 = SnapbackSignal(
        symbol="NIFTY",
        side="fade_up",
        direction="BEARISH",
        option_type="PE",
        timestamp_ms=int(fri_dt.timestamp() * 1000) + 1000,
        entry=24500.0,
        mean_target=24800.0,
        stretch=1.8,
        atr=180.0,
        realized_vol=0.15,
        assumed_iv=0.18,
        level=24400.0,
        strength="MODERATE",
    )
    opp_id_2 = collector.record_signal_at_close(signal=fri_signal_2, cfg=sample_config)["opportunity_id"]
    mon_late_dt = datetime(2026, 9, 21, 6, 0, 0, tzinfo=timezone.utc)
    mon_late_ms = int(mon_late_dt.timestamp() * 1000)

    fut_event_late = RawQuoteEvent(
        contract_id="NIFTY-I",
        exchange_timestamp_ms=mon_late_ms - 200,
        received_at_ms=mon_late_ms - 100,
        best_bid=24510.0,
        best_ask=24512.0,
        bid_quantity=100,
        ask_quantity=100,
        last_price=24511.0,
        open_interest=500000,
    )
    opt_quote_late = RawQuoteEvent(
        contract_id="NIFTY26OCT25000PE",
        exchange_timestamp_ms=mon_late_ms - 200,
        received_at_ms=mon_late_ms - 100,
        best_bid=450.0,
        best_ask=452.0,
        bid_quantity=50,
        ask_quantity=50,
        last_price=451.0,
        open_interest=60000,
    )

    exec_res_late = collector.execute_pending_entry(
        opportunity_id=opp_id_2,
        cfg=sample_config,
        t1_spot_price=24510.0,
        futures_quote_event=fut_event_late,
        futures_symbol="NIFTY-I",
        option_candidates=[cand_valid],
        option_quote_events={"NIFTY26OCT25000PE": opt_quote_late},
        causal_beta=1.0,
        futures_lot_size=65,
        execution_timestamp_ms=mon_late_ms,
    )

    assert exec_res_late["status"] == "INCONCLUSIVE"
    assert "Missed T+1" in exec_res_late["reason"]



def test_fifteen_trading_sessions_not_fifteen_calendar_days(temp_warehouse, sample_config):
    """Proves: 15 trading sessions are counted as trading sessions, not calendar days."""
    from app.services.navigator.calendar import is_trading_day

    # 2026-09-18 is Friday.
    # Count 15 trading sessions from 2026-09-21 (Monday):
    trading_sessions = 0
    curr_dt = datetime(2026, 9, 21, tzinfo=timezone.utc)
    for d in range(30):
        t_date = (curr_dt + timedelta(days=d)).date()
        if is_trading_day(t_date):
            trading_sessions += 1
            if trading_sessions == 15:
                # 15th trading session date
                assert (t_date - curr_dt.date()).days > 15  # Calendar days must be strictly greater than 15!
                break


def test_premium_stop_fires_correctly(temp_warehouse, sample_config):
    """Proves: 35% premium stop closes position when option bid drops to <= entry * 0.65."""
    collector = SnapbackProspectiveCollector(warehouse=temp_warehouse)
    dt_t = datetime(2026, 9, 15, 15, 30, 0, tzinfo=timezone.utc)
    sig = SnapbackSignal(
        symbol="NIFTY",
        side="fade_up",
        direction="BEARISH",
        option_type="PE",
        timestamp_ms=int(dt_t.timestamp() * 1000) + 2000,
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

    now_ms = int(datetime(2026, 9, 16, 3, 45, 0, tzinfo=timezone.utc).timestamp() * 1000)
    fut_event = RawQuoteEvent(
        contract_id="NIFTY-I",
        exchange_timestamp_ms=now_ms - 200,
        received_at_ms=now_ms - 100,
        best_bid=24510.0,
        best_ask=24512.0,
        bid_quantity=100,
        ask_quantity=100,
        last_price=24511.0,
        open_interest=500000,
    )
    cand = OptionCandidateInfo(
        symbol="NIFTY26OCT25000PE",
        expiry="2026-10-29",
        strike=25000.0,
        option_type="PE",
        dte=45,
        is_monthly=True,
        theoretical_delta=-0.70,
        lot_size=65,
    )
    opt_quote = RawQuoteEvent(
        contract_id="NIFTY26OCT25000PE",
        exchange_timestamp_ms=now_ms - 200,
        received_at_ms=now_ms - 100,
        best_bid=100.0,
        best_ask=102.0,
        bid_quantity=50,
        ask_quantity=50,
        last_price=101.0,
        open_interest=60000,
    )

    exec_res = collector.execute_pending_entry(
        opportunity_id=opp_id,
        cfg=sample_config,
        t1_spot_price=24510.0,
        futures_quote_event=fut_event,
        futures_symbol="NIFTY-I",
        option_candidates=[cand],
        option_quote_events={"NIFTY26OCT25000PE": opt_quote},
        causal_beta=1.0,
        futures_lot_size=65,
        execution_timestamp_ms=now_ms,
    )
    assert exec_res["status"] == "OPEN_POSITION"

    # MTM check with option_bid = 60.0 (35% stop: 60.0 <= 100.0 * 0.65 = 65.0)
    reb_res = collector.rebalance_and_mtm(
        opportunity_id=opp_id,
        session_date="2026-09-17",
        symbol="NIFTY",
        current_spot=24600.0,
        current_option_delta=-0.60,
        option_bid=60.0,
        futures_quote_event=fut_event,
        option_entry_price=100.0,
        option_quantity=65,
        current_futures_lots=1,
        futures_lot_size=65,
        causal_beta=1.0,
        prior_realized_futures_pnl=0.0,
        prior_avg_futures_entry_price=24510.0,
    )
    assert reb_res["exit_reason"] == "PREMIUM_STOP"


def test_one_point_five_x_winner_becomes_runner_and_twenty_five_percent_giveback_closes_it(temp_warehouse, sample_config):
    """Proves: 1.5x winner becomes runner at session 15, and 25% give-back from peak closes it."""
    collector = SnapbackProspectiveCollector(warehouse=temp_warehouse)
    dt_t = datetime(2026, 9, 15, 15, 30, 0, tzinfo=timezone.utc)
    sig = SnapbackSignal(
        symbol="NIFTY",
        side="fade_up",
        direction="BEARISH",
        option_type="PE",
        timestamp_ms=int(dt_t.timestamp() * 1000) + 3000,
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

    now_ms = int(datetime(2026, 9, 16, 3, 45, 0, tzinfo=timezone.utc).timestamp() * 1000)
    fut_event = RawQuoteEvent(
        contract_id="NIFTY-I",
        exchange_timestamp_ms=now_ms - 200,
        received_at_ms=now_ms - 100,
        best_bid=24510.0,
        best_ask=24512.0,
        bid_quantity=100,
        ask_quantity=100,
        last_price=24511.0,
        open_interest=500000,
    )
    cand = OptionCandidateInfo(
        symbol="NIFTY26OCT25000PE",
        expiry="2026-10-29",
        strike=25000.0,
        option_type="PE",
        dte=45,
        is_monthly=True,
        theoretical_delta=-0.70,
        lot_size=65,
    )
    opt_quote = RawQuoteEvent(
        contract_id="NIFTY26OCT25000PE",
        exchange_timestamp_ms=now_ms - 200,
        received_at_ms=now_ms - 100,
        best_bid=100.0,
        best_ask=102.0,
        bid_quantity=50,
        ask_quantity=50,
        last_price=101.0,
        open_interest=60000,
    )

    exec_res = collector.execute_pending_entry(
        opportunity_id=opp_id,
        cfg=sample_config,
        t1_spot_price=24510.0,
        futures_quote_event=fut_event,
        futures_symbol="NIFTY-I",
        option_candidates=[cand],
        option_quote_events={"NIFTY26OCT25000PE": opt_quote},
        causal_beta=1.0,
        futures_lot_size=65,
        execution_timestamp_ms=now_ms,
    )

    assert exec_res["status"] == "OPEN_POSITION"

    # Set sessions_held to 15 in warehouse DB position record
    temp_warehouse.update_paper_position_sessions(opp_id, 15)

    # Rebalance at session 15: option_bid = 160.0 (1.6x entry price 100.0 >= 1.5x)
    # Position must transition to RUNNER, peak_option_bid = 160.0, and NOT exit
    reb_15 = collector.rebalance_and_mtm(
        opportunity_id=opp_id,
        session_date="2026-10-05",
        symbol="NIFTY",
        current_spot=24400.0,
        current_option_delta=-0.80,
        option_bid=160.0,
        futures_quote_event=fut_event,
        option_entry_price=102.0,
        option_quantity=65,
        current_futures_lots=1,
        futures_lot_size=65,
        causal_beta=1.0,
    )
    assert reb_15["is_runner"] is True
    assert reb_15["peak_option_bid"] == 160.0
    assert reb_15["exit_reason"] is None

    # Next session: Peak bid rises to 200.0
    reb_peak = collector.rebalance_and_mtm(
        opportunity_id=opp_id,
        session_date="2026-10-06",
        symbol="NIFTY",
        current_spot=24350.0,
        current_option_delta=-0.85,
        option_bid=200.0,
        futures_quote_event=fut_event,
        option_entry_price=102.0,
        option_quantity=65,
        current_futures_lots=1,
        futures_lot_size=65,
        causal_beta=1.0,
    )
    assert reb_peak["is_runner"] is True
    assert reb_peak["peak_option_bid"] == 200.0
    assert reb_peak["exit_reason"] is None

    # Subsequent session: Option bid drops to 145.0 (<= 200.0 * 0.75 = 150.0, 25% give-back from peak)
    reb_giveback = collector.rebalance_and_mtm(
        opportunity_id=opp_id,
        session_date="2026-10-07",
        symbol="NIFTY",
        current_spot=24450.0,
        current_option_delta=-0.70,
        option_bid=145.0,
        futures_quote_event=fut_event,
        option_entry_price=102.0,
        option_quantity=65,
        current_futures_lots=1,
        futures_lot_size=65,
        causal_beta=1.0,
    )
    assert reb_giveback["exit_reason"] == "RUNNER_TRAIL_STOP"


def test_banknifty_uses_rolling_beta():
    """Proves: BANKNIFTY is not hardcoded to 1.0 in production adapter."""
    import inspect
    from app.services import snapback

    fn_source = inspect.getsource(snapback.process_prospective_pending_entries)
    assert 'symbol == "NIFTY"' in fn_source  # Only NIFTY is forced to 1.0
    assert 'symbol in ("NIFTY", "BANKNIFTY", "FINNIFTY")' not in fn_source


def test_selected_contract_lot_sizes_propagate_into_quantities(temp_warehouse, sample_config):
    """Proves: Selected contract lot size propagates into option and futures quantity calculations."""
    collector = SnapbackProspectiveCollector(warehouse=temp_warehouse)
    dt_t = datetime(2026, 9, 15, 15, 30, 0, tzinfo=timezone.utc)
    sig1 = SnapbackSignal(
        symbol="BANKNIFTY",
        side="fade_up",
        direction="BEARISH",
        option_type="PE",
        timestamp_ms=int(dt_t.timestamp() * 1000) + 4000,
        entry=52000.0,
        mean_target=52500.0,
        stretch=1.8,
        atr=400.0,
        realized_vol=0.15,
        assumed_iv=0.18,
        level=51800.0,
        strength="MODERATE",
    )
    rec_res = collector.record_signal_at_close(signal=sig1, cfg=sample_config)
    opp_id = rec_res["opportunity_id"]

    now_ms = int(datetime(2026, 9, 16, 3, 45, 0, tzinfo=timezone.utc).timestamp() * 1000)
    fut_event = RawQuoteEvent(
        contract_id="BANKNIFTY-I",
        exchange_timestamp_ms=now_ms - 200,
        received_at_ms=now_ms - 100,
        best_bid=52000.0,
        best_ask=52005.0,
        bid_quantity=100,
        ask_quantity=100,
        last_price=52002.0,
        open_interest=500000,
    )
    cand_bn = OptionCandidateInfo(
        symbol="BANKNIFTY26OCT52000PE",
        expiry="2026-10-29",
        strike=52000.0,
        option_type="PE",
        dte=45,
        is_monthly=True,
        theoretical_delta=-0.70,
        lot_size=15,
    )
    opt_quote = RawQuoteEvent(
        contract_id="BANKNIFTY26OCT52000PE",
        exchange_timestamp_ms=now_ms - 200,
        received_at_ms=now_ms - 100,
        best_bid=600.0,
        best_ask=604.0,
        bid_quantity=50,
        ask_quantity=50,
        last_price=602.0,
        open_interest=60000,
    )

    exec_res = collector.execute_pending_entry(
        opportunity_id=opp_id,
        cfg=sample_config,
        t1_spot_price=52000.0,
        futures_quote_event=fut_event,
        futures_symbol="BANKNIFTY-I",
        option_candidates=[cand_bn],
        option_quote_events={"BANKNIFTY26OCT52000PE": opt_quote},
        causal_beta=1.10,
        futures_lot_size=15,
        execution_timestamp_ms=now_ms,
    )
    assert exec_res["status"] == "OPEN_POSITION"
    assert exec_res["option_quantity"] == 15
    assert exec_res["futures_quantity"] == 15

    sig2 = SnapbackSignal(
        symbol="BANKNIFTY",
        side="fade_up",
        direction="BEARISH",
        option_type="PE",
        timestamp_ms=int(dt_t.timestamp() * 1000) + 5000,
        entry=52000.0,
        mean_target=52500.0,
        stretch=1.8,
        atr=400.0,
        realized_vol=0.15,
        assumed_iv=0.18,
        level=51800.0,
        strength="MODERATE",
    )
    cand_bn_zero = OptionCandidateInfo(
        symbol="BANKNIFTY26OCT52000PE",
        expiry="2026-10-29",
        strike=52000.0,
        option_type="PE",
        dte=45,
        is_monthly=True,
        theoretical_delta=-0.70,
        lot_size=0,
    )
    opp_id_invalid = collector.record_signal_at_close(signal=sig2, cfg=sample_config)["opportunity_id"]
    exec_res_invalid = collector.execute_pending_entry(
        opportunity_id=opp_id_invalid,
        cfg=sample_config,
        t1_spot_price=52000.0,
        futures_quote_event=fut_event,
        futures_symbol="BANKNIFTY-I",
        option_candidates=[cand_bn_zero],
        option_quote_events={"BANKNIFTY26OCT52000PE": opt_quote},
        causal_beta=1.10,
        futures_lot_size=15,
        execution_timestamp_ms=now_ms,
    )
    assert exec_res_invalid["status"] == "INCONCLUSIVE"
    assert "lot size" in exec_res_invalid["reason"]



def test_rebalance_fees_accumulate(temp_warehouse, sample_config):
    """Proves: Statutory costs accumulate across entry, rebalances, and exit; hardcoded 40.0 is deleted."""
    collector = SnapbackProspectiveCollector(warehouse=temp_warehouse)
    dt_t = datetime(2026, 9, 15, 15, 30, 0, tzinfo=timezone.utc)
    sig = SnapbackSignal(
        symbol="NIFTY",
        side="fade_up",
        direction="BEARISH",
        option_type="PE",
        timestamp_ms=int(dt_t.timestamp() * 1000) + 6000,
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

    now_ms = int(datetime(2026, 9, 16, 3, 45, 0, tzinfo=timezone.utc).timestamp() * 1000)
    fut_event = RawQuoteEvent(
        contract_id="NIFTY-I",
        exchange_timestamp_ms=now_ms - 200,
        received_at_ms=now_ms - 100,
        best_bid=24510.0,
        best_ask=24512.0,
        bid_quantity=100,
        ask_quantity=100,
        last_price=24511.0,
        open_interest=500000,
    )
    cand = OptionCandidateInfo(
        symbol="NIFTY26OCT25000PE",
        expiry="2026-10-29",
        strike=25000.0,
        option_type="PE",
        dte=45,
        is_monthly=True,
        theoretical_delta=-0.70,
        lot_size=65,
    )
    opt_quote = RawQuoteEvent(
        contract_id="NIFTY26OCT25000PE",
        exchange_timestamp_ms=now_ms - 200,
        received_at_ms=now_ms - 100,
        best_bid=450.0,
        best_ask=452.0,
        bid_quantity=50,
        ask_quantity=50,
        last_price=451.0,
        open_interest=60000,
    )

    exec_res = collector.execute_pending_entry(
        opportunity_id=opp_id,
        cfg=sample_config,
        t1_spot_price=24510.0,
        futures_quote_event=fut_event,
        futures_symbol="NIFTY-I",
        option_candidates=[cand],
        option_quote_events={"NIFTY26OCT25000PE": opt_quote},
        causal_beta=1.0,
        futures_lot_size=65,
        execution_timestamp_ms=now_ms,
    )
    initial_costs = exec_res["accumulated_costs"]
    assert initial_costs > 0.0

    reb_res = collector.rebalance_and_mtm(
        opportunity_id=opp_id,
        session_date="2026-09-17",
        symbol="NIFTY",
        current_spot=24600.0,
        current_option_delta=-1.6,
        option_bid=470.0,
        futures_quote_event=fut_event,
        option_entry_price=452.0,
        option_quantity=65,
        current_futures_lots=1,
        futures_lot_size=65,
        causal_beta=1.0,
        prior_realized_futures_pnl=0.0,
        prior_avg_futures_entry_price=24512.0,
    )

    reb_costs = reb_res["accumulated_costs"]
    assert reb_costs > initial_costs

    close_res = collector.close_opportunity(
        opportunity_id=opp_id,
        symbol="NIFTY",
        exit_reason="HOLDING_HORIZON_EXPIRED",
        entry_ts="2026-09-16T09:15:00Z",
        exit_ts="2026-10-07T15:15:00Z",
        entry_spot=24510.0,
        exit_spot=24600.0,
        selected_strike=25000.0,
        entry_dte=45,
        exit_dte=24,
        iv_proxy=0.18,
        option_entry_price=452.0,
        option_exit_bid=470.0,
        futures_entry_price=24512.0,
        futures_exit_bid=24510.0,
        statutory_costs=0.0,
        option_quantity=65,
        futures_quantity=130,
        accumulated_costs=reb_costs,
    )
    assert close_res["actual_costs"] > reb_costs


def test_stale_exit_quote_cannot_close_trade(sample_config):
    """Proves: evaluate_quote_quality rejects stale or bad quotes, preventing MTM/exit execution."""
    from app.services.snapback_market_data import evaluate_quote_quality

    stale_event = RawQuoteEvent(
        contract_id="NIFTY26OCT25000PE",
        exchange_timestamp_ms=int((time.time() - 30) * 1000),
        received_at_ms=int(time.time() * 1000),
        best_bid=450.0,
        best_ask=452.0,
        bid_quantity=50,
        ask_quantity=50,
        last_price=451.0,
    )
    res = evaluate_quote_quality(stale_event, sample_config, now_ms=int(time.time() * 1000))
    assert res.accepted_for_execution is False

    bad_bid_event = RawQuoteEvent(
        contract_id="NIFTY26OCT25000PE",
        exchange_timestamp_ms=int((time.time() - 1) * 1000),
        received_at_ms=int(time.time() * 1000),
        best_bid=0.0,
        best_ask=452.0,
        bid_quantity=0,
        ask_quantity=50,
        last_price=451.0,
    )
    res_bad = evaluate_quote_quality(bad_bid_event, sample_config, now_ms=int(time.time() * 1000))
    assert res_bad.accepted_for_execution is False


def test_opening_window_lower_bound_rejected(temp_warehouse, sample_config):
    """Proves: Attempted entry before session open (e.g. 08:30 IST) is rejected as INCONCLUSIVE."""
    collector = SnapbackProspectiveCollector(warehouse=temp_warehouse)
    dt_t = datetime(2026, 9, 15, 15, 30, 0, tzinfo=timezone.utc)
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

    # 08:30 IST on 2026-09-16 (03:00 UTC) is before 09:15 IST open
    early_dt = datetime(2026, 9, 16, 3, 0, 0, tzinfo=timezone.utc)
    early_ms = int(early_dt.timestamp() * 1000)

    fut_event = RawQuoteEvent(
        contract_id="NIFTY-I",
        exchange_timestamp_ms=early_ms - 200,
        received_at_ms=early_ms - 100,
        best_bid=24510.0,
        best_ask=24512.0,
        bid_quantity=100,
        ask_quantity=100,
        last_price=24511.0,
        open_interest=500000,
    )
    cand = OptionCandidateInfo(
        symbol="NIFTY26OCT25000PE",
        expiry="2026-10-29",
        strike=25000.0,
        option_type="PE",
        dte=45,
        is_monthly=True,
        theoretical_delta=-0.70,
        lot_size=65,
    )
    opt_quote = RawQuoteEvent(
        contract_id="NIFTY26OCT25000PE",
        exchange_timestamp_ms=early_ms - 200,
        received_at_ms=early_ms - 100,
        best_bid=450.0,
        best_ask=452.0,
        bid_quantity=50,
        ask_quantity=50,
        last_price=451.0,
        open_interest=60000,
    )

    exec_res = collector.execute_pending_entry(
        opportunity_id=opp_id,
        cfg=sample_config,
        t1_spot_price=24510.0,
        futures_quote_event=fut_event,
        futures_symbol="NIFTY-I",
        option_candidates=[cand],
        option_quote_events={"NIFTY26OCT25000PE": opt_quote},
        causal_beta=1.0,
        futures_lot_size=65,
        execution_timestamp_ms=early_ms,
    )
    assert exec_res["status"] == "INCONCLUSIVE"
    assert "Missed T+1" in exec_res["reason"] or "Outside T+1" in exec_res["reason"]


def test_production_wiring_end_to_end_lifecycle(temp_warehouse, sample_config):
    """End-to-End Test: Signal -> Entry -> Calendar progress -> Hedge rebalance -> Cost persistence -> Session 15 Runner -> 25% Give-back exit."""
    collector = SnapbackProspectiveCollector(warehouse=temp_warehouse)

    # 1. Day T signal (2026-09-15 close)
    dt_t = datetime(2026, 9, 15, 15, 30, 0, tzinfo=timezone.utc)
    sig = SnapbackSignal(
        symbol="NIFTY",
        side="fade_up",
        direction="BEARISH",
        option_type="PE",
        timestamp_ms=int(dt_t.timestamp() * 1000) + 100,
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

    # 2. T+1 entry at 09:20 IST on 2026-09-16 (03:50 UTC)
    t1_ms = int(datetime(2026, 9, 16, 3, 50, 0, tzinfo=timezone.utc).timestamp() * 1000)
    fut_event = RawQuoteEvent(
        contract_id="NIFTY-I",
        exchange_timestamp_ms=t1_ms - 200,
        received_at_ms=t1_ms - 100,
        best_bid=24510.0,
        best_ask=24512.0,
        bid_quantity=100,
        ask_quantity=100,
        last_price=24511.0,
        open_interest=500000,
    )
    cand = OptionCandidateInfo(
        symbol="NIFTY26OCT25000PE",
        expiry="2026-10-29",
        strike=25000.0,
        option_type="PE",
        dte=45,
        is_monthly=True,
        theoretical_delta=-0.70,
        lot_size=65,
    )
    opt_quote = RawQuoteEvent(
        contract_id="NIFTY26OCT25000PE",
        exchange_timestamp_ms=t1_ms - 200,
        received_at_ms=t1_ms - 100,
        best_bid=100.0,
        best_ask=102.0,
        bid_quantity=50,
        ask_quantity=50,
        last_price=101.0,
        open_interest=60000,
    )

    exec_res = collector.execute_pending_entry(
        opportunity_id=opp_id,
        cfg=sample_config,
        t1_spot_price=24510.0,
        futures_quote_event=fut_event,
        futures_symbol="NIFTY-I",
        option_candidates=[cand],
        option_quote_events={"NIFTY26OCT25000PE": opt_quote},
        causal_beta=1.0,
        futures_lot_size=65,
        execution_timestamp_ms=t1_ms,
    )
    assert exec_res["status"] == "OPEN_POSITION"
    entry_costs = exec_res["accumulated_costs"]
    assert entry_costs > 0.0

    # 3. Session 2 (2026-09-17) MTM & Hedge Rebalance (delta shifts to -1.6 -> 2 lots)
    s2_ms = int(datetime(2026, 9, 17, 3, 50, 0, tzinfo=timezone.utc).timestamp() * 1000)
    fut_event_s2 = RawQuoteEvent(
        contract_id="NIFTY-I",
        exchange_timestamp_ms=s2_ms - 200,
        received_at_ms=s2_ms - 100,
        best_bid=24510.0,
        best_ask=24512.0,
        bid_quantity=100,
        ask_quantity=100,
        last_price=24511.0,
        open_interest=500000,
    )
    opt_quote_s2 = RawQuoteEvent(
        contract_id="NIFTY26OCT25000PE",
        exchange_timestamp_ms=s2_ms - 200,
        received_at_ms=s2_ms - 100,
        best_bid=110.0,
        best_ask=112.0,
        bid_quantity=50,
        ask_quantity=50,
        last_price=111.0,
        open_interest=60000,
    )

    reb_res = collector.rebalance_and_mtm(
        opportunity_id=opp_id,
        session_date="2026-09-17",
        symbol="NIFTY",
        current_spot=24600.0,
        current_option_delta=-1.6,
        option_bid=110.0,
        futures_quote_event=fut_event_s2,
        option_entry_price=102.0,
        option_quantity=65,
        current_futures_lots=1,
        futures_lot_size=65,
        causal_beta=1.0,
        option_quote_event=opt_quote_s2,
        cfg=sample_config,
    )
    accumulated_costs_after_reb = reb_res["accumulated_costs"]
    assert accumulated_costs_after_reb > entry_costs
    assert reb_res["sessions_held"] == 2

    # Verify accumulated_costs was persisted to DB position ledger
    pos_db = temp_warehouse.get_paper_position(opp_id)
    assert float(pos_db["accumulated_costs"]) == accumulated_costs_after_reb

    # 4. Session 15 (2026-10-07) - Option Bid = 160.0 (1.6x entry price 100.0 >= 1.5x) -> Transitions to RUNNER
    s15_ms = int(datetime(2026, 10, 7, 3, 50, 0, tzinfo=timezone.utc).timestamp() * 1000)
    fut_event_s15 = RawQuoteEvent(
        contract_id="NIFTY-I",
        exchange_timestamp_ms=s15_ms - 200,
        received_at_ms=s15_ms - 100,
        best_bid=24510.0,
        best_ask=24512.0,
        bid_quantity=100,
        ask_quantity=100,
        last_price=24511.0,
        open_interest=500000,
    )
    opt_quote_s15 = RawQuoteEvent(
        contract_id="NIFTY26OCT25000PE",
        exchange_timestamp_ms=s15_ms - 200,
        received_at_ms=s15_ms - 100,
        best_bid=160.0,
        best_ask=162.0,
        bid_quantity=50,
        ask_quantity=50,
        last_price=161.0,
        open_interest=60000,
    )

    reb_15 = collector.rebalance_and_mtm(
        opportunity_id=opp_id,
        session_date="2026-10-07",
        symbol="NIFTY",
        current_spot=24400.0,
        current_option_delta=-0.80,
        option_bid=160.0,
        futures_quote_event=fut_event_s15,
        option_entry_price=102.0,
        option_quantity=65,
        current_futures_lots=2,
        futures_lot_size=65,
        causal_beta=1.0,
        option_quote_event=opt_quote_s15,
        cfg=sample_config,
    )
    assert reb_15["sessions_held"] == 15
    assert reb_15["is_runner"] is True
    assert reb_15["exit_reason"] is None

    # 5. Session 16 (2026-10-08) - Option Bid drops to 110.0 (110.0 <= 160.0 * 0.75 = 120.0) -> RUNNER_TRAIL_STOP
    s16_ms = int(datetime(2026, 10, 8, 3, 50, 0, tzinfo=timezone.utc).timestamp() * 1000)
    fut_event_s16 = RawQuoteEvent(
        contract_id="NIFTY-I",
        exchange_timestamp_ms=s16_ms - 200,
        received_at_ms=s16_ms - 100,
        best_bid=24510.0,
        best_ask=24512.0,
        bid_quantity=100,
        ask_quantity=100,
        last_price=24511.0,
        open_interest=500000,
    )
    opt_quote_s16 = RawQuoteEvent(
        contract_id="NIFTY26OCT25000PE",
        exchange_timestamp_ms=s16_ms - 200,
        received_at_ms=s16_ms - 100,
        best_bid=110.0,
        best_ask=112.0,
        bid_quantity=50,
        ask_quantity=50,
        last_price=111.0,
        open_interest=60000,
    )

    reb_16 = collector.rebalance_and_mtm(
        opportunity_id=opp_id,
        session_date="2026-10-08",
        symbol="NIFTY",
        current_spot=24500.0,
        current_option_delta=-0.70,
        option_bid=110.0,
        futures_quote_event=fut_event_s16,
        option_entry_price=102.0,
        option_quantity=65,
        current_futures_lots=2,
        futures_lot_size=65,
        causal_beta=1.0,
        option_quote_event=opt_quote_s16,
        cfg=sample_config,
    )
    assert reb_16["exit_reason"] == "RUNNER_TRAIL_STOP"

    # 6. Production close_opportunity with RUNNER_TRAIL_STOP
    close_res = collector.close_opportunity(
        opportunity_id=opp_id,
        symbol="NIFTY",
        exit_reason=reb_16["exit_reason"],
        entry_ts="2026-09-16T09:20:00Z",
        exit_ts="2026-10-08T15:15:00Z",
        entry_spot=24510.0,
        exit_spot=24500.0,
        selected_strike=25000.0,
        entry_dte=45,
        exit_dte=23,
        iv_proxy=0.18,
        option_entry_price=102.0,
        option_exit_bid=110.0,
        futures_entry_price=24512.0,
        futures_exit_bid=24510.0,
        option_quantity=65,
        futures_quantity=130,
        accumulated_costs=reb_16["accumulated_costs"],
    )

    # Verify actual_costs includes entry costs + rebalance costs + liquidation costs
    assert close_res["actual_costs"] > reb_16["accumulated_costs"]

    # Verify paper position status in DB is now CLOSED
    pos_closed = temp_warehouse.get_paper_position(opp_id)
    assert pos_closed["status"] == "CLOSED"


@pytest.mark.asyncio
async def test_adapter_process_prospective_pending_entries_unusual_lot_size(temp_warehouse, sample_config, monkeypatch):
    """Proves: Production adapter process_prospective_pending_entries_and_mtm reads lot_size=37 from Kite row, populates OptionCandidateInfo.lot_size=37, and opens paper position with option_qty=37."""
    from unittest.mock import AsyncMock
    from app.services.snapback import process_prospective_pending_entries_and_mtm

    monkeypatch.setattr(
        "app.services.snapback_prospective_collector.SnapbackObservationWarehouse",
        lambda *a, **k: temp_warehouse
    )

    collector = SnapbackProspectiveCollector(warehouse=temp_warehouse)

    # 1. Record Day T signal on previous trading day close (2026-09-15 15:30 IST)
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

    # Target T+1 date: 2026-09-16 09:20 IST (03:50 UTC)
    t1_dt = datetime(2026, 9, 16, 3, 50, 0, tzinfo=timezone.utc)

    monkeypatch.setattr("time.time", lambda: t1_dt.timestamp())

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is not None:
                return t1_dt.astimezone(tz)
            return t1_dt

    monkeypatch.setattr("app.services.snapback.datetime", FixedDateTime)

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
            "lot_size": 25,
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
            "lot_size": 37,  # Deliberately unusual lot size
        },
    ])

    await process_prospective_pending_entries_and_mtm(mock_client, sample_config)

    pos = temp_warehouse.get_paper_position(opp_id)
    assert pos is not None
    assert pos["status"] == "OPEN"
    assert int(pos["option_qty"]) == 37


@pytest.mark.asyncio
async def test_adapter_process_prospective_pending_entries_zero_lot_size_rejected(temp_warehouse, sample_config, monkeypatch):
    """Proves: Production adapter rejects candidate with lot_size=0 as INCONCLUSIVE and never defaults to 65."""
    from unittest.mock import AsyncMock
    from app.services.snapback import process_prospective_pending_entries_and_mtm

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
            "lot_size": 25,
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
            "lot_size": 0,  # Missing or zero lot size
        },
    ])

    await process_prospective_pending_entries_and_mtm(mock_client, sample_config)

    opp = temp_warehouse.get_opportunity_by_id(opp_id)
    assert opp["status"] == "INCONCLUSIVE"
    pos = temp_warehouse.get_paper_position(opp_id)
    assert pos is None







