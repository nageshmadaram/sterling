"""Every automatic SELL path, and the one that must NOT fire.

Three verified defects are pinned here, all of the same family: the account ending
up SHORT an option it never sold on purpose.

1. With ``stop_mode="both"`` the broker GTT and the tick monitor were armed at the
   IDENTICAL price. Market data reaches us before a fill postback does, so on a
   price breach the GTT has very likely already fired and its SELL is live at the
   exchange — and the monitor placed a second one. `_exiting` never guarded this:
   it only serialises OUR coroutines, and Zerodha's GTT engine never takes it.
2. A protective GTT armed against an entry that was then REJECTED was never
   cancelled — a resting SELL with no position behind it.
3. ``filled_quantity`` was never read, so a partial fill left the system believing
   it held the full intended quantity and arming exits for all of it.

Plus the new surface: a hand-placed order is now registered and armed like an
auto-executed one, and a target is enforced BROKER-side as the second leg of an
OCO so stop and target can never both fire.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.services.kite_engine import monitor
from app.services.kite_engine import positions as pos
from app.services.kite_engine import protection, protective_stop
import app.services.exchanges.kite.ticker_manager as _ticker_manager
from tests.engines.sterling_kite_engine.canonical_double import CanonicalBrokerDouble


class _NotFound(Exception):
    """A broker 404 — the trigger id is unknown to Zerodha."""
    status_code = 404


class _InputError(Exception):
    """What Kite is just as likely to answer for a trigger it no longer holds: a 400
    ``InputException``, not a 404. Trusting only the 404 left this case UNVERIFIED."""
    status_code = 400
    error_type = "InputException"


class _FakeClient(CanonicalBrokerDouble):
    """Records what was sent to the broker. ``cancel_error`` makes delete_gtt fail
    the way Zerodha would for a trigger that has already fired.

    ``gtt_status`` is what GET /gtt/triggers/{id} reports afterwards — the question
    that decides whether our own exit is a backstop or a second SELL. "missing" makes
    the lookup 404 the way a trigger deleted in the Kite app would; "error" makes it
    fail with a 400 instead, which is the answer we cannot read a verdict out of.

    ``gtt_book`` is GET /gtt/triggers (the second opinion) and ``order_book`` is
    GET /orders (the third) — the two reads that turn "the trigger is not there" into
    "and nothing is selling this for us either". Both default to empty, i.e. no
    trigger resting and no exit order working.
    """

    def __init__(self, cancel_error: str | None = None, *, gtt_status: str = "active",
                 ltp: float = 0.0, move_fails: bool = False, order_history: list | None = None,
                 gtt_book: list | None = None, order_book: list | None = None,
                 book_error: str | None = None, net_positions: dict | None = None):
        self.sells: list = []
        self.cancelled: list = []
        self.gtts: list = []
        self.modified: list = []
        self.calls: list = []          # ordered log of every broker call
        self.cancel_error = cancel_error
        self.gtt_status = gtt_status
        self.ltp = ltp
        self.move_fails = move_fails
        self.order_history = order_history or []
        self.gtt_book = gtt_book or []
        self.order_book = order_book or []
        self.book_error = book_error
        #: GET /portfolio/positions. Default: the broker still holds everything, so a
        #: test must opt IN to "the position is gone" rather than get it by accident.
        self.net_positions = net_positions if net_positions is not None else {"net": []}

    async def get_gtts(self):
        self.calls.append("get_gtts")
        if self.book_error == "gtts":
            raise RuntimeError("gtt list unreachable")
        return list(self.gtt_book)

    async def get_orders(self):
        self.calls.append("get_orders")
        if self.book_error == "orders":
            raise RuntimeError("order book unreachable")
        return list(self.order_book)

    async def place_order_option(self, sym, side, size, **kw):
        self.sells.append((sym, side, size))
        self.calls.append("sell")
        return {"order_id": "EXIT-1"}

    async def place_order_future(self, sym, side, size, **kw):
        self.calls.append("sell_future")
        return {"order_id": "EXIT-F"}

    async def delete_gtt(self, tid):
        self.calls.append("cancel")
        if self.cancel_error:
            raise RuntimeError(self.cancel_error)
        self.cancelled.append(tid)
        return {"trigger_id": tid}

    async def get_gtt(self, tid):
        self.calls.append("get_gtt")
        if self.gtt_status == "missing":
            raise _NotFound("gtt not found")
        if self.gtt_status == "error":
            raise _InputError("Invalid trigger_id")
        return {"id": tid, "status": self.gtt_status}

    async def place_gtt(self, **kw):
        self.gtts.append(kw)
        return {"trigger_id": 4242}

    async def modify_gtt(self, tid, **kw):
        self.calls.append("modify")
        if self.move_fails:
            raise RuntimeError("gtt modify rejected")
        self.modified.append((tid, kw))
        return {"trigger_id": tid}

    async def get_ltp(self, keys):
        self.calls.append("ltp")
        if self.ltp <= 0:
            return {}
        return {k: {"last_price": self.ltp} for k in keys}

    async def get_order_history(self, order_id):
        self.calls.append("order_history")
        return self.order_history

    async def get_positions_raw(self):
        self.calls.append("positions")
        if self.book_error == "positions":
            raise RuntimeError("positions unreachable")
        return self.net_positions


@pytest.fixture(autouse=True)
def _no_network(monkeypatch):
    async def _noop(uid, tokens, **kw):
        return {"ok": True}
    monkeypatch.setattr(_ticker_manager, "unsubscribe", _noop)
    monkeypatch.setattr(_ticker_manager, "subscribe", _noop)
    monitor.forget_holdings()
    monkeypatch.setattr("app.services.kite_engine.monitor.state.clear_auto_open",
                        lambda *a, **k: None)
    # The "what became of this GTT?" answer is cached for 15s per (uid, symbol, trigger).
    # Tests reuse those triples, so a verdict must not leak from one case into the next.
    monitor._stop_probe.clear()


def _held(uid="p1", *, gtt_id=555, stop=80.0, target=0.0, status=None, qty=50):
    pos.reset(uid)
    return pos.register(pos.OpenPosition(
        uid=uid, symbol="NIFTY24JUN24000CE", exchange="NFO", token=777,
        qty=qty, lot_size=50, entry_premium=100.0, stop_premium=stop,
        order_id="O1", status=status or pos.OPEN, gtt_id=gtt_id,
        guard_key="NIFTY24JUN24000CE", target_premium=target))


# ── 1. the double-sell ────────────────────────────────────────────────────────

class TestBrokerStopWinsThePriceRace:
    @pytest.mark.asyncio
    async def test_no_second_sell_when_the_gtt_could_not_be_cancelled(self):
        """The exact naked-short sequence: price hits the shared trigger, the GTT has
        already fired (so the cancel fails), and we must NOT add a second SELL."""
        _held("p1")
        client = _FakeClient(cancel_error="Trigger already triggered")

        out = await monitor.on_tick("p1", 777, 79.0, client=client)

        assert out is None, "the monitor reported an exit it did not place"
        assert client.sells == [], "second SELL placed while the broker's was already live"
        held = pos.get("p1", "NIFTY24JUN24000CE")
        assert held.status == pos.OPEN, (
            "position must stay open until the broker's fill postback reconciles it — "
            "closing it here would lose the position we still hold"
        )

    @pytest.mark.asyncio
    async def test_cancel_happens_before_the_sell(self):
        """Ordering is the fix. Cancelling AFTER selling cannot prevent anything."""
        _held("p2")
        client = _FakeClient()

        await monitor.on_tick("p2", 777, 79.0, client=client)

        # Order, not an exact list — the exit path also reads the portfolio before
        # selling (see "never sell what we do not hold"). What matters here is that
        # the cancel precedes the sell; cancelling after it cannot prevent anything.
        assert client.calls.index("cancel") < client.calls.index("sell")
        assert client.cancelled == [555]
        assert client.sells == [("NIFTY24JUN24000CE", "sell", 50)]
        await confirm_exit('p2', 80)
        assert pos.get("p2", "NIFTY24JUN24000CE").status == pos.CLOSED

    @pytest.mark.asyncio
    async def test_non_price_exit_waits_for_confirmed_stop_cancellation(self):
        """An active GTT can fire independently while a red-count SELL awaits fill."""
        p = _held("p3", stop=80.0)
        pos.update_health("p3", p.symbol, red_count=3, exit_mode="one_red")
        client = _FakeClient(cancel_error="network unreachable")

        # price is nowhere near the stop, so this can only be the red-count exit
        out = await monitor.on_tick("p3", 777, 150.0, client=client)

        assert out is None and client.sells == []
        assert pos.get("p3", "NIFTY24JUN24000CE").status == pos.OPEN

    @pytest.mark.asyncio
    async def test_position_with_no_gtt_is_unaffected(self):
        """stop_mode="monitor" has no broker stop to race with."""
        _held("p4", gtt_id=0)
        client = _FakeClient()

        out = await monitor.on_tick("p4", 777, 79.0, client=client)

        assert out == "NIFTY24JUN24000CE"
        assert "cancel" not in client.calls, "there was no trigger to cancel"
        assert "sell" in client.calls


# ── 2. the orphaned GTT ───────────────────────────────────────────────────────

class TestRejectedEntryLeavesNothingArmed:
    @pytest.mark.asyncio
    async def test_rejected_entry_cancels_its_protective_gtt(self):
        _held("r1", status=pos.PENDING)
        client = _FakeClient()

        await monitor.on_order_update(
            "r1", {"tradingsymbol": "NIFTY24JUN24000CE", "status": "REJECTED",
                   "order_id": "O1", "filled_quantity": 0}, client=client)

        assert client.cancelled == [555], "an armed SELL was left resting at Zerodha"
        held = pos.get("r1", "NIFTY24JUN24000CE")
        assert held.status == pos.REJECTED
        assert held.gtt_id == 0, "registry still points at a GTT that no longer exists"

    @pytest.mark.asyncio
    async def test_a_failed_cancel_is_reported_not_swallowed(self):
        """The operator has to be told to go look — this is the one case the code
        cannot fix by itself."""
        _held("r2", status=pos.PENDING)
        client = _FakeClient(cancel_error="gateway timeout")
        logged: list = []

        from app.services.kite_engine import state as kstate
        original = kstate.log
        kstate.log = lambda uid, kind, msg: logged.append(msg)
        try:
            await monitor.on_order_update(
                "r2", {"tradingsymbol": "NIFTY24JUN24000CE", "status": "REJECTED",
                       "order_id": "O1", "filled_quantity": 0}, client=client)
        finally:
            kstate.log = original

        assert any("could NOT be" in m for m in logged)


# ── 3. partial fills ─────────────────────────────────────────────────────────

class TestPartialFills:
    @pytest.mark.asyncio
    async def test_a_partial_fill_is_a_position_not_a_rejection(self):
        """CANCELLED after 1 of 3 lots filled means we HOLD 1 lot. Treating it as a
        rejection dropped a real position out of the registry — unguarded, invisible,
        and never squared off at expiry."""
        _held("f1", status=pos.PENDING, qty=150)
        client = _FakeClient()

        await monitor.on_order_update(
            "f1", {"tradingsymbol": "NIFTY24JUN24000CE", "status": "CANCELLED",
                   "order_id": "O1", "filled_quantity": 50, "average_price": 101.5},
            client=client)

        held = pos.get("f1", "NIFTY24JUN24000CE")
        assert held.status == pos.OPEN
        assert held.qty == 50, "still believes it holds the intended quantity"
        assert held.fill_price == pytest.approx(101.5)
        assert client.cancelled == [], "the stop must stay armed on what we do hold"

    @pytest.mark.asyncio
    async def test_a_full_fill_records_the_broker_quantity(self):
        _held("f2", status=pos.PENDING, qty=150)
        client = _FakeClient()

        await monitor.on_order_update(
            "f2", {"tradingsymbol": "NIFTY24JUN24000CE", "status": "COMPLETE",
                   "order_id": "O1", "filled_quantity": 100, "average_price": 99.0},
            client=client)

        held = pos.get("f2", "NIFTY24JUN24000CE")
        assert (held.status, held.qty) == (pos.OPEN, 100)

    @pytest.mark.asyncio
    async def test_the_exit_sells_only_what_is_held(self):
        """The whole point of reading the fill: the exit quantity follows it."""
        _held("f3", status=pos.PENDING, qty=150)
        client = _FakeClient()
        await monitor.on_order_update(
            "f3", {"tradingsymbol": "NIFTY24JUN24000CE", "status": "COMPLETE",
                   "order_id": "O1", "filled_quantity": 50, "average_price": 100.0},
            client=client)

        await monitor.on_tick("f3", 777, 79.0, client=client)

        assert client.sells == [("NIFTY24JUN24000CE", "sell", 50)]


# ── the target, enforced by the exchange ──────────────────────────────────────

class TestTargetIsAnOcoLeg:
    @pytest.mark.asyncio
    async def test_stop_and_target_go_out_as_one_two_leg_trigger(self):
        client = _FakeClient()
        tid = await protective_stop.place_stop(
            client, tradingsymbol="X", exchange="NFO", qty=50,
            trigger_premium=80.0, last_price=100.0, direction="long",
            target_premium=160.0)

        assert tid == 4242
        sent = client.gtts[0]
        assert sent["trigger_type"] == "two-leg"
        assert sent["trigger_values"] == [80.0, 160.0], "OCO triggers must ascend"
        assert len(sent["orders"]) == 2

    @pytest.mark.asyncio
    async def test_a_target_on_the_wrong_side_of_the_stop_is_refused(self):
        """A "target" below a long's stop would fire instantly for a loss. Fall back
        to a plain stop rather than arming nonsense."""
        client = _FakeClient()
        await protective_stop.place_stop(
            client, tradingsymbol="X", exchange="NFO", qty=50,
            trigger_premium=80.0, last_price=100.0, direction="long",
            target_premium=70.0)

        assert client.gtts[0]["trigger_type"] == "single"
        assert client.gtts[0]["trigger_values"] == [80.0]

    @pytest.mark.asyncio
    async def test_trailing_keeps_the_target_leg(self):
        """A GTT modify rewrites the whole trigger, so a move that forgot the target
        would silently drop it the first time the trail ratcheted."""
        client = _FakeClient()
        await protective_stop.move_stop(
            client, trigger_id=99, tradingsymbol="X", exchange="NFO", qty=50,
            trigger_premium=90.0, last_price=120.0, direction="long",
            target_premium=160.0)

        _tid, sent = client.modified[0]
        assert sent["trigger_type"] == "two-leg"
        assert sent["trigger_values"] == [90.0, 160.0]

    @pytest.mark.asyncio
    async def test_the_monitor_does_not_also_chase_a_target_the_broker_holds(self):
        """Two sell paths for one exit is the bug this whole file is about."""
        _held("t1", target=160.0, gtt_id=555)
        client = _FakeClient()

        out = await monitor.on_tick("t1", 777, 165.0, client=client)

        assert out is None and client.sells == []

    @pytest.mark.asyncio
    async def test_the_monitor_books_the_target_when_no_gtt_holds_it(self):
        """stop_mode="monitor" has no broker leg, so the target is ours to enforce."""
        _held("t2", target=160.0, gtt_id=0)
        client = _FakeClient()

        out = await monitor.on_tick("t2", 777, 165.0, client=client)

        assert out == "NIFTY24JUN24000CE"
        assert "target reached" in pos.get("t2", "NIFTY24JUN24000CE").exit_reason

    @pytest.mark.asyncio
    async def test_a_bar_that_hits_both_is_booked_as_the_stop(self):
        """Never resolve an ambiguous tick in our own favour."""
        _held("t3", stop=80.0, target=160.0, gtt_id=0)
        client = _FakeClient()

        await monitor.on_tick("t3", 777, 79.0, client=client)

        assert "trail breach" in pos.get("t3", "NIFTY24JUN24000CE").exit_reason


# ── re-entry must not double-arm ──────────────────────────────────────────────

class TestArmingIsIdempotentPerSymbol:
    @pytest.mark.asyncio
    async def test_re_arming_cancels_the_previous_trigger(self):
        """`positions.register` overwrites by symbol, so without this the old gtt_id
        was dropped and a SECOND trigger armed for one position — two SELLs, net short."""
        _held("a1", gtt_id=555)
        client = _FakeClient()

        await protection.arm_position(
            client, "a1", symbol="NIFTY24JUN24000CE", exchange="NFO", token=777,
            qty=50, lot_size=50, entry_premium=100.0, stop_premium=80.0,
            order_id="O2", stop_mode="both")

        assert client.cancelled == [555]
        assert pos.get("a1", "NIFTY24JUN24000CE").gtt_id == 4242

    @pytest.mark.asyncio
    async def test_arming_reports_what_it_actually_armed(self):
        pos.reset("a2")
        client = _FakeClient()

        armed = await protection.arm_position(
            client, "a2", symbol="X24JUN100CE", exchange="NFO", token=1,
            qty=50, lot_size=50, entry_premium=100.0, stop_premium=80.0,
            order_id="O3", stop_mode="both", target_premium=160.0)

        assert armed.protected is True
        assert "GTT #4242" in armed.describe() and "target" in armed.describe()

    @pytest.mark.asyncio
    async def test_no_stop_means_not_protected_and_says_so(self):
        pos.reset("a3")
        client = _FakeClient()

        armed = await protection.arm_position(
            client, "a3", symbol="X24JUN100CE", exchange="NFO", token=1,
            qty=50, lot_size=50, entry_premium=100.0, stop_premium=0.0,
            order_id="O4", stop_mode="both")

        assert armed.protected is False
        assert client.gtts == []
        assert "no stop" in armed.describe()


# ── the hand-placed order ─────────────────────────────────────────────────────

class TestManualOrdersAreProtected:
    """`positions.register` used to have exactly ONE call site — the auto-exec path.
    An order placed by hand from the signal board got no registry entry, no broker
    stop, no tick monitor and no expiry square-off, while the board went on showing
    it an SL, a TSL and a Target. The display was the dangerous part."""

    @staticmethod
    def _board_row(symbol="BANKNIFTY26AUG57000CE", *, stop=255.0, target=540.0):
        from app.engines.sterling_kite_engine.schemas import AlignmentChip, EngineSignalRow, OptionLeg
        leg = OptionLeg(
            moneyness="ATM", option_type="CE", option_symbol=symbol, strike=57_000.0,
            expiry="2026-08-25", lot_size=35, token=9_001, premium_spot=320.0,
            entry_sl=210.0, premium_sl=stop, premium_target=target, is_active=True)
        return EngineSignalRow(
            underlying="NIFTY BANK", token=260_105, exchange="NFO", regime="BULL",
            alignment=AlignmentChip(fast=1, mid=1, slow=1), direction="long",
            option_type="CE", legs=[leg], spot=57_100.0, underlying_spot=57_100.0,
            stop_loss=56_900.0, score=85.0, timestamp_ms=1_785_404_700_000,
            is_active=True, is_fresh=True, source="spot")

    @pytest.fixture()
    def _wired(self, monkeypatch):
        """A live account + warm client + one row on the board."""
        from app.services.exchanges.kite import accounts as kite_accounts
        from app.services.kite_engine import service as ksvc
        from app.services.kite_engine.scanner import scanner

        client = _FakeClient()

        class _Acct:
            user_id, id, is_paper, connected = "m-user", 1, False, True

        async def _order(symbol, side, qty, **kw):
            client.calls.append(f"entry:{side}")
            return {"order_id": "ENTRY-9"}

        client.place_order_option_entry = _order
        monkeypatch.setattr(kite_accounts, "get_active", lambda uid: _Acct())
        monkeypatch.setattr(kite_accounts, "acquire_client", lambda acct: _async(client))
        # SafetySupervisor asks the same authority positionally, so the double
        # has to accept both spellings or admission fails closed on a TypeError.
        monkeypatch.setattr(ksvc.live_safety, "assert_safe_to_trade",
                            lambda *a, **kw: type("D", (), {"allowed": True, "code": "", "reason": ""})())
        monkeypatch.setattr(ksvc.live_safety, "check_idempotency", lambda key: None)
        monkeypatch.setattr(ksvc.live_safety, "record_idempotency", lambda key, oid: None)
        pos.reset("m-user")
        scanner.snapshot("m-user").rows = [self._board_row()]
        yield client
        scanner._users.pop("m-user", None)

    @pytest.mark.asyncio
    async def test_a_manual_buy_is_registered_and_armed_from_the_boards_plan(self, _wired):
        from app.services.kite_engine import service as ksvc

        res = await ksvc.place_manual_order(
            "m-user", "BANKNIFTY26AUG57000CE", "BUY", 35, exchange="NFO")

        assert res["status"] == "ok"
        assert res["protected"] is True
        held = pos.get("m-user", "BANKNIFTY26AUG57000CE")
        assert held is not None, "a hand-placed order still went unregistered"
        assert held.stop_premium == pytest.approx(255.0), "stop must be the board's own TSL"
        assert held.target_premium == pytest.approx(540.0)
        # and it went to the broker as one OCO carrying both levels
        assert _wired.gtts[0]["trigger_type"] == "two-leg"
        assert _wired.gtts[0]["trigger_values"] == [255.0, 540.0]

    @pytest.mark.asyncio
    async def test_a_contract_not_on_the_board_is_reported_unprotected_not_blocked(self, _wired):
        from app.services.kite_engine import service as ksvc

        res = await ksvc.place_manual_order(
            "m-user", "BANKNIFTY26AUG99000CE", "BUY", 35, exchange="NFO")

        assert res["status"] == "ok", "never block a trade the user asked for"
        assert res["protected"] is False
        assert "no stop to arm" in res["protection"]
        assert _wired.gtts == []

    @pytest.mark.asyncio
    async def test_a_leg_with_no_premium_stop_is_reported_unprotected(self, _wired, monkeypatch):
        from app.services.kite_engine import service as ksvc
        from app.services.kite_engine.scanner import scanner

        scanner.snapshot("m-user").rows = [self._board_row(stop=0.0, target=0.0)]
        # entry_sl is the fallback, so null that too by rebuilding the leg
        scanner.snapshot("m-user").rows[0].legs[0].entry_sl = None

        res = await ksvc.place_manual_order(
            "m-user", "BANKNIFTY26AUG57000CE", "BUY", 35, exchange="NFO")

        assert res["status"] == "ok" and res["protected"] is False
        assert "no premium stop" in res["protection"]

    @pytest.mark.asyncio
    async def test_the_toggle_off_leaves_manual_orders_alone(self, _wired, monkeypatch):
        from app.services.kite_engine import service as ksvc

        cfg = ksvc.state.get_config("m-user")
        monkeypatch.setattr(ksvc.state, "get_config",
                            lambda uid: cfg.model_copy(update={"protect_manual_orders": False}))

        res = await ksvc.place_manual_order(
            "m-user", "BANKNIFTY26AUG57000CE", "BUY", 35, exchange="NFO")

        assert res["protected"] is False
        assert "switched off" in res["protection"]
        assert pos.get("m-user", "BANKNIFTY26AUG57000CE") is None

    @pytest.mark.asyncio
    async def test_a_manual_sell_of_a_held_position_goes_through_the_exit_path(self, _wired):
        """One SELL, one close, one realized PnL — and it takes the same `_exiting`
        claim an automatic exit takes, so the two can never both sell."""
        from app.services.kite_engine import service as ksvc

        pos.register(pos.OpenPosition(
            uid="m-user", symbol="BANKNIFTY26AUG57000CE", exchange="NFO", token=9_001,
            qty=35, lot_size=35, entry_premium=320.0, stop_premium=255.0,
            order_id="ENTRY-1", status=pos.OPEN, gtt_id=555))

        res = await ksvc.place_manual_order(
            "m-user", "BANKNIFTY26AUG57000CE", "SELL", 35, exchange="NFO")

        assert res["status"] == "ok"
        held = pos.get("m-user", "BANKNIFTY26AUG57000CE")
        await confirm_exit("m-user",320)
        assert held.status == pos.CLOSED
        assert "manual exit" in held.exit_reason
        assert _wired.sells == [("BANKNIFTY26AUG57000CE", "sell", 35)]
        assert _wired.cancelled == [555], "the broker stop must not outlive the position"


def _async(value):
    async def _wrap(*a, **kw):
        return value
    return _wrap()


# ── 6. whose exit is it? intent, not price ────────────────────────────────────

class TestTheExitDecisionUsesIntentNotPrice:
    """`_exit_position` used to infer "this is a price-stop exit" from the PRICE. But a
    manual exit passes the stop itself as its price, and an expiry square-off or a
    red-count exit routinely happens with the premium already under the stop — so the
    exits a broker GTT will NEVER perform were exactly the ones it skipped."""

    @pytest.mark.asyncio
    async def test_a_manual_exit_never_races_an_uncancelled_stop(self):
        p = _held("i1", stop=80.0)
        client = _FakeClient(cancel_error="gateway timeout", gtt_status="active")

        sold = await monitor._exit_position(client, "i1", p, 80.0,
                                           reason="manual exit from the board",
                                           price_stop_exit=False)

        assert sold is False and client.sells == []
        assert p.status == pos.OPEN and p.gtt_id == 555

    @pytest.mark.asyncio
    async def test_a_price_stop_exit_stands_down_while_the_broker_stop_is_active(self):
        p = _held("i2", stop=80.0)
        client = _FakeClient(cancel_error="gateway timeout", gtt_status="active")

        sold = await monitor._exit_position(client, "i2", p, 79.0, price_stop_exit=True)

        assert sold is False and client.sells == [], "a second SELL at a shared trigger"
        assert pos.get("i2", "NIFTY24JUN24000CE").status == pos.OPEN

    @pytest.mark.asyncio
    async def test_a_price_stop_exit_sells_when_the_broker_has_no_such_trigger(self):
        """The permanent-no-exit case: the trigger was deleted in the Kite app, or it
        fired and its SELL was rejected. "Cancel failed" reads identically to "already
        triggered", so the monitor stood down forever and logged "awaiting its fill"."""
        p = _held("i3", stop=80.0)
        client = _FakeClient(cancel_error="trigger not found", gtt_status="missing")

        sold = await monitor._exit_position(client, "i3", p, 79.0, price_stop_exit=True)

        assert sold is True, "nothing at the broker was going to exit this position"
        assert client.sells == [("NIFTY24JUN24000CE", "sell", 50)]
        await confirm_exit("i3",80)
        assert pos.get("i3", "NIFTY24JUN24000CE").status == pos.CLOSED

    @pytest.mark.asyncio
    async def test_a_triggered_broker_stop_blocks_even_a_non_price_exit(self):
        """An OCO's TARGET leg fires on the way UP, so no price-against-the-stop test
        can see it. Only the broker's own status can."""
        p = _held("i4", stop=80.0, target=160.0)
        client = _FakeClient(cancel_error="already triggered", gtt_status="triggered")

        sold = await monitor._exit_position(client, "i4", p, 165.0,
                                           reason="red count exit 3/1 (one_red)",
                                           price_stop_exit=False)

        assert sold is False and client.sells == [], "the broker's SELL is already out"

    @pytest.mark.asyncio
    async def test_the_broker_is_asked_once_not_on_every_tick(self):
        """A stood-down position re-enters on EVERY tick; asking each time would burn
        the broker's rate limit for an answer that does not change."""
        _held("i5", stop=80.0)
        client = _FakeClient(cancel_error="gateway timeout", gtt_status="active")

        await monitor.on_tick("i5", 777, 79.0, client=client)
        await monitor.on_tick("i5", 777, 78.5, client=client)

        assert client.calls.count("get_gtt") == 1

    @pytest.mark.asyncio
    async def test_a_failed_sell_after_a_cancelled_stop_clears_the_trigger_id(self):
        """We took the broker's stop off and then could not sell. Leaving the id set
        made every later tick defer to a trigger we had cancelled ourselves."""
        p = _held("i6", stop=80.0)

        class _NoSell(_FakeClient):
            async def place_order_option(self, *a, **kw):
                raise RuntimeError("exchange rejected")

        client = _NoSell()
        sold = await monitor._exit_position(client, "i6", p, 79.0, price_stop_exit=True)

        assert sold is False and client.cancelled == [555]
        assert pos.get("i6", "NIFTY24JUN24000CE").gtt_id == 0


