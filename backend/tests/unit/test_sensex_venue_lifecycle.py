"""SENSEX end to end on BFO, using stored identity at every step.

This is the proof the containment tripwire asked for: entry, MTM, intraday risk
and exit all address a BSE/BFO instrument correctly, because every quote key is
built from the identity observed at signal time rather than rebuilt from the
canonical name.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services.snapback_capacity import _LIFECYCLE_UNSUPPORTED_UNDERLYINGS
from app.services.snapback_instrument_identity import identity_from_instrument
from app.services.snapback_observation_warehouse import (
    SnapbackObservationWarehouse,
    venue_columns,
)
from app.services.snapback_venue import (
    VenueUnknown,
    quote_key,
    resolve_position_venue,
    venue_from_opportunity,
)

SNAPBACK_SRC = Path(__file__).resolve().parents[2] / "app" / "services" / "snapback.py"


def _sensex_identity():
    return identity_from_instrument(
        {"tradingsymbol": "SENSEX", "instrument_token": 265, "exchange": "BSE",
         "name": "SENSEX"},
        canonical_symbol="SENSEX",
    )


def _nifty_identity():
    return identity_from_instrument(
        {"tradingsymbol": "NIFTY 50", "instrument_token": 256265, "exchange": "NSE",
         "name": "NIFTY"},
        canonical_symbol="NIFTY",
    )


@pytest.fixture()
def warehouse(tmp_path) -> SnapbackObservationWarehouse:
    w = SnapbackObservationWarehouse(db_path=str(tmp_path / "obs.db"))
    w.init_db()
    return w


# ── the reconstruction is gone ────────────────────────────────────────────


def test_the_lifecycle_no_longer_rebuilds_any_quote_key():
    source = SNAPBACK_SRC.read_text(encoding="utf-8")
    assert re.findall(r'f"(?:NFO|NSE|BFO|BSE):\{', source) == []


def test_the_containment_is_lifted():
    """Its cause is fixed, and a containment outlived by its cause is a
    silently narrowed universe."""
    assert "SENSEX" not in _LIFECYCLE_UNSUPPORTED_UNDERLYINGS
    assert _LIFECYCLE_UNSUPPORTED_UNDERLYINGS == frozenset()


# ── venue resolution ──────────────────────────────────────────────────────


def test_a_sensex_opportunity_resolves_to_bse_and_bfo(warehouse):
    warehouse.record_opportunity(
        identity=_sensex_identity(), opportunity_id="OPP-SX-1", symbol="SENSEX",
        signal_type="SNAPBACK_FADE_UP", spot_price=80_000.0, status="OPEN_POSITION",
    )
    row = warehouse.get_opportunity_by_id("OPP-SX-1")
    venue = venue_from_opportunity(row)
    assert venue.cash_exchange == "BSE"
    assert venue.derivative_exchange == "BFO"
    assert venue.spot_key() == "BSE:SENSEX"
    assert venue.derivative_key("SENSEX26SEP80000CE") == "BFO:SENSEX26SEP80000CE"


def test_a_nifty_opportunity_still_resolves_to_nse_and_nfo(warehouse):
    warehouse.record_opportunity(
        identity=_nifty_identity(), opportunity_id="OPP-NF-1", symbol="NIFTY",
        signal_type="SNAPBACK_FADE_UP", spot_price=24_000.0, status="OPEN_POSITION",
    )
    venue = venue_from_opportunity(warehouse.get_opportunity_by_id("OPP-NF-1"))
    assert venue.spot_key() == "NSE:NIFTY 50"
    assert venue.derivative_key("NIFTY26SEP24000CE") == "NFO:NIFTY26SEP24000CE"


def test_the_index_spot_key_is_the_provider_symbol_not_the_canonical_name(warehouse):
    """"NIFTY" is not what the provider calls the index; "NIFTY 50" is."""
    warehouse.record_opportunity(
        identity=_nifty_identity(), opportunity_id="OPP-NF-2", symbol="NIFTY",
        signal_type="SNAPBACK_FADE_UP", spot_price=24_000.0,
    )
    venue = venue_from_opportunity(warehouse.get_opportunity_by_id("OPP-NF-2"))
    assert venue.spot_key() != "NSE:NIFTY"


# ── a position carries its own venue, and falls back when it cannot ───────


def _open_position(warehouse, identity, *, opp_id: str, symbol: str,
                   option_symbol: str, futures_symbol: str, with_venue: bool):
    warehouse.record_opportunity(
        identity=identity, opportunity_id=opp_id, symbol=symbol,
        signal_type="SNAPBACK_FADE_UP", spot_price=1.0, status="OPEN_POSITION",
    )
    warehouse.save_paper_position(
        opportunity_id=opp_id, symbol=symbol, option_symbol=option_symbol,
        option_qty=10, option_entry_price=100.0, option_expiry="2026-09-24",
        option_strike=80_000.0, futures_symbol=futures_symbol, futures_lot_size=10,
        current_futures_lots=1, avg_futures_entry_price=80_000.0,
        realized_futures_pnl=0.0, entry_spot=80_000.0,
        entry_timestamp="2026-09-18T11:00:00+05:30", entry_dte=45, entry_iv=14.0,
        causal_beta=1.0, identity=identity if with_venue else None,
    )
    return next(
        p for p in warehouse.get_active_paper_positions()
        if p["opportunity_id"] == opp_id
    )


def test_a_sensex_position_carries_its_venue_on_its_own_row(warehouse):
    pos = _open_position(
        warehouse, _sensex_identity(), opp_id="OPP-SX-2", symbol="SENSEX",
        option_symbol="SENSEX26SEP80000CE", futures_symbol="SENSEX26SEPFUT",
        with_venue=True,
    )
    assert pos["cash_exchange"] == "BSE"
    assert pos["option_exchange"] == "BFO"
    venue = resolve_position_venue(warehouse, pos)
    assert venue.source == "position"
    assert venue.derivative_key("SENSEX26SEPFUT") == "BFO:SENSEX26SEPFUT"


def test_a_position_written_before_the_venue_columns_falls_back(warehouse):
    """Rows opened earlier must still mark correctly."""
    pos = _open_position(
        warehouse, _sensex_identity(), opp_id="OPP-SX-3", symbol="SENSEX",
        option_symbol="SENSEX26SEP80000CE", futures_symbol="SENSEX26SEPFUT",
        with_venue=False,
    )
    assert pos["cash_exchange"] == ""
    venue = resolve_position_venue(warehouse, pos)
    assert venue.source == "opportunity"
    assert venue.spot_key() == "BSE:SENSEX"


def test_an_unresolvable_venue_raises_rather_than_guessing_nfo(warehouse):
    with pytest.raises(VenueUnknown):
        resolve_position_venue(warehouse, {"opportunity_id": "OPP-NONE", "symbol": "X"})


def test_the_cache_does_not_leak_one_position_venue_onto_another(warehouse):
    cache: dict = {}
    sx = _open_position(
        warehouse, _sensex_identity(), opp_id="OPP-SX-4", symbol="SENSEX",
        option_symbol="SENSEX26SEP80000CE", futures_symbol="SENSEX26SEPFUT",
        with_venue=True,
    )
    nf = _open_position(
        warehouse, _nifty_identity(), opp_id="OPP-NF-4", symbol="NIFTY",
        option_symbol="NIFTY26SEP24000CE", futures_symbol="NIFTY26SEPFUT",
        with_venue=True,
    )
    assert resolve_position_venue(warehouse, sx, cache=cache).cash_exchange == "BSE"
    assert resolve_position_venue(warehouse, nf, cache=cache).cash_exchange == "NSE"
    assert resolve_position_venue(warehouse, sx, cache=cache).cash_exchange == "BSE"


# ── key construction ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "exchange,symbol,expected",
    [
        ("BFO", "SENSEX26SEP80000CE", "BFO:SENSEX26SEP80000CE"),
        ("bse", "SENSEX", "BSE:SENSEX"),
        ("NFO", "NIFTY26SEP24000CE", "NFO:NIFTY26SEP24000CE"),
    ],
)
def test_quote_keys_are_built_from_the_given_venue(exchange, symbol, expected):
    assert quote_key(exchange, symbol) == expected


@pytest.mark.parametrize("bad", ["", "MCX", "NSEX", None])
def test_an_unusable_exchange_refuses(bad):
    """":NIFTY" would come back as a missing quote and read as a quiet market."""
    with pytest.raises(VenueUnknown):
        quote_key(bad, "NIFTY")


def test_an_empty_tradingsymbol_refuses():
    with pytest.raises(VenueUnknown):
        quote_key("NFO", "")


def test_venue_columns_without_an_identity_are_empty_not_guessed():
    assert venue_columns(None) == {
        "cash_exchange": "", "cash_tradingsymbol": "", "option_exchange": ""
    }
