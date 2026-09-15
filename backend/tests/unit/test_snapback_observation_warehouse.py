"""Unit tests for Snapback Prospective Observation Warehouse Service & Collector."""
import os
import sqlite3
import tempfile
import pytest

from app.engines.snapback.models import SnapbackSignal
from app.services.snapback_observation_warehouse import SnapbackObservationWarehouse
from app.services.snapback_prospective_collector import (
    FuturesQuoteSnapshot,
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
        signal_type="BEARISH_SNAPBACK",
        spot_price=24500.0,
        ema_50=24800.0,
        ema_200=24200.0,
        trend="BEARISH",
        is_valid=True,
    )

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


def test_collector_no_signal_opens_no_position(temp_warehouse):
    collector = SnapbackProspectiveCollector(warehouse=temp_warehouse)

    res = collector.process_signal_and_snapshot(
        signal=None,  # Frozen engine emitted NO signal
        futures_quote=FuturesQuoteSnapshot(futures_symbol="NIFTY-I", bid=24510.0, ask=24512.0),
        option_candidates=[],
        causal_beta=1.15,
        option_lot_size=65,
        futures_lot_size=65,
    )

    assert res["status"] == "NO_SIGNAL"
    assert res["position_opened"] is False


def test_collector_8_dte_option_rejected(temp_warehouse, sample_fade_up_signal):
    collector = SnapbackProspectiveCollector(warehouse=temp_warehouse)

    # Candidate has 8 DTE (invalid: frozen rules require 40-60 DTE)
    cand_8_dte = OptionCandidateSnapshot(
        symbol="NIFTY2692425000PE",
        expiry="2026-09-24",
        strike=25000.0,
        option_type="PE",
        dte=8,
        is_monthly=True,
        theoretical_delta=-0.70,
        bid=450.0,
        ask=452.0,
        oi=60000,
    )

    res = collector.process_signal_and_snapshot(
        signal=sample_fade_up_signal,
        futures_quote=FuturesQuoteSnapshot(futures_symbol="NIFTY-I", bid=24510.0, ask=24512.0),
        option_candidates=[cand_8_dte],
        causal_beta=1.15,
        option_lot_size=65,
        futures_lot_size=65,
    )

    assert res["status"] == "NO_FILL"
    assert res["position_opened"] is False


def test_collector_missing_ask_no_fill(temp_warehouse, sample_fade_up_signal):
    collector = SnapbackProspectiveCollector(warehouse=temp_warehouse)

    # Missing ask (ask = 0.0) -> Must return NO_FILL and NEVER fall back to LTP
    cand_no_ask = OptionCandidateSnapshot(
        symbol="NIFTY26OCT25000PE",
        expiry="2026-10-29",
        strike=25000.0,
        option_type="PE",
        dte=45,
        is_monthly=True,
        theoretical_delta=-0.70,
        bid=450.0,
        ask=0.0,  # Missing ask!
        ltp=451.0,
        oi=60000,
    )

    res = collector.process_signal_and_snapshot(
        signal=sample_fade_up_signal,
        futures_quote=FuturesQuoteSnapshot(futures_symbol="NIFTY-I", bid=24510.0, ask=24512.0),
        option_candidates=[cand_no_ask],
        causal_beta=1.15,
        option_lot_size=65,
        futures_lot_size=65,
    )

    assert res["status"] == "NO_FILL"
    assert res["position_opened"] is False


def test_collector_stale_futures_quote_inconclusive(temp_warehouse, sample_fade_up_signal):
    collector = SnapbackProspectiveCollector(warehouse=temp_warehouse)

    cand_valid = OptionCandidateSnapshot(
        symbol="NIFTY26OCT25000PE",
        expiry="2026-10-29",
        strike=25000.0,
        option_type="PE",
        dte=45,
        is_monthly=True,
        theoretical_delta=-0.70,
        bid=450.0,
        ask=452.0,
        oi=60000,
    )

    # Stale/missing futures quote
    stale_futures = FuturesQuoteSnapshot(
        futures_symbol="NIFTY-I", bid=0.0, ask=0.0, is_stale=True
    )

    res = collector.process_signal_and_snapshot(
        signal=sample_fade_up_signal,
        futures_quote=stale_futures,
        option_candidates=[cand_valid],
        causal_beta=1.15,
        option_lot_size=65,
        futures_lot_size=65,
    )

    assert res["status"] == "INCONCLUSIVE"
    assert res["position_opened"] is False