# ── 7. the manual exit must not claim a close it did not make ─────────────────

class TestManualExitTellsTheTruth:
    @pytest.mark.asyncio
    async def test_a_manual_exit_that_sold_nothing_is_reported_as_an_error(
            self, monkeypatch):
        """The broker's OCO has already fired, so we correctly do NOT add a second SELL.
        What must not happen is telling the user "Position closed at market" for an order
        of theirs that placed nothing — they act on that."""
        from app.services.exchanges.kite import accounts as kite_accounts
        from app.services.kite_engine import service as ksvc

        client = _FakeClient(cancel_error="already triggered", gtt_status="triggered")
        recorded: list = []

        class _Acct:
            user_id, id, is_paper, connected = "x-user", 1, False, True

        monkeypatch.setattr(kite_accounts, "get_active", lambda uid: _Acct())
        monkeypatch.setattr(kite_accounts, "acquire_client", lambda acct: _async(client))
        # SafetySupervisor asks the same authority positionally, so the double
        # has to accept both spellings or admission fails closed on a TypeError.
        monkeypatch.setattr(ksvc.live_safety, "assert_safe_to_trade",
                            lambda *a, **kw: type("D", (), {"allowed": True, "code": "", "reason": ""})())
        monkeypatch.setattr(ksvc.live_safety, "check_idempotency", lambda key: None)
        monkeypatch.setattr(ksvc.live_safety, "record_idempotency",
                            lambda key, oid: recorded.append(key))
        pos.reset("x-user")
        pos.register(pos.OpenPosition(
            uid="x-user", symbol="NIFTY24JUN24000CE", exchange="NFO", token=777,
            qty=50, lot_size=50, entry_premium=100.0, stop_premium=80.0,
            order_id="E1", status=pos.OPEN, gtt_id=555))

        res = await ksvc.place_manual_order(
            "x-user", "NIFTY24JUN24000CE", "SELL", 50, exchange="NFO")

        assert res["status"] == "error", "the user was told a live position was closed"
        assert "still open" in res["message"]
        assert client.sells == []
        assert pos.get("x-user", "NIFTY24JUN24000CE").status == pos.OPEN
        assert recorded == [], "a burnt idempotency key blocks the retry for 60s"


