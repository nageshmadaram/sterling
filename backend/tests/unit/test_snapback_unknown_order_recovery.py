"""H5: an unknown submission is reconciled, never retried blindly.

A timeout after the broker received the order is the one case where retrying doubles
real exposure. The only safe next step is to ask the broker what happened.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.snapback_lifecycle import (
    UnknownSubmission,
    resolve_unknown_submission,
)


def _client(orders=None, trades=None):
    client = AsyncMock()
    client.get_orders = AsyncMock(return_value=list(orders or []))
    client.get_trades = AsyncMock(return_value=list(trades or []))
    return client


@pytest.mark.asyncio
async def test_an_order_found_by_tag_is_adopted_not_resubmitted():
    client = _client(orders=[{
        "order_id": "OID-1", "tag": "INT-1", "status": "COMPLETE",
        "filled_quantity": 850, "tradingsymbol": "NIFTY26OCT25000PE",
    }])

    result = await resolve_unknown_submission(
        client=client, intent_id="INT-1", tradingsymbol="NIFTY26OCT25000PE",
    )

    assert result.resolution == "FOUND"
    assert result.order_id == "OID-1"
    assert result.filled_quantity == 850


@pytest.mark.asyncio
async def test_a_partial_fill_is_reported_as_such():
    client = _client(orders=[{
        "order_id": "OID-1", "tag": "INT-1", "status": "OPEN",
        "filled_quantity": 425, "tradingsymbol": "NIFTY26OCT25000PE",
    }])

    result = await resolve_unknown_submission(
        client=client, intent_id="INT-1", tradingsymbol="NIFTY26OCT25000PE",
    )

    assert result.resolution == "FOUND"
    assert result.filled_quantity == 425


@pytest.mark.asyncio
async def test_an_order_the_broker_never_saw_is_absent():
    result = await resolve_unknown_submission(
        client=_client(orders=[]), intent_id="INT-1",
        tradingsymbol="NIFTY26OCT25000PE",
    )

    assert result.resolution == "ABSENT"
    assert result.safe_to_resubmit is True


@pytest.mark.asyncio
async def test_a_broker_that_cannot_answer_is_never_safe_to_resubmit():
    client = AsyncMock()
    client.get_orders = AsyncMock(side_effect=RuntimeError("broker down"))
    client.get_trades = AsyncMock(return_value=[])

    result = await resolve_unknown_submission(
        client=client, intent_id="INT-1", tradingsymbol="NIFTY26OCT25000PE",
    )

    assert result.resolution == "UNRESOLVED"
    assert result.safe_to_resubmit is False


@pytest.mark.asyncio
async def test_a_found_order_is_never_safe_to_resubmit():
    client = _client(orders=[{
        "order_id": "OID-1", "tag": "INT-1", "status": "COMPLETE",
        "filled_quantity": 850, "tradingsymbol": "NIFTY26OCT25000PE",
    }])

    result = await resolve_unknown_submission(
        client=client, intent_id="INT-1", tradingsymbol="NIFTY26OCT25000PE",
    )

    assert result.safe_to_resubmit is False


@pytest.mark.asyncio
async def test_a_trade_without_a_matching_order_still_counts_as_found():
    client = _client(orders=[], trades=[{
        "order_id": "OID-9", "tag": "INT-1", "quantity": 850,
        "tradingsymbol": "NIFTY26OCT25000PE",
    }])

    result = await resolve_unknown_submission(
        client=client, intent_id="INT-1", tradingsymbol="NIFTY26OCT25000PE",
    )

    assert result.resolution == "FOUND"
    assert result.safe_to_resubmit is False


def test_the_unknown_state_is_explicit():
    unknown = UnknownSubmission(
        intent_id="INT-1", resolution="UNRESOLVED", safe_to_resubmit=False,
    )

    assert unknown.resolution == "UNRESOLVED"
    assert unknown.safe_to_resubmit is False
