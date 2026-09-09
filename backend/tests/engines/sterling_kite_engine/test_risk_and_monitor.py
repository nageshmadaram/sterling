"""Tests for workstreams F (sizing), E (fill tracking), C/D (tick-driven exit)."""
import asyncio

import pytest

from app.services.kite_engine import sizing
from app.services.kite_engine import positions as pos
from app.services.kite_engine import monitor
from app.services.kite_engine import state
import app.services.exchanges.kite.ticker_manager as _ticker_manager


# ── F: risk sizing ───────────────────────────────────────────────────────────
class TestSizing:
    def test_sizes_to_risk_budget(self):
        # entry 100, stop 80 → ₹20/share × 50 lot = ₹1000 risk/lot.
        # 1% of ₹500,000 = ₹5000 budget → 5 lots.
        r = sizing.size_position(entry_premium=100, stop_premium=80, lot_size=50,
                                 available_capital=500_000, risk_pct=1.0, max_lots=10)
        assert r.lots == 5
        assert r.qty == 250
        assert r.risk_per_lot == pytest.approx(1000.0)
        assert r.est_risk == pytest.approx(5000.0)

    def test_blocks_when_one_lot_would_exceed_the_risk_budget(self):
        """risk/lot ₹1000 > budget (1% of ₹50,000 = ₹500).

        This used to floor to 1 lot and trade anyway, which made ``risk_pct``
        advisory: ₹1000 of risk against a ₹500 budget is 2% on a 1% setting, and on a
        real index option the gap is far wider than that. A control that says it caps
        risk has to cap it.
        """
        r = sizing.size_position(entry_premium=100, stop_premium=80, lot_size=50,
                                 available_capital=50_000, risk_pct=1.0, max_lots=10)
        assert r.blocked is True
        assert r.lots == 0 and r.qty == 0
        assert "₹1000" in r.reason and "₹500" in r.reason

    def test_floors_at_one_lot_when_the_override_is_set(self):
        r = sizing.size_position(entry_premium=100, stop_premium=80, lot_size=50,
                                 available_capital=50_000, risk_pct=1.0, max_lots=10,
                                 allow_min_lot_over_risk=True)
        assert r.blocked is False
        assert r.lots == 1
        assert "floored to 1 lot" in r.reason

    def test_futures_sizing_blocks_on_the_same_rule(self):
        r = sizing.size_future_position(entry_price=24_000, stop_price=23_900, lot_size=50,
                                        available_capital=50_000, risk_pct=1.0, max_lots=10)
        assert r.blocked is True and r.qty == 0
        r_allowed = sizing.size_future_position(
            entry_price=24_000, stop_price=23_900, lot_size=50,
            available_capital=50_000, risk_pct=1.0, max_lots=10,
            allow_min_lot_over_risk=True)
        assert r_allowed.blocked is True and r_allowed.lots == 0  # unaffordable margin

    def test_unreadable_capital_blocks_without_a_budget(self):
        """available_fo_capital returns 0.0 when the margins call fails.

        Reading that as "over budget" would make a transient broker outage halt every
        automatic entry — indistinguishable, from the outside, from the engine being
        down. Only a capital figure we actually have can be exceeded.
        """
        r = sizing.size_position(entry_premium=100, stop_premium=80, lot_size=50,
                                 available_capital=0.0, risk_pct=1.0, max_lots=10)
        assert r.blocked is True and r.lots == 0
        rf = sizing.size_future_position(entry_price=24_000, stop_price=23_900, lot_size=50,
                                         available_capital=0.0, risk_pct=1.0, max_lots=10)
        assert rf.blocked is True and rf.lots == 0

    def test_a_sizeable_trade_is_never_blocked(self):
        r = sizing.size_position(entry_premium=100, stop_premium=80, lot_size=50,
                                 available_capital=500_000, risk_pct=1.0, max_lots=10)
        assert r.blocked is False and r.lots == 5

    def test_capped_by_max_lots(self):
        r = sizing.size_position(entry_premium=10, stop_premium=9, lot_size=50,
                                 available_capital=10_000_000, risk_pct=5.0, max_lots=10)
        assert r.lots == 10
        assert "max_lots" in r.reason

    def test_capped_by_margin_affordability(self):
        # risk allows many lots, but only ~3 lots affordable by outlay.
        # entry 100 × 50 = ₹5000/lot; capital ₹16,000 → 3 lots affordable.
        r = sizing.size_position(entry_premium=100, stop_premium=99, lot_size=50,
                                 available_capital=16_000, risk_pct=50.0, max_lots=100)
        assert r.lots == 3
        assert "margin affords" in r.reason

    def test_stop_above_entry_blocks(self):
        r = sizing.size_position(entry_premium=100, stop_premium=120, lot_size=50,
                                 available_capital=500_000, risk_pct=1.0, max_lots=10)
        assert r.blocked and r.lots == 0
        assert "below entry" in r.reason

    def test_zero_lot_size_yields_nothing(self):
        r = sizing.size_position(entry_premium=100, stop_premium=80, lot_size=0,
                                 available_capital=500_000, risk_pct=1.0, max_lots=10)
        assert r.lots == 0 and r.qty == 0


