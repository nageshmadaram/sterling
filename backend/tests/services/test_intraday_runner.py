"""The live path: gates, sizing, protection, the exit claim, and reconcile.

These are the cases that only bite with real money — a double-click that
becomes a double entry, an exit order placed twice for one position, a stop
that was never actually rested at the broker, a restart that re-protects lots
Zerodha no longer holds.
"""
from __future__ import annotations

import pytest

from app.engines.intraday import IntradayConfig
from app.engines.intraday.position import ContractRef, IntradayPosition
from app.services import intraday as svc
from app.services import intraday_positions as store
from app.services import intraday_runner as runner


class FakeClient:
    """Enough Kite to exercise the runner, and a record of what it was told."""

    def __init__(self, premium=100.0, spread=None, reject=False, no_order_id=False,
                 book=None, raise_on_order=False):
        self.premium = premium
        self.spread = spread
        self.reject = reject
        self.no_order_id = no_order_id
        self.raise_on_order = raise_on_order
        self.orders: list[dict] = []
        self.gtts: list[dict] = []
        self.cancelled: list[int] = []
        self.moved: list[dict] = []
        self._book = book if book is not None else []
        self.candles: list = [{"time": 1_757_000_000, "open": 1.0, "high": 1.0,
                               "low": 1.0, "close": 1.0, "volume": 1.0}]

    async def get_quote(self, keys):
        depth = {}
        if self.spread is not None:
            half = self.premium * self.spread / 200.0
            depth = {"buy": [{"price": self.premium - half}],
                     "sell": [{"price": self.premium + half}]}
        return {k: {"last_price": self.premium, "oi": 500000, "volume": 200000,
                    "depth": depth} for k in keys}

    async def place_order(self, symbol, side, qty, **kw):
        if self.raise_on_order:
            raise RuntimeError("broker said no")
        self.orders.append({"symbol": symbol, "side": side, "qty": qty, **kw})
        return {} if self.no_order_id else {"order_id": f"O{len(self.orders)}"}

    async def get_order_history(self, order_id):
        status = "REJECTED" if self.reject else "COMPLETE"
        return [{"status": status, "average_price": self.premium}]

    async def place_gtt(self, **kw):
        self.gtts.append(kw)
        return {"trigger_id": 900 + len(self.gtts)}

    async def modify_gtt(self, trigger_id, **kw):
        self.moved.append({"trigger_id": trigger_id, **kw})
        return {"trigger_id": trigger_id}

    async def delete_gtt(self, trigger_id):
        self.cancelled.append(int(trigger_id))
        return {"trigger_id": trigger_id}

    async def get_positions(self):
        return {"net": self._book}

    async def get_candles(self, instrument, resolution, limit=200):
        return self.candles

    async def search_instruments(self, query, exchange, limit=5):
        if exchange != "NFO":
            return []
        return [{"tradingsymbol": query, "name": "NIFTY", "instrument_token": 1234,
                 "instrument_type": "CE", "strike": 24800.0, "expiry": "2026-09-17",
                 "lot_size": 75, "tick_size": 0.05}]


@pytest.fixture
def live(tmp_path, monkeypatch):
    monkeypatch.setenv("STERLING_DB_PATH", str(tmp_path / "t.db"))
    from app.services import db
    monkeypatch.setattr(db, "_DB_PATH", str(tmp_path / "t.db"), raising=False)
    db.init()
    store.reset()
    svc._state.clear()
    runner._notes.clear()
    runner._subscribed.clear()

    client = FakeClient()
    # The real subscribe is kept so one test can exercise it; everything else
    # wants it inert, because a ticker subscription is not what those are about.
    client.real_subscribe = runner._subscribe
    monkeypatch.setattr(runner, "_client", _const(client))
    monkeypatch.setattr(runner, "_subscribe", _noop)
    monkeypatch.setattr(runner, "_entries_allowed", lambda cfg: True)
    monkeypatch.setattr(runner, "_is_market_open", lambda cfg: True)
    monkeypatch.setattr(runner, "is_paper", lambda uid: False)
    monkeypatch.setattr(runner, "_safety", lambda uid, key: (True, ""))
    monkeypatch.setattr(runner, "_replay_owns_the_board", lambda: False)
    yield client
    store.reset()
    svc._state.clear()


