"""
Sterling v4 — OrderRouter unit tests.

Exercises every dispatch path (paper, shadow, live) and every reject code:
  * unknown_underlying
  * kill_switch
  * daily_loss_halt
  * duplicate_order
  * cooldown_active
  * portfolio_cap_breach
  * microstructure_veto
  * correlation_size_zero
  * exchange_error  (with retry-queue enqueue)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock

import pytest

from app.services import live_safety
from app.services.execution.order_router import (
    OrderRouter, OrderRouterRequest, OrderRouterResponse, RouterDeps, RouterMode,
)


# ─── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _init_test_db(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test_order_router.db")
    monkeypatch.setenv("STERLING_DB_PATH", db_file)
    from app.services import db
    monkeypatch.setattr(db, "_DB_PATH", db_file)
    monkeypatch.setattr(db, "_available", True)
    db.init()
    yield


@dataclass
class _FakeInstrument:
    underlying: str = "NIFTY"


@dataclass
class _FakePosition:
    exit_timestamp_ms: Optional[int] = None
    realized_pnl_usd: Optional[float] = None


def _resolve(_sym: str) -> _FakeInstrument:
    return _FakeInstrument()


def _make_deps(
    paper_create_returns: str = "PP_001",
    cooldown_blocked: bool = False,
    correlation_penalty: float = 1.0,
    portfolio_breach: Optional[str] = None,
    micro_veto: Optional[str] = None,
    open_positions: Optional[List[Any]] = None,
) -> RouterDeps:
    return RouterDeps(
        list_open_positions=lambda: open_positions or [],
        create_paper_position=lambda *_args, **_kwargs: paper_create_returns,
        cooldown_blocked=lambda *_a, **_k: cooldown_blocked,
        correlation_penalty=lambda *_a, **_k: correlation_penalty,
        portfolio_cap_breach=lambda *_a, **_k: portfolio_breach,
        microstructure_veto=lambda *_a, **_k: micro_veto,
    )


def _make_adapter(
    place_order_result: Optional[Dict[str, Any]] = None,
    place_order_raises: Optional[Exception] = None,
    index_price: float = 50_000.0,
) -> AsyncMock:
    mock = AsyncMock()
    mock.get_index_price.return_value = index_price
    mock.get_product_id.return_value = 27
    if place_order_raises:
        mock.place_order.side_effect = place_order_raises
        mock.place_order_option.side_effect = place_order_raises
    else:
        mock.place_order.return_value = place_order_result or {
            "id": "ORD123", "average_fill_price": 50_001.5,
        }
        mock.place_order_option.return_value = place_order_result or {
            "id": "OPT456", "average_fill_price": 0.05,
        }
    return mock


def _basic_req(**overrides: Any) -> OrderRouterRequest:
    base = dict(
        underlying="NIFTY", direction="long",
        instrument_type="futures", size=1,
        order_type="market",
    )
    base.update(overrides)
    return OrderRouterRequest(**base)


@pytest.fixture(autouse=True)
def _reset_safety():
    live_safety.reset_all_for_tests()
    yield
    live_safety.reset_all_for_tests()


# ─── Paper mode ───────────────────────────────────────────────────────────


class TestPaperMode:

    @pytest.mark.asyncio
    async def test_paper_creates_position_no_exchange_call(self) -> None:
        adapter = _make_adapter()
        deps = _make_deps(paper_create_returns="P_42")
        router = OrderRouter(RouterMode.PAPER, adapter, deps, _resolve)

        resp = await router.submit(_basic_req())

        assert resp.accepted is True
        assert resp.mode == "paper"
        assert resp.paper_position_id == "P_42"
        assert resp.entry_price == 50_000.0
        adapter.place_order.assert_not_called()

    @pytest.mark.asyncio
    async def test_paper_uses_zero_when_index_unavailable(self) -> None:
        adapter = _make_adapter()
        adapter.get_index_price.side_effect = RuntimeError("no data")
        deps = _make_deps()
        router = OrderRouter(RouterMode.PAPER, adapter, deps, _resolve)

        resp = await router.submit(_basic_req())

        assert resp.entry_price == 0.0
        assert resp.accepted is True


# ─── Live mode ────────────────────────────────────────────────────────────


class TestLiveMode:

    @pytest.mark.asyncio
    async def test_live_calls_place_order_returns_id(self) -> None:
        adapter = _make_adapter(place_order_result={"id": "ABC", "average_fill_price": 49_999.0})
        deps = _make_deps()
        router = OrderRouter(RouterMode.LIVE, adapter, deps, _resolve)

        resp = await router.submit(_basic_req(client_order_id="ck1"))

        assert resp.accepted is True
        assert resp.order_id == "ABC"
        assert resp.entry_price == 49_999.0
        adapter.place_order.assert_called_once()

    @pytest.mark.asyncio
    async def test_live_options_uses_buy_side(self) -> None:
        adapter = _make_adapter(place_order_result={"id": "OP", "average_fill_price": 0.05})
        deps = _make_deps()
        router = OrderRouter(RouterMode.LIVE, adapter, deps, _resolve)

        resp = await router.submit(_basic_req(
            instrument_type="options", option_symbol="C-NIFTY-50000-051226",
            direction="short",         # short via PE — but order side is still buy
        ))

        assert resp.accepted is True
        kwargs = adapter.place_order_option.call_args.kwargs
        assert kwargs["side"] == "buy"

    @pytest.mark.asyncio
    async def test_live_failure_enqueues_retry(self) -> None:
        adapter = _make_adapter(place_order_raises=RuntimeError("network blip"))
        deps = _make_deps()
        router = OrderRouter(RouterMode.LIVE, adapter, deps, _resolve)

        resp = await router.submit(_basic_req(client_order_id="r1"))

        assert resp.accepted is False
        assert resp.code == "exchange_error"
        assert resp.retry_id is not None
        assert len(live_safety.list_retries()) == 1


# ─── Shadow mode ──────────────────────────────────────────────────────────


class TestShadowMode:

    @pytest.mark.asyncio
    async def test_shadow_records_paper_and_live(self) -> None:
        adapter = _make_adapter(place_order_result={"id": "SHD", "average_fill_price": 50_100.0})
        deps = _make_deps(paper_create_returns="P_SHD")
        router = OrderRouter(RouterMode.SHADOW, adapter, deps, _resolve)

        resp = await router.submit(_basic_req(client_order_id="s1"))

        assert resp.accepted is True
        assert resp.mode == "shadow"
        assert resp.order_id == "SHD"
        assert resp.paper_position_id == "P_SHD"

    @pytest.mark.asyncio
    async def test_shadow_paper_failure_does_not_break_live(self) -> None:
        adapter = _make_adapter(place_order_result={"id": "SHD", "average_fill_price": 50_100.0})

        def _bad_paper(*_a, **_k):
            raise RuntimeError("disk full")

        deps = RouterDeps(
            list_open_positions=lambda: [],
            create_paper_position=_bad_paper,
        )
        router = OrderRouter(RouterMode.SHADOW, adapter, deps, _resolve)

        resp = await router.submit(_basic_req(client_order_id="s2"))

        assert resp.accepted is True
        assert resp.order_id == "SHD"
        assert "shadow paper-record failed" in resp.reason


# ─── Safety reject paths ─────────────────────────────────────────────────


class TestSafetyRejects:

    @pytest.mark.asyncio
    async def test_unknown_underlying(self) -> None:
        deps = _make_deps()
        router = OrderRouter(RouterMode.PAPER, _make_adapter(), deps, lambda _s: None)

        resp = await router.submit(_basic_req())

        assert resp.accepted is False
        assert resp.code == "unknown_underlying"

    @pytest.mark.asyncio
    async def test_kill_switch_rejects(self) -> None:
        live_safety.set_kill_switch(True, reason="drill")
        deps = _make_deps()
        router = OrderRouter(RouterMode.LIVE, _make_adapter(), deps, _resolve)

        resp = await router.submit(_basic_req())

        assert resp.accepted is False
        assert resp.code == "kill_switch"

    @pytest.mark.asyncio
    async def test_daily_loss_halt_rejects(self) -> None:
        import time
        positions = [_FakePosition(exit_timestamp_ms=int(time.time() * 1000),
                                    realized_pnl_usd=-2_000.0)]
        deps = _make_deps(open_positions=positions)
        router = OrderRouter(RouterMode.LIVE, _make_adapter(), deps, _resolve)

        resp = await router.submit(_basic_req())

        assert resp.accepted is False
        assert resp.code == "daily_loss_halt"

    @pytest.mark.asyncio
    async def test_idempotency_returns_prior_id(self) -> None:
        adapter = _make_adapter(place_order_result={"id": "I1", "average_fill_price": 50_000.0})
        deps = _make_deps()
        router = OrderRouter(RouterMode.LIVE, adapter, deps, _resolve)

        first = await router.submit(_basic_req(client_order_id="dup"))
        second = await router.submit(_basic_req(client_order_id="dup"))

        assert first.accepted is True
        assert second.accepted is True
        assert second.status == "duplicate"
        assert second.order_id == first.order_id

    @pytest.mark.asyncio
    async def test_cooldown_active_rejects(self) -> None:
        deps = _make_deps(cooldown_blocked=True)
        router = OrderRouter(RouterMode.PAPER, _make_adapter(), deps, _resolve)

        resp = await router.submit(_basic_req())

        assert resp.accepted is False
        assert resp.code == "cooldown_active"

    @pytest.mark.asyncio
    async def test_portfolio_cap_breach_rejects(self) -> None:
        deps = _make_deps(portfolio_breach="bucket overflow 9.1% > 8.0%")
        router = OrderRouter(RouterMode.PAPER, _make_adapter(), deps, _resolve)

        resp = await router.submit(_basic_req())

        assert resp.accepted is False
        assert resp.code == "portfolio_cap_breach"

    @pytest.mark.asyncio
    async def test_microstructure_veto_rejects(self) -> None:
        deps = _make_deps(micro_veto="book imbalance hostile")
        router = OrderRouter(RouterMode.PAPER, _make_adapter(), deps, _resolve)

        resp = await router.submit(_basic_req())

        assert resp.accepted is False
        assert resp.code == "microstructure_veto"

    @pytest.mark.asyncio
    async def test_correlation_zero_size_rejects(self) -> None:
        # Phase-0 fix: the correlation_penalty path now preserves fractional
        # contracts (was silently rounding 0.4 → 1, a 60% size error on
        # high-notional options). The reject path is now gated at 0.01
        # contracts — small enough to be meaningless, large enough that no
        # legitimate request ever lands below it.
        deps = _make_deps(correlation_penalty=0.005)
        router = OrderRouter(RouterMode.PAPER, _make_adapter(), deps, _resolve)

        # size=1 * 0.005 = 0.005 → below the 0.01 floor → reject.
        resp = await router.submit(_basic_req(size=1))

        assert resp.accepted is False
        assert resp.code == "correlation_size_zero"


# ─── Mode hot-swap ────────────────────────────────────────────────────────


class TestModeHotSwap:

    @pytest.mark.asyncio
    async def test_can_change_mode_between_calls(self) -> None:
        adapter = _make_adapter()
        deps = _make_deps()
        router = OrderRouter(RouterMode.PAPER, adapter, deps, _resolve)

        # Submit in paper
        r1 = await router.submit(_basic_req(client_order_id="m1"))
        assert r1.mode == "paper"

        # Hot-swap to live and submit fresh idempotency key
        router.mode = RouterMode.LIVE
        r2 = await router.submit(_basic_req(client_order_id="m2"))
        assert r2.mode == "live"
        adapter.place_order.assert_called_once()