# ── C/D: pure exit predicate ─────────────────────────────────────────────────
class TestExitPredicate:
    def test_exits_when_premium_breaches_trail(self):
        assert pos.should_exit(stop_premium=80, ltp=79.5) is True
        assert pos.should_exit(stop_premium=80, ltp=80.0) is True  # at trail = breach

    def test_holds_above_trail(self):
        assert pos.should_exit(stop_premium=80, ltp=80.5) is False

    def test_no_stop_never_exits(self):
        assert pos.should_exit(stop_premium=0, ltp=10) is False

    def test_stale_tick_ignored(self):
        assert pos.should_exit(stop_premium=80, ltp=0) is False


# ── E + C/D: monitor against a fake client ───────────────────────────────────
class _FakeClient:
    def __init__(self):
        self.sells = []
        self.cancelled = []
        self.futures_exits = []
        self.unsubscribed = []
    async def place_order_option(self, sym, side, size, **kw):
        self.sells.append((sym, side, size))
        return {"order_id": "EXIT-1"}
    async def place_order_future(self, sym, side, size, **kw):
        self.futures_exits.append((sym, side, size))
        return {"order_id": "EXIT-F"}
    async def delete_gtt(self, tid):
        self.cancelled.append(tid)
        return {"trigger_id": tid}


@pytest.fixture(autouse=True)
def _patch_ticker_unsubscribe(monkeypatch):
    """Prevent all monitor tests from making real ticker network calls."""
    async def _noop(uid, tokens):
        return {"ok": True}
    monkeypatch.setattr(_ticker_manager, "unsubscribe", _noop)


@pytest.mark.asyncio
async def test_tick_exit_sells_and_cancels_gtt(monkeypatch):
    pos.reset("m1")
    # avoid DB + auto_open coupling noise
    monkeypatch.setattr("app.services.kite_engine.monitor.state.clear_auto_open",
                        lambda *a, **k: None)
    p = pos.register(pos.OpenPosition(
        uid="m1", symbol="NIFTY24JUN24000CE", exchange="NFO", token=777,
        qty=50, lot_size=50, entry_premium=100, stop_premium=80,
        order_id="O1", status=pos.OPEN, gtt_id=555, guard_key="NIFTY24JUN24000CE"))
    client = _FakeClient()

    # tick above trail → no exit
    out = await monitor.on_tick("m1", 777, 85.0, client=client)
    assert out is None and client.sells == []

    # tick at/below trail → exit + GTT cancel + position closed
    out = await monitor.on_tick("m1", 777, 79.0, client=client)
    assert out == "NIFTY24JUN24000CE"
    assert client.sells == [("NIFTY24JUN24000CE", "sell", 50)]
    assert client.cancelled == [555]
    await confirm_exit('m1', 79)
    assert pos.get("m1", "NIFTY24JUN24000CE").status == pos.CLOSED


