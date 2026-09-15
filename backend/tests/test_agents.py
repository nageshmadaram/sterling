"""
AGENTS (Phase 3c) — thin facades + the Fill→PNL reference flow.

Each agent delegates to an injected dependency (no business logic moved) and,
where relevant, integrates with the EventBus. The PNL flow is the spec's
reference wiring proving the bus is exercised, not dead code.
"""
from unittest.mock import AsyncMock

import pytest

from app.bus.event_bus import EventBus
from app.domain.events import FillReceived, PositionClosed, RiskBreach, OrderSubmitted, OrderAccepted
from app.domain.models import Signal
from app.agents.broker_agent import BrokerAgent
from app.agents.market_agent import MarketAgent
from app.agents.strategy_agent import StrategyAgent
from app.agents.execution_agent import ExecutionAgent
from app.agents.risk_agent import RiskAgent
from app.agents.pnl_agent import PNLAgent
from app.agents.reconciliation_agent import ReconciliationAgent
from app.services.execution.order_router import (
    OrderRouter, OrderRouterRequest, RouterDeps, RouterMode,
)
from app.services import live_safety


@pytest.fixture(autouse=True)
def _init_test_db(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test_agents.db")
    monkeypatch.setenv("STERLING_DB_PATH", db_file)
    from app.services import db
    monkeypatch.setattr(db, "_DB_PATH", db_file)
    monkeypatch.setattr(db, "_available", True)
    db.init()
    yield


@pytest.fixture(autouse=True)
def _reset_safety():
    live_safety.reset_all_for_tests()
    yield
    live_safety.reset_all_for_tests()


# ── reference flow: Fill/PositionClosed → PNLAgent ────────────────────────
@pytest.mark.asyncio
async def test_pnl_agent_reference_flow():
    bus = EventBus()
    pnl = PNLAgent(bus=bus)
    await bus.publish(FillReceived(symbol="NIFTYUSD", side="buy", size=1.0, price=50000.0))
    await bus.publish(FillReceived(symbol="NIFTYUSD", side="sell", size=1.0, price=51000.0))
    await bus.publish(PositionClosed(symbol="NIFTYUSD", realized_pnl_usd=1000.0))
    snap = pnl.snapshot()
    assert snap["fills"] == 2
    assert snap["realized_pnl_usd"] == 1000.0


# ── ExecutionAgent wraps the real OrderRouter (paper) + emits events ──────
class _Inst:
    underlying = "NIFTY"


@pytest.mark.asyncio
async def test_execution_agent_routes_and_emits_events():
    bus = EventBus()
    events = []
    bus.subscribe(OrderSubmitted, lambda e: events.append(e.event_type))
    bus.subscribe(OrderAccepted, lambda e: events.append(e.event_type))
    router = OrderRouter(
        mode=RouterMode.PAPER, adapter=None,
        deps=RouterDeps(list_open_positions=lambda: [],
                        create_paper_position=lambda *_a, **_k: "PP"),
        instrument_resolver=lambda _s: _Inst(),
    )
    agent = ExecutionAgent(router=router, bus=bus)
    resp = await agent.execute(OrderRouterRequest(
        underlying="NIFTY", direction="long", instrument_type="futures", size=1,
    ))
    assert resp.accepted is True
    assert "OrderSubmitted" in events and "OrderAccepted" in events


# ── RiskAgent returns first breach + publishes RiskBreach ─────────────────
@pytest.mark.asyncio
async def test_risk_agent_first_breach_wins_and_publishes():
    bus = EventBus()
    breaches = []
    bus.subscribe(RiskBreach, lambda e: breaches.append(e.payload.get("code")))
    agent = RiskAgent(rules=[lambda ctx: None, lambda ctx: "max_dd_breach", lambda ctx: "never"], bus=bus)
    result = await agent.check(context={})
    assert result == "max_dd_breach"
    assert breaches == ["max_dd_breach"]


# ── ReconciliationAgent finds size discrepancies ──────────────────────────
def test_reconciliation_agent_detects_drift():
    agent = ReconciliationAgent()
    diff = agent.reconcile(internal={"NIFTYUSD": 1.0, "BANKNIFTYUSD": 2.0},
                           broker={"NIFTYUSD": 1.0, "BANKNIFTYUSD": 3.0})
    assert "BANKNIFTYUSD" in diff
    assert "NIFTYUSD" not in diff
    assert diff["BANKNIFTYUSD"] == {"internal": 2.0, "broker": 3.0}


# ── BrokerAgent passes through to the adapter ─────────────────────────────
@pytest.mark.asyncio
async def test_broker_agent_passes_through():
    adapter = AsyncMock()
    adapter.place_order.return_value = {"id": "ORD1"}
    agent = BrokerAgent(adapter=adapter)
    out = await agent.place_order(symbol="NIFTYUSD", side="buy", size=1)
    assert out == {"id": "ORD1"}
    adapter.place_order.assert_awaited_once()


# ── MarketAgent normalizes price access ───────────────────────────────────
@pytest.mark.asyncio
async def test_market_agent_price():
    adapter = AsyncMock()
    adapter.get_index_price.return_value = 50000.0
    agent = MarketAgent(adapter=adapter)
    assert await agent.price(_Inst()) == 50000.0


# ── StrategyAgent emits SignalRaised per signal ───────────────────────────
@pytest.mark.asyncio
async def test_strategy_agent_emits_signals():
    bus = EventBus()
    raised = []
    from app.domain.events import SignalRaised
    bus.subscribe(SignalRaised, lambda e: raised.append(e.payload.get("underlying")))
    sig = Signal(underlying="NIFTY", direction="long")
    agent = StrategyAgent(generator=lambda: [sig], bus=bus)
    out = await agent.run()
    assert out == [sig]
    assert raised == ["NIFTY"]