# ── 8. expiry square-off is not a price exit ──────────────────────────────────

class TestExpirySquareOffCancellationSafety:
    @pytest.mark.asyncio
    async def test_it_defers_and_keeps_exposure_visible_when_cancel_fails(self, monkeypatch):
        """Expiry urgency cannot authorize a second exit against an active stop."""
        from app.services.kite_engine import service as ksvc

        pos.reset("e1")
        pos.register(pos.OpenPosition(
            uid="e1", symbol="TCS26AUG2440CE", exchange="NFO", token=888,
            qty=175, lot_size=175, entry_premium=60.0, stop_premium=40.0,
            order_id="E1", status=pos.OPEN, gtt_id=555,
            expiry=datetime.now(timezone.utc).date().isoformat()))
        client = _FakeClient(cancel_error="gateway timeout", gtt_status="active")
        monkeypatch.setattr(ksvc, "is_market_open", lambda: True)

        await ksvc._square_off_expiring(client, "e1")

        assert client.sells == []
        assert pos.get("e1", "TCS26AUG2440CE").status == pos.OPEN


# ── 9. the GTT quantity must match what we hold ───────────────────────────────

class TestTheBrokerStopTracksTheRealQuantity:
    @pytest.mark.asyncio
    async def test_a_partial_fill_resizes_the_resting_trigger(self):
        """The GTT was armed for the full intended size. Holding less than that leaves
        the surplus as a NAKED SHORT the moment the trigger fires."""
        _held("q1", status=pos.PENDING, qty=150)
        client = _FakeClient()

        await monitor.on_order_update(
            "q1", {"tradingsymbol": "NIFTY24JUN24000CE", "status": "CANCELLED",
                   "order_id": "O1", "filled_quantity": 50, "average_price": 101.5},
            client=client)

        assert pos.get("q1", "NIFTY24JUN24000CE").qty == 50
        assert client.modified, "the trigger still sells 150 of something we hold 50 of"
        assert client.modified[0][1]["orders"][0]["quantity"] == 50

    @pytest.mark.asyncio
    async def test_a_scale_in_arms_the_stop_for_the_total_holding(self):
        """`register` overwrites by symbol, so a second buy used to store only the new
        order's qty — the earlier lot kept no stop at all."""
        _held("q2", qty=35, gtt_id=555)
        client = _FakeClient()

        await protection.arm_position(
            client, "q2", symbol="NIFTY24JUN24000CE", exchange="NFO", token=777,
            qty=35, lot_size=35, entry_premium=100.0, stop_premium=80.0,
            order_id="O2", stop_mode="both")

        held = pos.get("q2", "NIFTY24JUN24000CE")
        assert held.qty == 70, "we hold two lots; the registry knew about one"
        assert client.gtts[0]["orders"][0]["quantity"] == 70
        # Both lots are accounted for by order id, so a fill postback for either one
        # corrects that lot instead of re-totalling to it alone.
        assert held.qty_by_order == {"O1": 35, "O2": 35}

    @pytest.mark.asyncio
    async def test_a_fill_on_the_new_lot_does_not_forget_the_old_one(self):
        """The scale-in and the postback have to agree, or the fix that added the lot is
        undone by the message confirming it."""
        _held("q4", qty=35, gtt_id=555)   # order_id "O1"
        client = _FakeClient()
        await protection.arm_position(
            client, "q4", symbol="NIFTY24JUN24000CE", exchange="NFO", token=777,
            qty=35, lot_size=35, entry_premium=100.0, stop_premium=80.0,
            order_id="O2", stop_mode="both")

        await monitor.on_order_update(
            "q4", {"tradingsymbol": "NIFTY24JUN24000CE", "status": "COMPLETE",
                   "order_id": "O2", "filled_quantity": 35, "average_price": 99.0},
            client=client)

        assert pos.get("q4", "NIFTY24JUN24000CE").qty == 70

    @pytest.mark.asyncio
    async def test_a_fill_postback_corrects_only_its_own_lot(self):
        pos.reset("q3")
        pos.register(pos.OpenPosition(
            uid="q3", symbol="NIFTY24JUN24000CE", exchange="NFO", token=777,
            qty=70, lot_size=35, entry_premium=100.0, stop_premium=80.0,
            order_id="O2", status=pos.PENDING, gtt_id=555,
            qty_by_order={"O1": 35, "O2": 35}))
        client = _FakeClient()

        await monitor.on_order_update(
            "q3", {"tradingsymbol": "NIFTY24JUN24000CE", "status": "COMPLETE",
                   "order_id": "O2", "filled_quantity": 18, "average_price": 99.0},
            client=client)

        held = pos.get("q3", "NIFTY24JUN24000CE")
        assert held.qty == 53, "the other lot was forgotten (35 + 18)"
        assert client.modified[0][1]["orders"][0]["quantity"] == 53


# ── 10. re-arming never adds a rival trigger ──────────────────────────────────

class TestReArmingNeverAddsASecondTrigger:
    @pytest.mark.asyncio
    async def test_an_unconfirmed_cancel_retargets_the_old_trigger(self):
        """Two resting SELLs against one long is the naked short this guard exists to
        prevent — and it only LOGGED the cancel outcome before arming another."""
        _held("s1", qty=35, gtt_id=555)
        client = _FakeClient(cancel_error="gateway timeout", gtt_status="active")

        armed = await protection.arm_position(
            client, "s1", symbol="NIFTY24JUN24000CE", exchange="NFO", token=777,
            qty=35, lot_size=35, entry_premium=100.0, stop_premium=80.0,
            order_id="O2", stop_mode="both")

        assert client.gtts == [], "a second trigger was armed for one position"
        assert client.modified and client.modified[0][0] == 555
        assert client.modified[0][1]["orders"][0]["quantity"] == 70
        assert armed.gtt_id == 555

    @pytest.mark.asyncio
    async def test_a_trigger_that_can_be_neither_cancelled_nor_moved_is_not_replaced(self):
        _held("s2", qty=35, gtt_id=555)
        client = _FakeClient(cancel_error="gateway timeout", gtt_status="active",
                             move_fails=True)

        armed = await protection.arm_position(
            client, "s2", symbol="NIFTY24JUN24000CE", exchange="NFO", token=777,
            qty=35, lot_size=35, entry_premium=100.0, stop_premium=80.0,
            order_id="O2", stop_mode="broker")

        assert client.gtts == []
        assert armed.stale_gtt is True
        assert armed.protected is False, "the board must not imply this stop is live"
        assert "EARLIER GTT" in armed.describe()


# ── 11. a stop that is not below the premium is not a stop ────────────────────

class TestAStaleStopIsNotArmed:
    @pytest.mark.asyncio
    async def test_a_stop_above_the_live_premium_is_refused_but_the_position_is_tracked(
            self, monkeypatch):
        """An ended row's trail is frozen where the signal died. Armed as a GTT it
        triggers on acceptance and market-sells the entry just made — while a position
        with no registry row would also miss the expiry square-off."""
        from app.services.exchanges.kite import accounts as kite_accounts
        from app.services.kite_engine import service as ksvc
        from app.services.kite_engine.scanner import scanner

        client = _FakeClient(ltp=200.0)   # premium has fallen through the 255 trail

        class _Acct:
            user_id, id, is_paper, connected = "z-user", 1, False, True

        monkeypatch.setattr(kite_accounts, "get_active", lambda uid: _Acct())
        monkeypatch.setattr(kite_accounts, "acquire_client", lambda acct: _async(client))
        # SafetySupervisor asks the same authority positionally, so the double
        # has to accept both spellings or admission fails closed on a TypeError.
        monkeypatch.setattr(ksvc.live_safety, "assert_safe_to_trade",
                            lambda *a, **kw: type("D", (), {"allowed": True, "code": "", "reason": ""})())
        monkeypatch.setattr(ksvc.live_safety, "check_idempotency", lambda key: None)
        monkeypatch.setattr(ksvc.live_safety, "record_idempotency", lambda key, oid: None)
        pos.reset("z-user")
        scanner.snapshot("z-user").rows = [
            TestManualOrdersAreProtected._board_row("BANKNIFTY26AUG57000CE", stop=255.0)]
        try:
            res = await ksvc.place_manual_order(
                "z-user", "BANKNIFTY26AUG57000CE", "BUY", 35, exchange="NFO")
        finally:
            scanner._users.pop("z-user", None)

        assert res["status"] == "ok" and res["protected"] is False
        assert "above the live premium" in res["protection"]
        assert client.gtts == [], "that GTT would have sold the entry immediately"
        held = pos.get("z-user", "BANKNIFTY26AUG57000CE")
        assert held is not None, "still tracked — the expiry square-off depends on it"
        assert held.stop_premium == 0.0


# ── 12. an exit that filled elsewhere must not leave a trigger behind ─────────

class TestNoOrphanedTriggerAfterAnOutsideExit:
    @pytest.mark.asyncio
    async def test_reconciling_a_broker_exit_fill_clears_the_trigger(self):
        """The exit may have come from a hand-placed SELL or the Kite web order book,
        not from the GTT. Whatever is left resting would sell an option we no longer own."""
        _held("o1", gtt_id=555).exit_order_id = "OTHER-9"  # broker-attributed exit
        client = _FakeClient()

        await monitor.on_order_update(
            "o1", {"tradingsymbol": "NIFTY24JUN24000CE", "status": "COMPLETE",
                   "order_id": "OTHER-9", "transaction_type": "SELL",
                   "filled_quantity": 50, "average_price": 90.0}, client=client)

        held = pos.get("o1", "NIFTY24JUN24000CE")
        assert held.status == pos.CLOSED
        assert client.cancelled == [555] and held.gtt_id == 0

    @pytest.mark.asyncio
    async def test_a_completed_exit_postback_cannot_resurrect_a_closed_position(self):
        """With no order_id the postback falls into the ENTRY branch, which flipped a
        CLOSED position back to OPEN at the exit price — and the next tick sold it again."""
        _held("o2", gtt_id=0)
        pos.close("o2", "NIFTY24JUN24000CE", reason="already exited")
        client = _FakeClient()

        await monitor.on_order_update(
            "o2", {"tradingsymbol": "NIFTY24JUN24000CE", "status": "COMPLETE",
                   "order_id": "", "filled_quantity": 50, "average_price": 90.0},
            client=client)

        assert pos.get("o2", "NIFTY24JUN24000CE").status == pos.CLOSED


# ── 13. a lost fill postback must not hide a position ────────────────────────

class TestPendingPositionsAreReconciled:
    @pytest.mark.asyncio
    async def test_a_missing_postback_is_recovered_from_the_order_book(self, monkeypatch):
        """PENDING is invisible to on_tick, the trail updater, the time stop AND the
        expiry square-off. A dropped WS message left a real position guarded by nothing
        but its GTT — and by nothing at all under stop_mode="monitor"."""
        from app.services.kite_engine import service as ksvc

        pos.reset("r9")
        pos.register(pos.OpenPosition(
            uid="r9", symbol="NIFTY24JUN24000CE", exchange="NFO", token=777,
            qty=50, lot_size=50, entry_premium=100.0, stop_premium=80.0,
            order_id="E1", status=pos.PENDING, gtt_id=555, opened_ms=1))
        client = _FakeClient(order_history=[
            {"status": "COMPLETE", "order_id": "E1", "filled_quantity": 50,
             "average_price": 101.25, "transaction_type": "BUY"}])

        await ksvc._reconcile_pending_positions(client, "r9")

        held = pos.get("r9", "NIFTY24JUN24000CE")
        assert held.status == pos.OPEN
        assert held.fill_price == pytest.approx(101.25)


# ── 14. "the broker did not say 404" is not evidence of anything ───────────────