def _const(value):
    async def _f(*a, **kw):
        return value
    return _f


async def _noop(*a, **kw):
    return None


def armed_row(**over) -> dict:
    row = {
        "signal_id": "pivot_break:NIFTY:1757000000000",
        "strategy": "pivot_break", "strategy_name": "Pivot Break",
        "symbol": "NIFTY", "state": "armed", "blockers": [], "spot": 24800.0,
        "timeframe": "5m",
        "contract": {"symbol": "NIFTY26SEP24800CE", "strike": 24800.0,
                     "option_type": "CE", "expiry": "2026-09-17", "dte": 5,
                     "lot_size": 75, "token": 1234, "exchange": "NFO",
                     "tick_size": 0.05},
        "quote": {"premium": 100.0, "spread_pct": 0.5, "blockers": []},
        "signal": {"strategy": "pivot_break", "symbol": "NIFTY",
                   "direction": "BULLISH", "opt_type": "CE",
                   "timestamp_ms": 1757000000000, "entry": 24800.0,
                   "stop": 24780.0, "target": 24840.0, "target2": 24860.0,
                   "risk": 20.0, "rr": 2.0, "strength": "STRONG",
                   "origin": "R1 + EMA9", "reasons": [], "metrics": {}},
        "metrics": {}, "generated_at_ms": 1757000000000,
    }
    row.update(over)
    return row


def put_armed(uid="u1", **over) -> str:
    row = armed_row(**over)
    svc.status(uid).signals[row["signal_id"]] = row
    return row["signal_id"]


def held(uid="u1", **over) -> IntradayPosition:
    base = dict(
        strategy="pivot_break", signal_id="s1", underlying="NIFTY",
        contract=ContractRef("NIFTY26SEP24800CE", "NFO", 1234, "CE", 24800.0,
                             "2026-09-17", 75, 0.05),
        thesis="BULLISH", spot_entry=24800.0, spot_stop=24780.0,
        entry=100.0, fill_price=100.0, stop=70.0, initial_stop=70.0, target=160.0,
        quantity=75, lots=1, peak=100.0, status="open", entry_day=runner._today(),
        gtt_id=901,
    )
    base.update(over)
    p = IntradayPosition(**base)
    store.put(uid, p)
    return p


# ------------------------------------------------------------------- entry