@pytest.mark.asyncio
async def test_tick_exit_unsubscribes_token(monkeypatch):
    """On trail breach, the position's token is unsubscribed from the ticker."""
    unsubbed = []

    async def _capture(uid, tokens):
        unsubbed.extend(tokens)
    monkeypatch.setattr(_ticker_manager, "unsubscribe", _capture)
    monkeypatch.setattr("app.services.kite_engine.monitor.state.clear_auto_open",
                        lambda *a, **k: None)

    pos.reset("mu1")
    pos.register(pos.OpenPosition(
        uid="mu1", symbol="NIFTY24JUN24000CE", exchange="NFO", token=999,
        qty=50, stop_premium=80, status=pos.OPEN))
    client = _FakeClient()
    await monitor.on_tick("mu1", 999, 75.0, client=client)
    await confirm_exit('mu1', 80)
    assert 999 in unsubbed, "token should be unsubscribed after exit"


@pytest.mark.asyncio
async def test_futures_long_tick_exit_sells(monkeypatch):
    """Futures long position: stop breach calls place_order_future with 'sell'."""
    pos.reset("mf1")
    monkeypatch.setattr("app.services.kite_engine.monitor.state.clear_auto_open",
                        lambda *a, **k: None)
    pos.register(pos.OpenPosition(
        uid="mf1", symbol="NIFTY25JULFUT", exchange="NFO", token=888,
        qty=50, lot_size=50, entry_premium=24000, stop_premium=23800,
        status=pos.OPEN, direction="long", vehicle="futures"))
    client = _FakeClient()

    # above stop → no exit
    out = await monitor.on_tick("mf1", 888, 23900.0, client=client)
    assert out is None and client.futures_exits == []

    # at/below stop → exit via SELL (close long)
    out = await monitor.on_tick("mf1", 888, 23750.0, client=client)
    assert out == "NIFTY25JULFUT"
    assert client.futures_exits == [("NIFTY25JULFUT", "sell", 50)]
    assert client.sells == []   # options path NOT used
    await confirm_exit('mf1', 80)
    assert pos.get("mf1", "NIFTY25JULFUT").status == pos.CLOSED


@pytest.mark.asyncio
async def test_futures_short_tick_exit_buys(monkeypatch):
    """Futures short position: stop breach calls place_order_future with 'buy'."""
    pos.reset("mf2")
    monkeypatch.setattr("app.services.kite_engine.monitor.state.clear_auto_open",
                        lambda *a, **k: None)
    pos.register(pos.OpenPosition(
        uid="mf2", symbol="BANKNIFTY25JULFUT", exchange="NFO", token=999,
        qty=15, lot_size=15, entry_premium=48000, stop_premium=48500,
        status=pos.OPEN, direction="short", vehicle="futures"))
    client = _FakeClient()

    # below stop (short: breach is price ≥ stop) → no exit
    out = await monitor.on_tick("mf2", 999, 48200.0, client=client)
    assert out is None

    # at/above stop → exit via BUY (cover short)
    out = await monitor.on_tick("mf2", 999, 48600.0, client=client)
    assert out == "BANKNIFTY25JULFUT"
    assert client.futures_exits == [("BANKNIFTY25JULFUT", "buy", 15)]
    await confirm_exit('mf2', 80)
    assert pos.get("mf2", "BANKNIFTY25JULFUT").status == pos.CLOSED


@pytest.mark.asyncio
async def test_pending_position_not_exited():
    pos.reset("m2")
    pos.register(pos.OpenPosition(
        uid="m2", symbol="X", exchange="NFO", token=1, qty=50,
        entry_premium=100, stop_premium=80, status=pos.PENDING))
    client = _FakeClient()
    out = await monitor.on_tick("m2", 1, 10.0, client=client)  # way below trail
    assert out is None and client.sells == []  # not filled yet → not protected


@pytest.mark.asyncio
async def test_order_update_confirms_fill():
    pos.reset("m3")
    pos.register(pos.OpenPosition(
        uid="m3", symbol="Y", exchange="NFO", token=2, qty=50,
        entry_premium=100, stop_premium=80, order_id="O9", status=pos.PENDING))
    await monitor.on_order_update("m3", {
        "tradingsymbol": "Y", "status": "COMPLETE", "average_price": 101.5, "order_id": "O9"})
    p = pos.get("m3", "Y")
    assert p.status == pos.OPEN and p.fill_price == pytest.approx(101.5)


