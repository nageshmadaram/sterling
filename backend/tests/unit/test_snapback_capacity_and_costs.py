"""Observed 2026 charges and real capacity.

Costs: one versioned calculator, side- and segment-correct. STT is sell-side only, so
charging it on an option buy overstates entry cost and understates exit cost.

Capacity: an unaffordable lot is NO_CAPACITY, not a paper trade. A book that could not
have been funded is not evidence. SAFE_MODE is part of admission: an operator stop
must block new exposure at the production choke point, not only change a status file.
"""

from __future__ import annotations

import pytest

from app.services.snapback_costs import (
    COST_SCHEDULE_VERSION,
    statutory_charges,
)
from app.services.snapback_capacity import (
    CapacityDecision,
    evaluate_capacity,
)


@pytest.fixture(autouse=True)
def _isolated_safe_mode(monkeypatch, tmp_path):
    """Capacity tests must not depend on an operator's real SAFE_MODE file.

    The file is initialised rather than merely pointed at: an absent safety state
    is itself SAFE_MODE now, so a test that wants to exercise capacity arithmetic
    has to stand up a NORMAL state first, exactly as a real machine does.
    """
    from app.services.safe_mode import SafeModeService

    path = tmp_path / "safe_mode.json"
    monkeypatch.setenv("STERLING_SAFE_MODE_FILE", str(path))
    SafeModeService(path).initialise(operator_ack=True, note="test fixture")


# ----------------------------------------------------------------- costs


def test_schedule_is_versioned():
    assert COST_SCHEDULE_VERSION == "zerodha_fno_costs_2026_04"


def test_option_buy_pays_no_stt():
    charges = statutory_charges(side="BUY", segment="OPTIONS", price=100.0, quantity=850)

    assert charges["stt"] == pytest.approx(0.0)
    assert charges["total"] > 0


def test_option_sell_pays_stt_on_premium():
    turnover = 120.0 * 850
    charges = statutory_charges(side="SELL", segment="OPTIONS", price=120.0, quantity=850)

    # 0.15% of sell-side premium (declared 2026-04 schedule).
    assert charges["stt"] == pytest.approx(turnover * 0.0015, rel=1e-6)


def test_futures_sell_stt_is_lower_than_options():
    fut = statutory_charges(side="SELL", segment="FUTURES", price=24500.0, quantity=75)
    turnover = 24500.0 * 75

    # 0.05% sell-side notional.
    assert fut["stt"] == pytest.approx(turnover * 0.0005, rel=1e-6)


def test_transaction_charges_differ_by_segment():
    opt = statutory_charges(side="BUY", segment="OPTIONS", price=100.0, quantity=850)
    fut = statutory_charges(side="BUY", segment="FUTURES", price=24500.0, quantity=75)

    assert opt["exchange_txn"] == pytest.approx(100.0 * 850 * 0.0003553, rel=1e-6)
    assert fut["exchange_txn"] == pytest.approx(24500.0 * 75 * 0.0000183, rel=1e-6)


def test_gst_is_charged_on_brokerage_sebi_and_transaction_only():
    charges = statutory_charges(side="BUY", segment="OPTIONS", price=100.0, quantity=850)

    taxable = charges["brokerage"] + charges["sebi"] + charges["exchange_txn"]
    assert charges["gst"] == pytest.approx(taxable * 0.18, rel=1e-6)


def test_stamp_duty_is_buy_side_only():
    buy = statutory_charges(side="BUY", segment="OPTIONS", price=100.0, quantity=850)
    sell = statutory_charges(side="SELL", segment="OPTIONS", price=100.0, quantity=850)

    assert buy["stamp_duty"] == pytest.approx(100.0 * 850 * 0.00003, rel=1e-6)  # 0.003% options buy
    assert sell["stamp_duty"] == pytest.approx(0.0)


def test_slippage_is_not_part_of_statutory_charges():
    charges = statutory_charges(side="BUY", segment="OPTIONS", price=100.0, quantity=850)

    assert "slippage" not in charges
    assert charges["total"] == pytest.approx(
        charges["brokerage"] + charges["stt"] + charges["exchange_txn"]
        + charges["gst"] + charges["sebi"] + charges["stamp_duty"]
    )


def test_total_is_the_sum_of_its_parts():
    for side in ("BUY", "SELL"):
        for segment in ("OPTIONS", "FUTURES"):
            c = statutory_charges(side=side, segment=segment, price=250.0, quantity=50)
            assert c["total"] == pytest.approx(
                c["brokerage"] + c["stt"] + c["exchange_txn"] + c["gst"]
                + c["sebi"] + c["stamp_duty"]
            )


# -------------------------------------------------------------- capacity