class TestAMissingTriggerIsProvedNotGuessed:
    """The one honest gap left by the previous round. `stop_status` concluded ABSENT —
    the verdict that lets us place our own exit — from a 404 and nothing else. Kite
    does not promise a 404 for a trigger it no longer holds; a 400 `InputException` is
    just as likely. On that answer the probe returned UNVERIFIED, the price stop stood
    down, and the position sat open with NO exit at all — exactly the defect the round
    set out to close, now merely announced instead of silent.

    So a missing trigger is proved against two independent reads: the trigger list
    (nothing resting → nothing will fire later) and the day's order book (nothing
    filled or working → nothing fired already). ABSENT still needs positive evidence;
    it is just no longer hostage to one HTTP status code.
    """

    @pytest.mark.asyncio
    async def test_a_400_on_the_lookup_no_longer_strands_the_position(self):
        """The headline case: deleted in the Kite app, and Kite answers the probe 400."""
        p = _held("n1", stop=80.0)
        client = _FakeClient(cancel_error="trigger not found", gtt_status="error")

        sold = await monitor._exit_position(client, "n1", p, 79.0, price_stop_exit=True)

        assert sold is True, (
            "no trigger on the broker's list and no exit order in the book — nothing "
            "was ever going to exit this position, so standing down abandons it"
        )
        assert client.sells == [("NIFTY24JUN24000CE", "sell", 50)]
        assert client.calls[:4] == ["cancel", "get_gtt", "get_gtts", "get_orders"], (
            "the two confirming reads must both happen, and only after the cheap one fails"
        )
        # then the holdings check, the sell, and the post-sell orphan chase
        assert client.calls[4:] == ["positions", "sell"]  # orphan cleanup waits for confirmed fill

    @pytest.mark.asyncio
    async def test_a_filled_exit_in_the_order_book_still_blocks_our_sell(self):
        """Same 400, but the trigger fired and its SELL is COMPLETE. Selling here is the
        naked short. The order book is what tells the two apart."""
        p = _held("n2", stop=80.0)
        client = _FakeClient(
            cancel_error="already triggered", gtt_status="error",
            order_book=[{"tradingsymbol": "NIFTY24JUN24000CE", "transaction_type": "SELL",
                         "status": "COMPLETE", "order_id": "GTT-SELL-1"}])

        sold = await monitor._exit_position(client, "n2", p, 79.0, price_stop_exit=True)

        assert sold is False and client.sells == [], "sold on top of the broker's own SELL"
        assert pos.get("n2", "NIFTY24JUN24000CE").status == pos.OPEN

    @pytest.mark.asyncio
    async def test_a_rejected_exit_in_the_order_book_does_not_count(self):
        """The trigger fired, the exchange bounced the SELL (freeze quantity, circuit,
        margin). Trigger consumed, position unexited — and this is the case no GTT
        status can express."""
        p = _held("n3", stop=80.0)
        client = _FakeClient(
            cancel_error="already triggered", gtt_status="error",
            order_book=[{"tradingsymbol": "NIFTY24JUN24000CE", "transaction_type": "SELL",
                         "status": "REJECTED", "order_id": "GTT-SELL-2"}])

        sold = await monitor._exit_position(client, "n3", p, 79.0, price_stop_exit=True)

        assert sold is True, "a rejected SELL exits nothing — we are still long"
        assert client.sells == [("NIFTY24JUN24000CE", "sell", 50)]

    @pytest.mark.asyncio
    async def test_a_working_exit_order_blocks_our_sell(self):
        """OPEN, not COMPLETE: the broker's exit is live and will fill. Also a stand-down."""
        p = _held("n4", stop=80.0)
        client = _FakeClient(
            cancel_error="already triggered", gtt_status="error",
            order_book=[{"tradingsymbol": "NIFTY24JUN24000CE", "transaction_type": "SELL",
                         "status": "OPEN", "order_id": "GTT-SELL-3"}])

        sold = await monitor._exit_position(client, "n4", p, 79.0, price_stop_exit=True)

        assert sold is False and client.sells == []

    @pytest.mark.asyncio
    async def test_our_own_entry_buy_is_not_mistaken_for_an_exit(self):
        """The book also holds the BUY that opened this position. Reading that as "the
        broker is exiting us" would strand every long."""
        p = _held("n5", stop=80.0)
        client = _FakeClient(
            cancel_error="trigger not found", gtt_status="error",
            order_book=[{"tradingsymbol": "NIFTY24JUN24000CE", "transaction_type": "BUY",
                         "status": "COMPLETE", "order_id": "O1"}])

        sold = await monitor._exit_position(client, "n5", p, 79.0, price_stop_exit=True)

        assert sold is True and client.sells == [("NIFTY24JUN24000CE", "sell", 50)]

    @pytest.mark.asyncio
    async def test_a_sell_in_another_symbol_is_not_ours(self):
        p = _held("n6", stop=80.0)
        client = _FakeClient(
            cancel_error="trigger not found", gtt_status="error",
            order_book=[{"tradingsymbol": "BANKNIFTY24JUN52000CE",
                         "transaction_type": "SELL", "status": "COMPLETE"}])

        sold = await monitor._exit_position(client, "n6", p, 79.0, price_stop_exit=True)

        assert sold is True and client.sells == [("NIFTY24JUN24000CE", "sell", 50)]

    @pytest.mark.asyncio
    async def test_a_trigger_still_on_the_list_is_believed_over_the_failed_lookup(self):
        """The lookup erroring does not mean the trigger is gone. If the list still shows
        it resting, it is going to fire and we stand down."""
        p = _held("n7", stop=80.0)
        client = _FakeClient(cancel_error="gateway timeout", gtt_status="error",
                             gtt_book=[{"id": 555, "status": "active"}])

        sold = await monitor._exit_position(client, "n7", p, 79.0, price_stop_exit=True)

        assert sold is False and client.sells == []
        assert "get_orders" not in client.calls, "no need to read the book — it is resting"

    @pytest.mark.asyncio
    async def test_an_inert_status_on_the_list_needs_the_order_book_too(self):
        """Listed as cancelled is an inert status, so nothing will fire later — but the
        trigger may have fired BEFORE being cancelled, so the book is still consulted."""
        p = _held("n8", stop=80.0)
        client = _FakeClient(cancel_error="gateway timeout", gtt_status="error",
                             gtt_book=[{"id": 555, "status": "cancelled"}])

        sold = await monitor._exit_position(client, "n8", p, 79.0, price_stop_exit=True)

        assert sold is True and client.sells == [("NIFTY24JUN24000CE", "sell", 50)]

    @pytest.mark.asyncio
    async def test_an_unreadable_order_book_stands_the_price_exit_down(self):
        """Two reads are required, so failing the second one is UNVERIFIED — never a
        licence to sell. The user is told, loudly."""
        p = _held("n9", stop=80.0)
        client = _FakeClient(cancel_error="trigger not found", gtt_status="error",
                             book_error="orders")

        sold = await monitor._exit_position(client, "n9", p, 79.0, price_stop_exit=True)

        assert sold is False and client.sells == []
        assert pos.get("n9", "NIFTY24JUN24000CE").status == pos.OPEN

    @pytest.mark.asyncio
    async def test_an_unreadable_trigger_list_stands_the_price_exit_down(self):
        p = _held("n10", stop=80.0)
        client = _FakeClient(cancel_error="gateway timeout", gtt_status="error",
                             book_error="gtts")

        sold = await monitor._exit_position(client, "n10", p, 79.0, price_stop_exit=True)

        assert sold is False and client.sells == []

    @pytest.mark.asyncio
    async def test_an_unrecognised_status_is_not_read_as_absent(self):
        """A status this code does not know means the trigger EXISTS and its state is
        unreadable. Guessing ABSENT there is the mistake that sells twice; the trigger
        list is consulted instead."""
        p = _held("n11", stop=80.0)
        client = _FakeClient(cancel_error="gateway timeout", gtt_status="some_new_state")

        sold = await monitor._exit_position(client, "n11", p, 79.0, price_stop_exit=True)

        assert sold is False and client.sells == [], "sold on a status we cannot read"

    @pytest.mark.asyncio
    async def test_a_non_price_exit_is_still_performed_when_nothing_is_working(self):
        """The other half: with the trigger gone and the book clean, a red-count or
        expiry exit must go through as before."""
        p = _held("n12", stop=80.0)
        pos.update_health("n12", p.symbol, red_count=3, exit_mode="one_red")
        client = _FakeClient(cancel_error="trigger not found", gtt_status="error")

        out = await monitor.on_tick("n12", 777, 150.0, client=client)

        assert out == "NIFTY24JUN24000CE"
        assert client.sells == [("NIFTY24JUN24000CE", "sell", 50)]

    @pytest.mark.asyncio
    async def test_a_short_position_watches_the_buy_side(self):
        """A short's exit is a BUY. Looking for a SELL there would find our own entry
        and never find the exit."""
        assert await protective_stop._exit_order_is_working(
            _FakeClient(order_book=[{"tradingsymbol": "NIFTY24JUNFUT",
                                     "transaction_type": "BUY", "status": "COMPLETE"}]),
            "NIFTY24JUNFUT", "short") is True
        assert await protective_stop._exit_order_is_working(
            _FakeClient(order_book=[{"tradingsymbol": "NIFTY24JUNFUT",
                                     "transaction_type": "SELL", "status": "COMPLETE"}]),
            "NIFTY24JUNFUT", "short") is False

    @pytest.mark.asyncio
    async def test_without_a_symbol_a_missing_trigger_is_only_unverified(self):
        """The order book cannot be consulted for an unnamed position, so the honest
        answer is "may still be armed" — no caller may read ABSENT out of silence."""
        client = _FakeClient(gtt_status="missing")

        assert await protective_stop.stop_status(client, 555) == protective_stop.STOP_UNVERIFIED
        assert await protective_stop.stop_status(
            client, 555, tradingsymbol="NIFTY24JUN24000CE") == protective_stop.STOP_ABSENT


# ── 15. auto-exec must not open what it cannot exit ───────────────────────────

def _spot_row(underlying="RELIANCE", direction="long", *, current_reds=None, ts=1000):
    """A SPOT-source signal row: the leg carries no premium, so entry and stop have to
    be resolved from a live quote (the case that used to fail open)."""
    from app.engines.sterling_kite_engine.schemas import (
        AlignmentChip, EngineSignalRow, OptionLeg)
    opt = "CE" if direction == "long" else "PE"
    trend = 1 if direction == "long" else -1
    return EngineSignalRow(
        underlying=underlying, token=111, exchange="NFO",
        regime="BULL" if direction == "long" else "BEAR",
        # The ENTRY-bar chip: fully aligned WITH the trade. Counting these against a
        # position is the bug - for a bear signal all three are -1.
        alignment=AlignmentChip(fast=trend, mid=trend, slow=trend),
        direction=direction, option_type=opt,
        legs=[OptionLeg(moneyness="ATM", option_type=opt,
                        option_symbol=f"{underlying}25JUN3000{opt}",
                        strike=3000, expiry="2026-06-26", lot_size=250)],
        spot=3010.0, stop_loss=2950.0 if direction == "long" else 3070.0,
        score=85.0, timestamp_ms=ts, current_reds=current_reds)


class _QuotelessClient(CanonicalBrokerDouble):
    """A broker that cannot answer a quote - strike untraded today, or the call was
    rate-limited and swallowed. entry_premium and therefore stop_premium resolve to 0."""

    def __init__(self):
        self.placed: list = []

    async def place_order_option(self, sym, side, size, **kw):
        self.placed.append((sym, side, size, kw.get("stop_loss")))
        return {"order_id": "O-" + sym}

    async def place_gtt(self, **kw):
        return {"trigger_id": 4242}


class _QuotingClient(_QuotelessClient):
    async def get_margins(self, segment="equity"):
        return {"available":{"live_balance":5_000_000}}

    def __init__(self, ltp: float = 90.0):
        super().__init__()
        self.ltp = ltp

    async def get_ltp(self, keys):
        return {k: {"last_price": self.ltp} for k in keys}


class TestAutoExecNeverOpensAnUnprotectablePosition:
    """The engine placed a REAL market BUY when the premium quote came back empty, then
    armed nothing: place_stop refuses a trigger of 0, should_exit(0, ltp) is False on
    every tick, and _retranslated_stop cannot re-derive a level from an entry premium of
    0 - so the stop stayed 0 for the life of the trade. The terminal printed
    "[both stop+monitor]" over it, because the log reported the CONFIG rather than what
    was installed. Only the T-1 expiry square-off would ever close it."""

    @pytest.mark.asyncio
    async def test_no_resolvable_stop_means_no_order_at_all(self):
        from app.services.kite_engine import service, state
        state.reset("nx1")
        client = _QuotelessClient()

        cb = service._make_place_cb(client, "nx1")
        await cb(_spot_row(), None)

        assert client.placed == [], (
            "auto-exec is unattended - opening a position with no resolvable stop means "
            "no GTT, an inert monitor, and no way to ever acquire one"
        )
        kinds = [e.kind for e in state.activity("nx1")]
        assert "order_blocked" in kinds and "order_placed" not in kinds

    @pytest.mark.asyncio
    async def test_the_guard_does_not_block_a_resolvable_entry(self):
        """The abort must be narrow: a quote that answers still trades."""
        from app.services.kite_engine import service, state
        state.reset("nx2")
        pos.reset("nx2")
        client = _QuotingClient(ltp=90.0)

        cb = service._make_place_cb(client, "nx2")
        await cb(_spot_row(), None)

        assert len(client.placed) == 1
        sym, side, size, stop_loss = client.placed[0]
        assert side == "buy" and size >= 250 and size % 250 == 0
        assert stop_loss is not None and stop_loss > 0, (
            "the resolved premium stop must reach the broker, not None")

    @pytest.mark.asyncio
    async def test_the_log_reports_the_stop_installed_not_the_one_configured(self):
        from app.services.kite_engine import service, state
        state.reset("nx3")
        pos.reset("nx3")
        client = _QuotingClient(ltp=90.0)

        cb = service._make_place_cb(client, "nx3")
        await cb(_spot_row(), None)

        placed = [e for e in state.activity("nx3") if e.kind == "order_placed"]
        assert placed, "the entry should have gone through"
        line = placed[-1].message
        assert "broker GTT #4242" in line, (
            f"the log must name the protection actually armed, got: {line}")


# ── 16. the red counter is defined against the SIGNAL, not the premium side ───

