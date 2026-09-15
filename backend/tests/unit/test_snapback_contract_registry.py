"""Unit tests for Snapback Dated Contract Registry."""
import pytest
from study.snapback_contract_registry import SnapbackContractRegistry, build_daily_contract_registry_dataframe


def test_contract_registry_lot_sizes_and_eligibility():
    registry = SnapbackContractRegistry()
    
    # NIFTY historical lot sizes
    assert registry.get_lot_size("NIFTY", "2019-05-15") == 75
    assert registry.get_lot_size("NIFTY", "2022-01-10") == 50
    assert registry.get_lot_size("NIFTY", "2024-05-01") == 25
    assert registry.get_lot_size("NIFTY", "2025-01-15") == 75
    
    # Stock contract spec
    spec_rel = registry.resolve_contract_spec("RELIANCE", "2024-06-01")
    assert spec_rel.symbol == "RELIANCE"
    assert spec_rel.lot_size == 250
    assert spec_rel.strike_step == 20.0
    assert spec_rel.exchange == "NFO"
    assert spec_rel.is_fo_eligible is True


def test_contract_registry_dataframe_builder():
    symbols = ["NIFTY", "RELIANCE"]
    dates = ["2024-06-01", "2024-06-02"]
    records = build_daily_contract_registry_dataframe(symbols, dates)
    
    assert len(records) == 4
    assert records[0]["symbol"] == "NIFTY"
    assert records[0]["date"] == "2024-06-01"