def test_affordable_trade_is_admitted():
    decision = evaluate_capacity(
        capital=100_000.0,
        reserved_margin=0.0,
        option_premium_cash=20_000.0,
        hedge_margin=30_000.0,
        fee_reserve=1_000.0,
        open_positions=0,
        max_open_positions=3,
        underlying="LAURUSLABS",
        open_underlyings=set(),
    )

    assert isinstance(decision, CapacityDecision)
    assert decision.allowed is True
    assert decision.reasons == []


def test_unaffordable_single_lot_is_no_capacity_not_a_paper_trade():
    decision = evaluate_capacity(
        capital=100_000.0,
        reserved_margin=0.0,
        option_premium_cash=150_000.0,
        hedge_margin=40_000.0,
        fee_reserve=1_000.0,
        open_positions=0,
        max_open_positions=3,
        underlying="LAURUSLABS",
        open_underlyings=set(),
    )

    assert decision.allowed is False
    assert decision.status == "NO_CAPACITY"
    assert "insufficient_capital" in decision.reasons


def test_existing_reserved_margin_reduces_headroom():
    decision = evaluate_capacity(
        capital=100_000.0,
        reserved_margin=80_000.0,
        option_premium_cash=15_000.0,
        hedge_margin=10_000.0,
        fee_reserve=500.0,
        open_positions=1,
        max_open_positions=3,
        underlying="INFY",
        open_underlyings={"TCS"},
    )

    assert decision.allowed is False
    assert "insufficient_capital" in decision.reasons


def test_position_cap_is_enforced():
    decision = evaluate_capacity(
        capital=1_000_000.0,
        reserved_margin=0.0,
        option_premium_cash=10_000.0,
        hedge_margin=10_000.0,
        fee_reserve=500.0,
        open_positions=3,
        max_open_positions=3,
        underlying="INFY",
        open_underlyings={"TCS", "SBIN", "LT"},
    )

    assert decision.allowed is False
    assert "max_open_positions" in decision.reasons


def test_one_position_per_underlying():
    decision = evaluate_capacity(
        capital=1_000_000.0,
        reserved_margin=0.0,
        option_premium_cash=10_000.0,
        hedge_margin=10_000.0,
        fee_reserve=500.0,
        open_positions=1,
        max_open_positions=3,
        underlying="INFY",
        open_underlyings={"INFY"},
    )

    assert decision.allowed is False
    assert "underlying_already_open" in decision.reasons


def test_unknown_hedge_margin_is_inconclusive_capacity_not_a_guess():
    decision = evaluate_capacity(
        capital=100_000.0,
        reserved_margin=0.0,
        option_premium_cash=10_000.0,
        hedge_margin=None,
        fee_reserve=500.0,
        open_positions=0,
        max_open_positions=3,
        underlying="INFY",
        open_underlyings=set(),
    )

    assert decision.allowed is False
    assert decision.status == "INCONCLUSIVE_CAPACITY"
    assert "hedge_margin_unavailable" in decision.reasons


def test_unknown_capital_is_inconclusive():
    decision = evaluate_capacity(
        capital=0.0,
        reserved_margin=0.0,
        option_premium_cash=10_000.0,
        hedge_margin=10_000.0,
        fee_reserve=500.0,
        open_positions=0,
        max_open_positions=3,
        underlying="INFY",
        open_underlyings=set(),
    )

    assert decision.allowed is False
    assert decision.status == "INCONCLUSIVE_CAPACITY"


def test_safe_mode_blocks_an_otherwise_affordable_entry(tmp_path):
    from app.services.safe_mode import SafeModeService, SafeModeTrigger

    service = SafeModeService(tmp_path / "safe_mode.json")
    service.engage(trigger=SafeModeTrigger.OPERATOR, reason="operator stop")

    decision = evaluate_capacity(
        capital=1_000_000.0,
        reserved_margin=0.0,
        option_premium_cash=10_000.0,
        hedge_margin=10_000.0,
        fee_reserve=500.0,
        open_positions=0,
        max_open_positions=3,
        underlying="INFY",
        open_underlyings=set(),
    )

    assert decision.allowed is False
    assert decision.status == "SAFE_MODE"
    assert "safe_mode_active" in decision.reasons


def test_unreadable_safe_mode_file_fails_closed(monkeypatch, tmp_path):
    state_path = tmp_path / "safe_mode.json"
    state_path.write_text("{not-json", encoding="utf-8")
    monkeypatch.setenv("STERLING_SAFE_MODE_FILE", str(state_path))

    decision = evaluate_capacity(
        capital=1_000_000.0,
        reserved_margin=0.0,
        option_premium_cash=10_000.0,
        hedge_margin=10_000.0,
        fee_reserve=500.0,
        open_positions=0,
        max_open_positions=3,
        underlying="INFY",
        open_underlyings=set(),
    )

    assert decision.allowed is False
    assert decision.status == "SAFE_MODE"