@pytest.mark.asyncio
async def test_order_update_rejection_releases_guard(monkeypatch):
    pos.reset("m4")
    released = []
    monkeypatch.setattr("app.services.kite_engine.monitor.state.clear_auto_open",
                        lambda uid, key: released.append((uid, key)))
    pos.register(pos.OpenPosition(
        uid="m4", symbol="Z", exchange="NFO", token=3, qty=50,
        status=pos.PENDING, guard_key="Z", order_id="ENTRY-Z"))
    await monitor.on_order_update("m4", {"tradingsymbol": "Z", "status": "REJECTED", "order_id":"ENTRY-Z"})
    assert pos.get("m4", "Z").status == pos.REJECTED
    assert released == [("m4", "Z")]


@pytest.mark.asyncio
async def test_unknown_order_ignored():
    pos.reset("m5")
    # no registered position — must be a no-op, not raise
    await monitor.on_order_update("m5", {"tradingsymbol": "UNKNOWN", "status": "COMPLETE"})
    assert pos.get("m5", "UNKNOWN") is None


@pytest.mark.asyncio
async def test_protective_exit_fill_closes_position_not_refills(monkeypatch):
    """A broker-GTT (or any protective) exit SELL that fills at the broker must
    reconcile our registry to CLOSED — NOT be mis-read as an entry fill. Before the
    fix, on_order_update matched by symbol only and marked the exit-price as a fill,
    leaving the position OPEN so the monitor could market-SELL a second time (a naked
    short). Guards against the double-sell."""
    pos.reset("mx1")
    released = []
    monkeypatch.setattr("app.services.kite_engine.monitor.state.clear_auto_open",
                        lambda uid, key: released.append((uid, key)))
    pos.register(pos.OpenPosition(
        uid="mx1", symbol="NIFTY24JUN24000CE", exchange="NFO", token=777,
        qty=50, entry_premium=100, stop_premium=80, order_id="ENTRY-1",
        status=pos.OPEN, direction="long", guard_key="NIFTY", exit_order_id="GTT-EXIT-1"))
    # GTT-fired exit: a SELL whose order_id is NOT the entry order.
    await monitor.on_order_update("mx1", {
        "tradingsymbol": "NIFTY24JUN24000CE", "status": "COMPLETE",
        "transaction_type": "SELL", "order_id": "GTT-EXIT-1", "average_price": 79.0, "filled_quantity":50})
    p = pos.get("mx1", "NIFTY24JUN24000CE")
    assert p.status == pos.CLOSED, "protective exit fill must close the position"
    assert released == [("mx1", "NIFTY")], "guard released after exit"

    # a subsequent tick must NOT place a second sell (position no longer OPEN)
    client = _FakeClient()
    out = await monitor.on_tick("mx1", 777, 40.0, client=client)
    assert out is None and client.sells == []


@pytest.mark.asyncio
async def test_futures_short_cover_fill_closes_position(monkeypatch):
    """For a short future the protective exit is a BUY-to-cover; its fill closes."""
    pos.reset("mx2")
    monkeypatch.setattr("app.services.kite_engine.monitor.state.clear_auto_open",
                        lambda *a, **k: None)
    pos.register(pos.OpenPosition(
        uid="mx2", symbol="BANKNIFTY25JULFUT", exchange="NFO", token=888,
        qty=15, entry_premium=48000, stop_premium=48500, order_id="ENTRY-F",
        status=pos.OPEN, exit_order_id="COVER-1", direction="short", vehicle="futures", guard_key="BANKNIFTY"))
    await monitor.on_order_update("mx2", {
        "tradingsymbol": "BANKNIFTY25JULFUT", "status": "COMPLETE",
        "transaction_type": "BUY", "order_id": "COVER-1", "average_price": 48550.0, "filled_quantity":15})
    assert pos.get("mx2", "BANKNIFTY25JULFUT").status == pos.CLOSED


@pytest.mark.asyncio
async def test_entry_fill_still_confirmed_when_order_id_matches():
    """Regression guard: the ENTRY fill (order_id matches) still marks OPEN, even
    though a BUY entry for a long shares the 'BUY' side."""
    pos.reset("mx3")
    pos.register(pos.OpenPosition(
        uid="mx3", symbol="NIFTY24JUN24000CE", exchange="NFO", token=1, qty=50,
        entry_premium=100, stop_premium=80, order_id="ENTRY-9",
        status=pos.PENDING, direction="long"))
    await monitor.on_order_update("mx3", {
        "tradingsymbol": "NIFTY24JUN24000CE", "status": "COMPLETE",
        "transaction_type": "BUY", "order_id": "ENTRY-9", "average_price": 101.5})
    p = pos.get("mx3", "NIFTY24JUN24000CE")
    assert p.status == pos.OPEN and p.fill_price == pytest.approx(101.5)


