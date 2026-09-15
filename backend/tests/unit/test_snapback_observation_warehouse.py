"""Unit tests for Snapback Prospective Observation Warehouse Service."""
import os
import tempfile
import pytest
from app.services.snapback_observation_warehouse import SnapbackObservationWarehouse


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


def test_record_opportunity_and_outcome(temp_warehouse):
    opp_id = "OPP-20260916-001"
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

    res = temp_warehouse.record_outcome(
        opportunity_id=opp_id,
        symbol="NIFTY",
        exit_reason="PROFIT_TARGET",
        entry_ts="2026-09-15T09:30:00Z",
        exit_ts="2026-09-16T15:15:00Z",
        modeled_option_pnl=5000.0,
        actual_option_pnl=4200.0,
        modeled_futures_pnl=-1500.0,
        actual_futures_pnl=-1800.0,
        modeled_costs=300.0,
        actual_costs=400.0,
    )

    assert res["opportunity_id"] == opp_id
    assert res["modeled_total_pnl"] == 3200.0
    assert res["actual_total_pnl"] == 2000.0
    assert res["observed_vs_model_error"] == -1200.0

    report = temp_warehouse.generate_falsification_report()
    assert report["total_outcomes"] == 1
    assert report["avg_modeled_pnl"] == 3200.0
    assert report["avg_actual_pnl"] == 2000.0
    assert report["avg_error"] == -1200.0
    assert report["model_optimism_bias"] == 1200.0
    assert report["survives_falsification"] is True