class TestRedCountUsesTheSignalNotTheOptionSide:
    """Every option position is LONG in premium space, CE and PE alike. The old code fed
    that `direction` to the red counter, so for a bear signal - whose three SuperTrends
    are all -1 by definition at entry - it counted 3 of 3 against the position it had
    just opened. exit_mode "one_red" fires at 1, so the PE was market-sold on the first
    tick after the first post-entry scan, with the trend still perfectly in its favour.
    It also read the ENTRY-bar alignment chip, which never moves, and took whichever row
    matched the underlying first - the bull row, for a bear position."""

    @staticmethod
    def _open(uid, symbol, *, signal_direction, underlying="RELIANCE", reds=0):
        pos.reset(uid)
        p = pos.register(pos.OpenPosition(
            uid=uid, symbol=symbol, exchange="NFO", token=111, qty=250, lot_size=250,
            entry_premium=90.0, stop_premium=70.0, order_id="O1", status=pos.OPEN,
            direction="long", signal_direction=signal_direction,
            underlying=underlying, exit_mode="one_red", current_red_count=reds))
        return p

    @staticmethod
    def _snapshot(uid, rows):
        from app.services.kite_engine.scanner import scanner
        snap = scanner.snapshot(uid)
        snap.rows = list(rows)
        return snap

    @pytest.mark.asyncio
    async def test_a_bear_position_is_not_red_counted_out_the_moment_it_opens(self):
        from app.services.kite_engine import service, state
        state.reset("rc1")
        self._open("rc1", "RELIANCE25JUN3000PE", signal_direction="short")
        # The bear row's SuperTrends are all -1 (that IS the bear signal), and the live
        # count against a SHORT signal is 0 - nothing has turned against it yet.
        self._snapshot("rc1", [_spot_row(direction="short", current_reds=0)])

        await service._update_open_position_trails(_QuotingClient(), "rc1")

        held = pos.get("rc1", "RELIANCE25JUN3000PE")
        assert held.current_red_count == 0, (
            "counted the bear signal's own alignment as 3 reds against itself - the "
            "monitor would market-sell this on the next tick")

    @pytest.mark.asyncio
    async def test_a_real_reversal_still_counts(self):
        """The other half: when the trend genuinely turns, the counter must fire."""
        from app.services.kite_engine import service, state
        state.reset("rc2")
        self._open("rc2", "RELIANCE25JUN3000PE", signal_direction="short")
        self._snapshot("rc2", [_spot_row(direction="short", current_reds=2)])

        await service._update_open_position_trails(_QuotingClient(), "rc2")

        assert pos.get("rc2", "RELIANCE25JUN3000PE").current_red_count == 2

    @pytest.mark.asyncio
    async def test_the_counter_comes_from_the_row_of_the_same_direction(self):
        """scan_source="both" yields a bull row AND a bear row per underlying. The old
        loop took the first match on underlying and broke, so a bear position could read
        the bull row's counter."""
        from app.services.kite_engine import service, state
        state.reset("rc3")
        self._open("rc3", "RELIANCE25JUN3000PE", signal_direction="short")
        self._snapshot("rc3", [
            _spot_row(direction="long", current_reds=3),   # the WRONG row, listed first
            _spot_row(direction="short", current_reds=1),  # ours
        ])

        await service._update_open_position_trails(_QuotingClient(), "rc3")

        assert pos.get("rc3", "RELIANCE25JUN3000PE").current_red_count == 1

    @pytest.mark.asyncio
    async def test_a_bull_position_reads_the_bull_row(self):
        from app.services.kite_engine import service, state
        state.reset("rc4")
        self._open("rc4", "RELIANCE25JUN3000CE", signal_direction="long")
        self._snapshot("rc4", [
            _spot_row(direction="short", current_reds=3),
            _spot_row(direction="long", current_reds=0),
        ])

        await service._update_open_position_trails(_QuotingClient(), "rc4")

        assert pos.get("rc4", "RELIANCE25JUN3000CE").current_red_count == 0

    @pytest.mark.asyncio
    async def test_no_matching_row_leaves_the_last_count_alone(self):
        """A scan whose universe no longer covers this underlying must not silently
        reset the counter to 0 - that disarms the red exit for a position already in
        trouble."""
        from app.services.kite_engine import service, state
        state.reset("rc5")
        self._open("rc5", "RELIANCE25JUN3000PE", signal_direction="short", reds=2)
        self._snapshot("rc5", [_spot_row(underlying="INFY", direction="short",
                                         current_reds=0)])

        await service._update_open_position_trails(_QuotingClient(), "rc5")

        assert pos.get("rc5", "RELIANCE25JUN3000PE").current_red_count == 2

    @pytest.mark.asyncio
    async def test_the_signal_direction_is_recorded_at_entry(self):
        """It cannot be inferred from the CE/PE suffix: a derivatives-source row runs the
        SuperTrend on the contract's own premium series, so a PE bought there really is a
        LONG signal. It has to be stamped when the position is armed."""
        from app.services.kite_engine import service, state
        state.reset("rc6")
        pos.reset("rc6")
        client = _QuotingClient(ltp=90.0)

        cb = service._make_place_cb(client, "rc6")
        await cb(_spot_row(direction="short"), None)

        held = pos.get("rc6", "RELIANCE25JUN3000PE")
        assert held is not None, "the bear entry should have been registered"
        assert held.direction == "long", "buying a PE is long in premium space"
        assert held.signal_direction == "short", "…but the SIGNAL is short"


# ── 17. a trigger that was orphaned BEFORE we were watching ───────────────────

class _GttBookClient:
    def __init__(self, triggers, net=None, fail=False):
        self.triggers, self.net, self.fail = triggers, net or [], fail
        self.deleted: list = []

    async def get_gtts(self):
        if self.fail:
            raise RuntimeError("gtt list unreachable")
        return list(self.triggers)

    async def get_positions_raw(self):
        return {"net": list(self.net), "day": []}

    async def delete_gtt(self, tid):
        self.deleted.append(tid)
        return {"trigger_id": tid}


def _trigger(tid, sym, status="active"):
    return {"id": tid, "status": status,
            "condition": {"tradingsymbol": sym, "exchange": "NFO"}}


class TestOrphanedStopsAreReported:
    """Every path that CREATES an orphan is closed at the point it happens. This is the
    one nobody covers: a trigger already resting when the process started, or one left
    behind by a cancel that failed and only logged. Nothing ever looked again."""

    @pytest.mark.asyncio
    async def test_a_resting_trigger_with_no_holding_is_reported(self):
        from app.services.kite_engine import service, state
        state.reset("o1")
        pos.reset("o1")
        service._orphan_warned.clear()
        client = _GttBookClient([_trigger(901, "NIFTY24JUN24000CE")])

        await service._reconcile_orphan_stops(client, "o1")

        msgs = [e.message for e in state.activity("o1") if e.kind == "order_failed"]
        assert any("ORPHANED STOP" in m and "#901" in m for m in msgs), msgs

    @pytest.mark.asyncio
    async def test_it_never_cancels_anything(self):
        """It cannot tell our abandoned trigger from a stop the user placed by hand, and
        deleting theirs would remove the protection they were relying on."""
        from app.services.kite_engine import service, state
        state.reset("o2")
        pos.reset("o2")
        service._orphan_warned.clear()
        client = _GttBookClient([_trigger(902, "NIFTY24JUN24000CE")])

        await service._reconcile_orphan_stops(client, "o2")

        assert client.deleted == []

    @pytest.mark.asyncio
    async def test_a_trigger_over_a_real_holding_is_left_alone(self):
        """Whoever placed it, a stop on something the user actually holds is doing its
        job — warning about it would train them to ignore the alert."""
        from app.services.kite_engine import service, state
        state.reset("o3")
        pos.reset("o3")
        service._orphan_warned.clear()
        client = _GttBookClient(
            [_trigger(903, "NIFTY24JUN24000CE")],
            net=[{"tradingsymbol": "NIFTY24JUN24000CE", "quantity": 75}])

        await service._reconcile_orphan_stops(client, "o3")

        assert not [e for e in state.activity("o3") if e.kind == "order_failed"]

    @pytest.mark.asyncio
    async def test_our_own_open_position_is_not_an_orphan(self):
        """Registered but not yet reflected in the broker's net (a PENDING entry)."""
        from app.services.kite_engine import service, state
        state.reset("o4")
        pos.reset("o4")
        service._orphan_warned.clear()
        pos.register(pos.OpenPosition(
            uid="o4", symbol="NIFTY24JUN24000CE", exchange="NFO", qty=75,
            order_id="O1", status=pos.PENDING, gtt_id=904))
        client = _GttBookClient([_trigger(904, "NIFTY24JUN24000CE")])

        await service._reconcile_orphan_stops(client, "o4")

        assert not [e for e in state.activity("o4") if e.kind == "order_failed"]

    @pytest.mark.asyncio
    async def test_a_triggered_trigger_is_not_an_orphan(self):
        """It has already fired — it cannot sell anything again."""
        from app.services.kite_engine import service, state
        state.reset("o5")
        pos.reset("o5")
        service._orphan_warned.clear()
        client = _GttBookClient([_trigger(905, "NIFTY24JUN24000CE", status="triggered")])

        await service._reconcile_orphan_stops(client, "o5")

        assert not [e for e in state.activity("o5") if e.kind == "order_failed"]

    @pytest.mark.asyncio
    async def test_the_same_orphan_is_reported_once_not_every_scan(self):
        from app.services.kite_engine import service, state
        state.reset("o6")
        pos.reset("o6")
        service._orphan_warned.clear()
        client = _GttBookClient([_trigger(906, "NIFTY24JUN24000CE")])

        await service._reconcile_orphan_stops(client, "o6")
        await service._reconcile_orphan_stops(client, "o6")

        hits = [e for e in state.activity("o6") if "ORPHANED STOP" in e.message]
        assert len(hits) == 1

    @pytest.mark.asyncio
    async def test_an_unreadable_book_is_silent_not_noisy(self):
        """A network blip must not manufacture an alarm about triggers we never saw."""
        from app.services.kite_engine import service, state
        state.reset("o7")
        pos.reset("o7")
        service._orphan_warned.clear()

        await service._reconcile_orphan_stops(_GttBookClient([], fail=True), "o7")

        assert not [e for e in state.activity("o7") if e.kind == "order_failed"]


# ── 18. the red counter has to reach the path real positions take ─────────────

class TestSignalDirectionOnEveryEntryPath:
    """Stamping the signal direction only in the auto-exec branch repeated the mistake
    that started all of this: auto_execute is OFF, so hand-placed is the path essentially
    every real position takes. `LegPlan.direction` is hardcoded "long" (correct — a PE is
    long premium), and the manual arm passed only that, so a hand-bought PE went into the
    registry claiming a LONG signal and matched the BULL row's red count."""

    @pytest.mark.asyncio
    async def test_a_hand_placed_bear_leg_records_a_short_signal(self):
        p = pos.OpenPosition(uid="s1", symbol="NIFTY24JUN24000PE", exchange="NFO",
                             qty=50, direction="long", signal_direction="short")
        assert pos.signal_direction_of(p) == "short"

    def test_a_position_from_before_the_field_is_not_assumed_long(self):
        """Positions are persisted with asdict and rebuilt with OpenPosition(**d), so a
        row written before this field existed reloads without it. Defaulting those to
        "long" hands a surviving bear position the exact wrong-way count."""
        legacy = pos.OpenPosition(uid="s2", symbol="NIFTY24JUN24000PE",
                                  exchange="NFO", qty=50, direction="long")
        assert legacy.signal_direction == "", "empty must mean UNKNOWN, not long"
        assert pos.signal_direction_of(legacy) == "short", (
            "a legacy PE must not be counted against the bull row")

    def test_a_legacy_call_position_reads_long(self):
        legacy = pos.OpenPosition(uid="s3", symbol="NIFTY24JUN24000CE",
                                  exchange="NFO", qty=50, direction="long")
        assert pos.signal_direction_of(legacy) == "long"

    def test_a_recorded_direction_beats_the_suffix(self):
        """A derivatives-source row runs the SuperTrend on the contract's own premium
        series, so a PE bought there really IS a long signal. What was recorded at entry
        must win over the suffix guess."""
        p = pos.OpenPosition(uid="s4", symbol="NIFTY24JUN24000PE", exchange="NFO",
                             qty=50, direction="long", signal_direction="long")
        assert pos.signal_direction_of(p) == "long"

    def test_a_legacy_future_falls_back_to_its_own_direction(self):
        p = pos.OpenPosition(uid="s5", symbol="NIFTY24JUNFUT", exchange="NFO",
                             qty=50, direction="short")
        assert pos.signal_direction_of(p) == "short"

    def test_a_round_trip_through_persistence_keeps_the_signal(self):
        from dataclasses import asdict
        original = pos.OpenPosition(uid="s6", symbol="NIFTY24JUN24000PE", exchange="NFO",
                                    qty=50, direction="long", signal_direction="short")
        restored = pos.OpenPosition(**asdict(original))
        assert pos.signal_direction_of(restored) == "short"

    @pytest.mark.asyncio
    async def test_a_hand_placed_bear_position_is_not_red_counted_out(self):
        """End to end on the manual path: the plan carries the row's direction through
        arming, so the trail updater matches the bear row instead of the bull one."""
        from app.services.kite_engine import service, state
        from app.services.kite_engine.scanner import scanner as _scanner
        state.reset("s7")
        pos.reset("s7")

        plan = protection.LegPlan(
            symbol="RELIANCE25JUN3000PE", exchange="NFO", token=111, lot_size=250,
            entry_premium=90.0, stop_premium=70.0, target_premium=0.0,
            strike=3000.0, expiry="2026-06-26", underlying="RELIANCE",
            direction="long", signal_direction="short", source="spot",
            entry_spot=3010.0, entry_delta=-0.35, live=True)
        client = _QuotingClient(ltp=90.0)
        await service.arm_manual_option_buy(
            client, "s7", option_symbol="RELIANCE25JUN3000PE", exchange="NFO",
            quantity=250, order_id="M1", plan=plan)

        held = pos.get("s7", "RELIANCE25JUN3000PE")
        assert held is not None and held.signal_direction == "short"

        snap = _scanner.snapshot("s7")
        snap.rows = [_spot_row(direction="long", current_reds=3),
                     _spot_row(direction="short", current_reds=0)]
        await service._update_open_position_trails(client, "s7")

        assert pos.get("s7", "RELIANCE25JUN3000PE").current_red_count == 0, (
            "the hand-placed bear position read the BULL row and would have been sold")