@pytest.mark.asyncio
async def test_monitor_own_exit_fill_not_double_booked(monkeypatch):
    """The monitor's OWN exit SELL fills, then Kite streams that SELL's COMPLETE
    postback. on_order_update must NOT re-book the realized PnL: the position is
    already CLOSED (the monitor recorded it once), so a second booking would trip the
    INR daily-loss breaker at ~half the real limit. Regression for the double-book."""
    uid = "mbook1"
    pos.reset(uid)
    state.reset(uid)  # clean daily-PnL slate (in-memory + persisted key)
    monkeypatch.setattr("app.services.kite_engine.monitor.state.clear_auto_open",
                        lambda *a, **k: None)
    pos.register(pos.OpenPosition(
        uid=uid, symbol="NIFTY24JUN24000CE", exchange="NFO", token=777,
        qty=50, entry_premium=100, stop_premium=80, order_id="ENTRY-1",
        status=pos.OPEN, direction="long", gtt_id=0, guard_key="NIFTY"))
    client = _FakeClient()

    # trail breach → monitor exits at 80 and books realized (80-100)*50 = -1000 once.
    out = await monitor.on_tick(uid, 777, 80.0, client=client)
    assert out == "NIFTY24JUN24000CE"
    assert client.sells == [("NIFTY24JUN24000CE", "sell", 50)]
    assert state.daily_realized_pnl(uid) == 0.0  # ACK is not a fill

    # Kite now streams the monitor's OWN SELL fill (different order_id than the entry).
    await monitor.on_order_update(uid, {
        "tradingsymbol": "NIFTY24JUN24000CE", "status": "COMPLETE",
        "transaction_type": "SELL", "order_id": "EXIT-1", "average_price": 80.0, "filled_quantity":50})
    # Still booked exactly once — NOT -2000.
    assert state.daily_realized_pnl(uid) == pytest.approx(-1000.0)


@pytest.mark.asyncio
async def test_concurrent_exit_paths_place_single_sell(monkeypatch):
    """Two exit paths (the WS tick monitor + a scan-loop square-off) can call
    _exit_position for the same position concurrently. The synchronous claim must let
    only the first place a SELL; the second bails during the placement await instead of
    double-selling into a naked short. Regression for the double-sell race."""
    uid = "mrace1"
    pos.reset(uid)
    state.reset(uid)
    monkeypatch.setattr("app.services.kite_engine.monitor.state.clear_auto_open",
                        lambda *a, **k: None)
    p = pos.register(pos.OpenPosition(
        uid=uid, symbol="NIFTY24JUN24000CE", exchange="NFO", token=777,
        qty=50, entry_premium=100, stop_premium=80, order_id="ENTRY-1",
        status=pos.OPEN, direction="long", gtt_id=0, guard_key="NIFTY"))

    class _SlowClient(_FakeClient):
        async def place_order_option(self, sym, side, size, **kw):
            await asyncio.sleep(0)  # yield mid-placement so the second caller interleaves
            return await super().place_order_option(sym, side, size, **kw)

    client = _SlowClient()
    # Fire both exit paths concurrently against the SAME open position.
    await asyncio.gather(
        monitor._exit_position(client, uid, p, 80.0, reason="tick breach"),
        monitor._exit_position(client, uid, p, 80.0, reason="expiry square-off"),
    )
    assert client.sells == [("NIFTY24JUN24000CE", "sell", 50)], "exactly one SELL placed"
    await confirm_exit(uid, 80)
    assert pos.get(uid, "NIFTY24JUN24000CE").status == pos.CLOSED
    assert state.daily_realized_pnl(uid) == pytest.approx(-1000.0), "booked once, not twice"


