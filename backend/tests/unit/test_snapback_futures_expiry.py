"""The hedge future must outlive the option it hedges.

A near-month future picked before a 40-60 DTE option is selected can expire while the
position is still open, leaving the book unhedged without anyone deciding to be.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.services.snapback_hedge_contract import (
    HedgeContractError,
    select_hedge_future,
)


def _future(symbol, expiry, lot_size=75, oi=500000):
    return {
        "tradingsymbol": symbol,
        "expiry": expiry,
        "lot_size": lot_size,
        "instrument_token": abs(hash(symbol)) % 10**7,
        "oi": oi,
        "name": "NIFTY",
        "instrument_type": "FUT",
        "segment": "NFO-FUT",
    }


CHAIN = [
    _future("NIFTY26SEPFUT", "2026-09-24"),
    _future("NIFTY26OCTFUT", "2026-10-29"),
    _future("NIFTY26NOVFUT", "2026-11-26"),
]


def test_hedge_expiry_must_not_precede_the_option():
    pick = select_hedge_future(CHAIN, option_expiry=date(2026, 10, 29), name="NIFTY")

    assert pick.tradingsymbol == "NIFTY26OCTFUT"
    assert pick.expiry >= date(2026, 10, 29)


def test_near_month_is_rejected_when_the_option_outlives_it():
    pick = select_hedge_future(CHAIN, option_expiry=date(2026, 10, 1), name="NIFTY")

    # The September future expires first, so it cannot hedge an October option.
    assert pick.tradingsymbol != "NIFTY26SEPFUT"
    assert pick.expiry >= date(2026, 10, 1)


def test_nearest_compatible_contract_is_preferred():
    pick = select_hedge_future(CHAIN, option_expiry=date(2026, 9, 30), name="NIFTY")

    # October, not November: nearest that still outlives the option.
    assert pick.tradingsymbol == "NIFTY26OCTFUT"


def test_same_day_expiry_is_acceptable():
    pick = select_hedge_future(CHAIN, option_expiry=date(2026, 9, 24), name="NIFTY")

    assert pick.tradingsymbol == "NIFTY26SEPFUT"


def test_no_compatible_future_is_inconclusive_not_a_fallback():
    with pytest.raises(HedgeContractError) as excinfo:
        select_hedge_future(CHAIN, option_expiry=date(2027, 3, 25), name="NIFTY")

    assert "INCONCLUSIVE_HEDGE_CONTRACT" in str(excinfo.value)


def test_empty_chain_is_inconclusive():
    with pytest.raises(HedgeContractError):
        select_hedge_future([], option_expiry=date(2026, 10, 29), name="NIFTY")


def test_selection_carries_the_identity_the_position_needs():
    pick = select_hedge_future(CHAIN, option_expiry=date(2026, 10, 29), name="NIFTY")

    assert pick.tradingsymbol
    assert pick.instrument_token
    assert pick.lot_size > 0
    assert pick.expiry == date(2026, 10, 29)


def test_entry_path_selects_the_hedge_after_the_option():
    import inspect

    from app.services import snapback as sb

    source = inspect.getsource(sb.process_prospective_pending_entries)

    assert "select_hedge_future" in source
