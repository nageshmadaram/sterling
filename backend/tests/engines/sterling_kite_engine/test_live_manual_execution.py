from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest

from app.engines.sterling_kite_engine.schemas import EngineConfigModel
from app.services.exchanges.kite import accounts
from app.services.kite_engine import service, state, positions, protection, execution_evidence
from app.services.kite_engine import order_journal


@pytest.fixture
def live_manual(monkeypatch):
    # A live manual entry is an exposure increase, so it goes through
    # CanonicalExecutionService — which sends with the generic `place_order`,
    # not the option wrapper the old bypass called.
    client = SimpleNamespace(_is_paper=False, _account_id="account",
                             place_order=AsyncMock(return_value={"order_id": "manual-1"}))
    plan = SimpleNamespace(live=True, protectable=True, exchange="NFO", lot_size=50,
        stop_premium=100, direction="long", signal_direction="long", underlying="NIFTY",
        token=0, entry_spot=25000, entry_delta=0.5, strike=25000, expiry="2026-09-10", target_premium=0)
    row = SimpleNamespace(source="derivatives", timestamp_ms=123, direction="long",
                          legs=[SimpleNamespace(option_symbol="OPT")])
    state.set_config("u", EngineConfigModel(risk_pct=2))
    monkeypatch.setattr(accounts, "client_is_current", lambda *a, **kw: True)
    monkeypatch.setattr(accounts, "get_active", lambda uid: object())
    monkeypatch.setattr(accounts, "acquire_client", AsyncMock(return_value=client))
    monkeypatch.setattr(protection, "plan_for_symbol", lambda *a: plan)
    monkeypatch.setattr(protection, "_rows_for", lambda uid: [row])
    monkeypatch.setattr(service, "entry_data_block_reason", lambda *a, **kw: "")
    monkeypatch.setattr(service, "available_fo_capital", AsyncMock(return_value=100000))
    monkeypatch.setattr(execution_evidence, "entry_evidence", AsyncMock(return_value=
        execution_evidence.EntryEvidence(120, 120.35, 119.65, 50, 0.05)))
    return client, plan


@pytest.mark.asyncio
async def test_manual_entry_has_journal_and_zero_confirmed_qty(live_manual):
    client, _ = live_manual
    result = await service.place_manual_order("u", "OPT", "BUY", 50)
    assert result["status"] == "ok" and result["protected"] is False
    assert positions.get("u", "OPT").qty == 0
    intent = order_journal.find(uid="u", account_id="account", order_id="manual-1")
    assert intent.capital_required == 6017.5
    assert client.place_order.await_args.kwargs["tag"] == intent.tag
    assert client.place_order.await_args.kwargs["limit_price"] == 120.35


@pytest.mark.asyncio
async def test_manual_timeout_blocks_repeat_submission(live_manual):
    client, _ = live_manual
    client.place_order.side_effect = TimeoutError("response lost")
    assert (await service.place_manual_order("u", "OPT", "BUY", 50))["status"] == "error"
    assert (await service.place_manual_order("u", "OPT", "BUY", 50))["status"] == "blocked"
    assert client.place_order.await_count == 1


@pytest.mark.asyncio
async def test_untracked_manual_sell_cannot_open_short(live_manual):
    client, _ = live_manual
    result = await service.place_manual_order("u", "OPT", "SELL", 50)
    assert result["status"] == "blocked"
    client.place_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_manual_plan_must_be_active_before_send(live_manual):
    client, plan = live_manual
    plan.live = False
    assert (await service.place_manual_order("u", "OPT", "BUY", 50))["status"] == "blocked"
    client.place_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_manual_limit_requires_exchange_tick(live_manual):
    client, _ = live_manual
    result = await service.place_manual_order("u", "OPT", "BUY", 50,
                                              order_type="LIMIT", limit_price=120.33)
    assert result["status"] == "blocked" and result["reason"] == "limit_price_not_on_exchange_tick"
    client.place_order.assert_not_awaited()