@pytest.mark.asyncio
async def test_exit_fill_postback_arriving_mid_placement_books_once(monkeypatch):
    """The ordering neither test above covers: our SELL's COMPLETE postback lands
    while ``_exit_position`` is still inside the placement await.

    ``_exiting`` is held, but the position is still OPEN — ``pos.close`` runs only
    after the order returns. ``on_order_update`` guards on status alone, so it saw a
    live position and booked the exit; ``_exit_position`` then resumed and booked it
    AGAIN. Double realized PnL feeding the INR daily-loss breaker, which then halts
    trading at half the configured limit (and, on a winner, licenses twice the loss).
    """
    uid = "mrace2"
    pos.reset(uid)
    state.reset(uid)
    monkeypatch.setattr("app.services.kite_engine.monitor.state.clear_auto_open",
                        lambda *a, **k: None)
    p = pos.register(pos.OpenPosition(
        uid=uid, symbol="NIFTY24JUN24000CE", exchange="NFO", token=777,
        qty=50, entry_premium=100, stop_premium=80, order_id="ENTRY-1",
        status=pos.OPEN, direction="long", gtt_id=0, guard_key="NIFTY"))

    class _PostbackDuringPlacement(_FakeClient):
        async def place_order_option(self, sym, side, size, **kw):
            result = await super().place_order_option(sym, side, size, **kw)
            # Kite streams the fill before our own coroutine gets to pos.close().
            await monitor.on_order_update(uid, {
                "tradingsymbol": sym, "status": "COMPLETE", "transaction_type": "SELL",
                "order_id": result["order_id"], "average_price": 80.0, "filled_quantity":size, "tag":kw["tag"]})
            return result

    client = _PostbackDuringPlacement()
    await monitor._exit_position(client, uid, p, 80.0, reason="tick breach")

    assert client.sells == [("NIFTY24JUN24000CE", "sell", 50)], "exactly one SELL"
    assert pos.get(uid, "NIFTY24JUN24000CE").status == pos.CLOSED
    assert state.daily_realized_pnl(uid) == pytest.approx(-1000.0), "booked once, not twice"


@pytest.mark.asyncio
async def test_broker_gtt_fill_during_our_exit_still_reconciles(monkeypatch):
    """The mirror case, which is why the guard cannot simply be "skip if _exiting".

    Here the BROKER's stop filled and its postback arrives while our own exit path
    holds the claim but has not placed anything. Someone must still reconcile the
    position to CLOSED and book it once — skipping on the claim alone would leave a
    flat position marked open, with its auto-open guard still held.
    """
    uid = "mrace3"
    pos.reset(uid)
    state.reset(uid)
    monkeypatch.setattr("app.services.kite_engine.monitor.state.clear_auto_open",
                        lambda *a, **k: None)
    pos.register(pos.OpenPosition(
        uid=uid, symbol="NIFTY24JUN24000CE", exchange="NFO", token=777,
        qty=50, entry_premium=100, stop_premium=80, order_id="ENTRY-1",
        status=pos.OPEN, exit_order_id="GTT-FILL", direction="long", gtt_id=0, guard_key="NIFTY"))
    monitor._exiting.add((uid, "NIFTY24JUN24000CE"))
    try:
        await monitor.on_order_update(uid, {
            "tradingsymbol": "NIFTY24JUN24000CE", "status": "COMPLETE",
            "transaction_type": "SELL", "order_id": "GTT-FILL", "average_price": 80.0, "filled_quantity":50})
    finally:
        monitor._exiting.discard((uid, "NIFTY24JUN24000CE"))
    assert pos.get(uid, "NIFTY24JUN24000CE").status == pos.CLOSED
    assert state.daily_realized_pnl(uid) == pytest.approx(-1000.0)


# ── C: broker-side protective GTT stop ───────────────────────────────────────
class _GttClient:
    def __init__(self):
        self.placed = []
        self.modified = []
    async def place_gtt(self, **kw):
        self.placed.append(kw)
        return {"trigger_id": 909}
    async def modify_gtt(self, tid, **kw):
        self.modified.append((tid, kw))
        return {"trigger_id": tid}


@pytest.mark.asyncio
async def test_protective_stop_places_single_leg_sell():
    from app.services.kite_engine import protective_stop as ps
    client = _GttClient()
    tid = await ps.place_stop(client, tradingsymbol="NIFTY24JUN24000CE", exchange="NFO",
                              qty=50, trigger_premium=80, last_price=100)
    assert tid == 909
    body = client.placed[0]
    assert body["trigger_values"] == [80.0]
    order = body["orders"][0]
    assert order["transaction_type"] == "SELL" and order["quantity"] == 50


