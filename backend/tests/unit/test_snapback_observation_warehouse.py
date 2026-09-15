"""Unit tests for Snapback Prospective Observation Warehouse Service & Collector (PROSPECTIVE CAPTURE 1.0)."""
import os
import sqlite3
import tempfile
import time
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
    return SnapbackSignal(
        symbol="NIFTY",
        side="fade_up",
        direction="BEARISH",
        option_type="PE",
        timestamp_ms=1789500000000,
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
    assert pending[0]["ema_50"] == 24800.0  # signal.mean_target
    assert pending[0]["ema_200"] == 24400.0  # signal.level


def test_collector_8_dte_option_rejected(temp_warehouse, sample_config, sample_fade_up_signal):
    collector = SnapbackProspectiveCollector(warehouse=temp_warehouse)
    rec_res = collector.record_signal_at_close(signal=sample_fade_up_signal, cfg=sample_config)
    opp_id = rec_res["opportunity_id"]

    now_ms = int(time.time() * 1000)
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

    # Candidate has DTE=8 (invalid: frozen rules require 40-60 DTE)
    cand_8_dte = OptionCandidateInfo(
        symbol="NIFTY2692425000PE",
        expiry="2026-09-24",
        strike=25000.0,
        option_type="PE",
        dte=8,
        is_monthly=True,
        theoretical_delta=-0.70,
    )

    opt_quote = RawQuoteEvent(
        contract_id="NIFTY2692425000PE",
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
        option_candidates=[cand_8_dte],
        option_quote_events={"NIFTY2692425000PE": opt_quote},
        causal_beta=1.15,
        option_lot_size=65,
        futures_lot_size=65,
    )

    assert exec_res["status"] == "NO_FILL"
    assert exec_res["candidates_recorded"] == 1


def test_collector_stale_futures_quote_inconclusive(temp_warehouse, sample_config, sample_fade_up_signal):
    collector = SnapbackProspectiveCollector(warehouse=temp_warehouse)
    rec_res = collector.record_signal_at_close(signal=sample_fade_up_signal, cfg=sample_config)
    opp_id = rec_res["opportunity_id"]

    now_ms = int(time.time() * 1000)
    # Stale futures quote (age > max_quote_age_ms, e.g. 100 seconds old)
    stale_fut_event = RawQuoteEvent(
        contract_id="NIFTY-I",
        exchange_timestamp_ms=now_ms - 100000,
        received_at_ms=now_ms - 100000,
        best_bid=24510.0,
        best_ask=24512.0,
        bid_quantity=100,
        ask_quantity=100,
        last_price=24511.0,
        open_interest=500000,
    )

    exec_res = collector.execute_pending_entry(
        opportunity_id=opp_id,
        cfg=sample_config,
        t1_spot_price=24510.0,
        futures_quote_event=stale_fut_event,
        futures_symbol="NIFTY-I",
        option_candidates=[],
        option_quote_events={},
        causal_beta=1.15,
        option_lot_size=65,
        futures_lot_size=65,
    )

    assert exec_res["status"] == "INCONCLUSIVE"


def test_collector_valid_fade_up_opens_position_long_futures_hedge(temp_warehouse, sample_config, sample_fade_up_signal):
    collector = SnapbackProspectiveCollector(warehouse=temp_warehouse)
    rec_res = collector.record_signal_at_close(signal=sample_fade_up_signal, cfg=sample_config)
    opp_id = rec_res["opportunity_id"]

    now_ms = int(time.time() * 1000)
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
    )

    assert exec_res["status"] == "OPEN_POSITION"
    assert exec_res["chosen_contract"] == "NIFTY26OCT25000PE"
    assert exec_res["causal_beta"] == 1.15
    assert exec_res["target_hedge_lots"] == 1

    # Verify decision recorded with causal beta
    decisions = temp_warehouse.get_records_by_table("decisions", opp_id)
    assert len(decisions) == 1
    assert decisions[0]["causal_beta"] == 1.15

    # Verify long index futures hedge entry recorded
    hedge_events = temp_warehouse.get_records_by_table("hedge_rebalances", opp_id)
    assert len(hedge_events) == 1
    assert hedge_events[0]["new_hedge_lots"] == 1

    # Test daily MTM
    mtm_res = collector.rebalance_and_mtm(
        opportunity_id=opp_id,
        session_date="2026-09-16",
        symbol="NIFTY",
        current_spot=24600.0,
        current_option_delta=-0.65,
        option_bid=470.0,
        futures_bid=24612.0,
        option_entry_price=exec_res["option_fill_price"],
        futures_entry_price=exec_res["futures_fill_price"],
        option_quantity=65,
        current_futures_lots=1,
        futures_lot_size=65,
        causal_beta=1.15,
    )

    assert mtm_res["option_mtm"] > 0
    assert mtm_res["futures_mtm"] > 0
    assert mtm_res["total_mtm"] == pytest.approx(mtm_res["option_mtm"] + mtm_res["futures_mtm"], abs=1e-2)

    # Test closing opportunity with canonical Black-Scholes counterfactual calculation
    close_res = collector.close_opportunity(
        opportunity_id=opp_id,
        symbol="NIFTY",
        exit_reason="HOLDING_HORIZON",
        entry_ts="2026-09-16T09:30:00Z",
        exit_ts="2026-10-01T15:15:00Z",
        entry_spot=24510.0,
        exit_spot=24700.0,
        selected_strike=25000.0,
        entry_dte=45,
        exit_dte=30,
        iv_proxy=0.18,
        option_entry_price=exec_res["option_fill_price"],
        option_exit_bid=520.0,
        futures_entry_price=exec_res["futures_fill_price"],
        futures_exit_bid=24710.0,
        statutory_costs=exec_res["statutory_costs"],
        option_quantity=65,
        futures_quantity=65,
    )

    assert close_res["status"] == "RECORDED"
    outcomes = temp_warehouse.get_records_by_table("outcomes", opp_id)
    assert len(outcomes) == 1
    assert outcomes[0]["modeled_total_pnl"] != 0.0
    assert outcomes[0]["actual_total_pnl"] != 0.0