@pytest.mark.asyncio
class TestArm:
    async def test_it_buys_sizes_and_rests_a_stop(self, live):
        svc.set_config({"sizing_mode": "LOTS", "lots": 2, "max_lots": 5}, "u1")
        res = await runner.arm("u1", put_armed())
        assert res["ok"], res
        assert live.orders[0]["side"] == "buy"
        assert live.orders[0]["qty"] == 150            # 2 lots × 75
        # The stop is rested at the broker, not merely held in memory.
        assert live.gtts and res["gtt_id"]
        pos = store.get("u1", "NIFTY26SEP24800CE")
        assert pos.status == "open" and pos.side == "long"
        # The spot stop converted by the contract's own solved delta, which is
        # nearer than the 30% fallback — so the SAFER of the two is taken.
        assert 70.0 < pos.stop < 100.0
        # And the target keeps the strategy's 1:2, whatever the stop worked out at.
        assert pos.target == pytest.approx(
            pos.effective_entry + 2.0 * (pos.effective_entry - pos.stop), abs=0.05)

    async def test_the_premium_target_keeps_the_strategy_s_ratio(self, live):
        svc.set_config({"sizing_mode": "LOTS", "lots": 1}, "u1")
        row = armed_row()
        row["signal"]["rr"] = 3.0
        svc.status("u1").signals[row["signal_id"]] = row
        await runner.arm("u1", row["signal_id"])
        pos = store.get("u1", "NIFTY26SEP24800CE")
        risk = pos.effective_entry - pos.stop
        assert pos.target == pytest.approx(pos.effective_entry + 3.0 * risk, abs=0.05)

    async def test_without_a_solvable_delta_it_falls_back_to_the_percentage(self, live):
        """A premium below intrinsic, an untraded contract, a passed expiry. The
        fallback is a fixed fraction rather than a guessed delta, because a
        guessed delta that is too high puts the stop below zero."""
        svc.set_config({"sizing_mode": "LOTS", "lots": 1,
                        "premium_stop_pct": 30.0}, "u1")
        row = armed_row()
        row["contract"]["expiry"] = ""       # nothing to solve against
        svc.status("u1").signals[row["signal_id"]] = row
        await runner.arm("u1", row["signal_id"])
        assert store.get("u1", "NIFTY26SEP24800CE").stop == pytest.approx(70.0)

    async def test_risk_sizing_blocks_rather_than_squeezing_in_one_lot(self, live):
        # 30 points of premium risk × 75 = Rs 2,250 a lot, against a Rs 500 budget.
        svc.set_config({"sizing_mode": "RISK_PCT", "risk_per_trade_pct": 0.5,
                        "capital_inr": 100000.0}, "u1")
        res = await runner.arm("u1", put_armed())
        assert res["ok"] is False
        assert "budget" in res["message"]
        assert not live.orders

    async def test_it_refuses_a_signal_the_scan_did_not_produce(self, live):
        res = await runner.arm("u1", "made:up:1")
        assert res["ok"] is False and "no armed signal" in res["message"]

    async def test_a_second_click_cannot_double_the_position(self, live):
        svc.set_config({"sizing_mode": "LOTS", "lots": 1}, "u1")
        sid = put_armed()
        assert (await runner.arm("u1", sid))["ok"]
        # The id is consumed AND the holding check refuses it.
        again = await runner.arm("u1", sid)
        assert again["ok"] is False
        assert len(live.orders) == 1

    async def test_a_wide_spread_refuses_the_entry(self, live):
        live.spread = 12.0
        svc.set_config({"sizing_mode": "LOTS", "lots": 1, "max_spread_pct": 2.0}, "u1")
        res = await runner.arm("u1", put_armed())
        assert res["ok"] is False and "spread" in res["message"]
        assert not live.orders

    async def test_a_rejected_order_is_not_recorded_as_a_position(self, live):
        live.reject = True
        svc.set_config({"sizing_mode": "LOTS", "lots": 1}, "u1")
        res = await runner.arm("u1", put_armed())
        assert res["ok"] is False and "rejected" in res["message"]
        assert store.get("u1", "NIFTY26SEP24800CE").status == "rejected"
        assert not store.open_positions("u1")

    async def test_a_broker_with_no_order_id_is_a_failure_not_a_silent_fill(self, live):
        live.no_order_id = True
        svc.set_config({"sizing_mode": "LOTS", "lots": 1}, "u1")
        res = await runner.arm("u1", put_armed())
        assert res["ok"] is False and "order id" in res["message"]

    async def test_the_safety_gate_is_honoured(self, live, monkeypatch):
        monkeypatch.setattr(runner, "_safety",
                            lambda uid, key: (False, "Kill switch active"))
        svc.set_config({"sizing_mode": "LOTS", "lots": 1}, "u1")
        res = await runner.arm("u1", put_armed())
        assert res["ok"] is False and "Kill switch" in res["message"]
        assert not live.orders

    async def test_monitor_mode_rests_nothing_at_the_broker(self, live):
        svc.set_config({"sizing_mode": "LOTS", "lots": 1, "stop_mode": "monitor"}, "u1")
        res = await runner.arm("u1", put_armed())
        assert res["ok"] and res["gtt_id"] == 0
        assert not live.gtts


# ------------------------------------------------------------------- gating