# ── 19. matching a position to the row that actually describes it ─────────────

def _deriv_row(underlying="RELIANCE", option_type="CE", *, legs, current_reds=None):
    """A derivatives-source row: the SuperTrend ran on each CONTRACT's own premium, and
    _compile_rows groups every strike under one parent. Every such row is direction
    "long" — long premium, whatever the strike — on the same underlying string the spot
    rows use, which is why underlying+direction cannot tell them apart."""
    from app.engines.sterling_kite_engine.schemas import (
        AlignmentChip, EngineSignalRow, OptionLeg)
    return EngineSignalRow(
        underlying=underlying, token=111, exchange="NFO",
        regime="BULL" if option_type == "CE" else "BEAR",
        alignment=AlignmentChip(fast=1, mid=1, slow=1),
        direction="long", option_type=option_type, source="derivatives",
        legs=[OptionLeg(moneyness=m, option_type=option_type, option_symbol=sym,
                        strike=st, expiry="2026-06-26", lot_size=250, current_reds=cr)
              for (m, sym, st, cr) in legs],
        spot=0.0, stop_loss=0.0, score=85.0, timestamp_ms=1000,
        current_reds=current_reds)


class TestTheRowMustDescribeThisPosition:
    """Matching on (underlying, direction) alone is not enough. Every derivatives row is
    direction "long" on the same underlying as the spot rows, so a derivatives PE would
    match the spot BULL row and be sold on its count — C5 again through another door.
    And grouping collapses many contracts into one parent, so the parent's count belongs
    to whichever leg arrived first, not to the strike actually held."""

    @staticmethod
    def _pos(symbol, *, signal_direction, underlying="RELIANCE"):
        return pos.OpenPosition(uid="m", symbol=symbol, exchange="NFO", qty=250,
                                direction="long", signal_direction=signal_direction,
                                underlying=underlying, status=pos.OPEN)

    def test_a_derivatives_position_reads_its_own_leg_not_the_spot_row(self):
        from app.services.kite_engine import service
        p = self._pos("RELIANCE25JUN3000PE", signal_direction="long")
        rows = [
            _spot_row(direction="long", current_reds=3),   # the collapsing underlying
            _deriv_row(option_type="PE",
                       legs=[("ATM", "RELIANCE25JUN3000PE", 3000.0, 0)]),
        ]
        assert service._live_red_count(p, rows) == 0, (
            "a derivatives PE whose own premium trend is intact must not inherit the "
            "spot bull row's reds and be market-sold")

    def test_each_strike_reads_its_own_count_not_the_first_legs(self):
        from app.services.kite_engine import service
        rows = [_deriv_row(option_type="CE", legs=[
            ("ATM", "RELIANCE25JUN3000CE", 3000.0, 0),
            ("ITM1", "RELIANCE25JUN2900CE", 2900.0, 3),
        ], current_reds=0)]
        healthy = self._pos("RELIANCE25JUN3000CE", signal_direction="long")
        dying = self._pos("RELIANCE25JUN2900CE", signal_direction="long")

        assert service._live_red_count(healthy, rows) == 0
        assert service._live_red_count(dying, rows) == 3, (
            "the held strike had fully reversed but read the parent's (first leg's) 0")

    def test_a_spot_position_still_matches_by_underlying_and_direction(self):
        from app.services.kite_engine import service
        p = self._pos("RELIANCE25JUN3000PE", signal_direction="short")
        rows = [_spot_row(direction="long", current_reds=3),
                _spot_row(direction="short", current_reds=1)]
        assert service._live_red_count(p, rows) == 1

    def test_an_unknown_count_is_not_read_as_zero(self):
        """A row hydrated from a cache written before the field existed has None. Zero
        would mean "nothing against us" and overwrite a real count of 2 or 3, disarming
        the red exit one tick before it fired."""
        from app.services.kite_engine import service
        p = self._pos("RELIANCE25JUN3000CE", signal_direction="long")
        assert service._live_red_count(p, [_spot_row(direction="long")]) is None

    def test_an_unknown_leg_count_does_not_fall_back_to_the_underlying(self):
        from app.services.kite_engine import service
        p = self._pos("RELIANCE25JUN3000PE", signal_direction="long")
        rows = [_spot_row(direction="long", current_reds=3),
                _deriv_row(option_type="PE",
                           legs=[("ATM", "RELIANCE25JUN3000PE", 3000.0, None)])]
        assert service._live_red_count(p, rows) is None


class TestTheCounterOutlivesTheSignal:
    """A row exists only where there was an entry TRANSITION.

    So a position routinely outlives the row that opened it, and the counter used to
    stop being refreshed at exactly that moment — the count froze at its last value
    and the red-count exit quietly stopped working for the rest of the trade. The scan
    computes the regime for every instrument it evaluates regardless of whether a row
    comes out; the snapshot now keeps that reading.
    """

    class _Snap:
        def __init__(self, underlying_reds=None, contract_reds=None):
            self.underlying_reds = underlying_reds or {}
            self.contract_reds = contract_reds or {}

    @staticmethod
    def _pos(symbol, *, signal_direction, underlying="RELIANCE"):
        return pos.OpenPosition(uid="m", symbol=symbol, exchange="NFO", qty=250,
                                direction="long", signal_direction=signal_direction,
                                underlying=underlying, status=pos.OPEN)

    def test_a_spot_position_reads_the_scan_when_its_signal_has_ended(self):
        from app.services.kite_engine import service
        p = self._pos("RELIANCE25JUN3000CE", signal_direction="long")
        snap = self._Snap(underlying_reds={"RELIANCE": {"long": 2, "short": 1}})
        assert service._live_red_count(p, [], snap) == 2

    def test_the_scan_fallback_still_respects_the_signal_direction(self):
        """The trap this counter has fallen into three times. A bear position's reds
        are the SHORT count; reading the long count would market-sell a position whose
        own trend is perfectly intact."""
        from app.services.kite_engine import service
        p = self._pos("RELIANCE25JUN3000PE", signal_direction="short")
        snap = self._Snap(underlying_reds={"RELIANCE": {"long": 3, "short": 0}})
        assert service._live_red_count(p, [], snap) == 0

    def test_a_held_contract_reads_its_own_premium_series(self):
        """Keyed by the exact symbol held, so it cannot pick up another strike's count
        the way an underlying match would."""
        from app.services.kite_engine import service
        p = self._pos("RELIANCE25JUN2900CE", signal_direction="long")
        snap = self._Snap(underlying_reds={"RELIANCE": {"long": 0, "short": 0}},
                          contract_reds={"RELIANCE25JUN2900CE": 3})
        assert service._live_red_count(p, [], snap) == 3

    def test_a_matching_row_still_wins_over_the_scan_reading(self):
        """A row is scoped to the exact signal, not just the instrument, so it stays
        the more specific answer."""
        from app.services.kite_engine import service
        p = self._pos("RELIANCE25JUN3000PE", signal_direction="short")
        snap = self._Snap(underlying_reds={"RELIANCE": {"long": 0, "short": 3}})
        assert service._live_red_count(p, [_spot_row(direction="short", current_reds=1)],
                                       snap) == 1

    def test_an_instrument_the_scan_never_looked_at_is_still_unknown(self):
        """The genuinely unknowable case must stay None — a 0 here would report
        "nothing against us" and disarm the exit."""
        from app.services.kite_engine import service
        p = self._pos("RELIANCE25JUN3000CE", signal_direction="long")
        assert service._live_red_count(p, [], self._Snap(underlying_reds={"INFY": {"long": 2}})) is None

    def test_a_missing_direction_in_the_reading_is_unknown_not_zero(self):
        from app.services.kite_engine import service
        p = self._pos("RELIANCE25JUN3000PE", signal_direction="short")
        assert service._live_red_count(p, [], self._Snap(
            underlying_reds={"RELIANCE": {"long": 2}})) is None

    def test_no_snapshot_at_all_behaves_as_before(self):
        from app.services.kite_engine import service
        p = self._pos("RELIANCE25JUN3000CE", signal_direction="long")
        assert service._live_red_count(p, []) is None

    def test_no_row_at_all_is_unknown(self):
        from app.services.kite_engine import service
        p = self._pos("RELIANCE25JUN3000CE", signal_direction="long")
        assert service._live_red_count(p, []) is None

    @pytest.mark.asyncio
    async def test_an_unknown_count_leaves_the_stored_one_untouched(self):
        from app.services.kite_engine import service, state
        from app.services.kite_engine.scanner import scanner as _scanner
        state.reset("m2")
        pos.reset("m2")
        pos.register(pos.OpenPosition(
            uid="m2", symbol="RELIANCE25JUN3000CE", exchange="NFO", token=111, qty=250,
            entry_premium=90.0, stop_premium=70.0, order_id="O1", status=pos.OPEN,
            direction="long", signal_direction="long", underlying="RELIANCE",
            current_red_count=2))
        _scanner.snapshot("m2").rows = [_spot_row(direction="long")]  # count unknown

        await service._update_open_position_trails(_QuotingClient(), "m2")

        assert pos.get("m2", "RELIANCE25JUN3000CE").current_red_count == 2


# ── 20. a rollback must not empty the registry ────────────────────────────────

class TestThePositionStoreSurvivesAnUnknownField:
    """Positions round-trip through JSON as whole dicts. A payload written by a NEWER
    build carries fields an older one has never heard of, and OpenPosition(**d) raises on
    the first — where the blanket except discarded the WHOLE registry, leaving every live
    position unguarded and freeing the auto-open guard to re-enter slots already held."""

    def test_an_unknown_field_does_not_discard_every_position(self, monkeypatch):
        import json as _json
        from app.services.kite_engine import positions as _pos
        saved = _json.dumps([
            {"uid": "z1", "symbol": "NIFTY24JUN24000CE", "exchange": "NFO", "qty": 75,
             "status": _pos.OPEN, "a_field_from_the_future": "boom"},
            {"uid": "z1", "symbol": "NIFTY24JUN24100CE", "exchange": "NFO", "qty": 75,
             "status": _pos.OPEN},
        ])
        monkeypatch.setattr(_pos.db, "get_config", lambda k: saved)
        _pos._positions.pop("z1", None)

        loaded = _pos._load("z1")

        assert set(loaded) == {"NIFTY24JUN24000CE", "NIFTY24JUN24100CE"}, (
            "one unreadable row must not take the others with it")

    def test_a_corrupt_row_is_skipped_not_fatal(self, monkeypatch):
        import json as _json
        from app.services.kite_engine import positions as _pos
        saved = _json.dumps([
            "not a dict at all",
            {"uid": "z2", "symbol": "NIFTY24JUN24000CE", "exchange": "NFO", "qty": 75},
        ])
        monkeypatch.setattr(_pos.db, "get_config", lambda k: saved)
        _pos._positions.pop("z2", None)

        assert set(_pos._load("z2")) == {"NIFTY24JUN24000CE"}

    def test_a_scale_in_keeps_the_recorded_signal_direction(self):
        """Re-registering is how a scale-in updates the row, so a second buy arriving
        without a signal direction must not blank the first entry's."""
        pos.reset("z3")
        pos.register(pos.OpenPosition(
            uid="z3", symbol="NIFTY24JUN24000PE", exchange="NFO", qty=75,
            order_id="O1", status=pos.OPEN, direction="long", signal_direction="short"))
        assert pos.signal_direction_of(pos.get("z3", "NIFTY24JUN24000PE")) == "short"


# ── 21. a counter that stopped counting must not look like a working one ──────

class TestAFrozenRedCounterIsAnnounced:
    @pytest.mark.asyncio
    async def test_a_stale_counter_is_reported(self):
        import time as _t
        from app.services.kite_engine import service, state
        from app.services.kite_engine.scanner import scanner as _scanner
        state.reset("f1")
        pos.reset("f1")
        service._red_stale_warned.clear()
        old = int(_t.time() * 1000) - service._RED_STALE_MS - 1
        pos.register(pos.OpenPosition(
            uid="f1", symbol="RELIANCE25JUN3000PE", exchange="NFO", token=111, qty=250,
            entry_premium=90.0, stop_premium=70.0, order_id="O1", status=pos.OPEN,
            direction="long", signal_direction="short", underlying="RELIANCE",
            current_red_count=1, red_count_ms=old, opened_ms=old))
        _scanner.snapshot("f1").rows = []   # nothing to refresh from

        await service._update_open_position_trails(_QuotingClient(), "f1")

        msgs = [e.message for e in state.activity("f1") if e.kind == "order_failed"]
        assert any("NOT being maintained" in m for m in msgs), msgs

    @pytest.mark.asyncio
    async def test_a_fresh_counter_is_not_reported(self):
        import time as _t
        from app.services.kite_engine import service, state
        from app.services.kite_engine.scanner import scanner as _scanner
        state.reset("f2")
        pos.reset("f2")
        service._red_stale_warned.clear()
        pos.register(pos.OpenPosition(
            uid="f2", symbol="RELIANCE25JUN3000PE", exchange="NFO", token=111, qty=250,
            entry_premium=90.0, stop_premium=70.0, order_id="O1", status=pos.OPEN,
            direction="long", signal_direction="short", underlying="RELIANCE",
            red_count_ms=int(_t.time() * 1000)))
        _scanner.snapshot("f2").rows = []

        await service._update_open_position_trails(_QuotingClient(), "f2")

        assert not [e for e in state.activity("f2") if e.kind == "order_failed"]

    @pytest.mark.asyncio
    async def test_the_warning_is_not_repeated_every_scan(self):
        import time as _t
        from app.services.kite_engine import service, state
        from app.services.kite_engine.scanner import scanner as _scanner
        state.reset("f3")
        pos.reset("f3")
        service._red_stale_warned.clear()
        old = int(_t.time() * 1000) - service._RED_STALE_MS - 1
        pos.register(pos.OpenPosition(
            uid="f3", symbol="RELIANCE25JUN3000PE", exchange="NFO", token=111, qty=250,
            entry_premium=90.0, stop_premium=70.0, order_id="O1", status=pos.OPEN,
            direction="long", signal_direction="short", underlying="RELIANCE",
            red_count_ms=old, opened_ms=old))
        _scanner.snapshot("f3").rows = []

        await service._update_open_position_trails(_QuotingClient(), "f3")
        await service._update_open_position_trails(_QuotingClient(), "f3")

        hits = [e for e in state.activity("f3") if "NOT being maintained" in e.message]
        assert len(hits) == 1

    def test_update_health_stamps_the_refresh_time(self):
        pos.reset("f4")
        pos.register(pos.OpenPosition(uid="f4", symbol="X25JUN100CE", exchange="NFO",
                                      qty=1, status=pos.OPEN))
        assert pos.get("f4", "X25JUN100CE").red_count_ms == 0
        pos.update_health("f4", "X25JUN100CE", 2, "one_red")
        assert pos.get("f4", "X25JUN100CE").red_count_ms > 0


