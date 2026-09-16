"""E09: broker identity is captured on Day-T and never reconstructed later.

Rebuilding `f"NSE:{symbol}"` at T+1 is wrong for anything whose canonical Sterling
name differs from its provider identity — SENSEX trades on BSE with options on BFO,
and a reconstructed NSE/NFO symbol either fails or, worse, resolves to something else.
"""

from __future__ import annotations

import os
import tempfile

import pytest

from app.services.snapback_instrument_identity import (
    IdentityError,
    UnderlyingIdentity,
    identity_from_instrument,
)


NIFTY_ROW = {
    "tradingsymbol": "NIFTY 50", "instrument_token": 256265, "exchange": "NSE",
    "name": "NIFTY", "segment": "INDICES",
}
SENSEX_ROW = {
    "tradingsymbol": "SENSEX", "instrument_token": 265, "exchange": "BSE",
    "name": "SENSEX", "segment": "INDICES",
}
STOCK_ROW = {
    "tradingsymbol": "LAURUSLABS", "instrument_token": 4923905, "exchange": "NSE",
    "name": "LAURUS LABS", "segment": "NSE",
}


def test_sensex_resolves_to_bse_and_bfo():
    identity = identity_from_instrument(SENSEX_ROW, canonical_symbol="SENSEX")

    assert identity.cash_exchange == "BSE"
    assert identity.option_exchange == "BFO"
    assert identity.cash_tradingsymbol == "SENSEX"
    assert identity.cash_instrument_token == 265


def test_nifty_keeps_its_exact_provider_symbol_and_token():
    identity = identity_from_instrument(NIFTY_ROW, canonical_symbol="NIFTY")

    assert identity.canonical_symbol == "NIFTY"
    assert identity.cash_tradingsymbol == "NIFTY 50"
    assert identity.cash_instrument_token == 256265
    assert identity.cash_exchange == "NSE"
    assert identity.option_exchange == "NFO"


def test_stock_keeps_its_actual_nse_identity():
    identity = identity_from_instrument(STOCK_ROW, canonical_symbol="LAURUSLABS")

    assert identity.cash_tradingsymbol == "LAURUSLABS"
    assert identity.cash_instrument_token == 4923905
    assert identity.option_exchange == "NFO"


def test_a_missing_provider_token_is_invalid():
    row = dict(STOCK_ROW, instrument_token=0)

    identity = identity_from_instrument(row, canonical_symbol="LAURUSLABS")

    with pytest.raises(IdentityError):
        identity.validate()


def test_an_unknown_exchange_is_invalid():
    row = dict(STOCK_ROW, exchange="MCX")

    identity = identity_from_instrument(row, canonical_symbol="LAURUSLABS")

    with pytest.raises(IdentityError):
        identity.validate()


def test_cash_quote_key_uses_the_stored_exchange():
    identity = identity_from_instrument(SENSEX_ROW, canonical_symbol="SENSEX")

    assert identity.cash_quote_key() == "BSE:SENSEX"


def test_identity_survives_a_restart(tmp_path):
    from app.services.snapback_observation_warehouse import SnapbackObservationWarehouse

    path = str(tmp_path / "w.db")
    wh = SnapbackObservationWarehouse(db_path=path)
    identity = identity_from_instrument(SENSEX_ROW, canonical_symbol="SENSEX")

    wh.record_opportunity(
        opportunity_id="OPP-1", symbol="SENSEX", signal_type="SNAPBACK_FADE_UP",
        spot_price=82000.0, trend="BEARISH", identity=identity,
    )

    del wh
    reopened = SnapbackObservationWarehouse(db_path=path)
    row = reopened.get_opportunity_by_id("OPP-1")

    assert row["cash_exchange"] == "BSE"
    assert row["option_exchange"] == "BFO"
    assert row["cash_tradingsymbol"] == "SENSEX"
    assert int(row["cash_instrument_token"]) == 265


def test_stored_identity_wins_over_a_later_lookup(tmp_path):
    """A provider dump that changes tomorrow cannot rewrite Day-T identity."""
    from app.services.snapback_instrument_identity import identity_from_opportunity
    from app.services.snapback_observation_warehouse import SnapbackObservationWarehouse

    wh = SnapbackObservationWarehouse(db_path=str(tmp_path / "w.db"))
    wh.record_opportunity(
        opportunity_id="OPP-1", symbol="SENSEX", signal_type="SNAPBACK_FADE_UP",
        spot_price=82000.0, trend="BEARISH",
        identity=identity_from_instrument(SENSEX_ROW, canonical_symbol="SENSEX"),
    )

    identity = identity_from_opportunity(wh.get_opportunity_by_id("OPP-1"))

    assert identity.cash_exchange == "BSE"
    assert identity.option_exchange == "BFO"


def test_an_opportunity_without_identity_cannot_be_resolved():
    from app.services.snapback_instrument_identity import identity_from_opportunity

    with pytest.raises(IdentityError):
        identity_from_opportunity({"opportunity_id": "OPP-1", "symbol": "SENSEX"})


def test_sensex_t1_never_reconstructs_nse_or_nfo(tmp_path):
    """The adversarial case: T+1 must read the stored identity, not the name."""
    from app.services.snapback_instrument_identity import identity_from_opportunity
    from app.services.snapback_observation_warehouse import SnapbackObservationWarehouse

    wh = SnapbackObservationWarehouse(db_path=str(tmp_path / "w.db"))
    wh.record_opportunity(
        opportunity_id="OPP-SENSEX", symbol="SENSEX", signal_type="SNAPBACK_FADE_UP",
        spot_price=82000.0, trend="BEARISH",
        identity=identity_from_instrument(SENSEX_ROW, canonical_symbol="SENSEX"),
    )

    identity = identity_from_opportunity(wh.get_opportunity_by_id("OPP-SENSEX"))

    assert identity.cash_exchange == "BSE"
    assert identity.option_exchange == "BFO"
    assert identity.cash_quote_key() == "BSE:SENSEX"
    assert "NSE:" not in identity.cash_quote_key()
    assert identity.option_exchange != "NFO"


def test_entry_path_reads_stored_identity_rather_than_rebuilding_it():
    import inspect

    from app.services import snapback as sb

    source = inspect.getsource(sb.process_prospective_pending_entries)

    assert "identity_from_opportunity" in source
    assert 'f"NSE:{symbol}"' not in source