class TestEntryBlocker:
    def test_the_position_cap_refuses_the_next_one(self, live):
        cfg = IntradayConfig(max_concurrent_positions=1).validate()
        held()
        assert "cap is 1" in runner.entry_blocker("u1", cfg, "OTHER26SEP1CE")

    def test_holding_the_same_contract_refuses_it(self, live):
        held()
        assert "already holding" in runner.entry_blocker(
            "u1", IntradayConfig(), "NIFTY26SEP24800CE")

    def test_the_daily_trade_cap_refuses_it(self, live):
        cfg = IntradayConfig(max_new_trades_per_day=2).validate()
        rec = store.load_record("u1").roll(runner._today())
        rec.record(10.0)
        rec.record(-5.0)
        store.save_record("u1", rec)
        assert "cap is 2" in runner.entry_blocker("u1", cfg, "X")

    def test_this_engine_s_own_daily_loss_limit_halts_it(self, live):
        cfg = IntradayConfig(daily_loss_limit_inr=5000.0).validate()
        rec = store.load_record("u1").roll(runner._today())
        rec.record(-6000.0)
        store.save_record("u1", rec)
        assert "down Rs" in runner.entry_blocker("u1", cfg, "X")

    def test_a_disabled_engine_refuses_everything(self, live):
        assert "switched off" in runner.entry_blocker(
            "u1", IntradayConfig(enabled=False), "X")


class TestSizing:
    def test_consecutive_losses_halve_the_cap(self, live):
        cfg = IntradayConfig(sizing_mode="LOTS", lots=8, max_lots=8,
                             descale_after_losses=2).validate()
        rec = store.load_record("u1").roll(runner._today())
        rec.record(-100.0)
        rec.record(-100.0)
        store.save_record("u1", rec)
        lots, qty, why = runner._size(cfg, premium=100.0, stop=70.0,
                                      lot_size=75, uid="u1")
        assert lots == 4 and qty == 300 and "de-scaled" in why

    def test_a_win_clears_the_de_scale(self, live):
        cfg = IntradayConfig(sizing_mode="LOTS", lots=8, max_lots=8,
                             descale_after_losses=2).validate()
        rec = store.load_record("u1").roll(runner._today())
        rec.record(-100.0)
        rec.record(-100.0)
        rec.record(500.0)
        store.save_record("u1", rec)
        lots, _, _ = runner._size(cfg, premium=100.0, stop=70.0, lot_size=75, uid="u1")
        assert lots == 8


# -------------------------------------------------------------------- exits

@pytest.mark.asyncio
class TestTicks:
    async def test_the_trail_moves_the_stop_and_the_broker_with_it(self, live):
        svc.set_config({"premium_trail_pct": 25.0, "trail_activate_r": 1.0}, "u1")
        held()
        await runner.on_ticks("u1", [{"instrument_token": 1234, "last_price": 200.0}])
        pos = store.get("u1", "NIFTY26SEP24800CE")
        assert pos.stop == pytest.approx(150.0)
        assert pos.breakeven_done is True
        # And the resting order was moved, not just the number in memory.
        assert live.moved, "the broker stop was never moved"

    async def test_a_stop_breach_exits_and_cancels_the_resting_order(self, live):
        held()
        await runner.on_ticks("u1", [{"instrument_token": 1234, "last_price": 65.0}])
        pos = store.get("u1", "NIFTY26SEP24800CE")
        assert pos.status == "closed" and pos.exit_reason == "stop"
        assert live.cancelled == [901]
        assert live.orders[-1]["side"] == "sell"
        assert store.load_record("u1").trades == 1

    async def test_the_target_closes_it(self, live):
        held()
        await runner.on_ticks("u1", [{"instrument_token": 1234, "last_price": 165.0}])
        assert store.get("u1", "NIFTY26SEP24800CE").exit_reason == "target"

    async def test_a_failed_exit_puts_the_stop_back(self, live):
        """The protection was cancelled to make room for the exit. If the exit
        does not go out, an unprotected position is the worst outcome."""
        live.raise_on_order = True
        held()
        await runner.on_ticks("u1", [{"instrument_token": 1234, "last_price": 65.0}])
        pos = store.get("u1", "NIFTY26SEP24800CE")
        assert pos.is_open and pos.exiting is False
        assert pos.gtt_id, "the position was left with no broker stop"

    async def test_a_position_already_exiting_is_not_sold_twice(self, live):
        held(exiting=True)
        await runner.on_ticks("u1", [{"instrument_token": 1234, "last_price": 65.0}])
        assert not [o for o in live.orders if o["side"] == "sell"]

    async def test_no_tick_for_a_contract_leaves_it_alone(self, live):
        held()
        await runner.on_ticks("u1", [{"instrument_token": 999, "last_price": 1.0}])
        assert store.get("u1", "NIFTY26SEP24800CE").is_open