# ── 22. every exit_mode's threshold must actually be reachable ────────────────

class TestEveryExitModeThresholdIsReachable:
    """A verifier claimed the red exit was unreachable under three_red /
    three_red_signal. The counter is bounded at 3 by construction (three SuperTrend
    lines), so a threshold above 3 would be unsatisfiable — check every mode."""

    def test_no_mode_needs_more_reds_than_exist(self):
        from app.engines.common.exit_counter import get_exit_threshold
        for mode in ("one_red", "two_red", "three_red", "three_red_signal"):
            thresh = get_exit_threshold(mode)
            assert 1 <= thresh <= 3, f"{mode} threshold {thresh} is outside 0..3 reds"

    def test_three_reds_is_a_value_the_scan_can_actually_produce(self):
        """Not just in range — reachable. All three lines against a long is exactly
        what red_line_count returns 3 for."""
        import numpy as np
        from app.engines.sterling_kite_engine.regime import RegimeSeries
        r = RegimeSeries.__new__(RegimeSeries)
        r.t_fast = np.array([-1]); r.t_mid = np.array([-1]); r.t_slow = np.array([-1])
        assert r.red_line_count("long", 0) == 3
        assert r.red_line_count("short", 0) == 0


# ── 23. auto-execute is gated on the positions already held ───────────────────

class TestAutoExecPreflight:
    def test_a_clean_book_does_not_block(self):
        from app.services.kite_engine import service
        pos.reset("g1")
        assert service.autoexec_preflight("g1") == []

    def test_an_unprotected_open_position_blocks(self):
        from app.services.kite_engine import service
        pos.reset("g2")
        pos.register(pos.OpenPosition(
            uid="g2", symbol="NIFTY24JUN24000CE", exchange="NFO", qty=75,
            status=pos.OPEN, stop_premium=0.0))
        reasons = service.autoexec_preflight("g2")
        assert any("NO stop" in r for r in reasons), reasons

    def test_a_position_stuck_pending_blocks(self):
        import time as _t
        from app.services.kite_engine import service
        pos.reset("g3")
        pos.register(pos.OpenPosition(
            uid="g3", symbol="NIFTY24JUN24000CE", exchange="NFO", qty=75,
            status=pos.PENDING, stop_premium=80.0,
            opened_ms=int(_t.time() * 1000) - service._PENDING_GRACE_MS - 1))
        assert any("PENDING" in r for r in service.autoexec_preflight("g3"))

    def test_a_frozen_counter_blocks(self):
        import time as _t
        from app.services.kite_engine import service
        pos.reset("g4")
        old = int(_t.time() * 1000) - service._RED_STALE_MS - 1
        pos.register(pos.OpenPosition(
            uid="g4", symbol="NIFTY24JUN24000CE", exchange="NFO", qty=75,
            status=pos.OPEN, stop_premium=80.0, opened_ms=old, red_count_ms=old))
        assert any("stopped updating" in r for r in service.autoexec_preflight("g4"))

    def test_a_healthy_position_does_not_block(self):
        import time as _t
        from app.services.kite_engine import service
        pos.reset("g5")
        now = int(_t.time() * 1000)
        pos.register(pos.OpenPosition(
            uid="g5", symbol="NIFTY24JUN24000CE", exchange="NFO", qty=75,
            status=pos.OPEN, stop_premium=80.0, opened_ms=now, red_count_ms=now))
        assert service.autoexec_preflight("g5") == []


# ── 24. an expired session must not read as evidence ──────────────────────────

class TestASessionlessClientCannotManufactureEvidence:
    """`stop_status` treats an empty GTT list plus an empty order book as POSITIVE
    evidence that nothing is protecting a position, and sells on it. Every read on the
    live client used to answer "[]" when the access_token was missing or expired — so a
    dropped session fabricated exactly that evidence and would have gone naked short on
    top of a live broker stop. The reads raise now; the probe reports UNVERIFIED."""

    @pytest.mark.asyncio
    async def test_an_expired_session_yields_unverified_not_absent(self):
        from app.services.exchanges.kite.client import KiteClient
        live = KiteClient(api_key="ak", api_secret="s", access_token="", is_paper=False)

        verdict = await protective_stop.stop_status(
            live, 555, tradingsymbol="NIFTY24JUN24000CE")

        assert verdict == protective_stop.STOP_UNVERIFIED, (
            "an expired session answered '[]' and was read as proof that nothing "
            "protects this position")

    @pytest.mark.asyncio
    async def test_a_price_stop_stands_down_on_an_expired_session(self):
        from app.services.exchanges.kite.client import KiteClient

        class _Sessionless(KiteClient):
            def __init__(self, **kw):
                super().__init__(**kw)
                self.attempted: list = []

            async def place_order_option(self, sym, side, size, **kw):
                # Record rather than raise: _exit_position catches exceptions from the
                # sell and reports False either way, so raising here would make this
                # test pass without proving anything.
                self.attempted.append((sym, side, size))
                return {"order_id": "X"}

            async def delete_gtt(self, tid):
                raise RuntimeError("session expired")

        p = _held("sess1", stop=80.0)
        client = _Sessionless(api_key="ak", access_token="", is_paper=False)

        sold = await monitor._exit_position(client, "sess1", p, 79.0, price_stop_exit=True)

        assert client.attempted == [], "sold on top of a possibly-live broker stop"
        assert sold is False
        assert pos.get("sess1", "NIFTY24JUN24000CE").status == pos.OPEN

    @pytest.mark.asyncio
    async def test_paper_mode_is_unaffected(self):
        """Paper has no session by design — its stubs stay stubs."""
        from app.services.exchanges.kite.client import KiteClient
        paper = KiteClient(api_key="ak", access_token="", is_paper=True)
        assert await paper.get_gtts() == []
        assert await paper.get_orders() == []


# ── 25. the risk cap has to stop the ORDER, not just the sizer ────────────────

class _CapitalClient(_QuotingClient):
    """A broker that answers a margins call, so risk sizing has a real budget to
    compare against. Without one, available_fo_capital returns 0 and the sizer
    deliberately keeps the 1-lot floor rather than halt every entry on an API blip."""

    def __init__(self, ltp: float = 90.0, capital: float = 50_000.0):
        super().__init__(ltp=ltp)
        self.capital = capital

    async def get_margins(self, segment="equity"):
        return {"available": {"live_balance": self.capital}}


class TestTheRiskCapBlocksTheEntryNotJustTheSize:
    """`size_position` refuses when one lot already breaks risk_pct, returning
    blocked=True and qty 0. That is only half the guarantee: `qty` still holds the
    un-risk-sized default from the signal args, so a caller that read only `qty > 0`
    would place the very order the cap just refused. The sizer is well covered; the
    CALLER path was not, and that is exactly where the two previous rounds of this
    work went wrong — right module, wrong path."""

    @staticmethod
    def _cfg(uid, **over):
        from app.services.kite_engine import state
        cfg = state.get_config(uid).model_dump()
        cfg.update({"risk_sizing": True, "risk_pct": 0.1, "max_lots": 10}, **{})
        cfg.update(over)
        from app.engines.sterling_kite_engine.schemas import EngineConfigModel
        return state.set_config(uid, EngineConfigModel(**cfg))

    @pytest.mark.asyncio
    async def test_an_over_budget_signal_places_no_order(self):
        from app.services.kite_engine import service, state
        state.reset("cap1")
        pos.reset("cap1")
        # ₹50,000 × 0.1% = ₹50 of budget against a 250-lot option risking far more.
        self._cfg("cap1")
        client = _CapitalClient(ltp=90.0, capital=50_000.0)

        await service._make_place_cb(client, "cap1")(_spot_row(), None)

        assert client.placed == [], (
            "the sizer refused every size that honours risk_pct, and the entry went in "
            "anyway at the un-risk-sized default")
        kinds = [e.kind for e in state.activity("cap1")]
        assert "order_blocked" in kinds and "order_placed" not in kinds

    @pytest.mark.asyncio
    async def test_the_block_says_what_to_change(self):
        from app.services.kite_engine import service, state
        state.reset("cap2")
        pos.reset("cap2")
        self._cfg("cap2")

        await service._make_place_cb(_CapitalClient(), "cap2")(_spot_row(), None)

        msg = next(e.message for e in state.activity("cap2") if e.kind == "order_blocked")
        assert "broker inputs" in msg and "risk budget" in msg, (
            f"a refusal the user cannot act on is a dead end: {msg}")

    @pytest.mark.asyncio
    async def test_allow_min_lot_over_risk_takes_the_smallest_lot(self):
        """The escape hatch is a deliberate choice, not the default — and it must
        actually reach the order."""
        from app.services.kite_engine import service, state
        state.reset("cap3")
        pos.reset("cap3")
        self._cfg("cap3", allow_min_lot_over_risk=True)

        client = _CapitalClient(ltp=90.0, capital=50_000.0)
        await service._make_place_cb(client, "cap3")(_spot_row(), None)

        assert len(client.placed) == 1, "the opt-in did not reach the order path"
        assert client.placed[0][2] == 250, "should take exactly one lot"

    @pytest.mark.asyncio
    async def test_a_comfortable_budget_still_trades(self):
        """The cap must not become a blanket halt."""
        from app.services.kite_engine import service, state
        state.reset("cap4")
        pos.reset("cap4")
        self._cfg("cap4", risk_pct=50.0)

        client = _CapitalClient(ltp=90.0, capital=50_000_000.0)
        await service._make_place_cb(client, "cap4")(_spot_row(), None)

        assert len(client.placed) == 1

    @pytest.mark.asyncio
    async def test_unknown_capital_blocks_entry(self):
        """A failed margins call returns 0. Reading that as "over budget" would turn a
        transient broker outage into a silent stop on all automatic entries — which
        looks exactly like the engine being broken."""
        from app.services.kite_engine import service, state
        state.reset("cap5")
        pos.reset("cap5")
        self._cfg("cap5")

        class _NoMargins(_QuotingClient):
            async def get_margins(self, segment="equity"):
                raise RuntimeError("margins unavailable")

        client = _NoMargins(ltp=90.0)
        await service._make_place_cb(client, "cap5")(_spot_row(), None)

        assert not client.placed, "unknown capital cannot authorize an entry"


# ── 26. the premium translation needs UNDERLYING-domain inputs ────────────────

class TestTheTranslationIsFedTheUnderlyingNotThePremium:
    """`_resolve_premium_stop` carries a SuperTrend level on the UNDERLYING's chart into
    a premium via delta, so both its `spot` and `trail_level` must be underlying-domain
    numbers. A derivatives-source row has neither: its ST ran on the contract's own
    premium series, so `spot` is that premium (place_cb sees the raw row, before
    grouping zeroes it) and `stop_loss` is a premium level too.

    Normal derivatives rows never reach this — their leg carries premium_spot/premium_sl
    so entry_px and stop_px are already set. A leg that arrived without them (a legacy
    cached row) did fall through, and priced a ₹90 premium against a ₹3000 strike: the
    vol solve fails, delta collapses to the ±0.5 fallback, and the resulting invented
    number became the broker's trigger."""

    @staticmethod
    def _deriv_row_without_premium_stop():
        from app.engines.sterling_kite_engine.schemas import (
            AlignmentChip, EngineSignalRow, OptionLeg)
        return EngineSignalRow(
            underlying="RELIANCE", token=111, exchange="NFO", regime="BULL",
            alignment=AlignmentChip(fast=1, mid=1, slow=1),
            direction="long", option_type="CE", source="derivatives",
            legs=[OptionLeg(moneyness="ATM", option_type="CE",
                            option_symbol="RELIANCE25JUN3000CE", strike=3000.0,
                            expiry="2026-06-26", lot_size=250)],  # no premium_spot/_sl
            spot=90.0,                 # the CONTRACT's premium, not an index level
            underlying_spot=3010.0,    # the underlying, captured separately
            stop_loss=70.0,            # a PREMIUM stop, not an underlying trail
            score=85.0, timestamp_ms=1000)

    @pytest.mark.asyncio
    async def test_a_derivatives_row_with_no_leg_premium_stop_is_refused(self):
        from app.services.kite_engine import service, state
        state.reset("dm1")
        pos.reset("dm1")
        client = _QuotingClient(ltp=90.0)

        await service._make_place_cb(client, "dm1")(
            self._deriv_row_without_premium_stop(), None)

        assert client.placed == [], (
            "opened a position whose broker trigger was derived by pricing a premium "
            "as if it were the underlying")
        kinds = [e.kind for e in state.activity("dm1")]
        assert "order_blocked" in kinds and "order_placed" not in kinds

    @pytest.mark.asyncio
    async def test_a_spot_row_still_translates_and_trades(self):
        """The guard must be narrow: a spot-source row's levels ARE underlying-domain,
        which is the case the translation exists for."""
        from app.services.kite_engine import service, state
        state.reset("dm2")
        pos.reset("dm2")
        client = _QuotingClient(ltp=90.0)

        await service._make_place_cb(client, "dm2")(_spot_row(), None)

        assert len(client.placed) == 1
        _sym, _side, _size, stop_loss = client.placed[0]
        assert stop_loss is not None and stop_loss > 0

    @pytest.mark.asyncio
    async def test_the_translation_is_given_the_underlying_spot(self):
        """Pins the input itself, so the board and auto-exec cannot drift apart on
        which number they call 'spot'."""
        from app.services.kite_engine import service, state
        state.reset("dm3")
        pos.reset("dm3")
        seen = {}

        async def _spy(client, **kw):
            seen.update(kw)
            return 90.0, 70.0, 0.5

        row = _spot_row()
        row.underlying_spot = 3010.0
        row.spot = 3010.0
        import app.services.kite_engine.service as svc
        orig = svc._resolve_premium_stop
        svc._resolve_premium_stop = _spy
        try:
            await service._make_place_cb(_QuotingClient(), "dm3")(row, None)
        finally:
            svc._resolve_premium_stop = orig

        assert seen.get("spot") == 3010.0, (
            f"the translation was handed {seen.get('spot')} as the underlying level")


