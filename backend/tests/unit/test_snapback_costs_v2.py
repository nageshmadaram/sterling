"""1.5 P0-EVIDENCE: costs must match the exchange actually traded, and the
option brokerage is flat.

Two defects, both of which inflate paper P&L:

  - statutory_charges() took no exchange, but the default universe includes
    SENSEX, whose options are resolved on BFO. NSE and BSE charge different
    exchange transaction rates, and BSE futures carry none at all.
  - brokerage was min(Rs 20, 0.03% of turnover) for BOTH segments. Zerodha
    charges options a FLAT Rs 20 per executed order. A one-lot option trade of
    Rs 10,000 turnover was being charged Rs 3 instead of Rs 20.

The old schedule is preserved unchanged. Reproducing an earlier result must stay
possible, so a corrected schedule is a new version, never an edit to the old one.
"""

from __future__ import annotations

import pytest

from app.services.snapback_costs_v2 import (
    COST_SCHEDULE_VERSION_V2,
    EXCHANGE_BFO,
    EXCHANGE_NFO,
    statutory_charges_v2,
)


def _charges(**over):
    base = dict(
        exchange=EXCHANGE_NFO, segment="OPTIONS", side="BUY",
        price=100.0, quantity=75,
    )
    base.update(over)
    return statutory_charges_v2(**base)


# ------------------------------------------------------------- the version


def test_the_new_schedule_has_its_own_version():
    from app.services.snapback_costs import COST_SCHEDULE_VERSION

    assert COST_SCHEDULE_VERSION_V2 != COST_SCHEDULE_VERSION
    assert "exchange_aware" in COST_SCHEDULE_VERSION_V2


def test_the_old_schedule_is_untouched():
    """Reproducing an earlier result must remain possible."""
    from app.services.snapback_costs import COST_SCHEDULE_VERSION, statutory_charges

    assert COST_SCHEDULE_VERSION == "zerodha_fno_costs_2026_04"
    old = statutory_charges(side="BUY", segment="OPTIONS", price=100.0, quantity=75)
    assert old["version"] == "zerodha_fno_costs_2026_04"


def test_the_result_records_which_exchange_priced_it():
    result = _charges(exchange=EXCHANGE_BFO)

    assert result["exchange"] == EXCHANGE_BFO
    assert result["version"] == COST_SCHEDULE_VERSION_V2


# ---------------------------------------------------------- option brokerage


def test_option_brokerage_is_flat_twenty_rupees():
    """The defect: a small option order was charged a percentage."""
    result = _charges(price=10.0, quantity=1000)      # Rs 10,000 turnover

    assert result["brokerage"] == pytest.approx(20.0)


def test_a_tiny_option_order_still_pays_twenty():
    result = _charges(price=1.0, quantity=75)          # Rs 75 turnover

    assert result["brokerage"] == pytest.approx(20.0)


def test_a_large_option_order_still_pays_only_twenty():
    result = _charges(price=5000.0, quantity=750)

    assert result["brokerage"] == pytest.approx(20.0)


def test_futures_brokerage_is_the_lower_of_twenty_or_three_basis_points():
    small = _charges(segment="FUTURES", price=100.0, quantity=10)   # Rs 1,000
    large = _charges(segment="FUTURES", price=25000.0, quantity=75)

    assert small["brokerage"] == pytest.approx(1000.0 * 0.0003)
    assert large["brokerage"] == pytest.approx(20.0)


def test_a_zero_turnover_order_costs_nothing():
    result = _charges(price=0.0, quantity=0)

    assert result["total"] == 0.0
    assert result["brokerage"] == 0.0


# ------------------------------------------------------ exchange-specific rates


def test_nse_and_bse_option_transaction_charges_differ():
    nse = _charges(exchange=EXCHANGE_NFO, price=100.0, quantity=750)
    bse = _charges(exchange=EXCHANGE_BFO, price=100.0, quantity=750)

    assert nse["exchange_txn"] == pytest.approx(75_000 * 0.0003553)
    assert bse["exchange_txn"] == pytest.approx(75_000 * 0.000325)
    assert nse["exchange_txn"] != bse["exchange_txn"]


def test_bse_futures_carry_no_exchange_transaction_charge():
    result = _charges(exchange=EXCHANGE_BFO, segment="FUTURES",
                      price=25000.0, quantity=75)

    assert result["exchange_txn"] == 0.0


def test_nse_futures_do_carry_one():
    result = _charges(exchange=EXCHANGE_NFO, segment="FUTURES",
                      price=25000.0, quantity=75)

    assert result["exchange_txn"] == pytest.approx(25000.0 * 75 * 0.0000183)


def test_an_unknown_exchange_is_refused():
    """Guessing a rate would silently misprice a whole venue."""
    with pytest.raises(ValueError):
        _charges(exchange="MCX")


def test_the_cash_exchange_is_not_accepted_for_a_derivative():
    with pytest.raises(ValueError):
        _charges(exchange="NSE")


# -------------------------------------------------------------- STT unchanged


def test_stt_is_still_sell_side_only():
    buy = _charges(side="BUY")
    sell = _charges(side="SELL")

    assert buy["stt"] == 0.0
    assert sell["stt"] == pytest.approx(100.0 * 75 * 0.0015)


def test_futures_sell_stt_rate():
    sell = _charges(segment="FUTURES", side="SELL", price=25000.0, quantity=75)

    assert sell["stt"] == pytest.approx(25000.0 * 75 * 0.0005)


# ------------------------------------------------------------ the undercharge


def test_the_new_schedule_charges_more_than_the_old_on_a_small_option_order():
    """This difference is the paper P&L that was being invented."""
    from app.services.snapback_costs import statutory_charges

    old = statutory_charges(side="BUY", segment="OPTIONS", price=10.0, quantity=1000)
    new = _charges(price=10.0, quantity=1000)

    assert new["total"] > old["total"]
    assert new["brokerage"] - old["brokerage"] == pytest.approx(17.0)


def test_gst_follows_the_corrected_brokerage():
    result = _charges(price=10.0, quantity=1000)

    expected = (result["brokerage"] + result["sebi"] + result["exchange_txn"]) * 0.18
    assert result["gst"] == pytest.approx(expected)


def test_the_total_is_the_sum_of_its_parts():
    result = _charges(side="SELL", price=120.0, quantity=750)

    parts = sum(result[k] for k in
                ("brokerage", "stt", "exchange_txn", "sebi", "gst", "stamp_duty"))
    assert result["total"] == pytest.approx(parts)


# --------------------------------------------------- exchange comes from the leg


def test_the_execution_event_carries_its_exchange():
    from app.services.snapback_costs_v2 import cost_event_for_execution_v2

    event = cost_event_for_execution_v2(
        execution_event_id="EXEC-1", opportunity_id="OPP-1",
        phase="OPTION_ENTRY", exchange=EXCHANGE_BFO, segment="OPTIONS",
        side="BUY", price=100.0, quantity=75,
    )

    assert event.exchange == EXCHANGE_BFO
    assert event.schedule_version == COST_SCHEDULE_VERSION_V2
    assert event.cost_id == "COST:EXEC-1"