@pytest.mark.asyncio
class TestManualExits:
    async def test_square_off_closes_everything(self, live):
        held()
        held(contract=ContractRef("BANKNIFTY26SEP1CE", "NFO", 77, "CE",
                                  1.0, "2026-09-17", 15, 0.05), gtt_id=902)
        res = await runner.square_off_all("u1")
        assert len(res["closed"]) == 2
        assert not store.open_positions("u1")

    async def test_exit_one_names_what_it_could_not_find(self, live):
        res = await runner.exit_one("u1", "NOTHELD")
        assert res["ok"] is False and "not holding" in res["message"]


@pytest.mark.asyncio
class TestReconcile:
    async def test_a_position_the_broker_does_not_have_is_closed(self, live):
        """Re-protecting it would rest a SELL against lots that are not there."""
        held()
        live._book = []
        res = await runner.reconcile("u1")
        assert res["vanished"] == 1 and res["gone"] == ["NIFTY26SEP24800CE"]
        assert not store.open_positions("u1")

    async def test_a_position_the_broker_has_is_kept_and_re_protected(self, live):
        held(gtt_id=0)
        live._book = [{"tradingsymbol": "NIFTY26SEP24800CE", "quantity": 75}]
        res = await runner.reconcile("u1")
        assert res["restored"] == 1 and res["reprotected"] == 1
        assert store.get("u1", "NIFTY26SEP24800CE").gtt_id

    async def test_a_broker_that_cannot_be_read_changes_nothing(self, live, monkeypatch):
        held()

        async def _boom():
            raise RuntimeError("network")
        monkeypatch.setattr(live, "get_positions", _boom)
        res = await runner.reconcile("u1")
        assert "error" in res
        assert store.open_positions("u1"), "a read failure must not close positions"


@pytest.mark.asyncio
class TestAdopt:
    async def test_adopting_protects_what_it_adopts(self, live):
        res = await runner.adopt("u1", "NIFTY26SEP24800CE", 75, 120.0)
        assert res["ok"] and res["gtt_id"]
        pos = store.get("u1", "NIFTY26SEP24800CE")
        assert pos.status == "open" and pos.stop == pytest.approx(84.0)

    async def test_it_will_not_adopt_what_it_already_manages(self, live):
        held()
        res = await runner.adopt("u1", "NIFTY26SEP24800CE", 75, 120.0)
        assert res["ok"] is False and "already managing" in res["message"]


class TestAutoExecute:
    def test_auto_needs_both_switches(self, live, monkeypatch):
        from app.services.kite_engine import state as engine_state
        monkeypatch.setattr(engine_state, "get_config",
                            lambda uid=None: type("C", (), {"auto_execute": True})())
        svc.set_config({"auto_execute": False}, "u1")
        assert runner.auto_execute("u1") is False
        svc.set_config({"auto_execute": True}, "u1")
        assert runner.auto_execute("u1") is True

    def test_the_shared_switch_alone_is_not_enough(self, live, monkeypatch):
        from app.services.kite_engine import state as engine_state
        monkeypatch.setattr(engine_state, "get_config",
                            lambda uid=None: type("C", (), {"auto_execute": False})())
        svc.set_config({"auto_execute": True}, "u1")
        assert runner.auto_execute("u1") is False