# ── the other end of the trade: an exit we were never told about ──────────────

class TestClosedPositionsAreReconciled:
    """`_reconcile_pending_positions` recovers an ENTRY whose postback was lost. This
    is the mirror at the exit, and it is the more dangerous half.

    An exit fills at Zerodha — a GTT firing, a square-off in the Kite app, or the exit
    the monitor deliberately stood down for when it could not confirm a cancel — and
    we hear about it only through an order postback. Miss that message and the registry
    still believes we hold the position. The board lies, the auto-open slot stays
    blocked forever, and the expiry square-off, the time stop and the tick monitor
    will each place a SELL for something the account no longer owns: a naked short.
    """

    @staticmethod
    def _open(uid, *, qty=50, gtt_id=555, guard="NIFTY24JUN24000CE"):
        pos.reset(uid)
        return pos.register(pos.OpenPosition(
            uid=uid, symbol="NIFTY24JUN24000CE", exchange="NFO", token=777,
            qty=qty, lot_size=50, entry_premium=100.0, fill_price=100.0,
            stop_premium=80.0, order_id="E1", status=pos.OPEN, gtt_id=gtt_id,
            guard_key=guard))

    @staticmethod
    def _net(qty, *, sell_price=0.0):
        """Kite keeps a squared-off position in `net` with quantity 0 for the rest of
        the day — that row, not its absence, is what says it closed."""
        return {"net": [{"tradingsymbol": "NIFTY24JUN24000CE", "exchange": "NFO",
                         "quantity": qty, "sell_price": sell_price}]}

    @pytest.mark.asyncio
    async def test_a_position_the_broker_no_longer_holds_is_closed(self, monkeypatch):
        from app.services.kite_engine import service as ksvc

        self._open("c1")
        client = _FakeClient(net_positions=self._net(0, sell_price=142.5))
        cleared: list = []
        monkeypatch.setattr(ksvc.state, "clear_auto_open",
                            lambda uid, key: cleared.append(key))

        await ksvc._reconcile_closed_positions(client, "c1")

        held = pos.get("c1", "NIFTY24JUN24000CE")
        assert held.status == pos.CLOSED
        assert "reconciled closed at broker" in held.exit_reason
        assert cleared == ["NIFTY24JUN24000CE"], "the auto-open slot stayed blocked"
        assert client.cancelled == [555], "our trigger was left resting over nothing"

    @pytest.mark.asyncio
    async def test_it_never_places_an_order(self, monkeypatch):
        """Reconciliation repairs bookkeeping. Selling on the strength of a positions
        read — which can be stale or wrong — is exactly what must not happen here."""
        from app.services.kite_engine import service as ksvc

        self._open("c2")
        client = _FakeClient(net_positions=self._net(0, sell_price=142.5))
        monkeypatch.setattr(ksvc.state, "clear_auto_open", lambda uid, key: None)

        await ksvc._reconcile_closed_positions(client, "c2")

        assert client.sells == []

    @pytest.mark.asyncio
    async def test_broker_day_average_cannot_establish_position_pnl(self, monkeypatch):
        from app.services.kite_engine import service as ksvc

        self._open("c3")
        client = _FakeClient(net_positions=self._net(0, sell_price=142.5))
        booked: list = []
        monkeypatch.setattr(ksvc.state, "clear_auto_open", lambda uid, key: None)
        monkeypatch.setattr(ksvc.state, "record_realized_pnl",
                            lambda uid, amount: booked.append(amount))

        await ksvc._reconcile_closed_positions(client, "c3")

        # (142.5 − 100.0) × 50
        assert booked == []
        assert any(p.pnl_reconciliation_required for p in pos._load("c3").values())

    @pytest.mark.asyncio
    async def test_no_exit_price_means_no_fabricated_pnl(self, monkeypatch):
        """A wrong realized number feeds the daily-loss breaker. Closing the position
        is the urgent part; guessing what it closed at is not."""
        from app.services.kite_engine import service as ksvc

        self._open("c4")
        client = _FakeClient(net_positions=self._net(0))  # no sell_price
        booked: list = []
        monkeypatch.setattr(ksvc.state, "clear_auto_open", lambda uid, key: None)
        monkeypatch.setattr(ksvc.state, "record_realized_pnl",
                            lambda uid, amount: booked.append(amount))

        await ksvc._reconcile_closed_positions(client, "c4")

        assert pos.get("c4", "NIFTY24JUN24000CE").status == pos.CLOSED
        assert booked == []

    @pytest.mark.asyncio
    async def test_a_still_held_position_is_left_alone(self, monkeypatch):
        from app.services.kite_engine import service as ksvc

        self._open("c5")
        client = _FakeClient(net_positions=self._net(50))
        monkeypatch.setattr(ksvc.state, "clear_auto_open", lambda uid, key: None)

        await ksvc._reconcile_closed_positions(client, "c5")

        assert pos.get("c5", "NIFTY24JUN24000CE").status == pos.OPEN
        assert client.cancelled == []

    @pytest.mark.asyncio
    async def test_a_symbol_absent_from_the_book_is_not_evidence_of_a_close(self, monkeypatch):
        """A carry-over position, or simply a symbol the day's book does not list, must
        not be closed on absence — only a present row with quantity 0 proves an exit."""
        from app.services.kite_engine import service as ksvc

        self._open("c6")
        client = _FakeClient(net_positions={"net": []})
        monkeypatch.setattr(ksvc.state, "clear_auto_open", lambda uid, key: None)

        await ksvc._reconcile_closed_positions(client, "c6")

        assert pos.get("c6", "NIFTY24JUN24000CE").status == pos.OPEN

    @pytest.mark.asyncio
    async def test_a_failed_positions_read_changes_nothing(self, monkeypatch):
        from app.services.kite_engine import service as ksvc

        self._open("c7")
        client = _FakeClient(book_error="positions")
        monkeypatch.setattr(ksvc.state, "clear_auto_open", lambda uid, key: None)

        await ksvc._reconcile_closed_positions(client, "c7")

        assert pos.get("c7", "NIFTY24JUN24000CE").status == pos.OPEN

    @pytest.mark.asyncio
    async def test_a_partial_exit_elsewhere_resizes_rather_than_closes(self, monkeypatch):
        """Overselling on the next exit is the failure being prevented: the trail and
        the GTT must be sized to what is actually held."""
        from app.services.kite_engine import service as ksvc

        self._open("c8", qty=150)
        client = _FakeClient(net_positions=self._net(50))
        monkeypatch.setattr(ksvc.state, "clear_auto_open", lambda uid, key: None)

        await ksvc._reconcile_closed_positions(client, "c8")

        held = pos.get("c8", "NIFTY24JUN24000CE")
        assert (held.status, held.qty) == (pos.OPEN, 50)

    @pytest.mark.asyncio
    async def test_a_pending_entry_is_left_to_its_own_reconciler(self, monkeypatch):
        """A PENDING entry legitimately has no holding yet — closing it on that basis
        would delete a position that is about to exist."""
        from app.services.kite_engine import service as ksvc

        p = self._open("c9")
        pos.register(p.__class__(**{**p.__dict__, "status": pos.PENDING}))
        client = _FakeClient(net_positions=self._net(0, sell_price=142.5))
        monkeypatch.setattr(ksvc.state, "clear_auto_open", lambda uid, key: None)

        await ksvc._reconcile_closed_positions(client, "c9")

        assert pos.get("c9", "NIFTY24JUN24000CE").status == pos.PENDING


# ── the last guard: never sell what we do not hold ────────────────────────────

class TestTheExitChecksWhatItIsSelling:
    """Everything above reasons about the broker's TRIGGER. None of it notices a
    position closed with no trigger involved at all — squared off in the Kite app, or
    an exit whose postback was lost before the next scan's reconcile pass. On the next
    tick through the stop the monitor would SELL a position the account no longer owns,
    which opens a naked short. Asking what we hold is the only guard that depends on
    neither a postback nor the GTT bookkeeping.
    """

    @staticmethod
    def _net(qty, symbol="NIFTY24JUN24000CE"):
        return {"net": [{"tradingsymbol": symbol, "exchange": "NFO", "quantity": qty}]}

    @pytest.mark.asyncio
    async def test_a_position_closed_elsewhere_is_reconciled_not_sold(self):
        _held("h1", gtt_id=0)
        client = _FakeClient(net_positions=self._net(0))

        out = await monitor.on_tick("h1", 777, 79.0, client=client)

        assert out is None
        assert client.sells == [], "sold a position the account no longer held"
        held = pos.get("h1", "NIFTY24JUN24000CE")
        assert held.status == pos.CLOSED
        assert "holds none" in held.exit_reason

    @pytest.mark.asyncio
    async def test_a_partial_exit_elsewhere_clamps_the_sell(self):
        """Selling the registry's larger figure would short the difference."""
        _held("h2", gtt_id=0, qty=150)
        client = _FakeClient(net_positions=self._net(50))

        await monitor.on_tick("h2", 777, 79.0, client=client)

        assert client.sells == [("NIFTY24JUN24000CE", "sell", 50)]

    @pytest.mark.asyncio
    async def test_a_symbol_absent_from_the_book_still_exits(self):
        """Absence is not evidence — a position carried from a previous day has no row
        in today's book. Refusing to exit on that would strand a real position."""
        _held("h3", gtt_id=0)
        client = _FakeClient(net_positions={"net": []})

        out = await monitor.on_tick("h3", 777, 79.0, client=client)

        assert out == "NIFTY24JUN24000CE"
        assert client.sells == [("NIFTY24JUN24000CE", "sell", 50)]

    @pytest.mark.asyncio
    async def test_an_unreachable_portfolio_still_exits(self):
        """Fail OPEN. A stop that will not fire because a read timed out is a worse
        outcome than the double-sell this check is guarding against."""
        _held("h4", gtt_id=0)
        client = _FakeClient(book_error="positions")

        out = await monitor.on_tick("h4", 777, 79.0, client=client)

        assert out == "NIFTY24JUN24000CE"
        assert client.sells == [("NIFTY24JUN24000CE", "sell", 50)]

    @pytest.mark.asyncio
    async def test_the_portfolio_is_read_once_across_a_burst_of_ticks(self):
        """The exit path is tick-driven; a collapsing premium delivers many ticks a
        second. One portfolio read per tick would add latency to a stop and earn a
        rate limit."""
        _held("h5", gtt_id=0, stop=80.0)
        client = _FakeClient(net_positions=self._net(50))

        for _ in range(5):
            await monitor.on_tick("h5", 777, 85.0, client=client)  # above the stop
        # not a breach, so no probe at all yet
        assert client.calls.count("positions") == 0

        await monitor.on_tick("h5", 777, 79.0, client=client)
        assert client.calls.count("positions") == 1

    @pytest.mark.asyncio
    async def test_our_own_sell_invalidates_the_cached_read(self):
        _held("h6", gtt_id=0)
        client = _FakeClient(net_positions=self._net(50))

        await monitor.on_tick("h6", 777, 79.0, client=client)

        assert monitor._holdings_probe.get("h6") is None, (
            "a cached portfolio that predates our own SELL would misreport the next check"
        )


# ── leads closed out from the 2026-08-04 audit ────────────────────────────────

class TestAFillLandingMidExitIsNotBookedTwice:
    """Audit lead 10. Every step before the SELL awaits — the GTT cancel, the status
    probe, the holdings read — and `on_order_update` does not take the `_exiting`
    claim. So an exit fill can land mid-flight, close the position and book its PnL,
    while this coroutine carries on with a `p` snapshot from before the await: a second
    SELL, and the same loss booked twice, which trips the INR daily-loss breaker at
    half the configured limit."""

    @pytest.mark.asyncio
    async def test_a_close_during_the_awaits_stops_the_sell(self, monkeypatch):
        booked: list = []
        from app.services.kite_engine import state as kstate
        monkeypatch.setattr(kstate, "record_realized_pnl",
                            lambda uid, amount: booked.append(amount))

        p = _held("mid1", gtt_id=555, stop=80.0)

        class _RacingClient(_FakeClient):
            """The broker's own exit fill arrives while we are cancelling its trigger."""

            async def delete_gtt(self, tid):
                p.exit_order_id = "GTT-FILL"  # discovered protective child order
                await monitor.on_order_update(
                    "mid1", {"tradingsymbol": p.symbol, "status": "COMPLETE",
                             "transaction_type": "SELL", "order_id": "GTT-FILL",
                             "average_price": 79.0, "filled_quantity":50}, client=self)
                return await super().delete_gtt(tid)

        client = _RacingClient()

        out = await monitor.on_tick("mid1", 777, 79.0, client=client)

        assert out is None
        assert client.sells == [], "second SELL placed after the position had closed"
        assert len(booked) == 1, f"realized PnL booked {len(booked)} times, expected once"
        assert pos.get("mid1", p.symbol).status == pos.CLOSED


@pytest.fixture(autouse=True)
def _valid_entry_data_for_execution_plumbing(monkeypatch):
    """Historical row fixtures isolate execution; session gates tested separately."""
    from app.services.kite_engine import service as execution
    monkeypatch.setattr(execution, "entry_data_block_reason", lambda *a, **kw: "")

from tests.engines.sterling_kite_engine.execution_fixtures import confirm_exit
