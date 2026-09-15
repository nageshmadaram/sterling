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

    # Day T+1 execution timestamp: 2026-09-16T09:15:00Z
    t1_dt = datetime(2026, 9, 16, 9, 15, 0, tzinfo=timezone.utc)
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
        option_lot_size=65,
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
        option_lot_size=65,
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
        option_lot_size=65,
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
    t1_ms = int(datetime(2026, 9, 16, 9, 15, 0, tzinfo=timezone.utc).timestamp() * 1000)

    res = collector.execute_pending_entry(
        opportunity_id=opp_id,
        cfg=tampered_cfg,
        t1_spot_price=24510.0,
        futures_quote_event=None,
        futures_symbol="NIFTY-I",
        option_candidates=[],
        option_quote_events={},
        causal_beta=1.0,
        option_lot_size=65,
        futures_lot_size=65,
        execution_timestamp_ms=t1_ms,
    )

    assert res["status"] == "NON_FROZEN_CONFIG"


def test_bid_ask_aware_futures_rebalancing(temp_warehouse, sample_config, sample_fade_up_signal):
    """Adversarial Test 5: Futures rebalance fills increases at ask, decreases at bid, retains realized P&L."""
    collector = SnapbackProspectiveCollector(warehouse=temp_warehouse)
    rec_res = collector.record_signal_at_close(signal=sample_fade_up_signal, cfg=sample_config)
    opp_id = rec_res["opportunity_id"]

    now_ms = int(datetime(2026, 9, 16, 9, 15, 0, tzinfo=timezone.utc).timestamp() * 1000)
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
        causal_beta=1.15,
        option_lot_size=65,
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
        current_option_delta=-0.85,
        option_bid=470.0,
        futures_quote_event=fut_event_up,
        option_entry_price=exec_res["option_fill_price"],
        option_quantity=130,  # 2 option lots
        current_futures_lots=1,
        futures_lot_size=65,
        causal_beta=1.15,
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
        causal_beta=1.15,
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