@pytest.mark.asyncio
class TestRuleExits:
    """The exit that is the STRATEGY's, not the stop's.

    The ribbon strategy is held until the opposite full cross. Nothing else in
    the live path evaluates that, so if this is not wired the trail is the only
    exit and a different strategy is being traded than the one on the board.
    """

    async def test_a_dead_ribbon_thesis_closes_the_position(self, live, monkeypatch):
        held(strategy="ma_ribbon", underlying_token=256265)
        monkeypatch.setattr(
            "app.engines.intraday.thesis_broken",
            lambda *a, **kw: (True, "the 55 crossed back through the whole ribbon"))
        monkeypatch.setattr("app.services.intraday._drop_forming",
                            lambda rows, cfg: rows)
        assert await runner.check_rules("u1") == 1
        pos = store.get("u1", "NIFTY26SEP24800CE")
        assert pos.status == "closed" and "ribbon" in pos.exit_reason

    async def test_a_live_thesis_is_left_alone(self, live, monkeypatch):
        held(strategy="ma_ribbon", underlying_token=256265)
        monkeypatch.setattr("app.engines.intraday.thesis_broken",
                            lambda *a, **kw: (False, ""))
        monkeypatch.setattr("app.services.intraday._drop_forming",
                            lambda rows, cfg: rows)
        assert await runner.check_rules("u1") == 0
        assert store.get("u1", "NIFTY26SEP24800CE").is_open

    async def test_an_adopted_position_has_no_rule_to_test(self, live):
        """It was not this engine's setup, so there is no thesis to declare dead.
        Its price stop still protects it."""
        held(strategy="adopted", underlying_token=256265)
        assert await runner.check_rules("u1") == 0
        assert store.get("u1", "NIFTY26SEP24800CE").is_open

    async def test_a_position_with_no_underlying_series_is_left_to_its_stop(self, live):
        held(strategy="ma_ribbon", underlying_token=0)
        assert await runner.check_rules("u1") == 0
        assert store.get("u1", "NIFTY26SEP24800CE").is_open


@pytest.mark.asyncio
class TestTheRunnerLeg:
    """1:2 then 1:3, which is what the board advertises.

    Before this existed the first target closed the whole position and the
    runner never ran — a UI claiming backend behaviour the backend did not
    honour, which is the failure this codebase keeps having to fix.
    """

    async def test_the_first_target_banks_half_and_lets_the_rest_run(self, live):
        held(quantity=150, lots=2, target=160.0, target2=190.0)
        await runner.on_ticks("u1", [{"instrument_token": 1234, "last_price": 165.0}])
        pos = store.get("u1", "NIFTY26SEP24800CE")
        assert pos.is_open, "the runner was closed instead of run"
        assert pos.quantity == 75 and pos.scaled_qty == 75
        assert pos.target1_done and pos.breakeven_done
        assert pos.stop >= pos.effective_entry
        # And the banked leg is counted once, when it was sold.
        assert store.load_record("u1").trades == 1

    async def test_the_runner_closes_at_the_second_target(self, live):
        held(quantity=150, lots=2, target=160.0, target2=190.0)
        await runner.on_ticks("u1", [{"instrument_token": 1234, "last_price": 165.0}])
        await runner.on_ticks("u1", [{"instrument_token": 1234, "last_price": 195.0}])
        pos = store.get("u1", "NIFTY26SEP24800CE")
        assert pos.status == "closed" and pos.exit_reason == "target2"

    async def test_protection_is_re_rested_for_what_is_LEFT(self, live):
        """The old GTT was for the full size. Leaving it would rest a sell
        against lots that are no longer there."""
        held(quantity=150, lots=2, target=160.0, target2=190.0)
        await runner.on_ticks("u1", [{"instrument_token": 1234, "last_price": 165.0}])
        assert live.cancelled == [901]
        assert live.gtts, "no stop was re-rested after banking half"
        assert live.gtts[-1]["orders"][0]["quantity"] == 75

    async def test_a_single_lot_cannot_be_halved_so_it_just_rides_the_trail(self, live):
        held(quantity=75, lots=1, target=160.0, target2=190.0)
        await runner.on_ticks("u1", [{"instrument_token": 1234, "last_price": 165.0}])
        pos = store.get("u1", "NIFTY26SEP24800CE")
        assert pos.is_open and pos.quantity == 75 and pos.scaled_qty == 0
        assert pos.target1_done and pos.stop >= pos.effective_entry
        assert not [o for o in live.orders if o["side"] == "sell"]

    async def test_a_strategy_with_one_objective_still_closes_at_it(self, live):
        held(quantity=150, lots=2, target=160.0, target2=0.0, strategy="ma_ribbon")
        await runner.on_ticks("u1", [{"instrument_token": 1234, "last_price": 165.0}])
        assert store.get("u1", "NIFTY26SEP24800CE").exit_reason == "target"

    async def test_a_failed_scale_out_leaves_the_position_protected(self, live):
        live.raise_on_order = True
        held(quantity=150, lots=2, target=160.0, target2=190.0)
        await runner.on_ticks("u1", [{"instrument_token": 1234, "last_price": 165.0}])
        pos = store.get("u1", "NIFTY26SEP24800CE")
        assert pos.quantity == 150 and pos.gtt_id, "left with no broker stop"


