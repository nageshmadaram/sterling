"""Unit tests for Snapback Prospective Observation Warehouse Service & Collector."""
import os
import sqlite3
import tempfile
import pytest

from app.services.snapback_observation_warehouse import SnapbackObservationWarehouse
from app.services.snapback_prospective_collector import (
    FuturesQuoteSnapshot,
    MarketSnapshot,
    OptionCandidateSnapshot,
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


def test_warehouse_immutable_inserts_reject_duplicates(temp_warehouse):
    opp_id = "OPP-DUP-TEST"
    temp_warehouse.record_opportunity(
        opportunity_id=opp_id,
        symbol="NIFTY",
        signal_type="BEARISH_SNAPBACK",
        spot_price=24500.0,
        ema_50=24800.0,
        ema_200=24200.0,
        trend="BEARISH",
        is_valid=True,
    )

    # Attempting duplicate insert must raise IntegrityError
    with pytest.raises(sqlite3.IntegrityError):
        temp_warehouse.record_opportunity(
            opportunity_id=opp_id,
            symbol="NIFTY",
            signal_type="BEARISH_SNAPBACK",
            spot_price=24600.0,
            ema_50=24800.0,
            ema_200=24200.0,
            trend="BEARISH",
            is_valid=True,
        )


def test_warehouse_all_11_table_writers(temp_warehouse):
    opp_id = "OPP-FULL-TABLE-TEST"
    symbol = "NIFTY"
    
    temp_warehouse.record_opportunity(
        opportunity_id=opp_id, symbol=symbol, signal_type="SNAPBACK", spot_price=24500.0,
        ema_50=24800.0, ema_200=24000.0, trend="BEARISH", is_valid=True
    )

    temp_warehouse.record_contract_candidate(
        candidate_id=f"CAND-{opp_id}", opportunity_id=opp_id, candidate_rank=1,
        option_type="PE", dte=8, strike_distance=500.0, theoretical_delta=-0.69,
        is_chosen=True, symbol="NIFTY2692425000PE", strike=25000.0
    )

    temp_warehouse.record_option_quote(
        quote_id=f"QUOTE-OPT-{opp_id}", opportunity_id=opp_id, symbol="NIFTY2692425000PE",
        bid=450.0, ask=452.0, ltp=451.0, oi=50000, iv=0.18, delta=-0.69
    )

    temp_warehouse.record_futures_quote(
        quote_id=f"QUOTE-FUT-{opp_id}", opportunity_id=opp_id, symbol=symbol,
        futures_symbol="NIFTY-I", bid=24510.0, ask=24512.0, ltp=24511.0, basis=11.0
    )

    temp_warehouse.record_decision(
        decision_id=f"DECISION-{opp_id}", opportunity_id=opp_id, symbol=symbol,
        decision="EXECUTE_PAPER", chosen_option_symbol="NIFTY2692425000PE", chosen_strike=25000.0, chosen_delta=-0.69
    )

    temp_warehouse.record_paper_fill(
        fill_id=f"FILL-{opp_id}", opportunity_id=opp_id, symbol="NIFTY2692425000PE",
        order_side="BUY", fill_price=452.2, fill_quantity=65, slippage=0.2
    )

    temp_warehouse.record_hedge_rebalance(
        rebalance_id=f"REB-{opp_id}", opportunity_id=opp_id, symbol=symbol,
        prior_hedge_lots=0, new_hedge_lots=1, futures_fill_price=24509.0, reason="INITIAL"
    )

    temp_warehouse.record_daily_mtm(
        mtm_id=f"MTM-{opp_id}", session_date="2026-09-16", opportunity_id=opp_id, symbol=symbol,
        option_mtm=650.0, futures_mtm=-300.0, total_mtm=350.0
    )

    temp_warehouse.record_margin_snapshot(
        snapshot_id=f"MARGIN-{opp_id}", opportunity_id=opp_id, symbol=symbol,
        option_margin_required=29380.0, futures_margin_required=190000.0, total_margin=219380.0, available_capital=1000000.0
    )

    temp_warehouse.record_cost(
        cost_id=f"COST-{opp_id}", opportunity_id=opp_id, symbol=symbol,
        brokerage=40.0, stt=36.0, exchange_txn_fee=15.0, gst=9.9, stamp_duty=0.8, total_statutory_costs=101.7
    )

    temp_warehouse.record_outcome(
        opportunity_id=opp_id, symbol=symbol, exit_reason="TARGET",
        entry_ts="2026-09-16T09:30:00Z", exit_ts="2026-09-16T15:15:00Z",
        modeled_option_pnl=3000.0, actual_option_pnl=2800.0,
        modeled_futures_pnl=-1000.0, actual_futures_pnl=-1100.0,
        modeled_costs=90.0, actual_costs=101.7
    )

    # Verify query returns data for all 11 tables
    for table_name in [
        "opportunities", "contract_candidates", "option_quotes", "futures_quotes",
        "decisions", "paper_fills", "hedge_rebalances", "daily_mtm",
        "margin_snapshots", "costs", "outcomes"
    ]:
        records = temp_warehouse.get_records_by_table(table_name, opportunity_id=opp_id)
        assert len(records) == 1, f"Expected 1 record in {table_name}, got {len(records)}"


def test_collector_valid_snapshot_full_pipeline(temp_warehouse):
    collector = SnapbackProspectiveCollector(warehouse=temp_warehouse)

    snapshot = MarketSnapshot(
        symbol="NIFTY",
        spot_price=24500.0,
        ema_50=24800.0,
        ema_200=25000.0,
        trend="BEARISH",
        futures_quote=FuturesQuoteSnapshot(
            futures_symbol="NIFTY-I",
            bid=24510.0,
            ask=24512.0,
            ltp=24511.0,
            basis=11.0,
        ),
        option_candidates=[
            OptionCandidateSnapshot(
                symbol="NIFTY2692425000PE",
                expiry="2026-09-24",
                strike=25000.0,
                option_type="PE",
                dte=8,
                strike_distance=500.0,
                theoretical_delta=-0.70,
                bid=450.0,
                ask=452.0,
                ltp=451.0,
                oi=50000,
                iv=0.18,
            ),
            OptionCandidateSnapshot(
                symbol="NIFTY2692424800PE",
                expiry="2026-09-24",
                strike=24800.0,
                option_type="PE",
                dte=8,
                strike_distance=300.0,
                theoretical_delta=-0.55,
                bid=310.0,
                ask=312.0,
                ltp=311.0,
                oi=42000,
                iv=0.17,
            ),
        ],
    )

    res = collector.process_snapshot(snapshot)

    assert res["status"] == "PAPER_POSITION_OPENED"
    assert res["chosen_contract"] == "NIFTY2692425000PE"
    assert res["candidates_recorded"] == 2
    assert res["target_hedge_lots"] == 1

    opp_id = res["opportunity_id"]
    
    # Check that candidate 1 AND candidate 2 were both recorded in contract_candidates
    candidates = temp_warehouse.get_records_by_table("contract_candidates", opp_id)
    assert len(candidates) == 2

    # Check option quotes recorded for both
    opt_quotes = temp_warehouse.get_records_by_table("option_quotes", opp_id)
    assert len(opt_quotes) == 2


def test_collector_rejected_opportunity_stored(temp_warehouse):
    collector = SnapbackProspectiveCollector(warehouse=temp_warehouse)

    # Spot price > EMA 50 -> Invalid setup
    snapshot = MarketSnapshot(
        symbol="NIFTY",
        spot_price=24900.0,
        ema_50=24800.0,
        ema_200=24500.0,
        trend="BULLISH",
        futures_quote=FuturesQuoteSnapshot(
            futures_symbol="NIFTY-I", bid=24910.0, ask=24912.0
        ),
        option_candidates=[],
    )

    res = collector.process_snapshot(snapshot)
    assert res["status"] == "REJECTED"
    assert "SPOT_NOT_BELOW_EMA50" in res["rejection_reason"]

    opp_id = res["opportunity_id"]
    opp_records = temp_warehouse.get_records_by_table("opportunities", opp_id)
    assert len(opp_records) == 1
    assert opp_records[0]["is_valid"] == 0
    assert "SPOT_NOT_BELOW_EMA50" in opp_records[0]["rejection_reason"]