@pytest.mark.asyncio
async def test_protective_stop_skips_when_no_stop():
    from app.services.kite_engine import protective_stop as ps
    client = _GttClient()
    assert await ps.place_stop(client, tradingsymbol="X", exchange="NFO",
                               qty=50, trigger_premium=0, last_price=100) is None
    assert client.placed == []


# ── Red-count aware exit in monitor (new for configurable counter exits) ─────
@pytest.mark.asyncio
async def test_tick_red_count_exit_for_two_red_mode(monkeypatch):
    """Monitor should exit on red count threshold even if price hasn't breached trail yet."""
    pos.reset("mr1")
    monkeypatch.setattr("app.services.kite_engine.monitor.state.clear_auto_open",
                        lambda *a, **k: None)
    # Position opened under two_red rule, trail is 80 but current reds=2 (meets threshold)
    p = pos.register(pos.OpenPosition(
        uid="mr1", symbol="NIFTY24JUN24000CE", exchange="NFO", token=777,
        qty=50, lot_size=50, entry_premium=100, stop_premium=80,
        order_id="O1", status=pos.OPEN, gtt_id=555, guard_key="NIFTY",
        exit_mode="two_red", current_red_count=2))
    client = _FakeClient()

    # Price still above trail (85 > 80), but red count hit → should exit with red reason
    out = await monitor.on_tick("mr1", 777, 85.0, client=client)
    assert out == "NIFTY24JUN24000CE"
    assert client.sells == [("NIFTY24JUN24000CE", "sell", 50)]
    assert client.cancelled == [555]
    closed = pos.get("mr1", "NIFTY24JUN24000CE")
    await confirm_exit('mr1', 80)
    assert closed.status == pos.CLOSED
    assert "red count exit 2/2 (two_red)" in closed.exit_reason


@pytest.mark.asyncio
async def test_tick_no_red_exit_below_threshold(monkeypatch):
    """No exit on red if below the mode's threshold (price ok too)."""
    pos.reset("mr2")
    monkeypatch.setattr("app.services.kite_engine.monitor.state.clear_auto_open",
                        lambda *a, **k: None)
    pos.register(pos.OpenPosition(
        uid="mr2", symbol="X", exchange="NFO", token=1, qty=50,
        entry_premium=100, stop_premium=80, status=pos.OPEN,
        exit_mode="three_red", current_red_count=1))  # only 1 red, needs 3
    client = _FakeClient()
    out = await monitor.on_tick("mr2", 1, 85.0, client=client)  # good price
    assert out is None and client.sells == []
    assert pos.get("mr2", "X").status == pos.OPEN


@pytest.mark.asyncio
async def test_tick_red_signal_exit_requires_counter_arrow(monkeypatch):
    """For three_red_signal, red count alone shouldn't exit; needs fresh counter arrow (simulated via health)."""
    pos.reset("mr3")
    monkeypatch.setattr("app.services.kite_engine.monitor.state.clear_auto_open",
                        lambda *a, **k: None)
    # reds=3 but counter_signal_fired is False: monitor must not exit on reds alone
    p = pos.register(pos.OpenPosition(
        uid="mr3", symbol="Y", exchange="NFO", token=2, qty=10,
        entry_premium=100, stop_premium=90, status=pos.OPEN,
        exit_mode="three_red_signal", current_red_count=3, counter_signal_fired=False))
    client = _FakeClient()
    out = await monitor.on_tick("mr3", 2, 95.0, client=client)  # price ok
    assert out is None
    assert pos.get("mr3", "Y").status == pos.OPEN

    # Now simulate health update recording a fresh counter signal arrow
    pos.update_health("mr3", "Y", 3, "three_red_signal", counter_signal_fired=True)
    out = await monitor.on_tick("mr3", 2, 95.0, client=client)
    assert out == "Y"
    await confirm_exit("mr3", 95.0)
    assert pos.get("mr3", "Y") is None or pos.get("mr3", "Y").status == pos.CLOSED

from tests.engines.sterling_kite_engine.execution_fixtures import confirm_exit