@pytest.mark.asyncio
class TestTheSpotStopIsActuallyEnforced:
    """The stop these strategies actually state.

    Every one of them states its stop in the UNDERLYING's points. Before this
    the tick loop only ever saw the contract's ticks, so the spot stop existed
    on the row, in the docs and in the tests — and could never fire.
    """

    async def test_the_underlying_is_subscribed_alongside_the_contract(self, live, monkeypatch):
        held(underlying_token=256265)
        seen: dict = {}

        async def _sub(uid, tokens, mode, owner=None):
            seen["tokens"] = set(tokens)
        import app.services.exchanges.kite.ticker_manager as tm
        monkeypatch.setattr(tm, "subscribe", _sub)
        monkeypatch.setattr(tm, "release", _sub)
        await live.real_subscribe("u1")
        assert {1234, 256265} <= seen["tokens"]

    async def test_spot_through_the_level_closes_a_stale_contract(self, live):
        """An illiquid option can sit at a stale premium straight through the
        level the entry was taken against."""
        held(underlying_token=256265, spot_stop=24780.0)
        await runner.on_ticks("u1", [
            {"instrument_token": 1234, "last_price": 100.0},     # premium fine
            {"instrument_token": 256265, "last_price": 24770.0},  # spot is not
        ])
        pos = store.get("u1", "NIFTY26SEP24800CE")
        assert pos.status == "closed" and pos.exit_reason == "spot stop"

    async def test_spot_still_above_the_level_changes_nothing(self, live):
        held(underlying_token=256265, spot_stop=24780.0)
        await runner.on_ticks("u1", [
            {"instrument_token": 1234, "last_price": 100.0},
            {"instrument_token": 256265, "last_price": 24810.0},
        ])
        assert store.get("u1", "NIFTY26SEP24800CE").is_open

    async def test_a_session_end_sweep_prices_the_exit_instead_of_assuming_it(self, live, monkeypatch):
        """With no tick for the contract, pricing at the stop would send a limit
        nowhere near the market."""
        monkeypatch.setattr(runner, "_is_market_open", lambda cfg: False)
        live.premium = 143.0
        held()
        await runner.on_ticks("u1", [])
        pos = store.get("u1", "NIFTY26SEP24800CE")
        assert pos.status == "closed" and pos.exit_reason == "session end"
        assert pos.exit_price == pytest.approx(143.0)


@pytest.mark.asyncio
class TestAutoEntryGating:
    async def test_it_stops_at_the_first_account_level_refusal(self, live, monkeypatch):
        """The caps are about the account, not the row, so the next row would be
        refused for the same reason and each attempt costs a quote."""
        svc.set_config({"max_concurrent_positions": 1, "sizing_mode": "LOTS",
                        "lots": 1}, "u1")
        held()                       # the one slot is taken
        for i in range(3):
            row = armed_row(signal_id=f"pivot_break:X{i}:1")
            row["contract"] = {**row["contract"], "symbol": f"X{i}CE"}
            svc.status("u1").signals[row["signal_id"]] = row
        assert await runner._auto_enter("u1") == 0
        assert not [o for o in live.orders if o["side"] == "buy"]
