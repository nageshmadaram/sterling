"""H1: the broker's book and Sterling's must agree before exposure increases.

Sterling does not own the account exclusively — somebody can trade it by hand, an
order can fill while the process was down, a hedge can be half established. Any of
those makes "flat" or "open" a guess, and a guess must block new exposure.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.snapback_reconciliation import (
    ReconciliationMismatch,
    ReconciliationSnapshot,
    reconcile_account,
)


def _broker(positions=None, orders=None, trades=None):
    client = AsyncMock()
    client.get_positions_raw = AsyncMock(return_value={"net": list(positions or [])})
    client.get_orders = AsyncMock(return_value=list(orders or []))
    client.get_trades = AsyncMock(return_value=list(trades or []))
    return client


def _position(symbol="NIFTY26OCT25000PE", quantity=850):
    return {"tradingsymbol": symbol, "quantity": quantity, "exchange": "NFO"}


def _sterling(symbol="NIFTY26OCT25000PE", quantity=850, futures=75):
    return [{
        "opportunity_id": "OPP-1", "option_symbol": symbol, "option_qty": quantity,
        "futures_symbol": "NIFTY26OCTFUT", "current_futures_lots": 1,
        "futures_lot_size": futures, "status": "OPEN",
    }]


@pytest.mark.asyncio
async def test_a_matching_book_is_clean():
    snapshot = await reconcile_account(
        client=_broker(positions=[_position(), _position("NIFTY26OCTFUT", 75)]),
        account_id="KITE-1",
        sterling_positions=_sterling(),
        sterling_intents=[],
    )

    assert isinstance(snapshot, ReconciliationSnapshot)
    assert snapshot.clean is True
    assert snapshot.mismatches == ()


@pytest.mark.asyncio
async def test_a_broker_position_sterling_does_not_know_about():
    snapshot = await reconcile_account(
        client=_broker(positions=[_position("BANKNIFTY26OCT50000CE", 15)]),
        account_id="KITE-1", sterling_positions=[], sterling_intents=[],
    )

    assert snapshot.clean is False
    assert any(m.code == "EXTERNAL_OR_UNKNOWN_POSITION" for m in snapshot.mismatches)


@pytest.mark.asyncio
async def test_a_sterling_position_missing_at_the_broker():
    snapshot = await reconcile_account(
        client=_broker(positions=[]), account_id="KITE-1",
        sterling_positions=_sterling(), sterling_intents=[],
    )

    assert snapshot.clean is False
    assert any(m.code == "POSITION_MISSING_AT_BROKER" for m in snapshot.mismatches)


@pytest.mark.asyncio
async def test_a_quantity_mismatch_is_reported_with_both_numbers():
    snapshot = await reconcile_account(
        client=_broker(positions=[_position(quantity=425),
                                  _position("NIFTY26OCTFUT", 75)]),
        account_id="KITE-1", sterling_positions=_sterling(), sterling_intents=[],
    )

    mismatch = next(m for m in snapshot.mismatches if m.code == "QUANTITY_MISMATCH")
    assert mismatch.broker_quantity == 425
    assert mismatch.sterling_quantity == 850


@pytest.mark.asyncio
async def test_a_hedge_mismatch_is_its_own_code():
    snapshot = await reconcile_account(
        client=_broker(positions=[_position(), _position("NIFTY26OCTFUT", 150)]),
        account_id="KITE-1", sterling_positions=_sterling(), sterling_intents=[],
    )

    assert any(m.code == "HEDGE_MISMATCH" for m in snapshot.mismatches)


@pytest.mark.asyncio
async def test_an_intent_the_broker_never_heard_of_is_unknown():
    snapshot = await reconcile_account(
        client=_broker(positions=[_position(), _position("NIFTY26OCTFUT", 75)]),
        account_id="KITE-1", sterling_positions=_sterling(),
        sterling_intents=[{"intent_id": "INT-1", "order_id": "", "status": "SUBMITTED"}],
    )

    assert snapshot.clean is False
    assert any(m.code == "UNKNOWN_ORDER" for m in snapshot.mismatches)


@pytest.mark.asyncio
async def test_two_open_entry_orders_for_one_opportunity():
    orders = [
        {"order_id": "A", "tradingsymbol": "NIFTY26OCT25000PE", "status": "OPEN",
         "transaction_type": "BUY", "tag": "OPP-1"},
        {"order_id": "B", "tradingsymbol": "NIFTY26OCT25000PE", "status": "OPEN",
         "transaction_type": "BUY", "tag": "OPP-1"},
    ]

    snapshot = await reconcile_account(
        client=_broker(positions=[_position(), _position("NIFTY26OCTFUT", 75)],
                       orders=orders),
        account_id="KITE-1", sterling_positions=_sterling(), sterling_intents=[],
    )

    assert any(m.code == "DUPLICATE_ENTRY_ORDER" for m in snapshot.mismatches)


@pytest.mark.asyncio
async def test_a_broker_that_cannot_answer_is_not_reconciled():
    client = AsyncMock()
    client.get_positions_raw = AsyncMock(side_effect=RuntimeError("broker down"))
    client.get_orders = AsyncMock(return_value=[])
    client.get_trades = AsyncMock(return_value=[])

    snapshot = await reconcile_account(
        client=client, account_id="KITE-1", sterling_positions=[], sterling_intents=[],
    )

    # Unknown is never "clean".
    assert snapshot.clean is False
    assert any(m.code == "BROKER_STATE_UNAVAILABLE" for m in snapshot.mismatches)


@pytest.mark.asyncio
async def test_no_client_is_not_reconciled():
    snapshot = await reconcile_account(
        client=None, account_id="KITE-1", sterling_positions=[], sterling_intents=[],
    )

    assert snapshot.clean is False


@pytest.mark.asyncio
async def test_the_snapshot_records_what_it_compared():
    snapshot = await reconcile_account(
        client=_broker(positions=[_position(), _position("NIFTY26OCTFUT", 75)]),
        account_id="KITE-1", sterling_positions=_sterling(), sterling_intents=[],
    )

    assert snapshot.account_id == "KITE-1"
    assert snapshot.observed_at
    assert len(snapshot.broker_positions) == 2
    assert len(snapshot.sterling_positions) == 1


def test_readiness_blocks_exposure_while_reconciling():
    from app.services.snapback_family_gate import LiveIntent, evaluate_live_gate

    decision = evaluate_live_gate(
        intent=LiveIntent.ENTER, evidence_verdict="PASSED", live_eligible=True,
        risk_approved=True, system_status="HEALTHY", broker_reconciled=False,
    )

    assert decision.allowed is False
    assert "broker_not_reconciled" in decision.reasons


def test_protective_exit_is_allowed_while_reconciling():
    from app.services.snapback_family_gate import LiveIntent, evaluate_live_gate

    decision = evaluate_live_gate(
        intent=LiveIntent.EXIT, evidence_verdict="INCONCLUSIVE", live_eligible=False,
        risk_approved=False, system_status="RECONCILING", broker_reconciled=False,
    )

    assert decision.allowed is True
    assert decision.protective is True