def test_collector_valid_fade_up_opens_position_long_futures_hedge(temp_warehouse, sample_fade_up_signal):
    collector = SnapbackProspectiveCollector(warehouse=temp_warehouse)

    valid_cand = OptionCandidateSnapshot(
        symbol="NIFTY26OCT25000PE",
        expiry="2026-10-29",
        strike=25000.0,
        option_type="PE",
        dte=45,
        is_monthly=True,
        theoretical_delta=-0.70,
        bid=450.0,
        ask=452.0,
        oi=60000,
    )

    futures_quote = FuturesQuoteSnapshot(
        futures_symbol="NIFTY-I", bid=24510.0, ask=24512.0
    )

    res = collector.process_signal_and_snapshot(
        signal=sample_fade_up_signal,
        futures_quote=futures_quote,
        option_candidates=[valid_cand],
        causal_beta=1.15,
        option_lot_size=65,
        futures_lot_size=65,
    )

    assert res["status"] == "PAPER_POSITION_OPENED"
    assert res["position_opened"] is True
    assert res["chosen_contract"] == "NIFTY26OCT25000PE"
    assert res["causal_beta"] == 1.15
    assert res["target_hedge_lots"] == 1

    opp_id = res["opportunity_id"]

    # Verify decision recorded with causal beta
    decisions = temp_warehouse.get_records_by_table("decisions", opp_id)
    assert len(decisions) == 1
    assert decisions[0]["causal_beta"] == 1.15

    # Verify long index futures hedge entry recorded
    hedge_events = temp_warehouse.get_records_by_table("hedge_rebalances", opp_id)
    assert len(hedge_events) == 1
    assert hedge_events[0]["new_hedge_lots"] == 1

    # Test MTM formula: Long futures earns (futures_bid - futures_entry) * qty
    mtm_res = collector.record_daily_mtm(
        opportunity_id=opp_id,
        session_date="2026-09-16",
        symbol="NIFTY",
        option_bid=470.0,  # +20 pts on 65 qty = +1300
        futures_bid=24612.0,  # +100 pts on futures entry 24513.25 = +6500
        option_entry_price=452.226,
        futures_entry_price=24512.226,
        option_quantity=65,
        futures_quantity=65,
    )

    assert mtm_res["option_mtm"] > 0
    assert mtm_res["futures_mtm"] > 0
    assert mtm_res["total_mtm"] == pytest.approx(mtm_res["option_mtm"] + mtm_res["futures_mtm"], abs=1e-2)


def test_collector_actual_outcome_preserves_entry_model_expectation(temp_warehouse, sample_fade_up_signal):
    collector = SnapbackProspectiveCollector(warehouse=temp_warehouse)

    valid_cand = OptionCandidateSnapshot(
        symbol="NIFTY26OCT25000PE",
        expiry="2026-10-29",
        strike=25000.0,
        option_type="PE",
        dte=45,
        is_monthly=True,
        theoretical_delta=-0.70,
        bid=450.0,
        ask=452.0,
        oi=60000,
    )

    open_res = collector.process_signal_and_snapshot(
        signal=sample_fade_up_signal,
        futures_quote=FuturesQuoteSnapshot(futures_symbol="NIFTY-I", bid=24510.0, ask=24512.0),
        option_candidates=[valid_cand],
        causal_beta=1.15,
        option_lot_size=65,
        futures_lot_size=65,
    )

    opp_id = open_res["opportunity_id"]
    entry_model = open_res["modeled_entry_expectation"]

    # Close opportunity and verify outcome uses frozen entry model expectations
    close_res = collector.close_opportunity(
        opportunity_id=opp_id,
        symbol="NIFTY",
        exit_reason="HOLDING_HORIZON",
        entry_ts="2026-09-16T09:30:00Z",
        exit_ts="2026-10-01T15:15:00Z",
        option_entry_price=452.23,
        option_exit_bid=520.0,
        futures_entry_price=24513.26,
        futures_exit_bid=24700.0,
        statutory_costs=150.0,
        option_quantity=65,
        futures_quantity=65,
        modeled_option_pnl=entry_model["modeled_option_pnl"],
        modeled_futures_pnl=entry_model["modeled_futures_pnl"],
        modeled_costs=entry_model["modeled_costs"],
    )

    outcomes = temp_warehouse.get_records_by_table("outcomes", opp_id)
    assert len(outcomes) == 1
    # Verify modeled total pnl equals frozen entry model expectation
    assert outcomes[0]["modeled_total_pnl"] == pytest.approx(entry_model["modeled_total_pnl"], abs=1e-2)

