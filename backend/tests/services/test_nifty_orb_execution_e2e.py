"""End-to-end truth for the live ORB order path.

`execute_scan` is written to fail closed. These tests prove it does, by driving
the whole path against a fake broker: idempotency, duplicate submission, broker
rejection, partial fill and remaining quantity, protection arming, expiry
policy, and daily trade-limit persistence.

Every collaborator is stubbed at its real module attribute, because
`execute_scan` imports them at call time.
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.services import nifty_orb_execution as execution

IST = ZoneInfo("Asia/Kolkata")
IDEM_TAG = "ORB-IDEM-TAG"
# A Tuesday inside both the session and the default 09:30-12:00 entry window.
NOW = datetime(2026, 8, 25, 10, 30, tzinfo=IST)
EXPIRY = "2026-08-27"
SYMBOL = "NIFTY26AUG25000CE"


# ---------------------------------------------------------------- fake broker

@dataclass
class FakeOrder:
    order_id: str
    quantity: int
    filled: int
    status: str
    average_price: float


class FakeClient:
    """Minimal Kite surface used by the ORB order path."""

    def __init__(self, *, fill=None, place_raises=None, order_id="OID1", existing=None):
        self.fill = fill if fill is not None else {"filled": 75, "status": "COMPLETE", "average_price": 18.0}
        self.place_raises = place_raises
        self.order_id = order_id
        self.existing = existing or []
        self.placed: list[dict] = []
        self.cancelled: list[str] = []
        self.sold: list[dict] = []

    async def place_order_option(self, symbol, side, quantity, *, exchange, tag):
        self.placed.append({"symbol": symbol, "side": side, "quantity": quantity, "tag": tag})
        if self.place_raises:
            raise self.place_raises
        return {"order_id": self.order_id} if self.order_id else {}

    async def get_orders(self):
        return self.existing

    async def get_order_history(self, order_id):
        return [{"order_id": order_id, "status": self.fill["status"],
                 "filled_quantity": self.fill["filled"], "average_price": self.fill["average_price"]}]

    async def get_order_trades(self, order_id):
        return []

    async def cancel_order(self, order_id):
        self.cancelled.append(order_id)
        self.fill = {**self.fill, "status": "CANCELLED"}
        return {"order_id": order_id}

    async def get_quote(self, symbols):
        return {s: {"last_price": 25000.0} for s in symbols}


@dataclass
class FakeUniversal:
    auto_execute: bool = True
    stop_mode: str = "broker_gtt"
    exit_mode: str = "one_red"


@dataclass
class FakeArm:
    protected: bool = True

    def describe(self):
        return "protection unavailable"


@dataclass
class FakeDecision:
    allowed: bool = True
    reason: str = ""
    code: str = ""


class Recorder:
    """Captures kill-switch trips and recorded idempotency keys."""

    def __init__(self):
        self.kill: list[str] = []
        self.idempotency: list[tuple[str, str]] = []


# ---------------------------------------------------------------- fixtures

def _signal_row(*, quantity=75, option_type="CE", direction="LONG", expiry=EXPIRY, strike=25000.0):
    return {
        "status": "signal",
        "underlying": "NIFTY",
        "spot": 25000.0,
        "signal": {"direction": direction, "timestamp": NOW.isoformat()},
        "trade": {
            "quantity": quantity,
            "underlying_entry": 25000.0,
            "stop_premium": 14.0,
            "target_premium": 26.0,
            "contract": {
                "symbol": SYMBOL, "option_type": option_type, "strike": strike,
                "expiry": expiry, "lot_size": 75, "delta": 0.5,
            },
        },
    }


@pytest.fixture
def harness(monkeypatch, tmp_path):
    """Wire every collaborator to a controllable stub and freeze the clock."""
    from app.services.kite_engine import state as engine_state, positions, protection
    from app.services import live_safety
    from app.services.exchanges.kite import accounts
    from app.services import nifty_orb_options

    rec = Recorder()
    store: dict[str, dict] = {}

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW if tz else NOW.replace(tzinfo=None)

    monkeypatch.setattr(execution, "datetime", FrozenDatetime)
    monkeypatch.setattr(engine_state, "get_config", lambda uid: FakeUniversal())
    monkeypatch.setattr(accounts, "get_active", lambda uid: object())
    monkeypatch.setattr(positions, "open_positions", lambda uid: [])
    monkeypatch.setattr(protection, "arm_position", _async(FakeArm()))
    monkeypatch.setattr(live_safety, "make_idempotency_key", lambda *parts: IDEM_TAG)
    monkeypatch.setattr(live_safety, "assert_safe_to_trade", lambda *a, **k: FakeDecision())
    monkeypatch.setattr(live_safety, "record_idempotency", lambda key, oid: rec.idempotency.append((key, oid)))
    monkeypatch.setattr(live_safety, "set_kill_switch", lambda on, reason="": rec.kill.append(reason))
    monkeypatch.setattr(nifty_orb_options, "get_config", lambda: nifty_orb_options.StrategyConfig())
    monkeypatch.setattr(execution, "_state", lambda uid: store.setdefault(uid, {"count": 0, "signals": []}))
    monkeypatch.setattr(execution, "_save_state", lambda uid, st: store.__setitem__(uid, st))
    monkeypatch.setattr(execution, "_find_contract", _async((
        "NFO",
        {"instrument_type": "CE", "strike": 25000.0, "expiry": EXPIRY, "lot_size": 75, "instrument_token": 12345},
    )))
    monkeypatch.setattr(execution, "_fresh_quote", _async({"ask": 18.0, "bid": 17.9, "volume": 5000.0, "oi": 50000.0}))
    return {
        "rec": rec, "store": store, "patch": monkeypatch.setattr,
        "protection": protection, "state": engine_state, "accounts": accounts,
        "safety": live_safety,
    }


def _async(result):
    async def call(*a, **k):
        if isinstance(result, Exception):
            raise result
        return result
    return call


async def _run(harness, client, *, rows=None, max_trades=2):
    harness["patch"](harness["accounts"], "acquire_client", _async(client))
    scan = {"signals": rows if rows is not None else [_signal_row()]}
    return await execution.execute_scan("u1", scan=scan, max_trades=max_trades)


# ---------------------------------------------------------------- happy path

@pytest.mark.asyncio
async def test_a_complete_fill_is_executed_protected_and_counted(harness):
    client = FakeClient()
    out = await _run(harness, client)
    assert out["status"] == "executed"
    entry = out["executed"][0]
    assert entry["status"] == "executed"
    assert entry["quantity"] == 75
    assert entry["protected"] is True
    assert entry["conservative_max_loss_inr"] == 1350.0     # 18.00 ask * 75
    assert harness["store"]["u1"]["count"] == 1
    assert harness["rec"].kill == []
    assert entry["ticket_fingerprint"]
    assert entry["ticket"]["symbol"] == SYMBOL
    assert entry["ticket"]["quantity"] == 75


@pytest.mark.asyncio
async def test_the_order_carries_the_idempotency_tag_and_records_it(harness):
    client = FakeClient()
    await _run(harness, client)
    assert client.placed[0]["tag"] == IDEM_TAG
    assert harness["rec"].idempotency == [(IDEM_TAG, "OID1")]


# ---------------------------------------------------------------- idempotency

@pytest.mark.asyncio
async def test_an_existing_broker_order_on_the_tag_is_adopted_not_duplicated(harness):
    """A retry after an ambiguous submission must not double the position."""
    client = FakeClient(existing=[{
        "order_id": "PRIOR", "tag": IDEM_TAG, "tradingsymbol": SYMBOL,
        "transaction_type": "BUY", "status": "COMPLETE",
    }])
    out = await _run(harness, client)
    assert client.placed == []                              # nothing re-submitted
    assert out["executed"][0]["order_id"] == "PRIOR"


@pytest.mark.asyncio
async def test_a_tag_mapped_to_a_foreign_order_trips_the_kill_switch(harness):
    """If our tag names someone else's order, broker state is not understood."""
    client = FakeClient(existing=[{
        "order_id": "ALIEN", "tag": IDEM_TAG, "tradingsymbol": "BANKNIFTY26AUG50000PE",
        "transaction_type": "SELL", "status": "COMPLETE",
    }])
    out = await _run(harness, client)
    assert client.placed == []
    assert out["executed"][0]["reason"] == "unexpected broker order"
    assert any("unexpected broker order" in r for r in harness["rec"].kill)


@pytest.mark.asyncio
async def test_a_tag_mapped_to_a_foreign_symbol_trips_the_kill_switch(harness):
    client = FakeClient(existing=[{
        "order_id": "ALIEN", "tag": IDEM_TAG, "tradingsymbol": "BANKNIFTY26AUG50000CE",
        "transaction_type": "BUY", "status": "COMPLETE",
    }])
    out = await _run(harness, client)
    assert client.placed == []
    assert out["executed"][0]["reason"] == "unexpected broker order"
    assert any("unexpected broker order" in r for r in harness["rec"].kill)


@pytest.mark.asyncio
async def test_a_tag_mapped_to_a_sell_trips_the_kill_switch(harness):
    client = FakeClient(existing=[{
        "order_id": "ALIEN", "tag": IDEM_TAG, "tradingsymbol": SYMBOL,
        "transaction_type": "SELL", "status": "COMPLETE",
    }])
    out = await _run(harness, client)
    assert client.placed == []
    assert out["executed"][0]["reason"] == "unexpected broker order"
    assert any("unexpected broker order" in r for r in harness["rec"].kill)


@pytest.mark.asyncio
async def test_the_same_signal_is_not_executed_twice_in_one_scan(harness):
    """Two rows on the same underlying: the second is skipped, not stacked."""
    client = FakeClient()
    out = await _run(harness, client, rows=[_signal_row(), _signal_row()])
    assert len([e for e in out["executed"] if e["status"] == "executed"]) == 1
    assert len(client.placed) == 1


# ---------------------------------------------------------------- broker failures

@pytest.mark.asyncio
async def test_a_broker_rejection_is_reported_and_nothing_is_counted(harness):
    client = FakeClient(place_raises=RuntimeError("margin shortfall"))
    out = await _run(harness, client)
    assert out["executed"][0]["status"] == "error"
    assert "margin shortfall" in out["executed"][0]["error"]
    assert harness["store"]["u1"]["count"] == 0


@pytest.mark.asyncio
async def test_a_submission_with_no_order_id_trips_the_kill_switch(harness):
    """An order may have reached the exchange, so the outcome is unknown."""
    client = FakeClient(order_id="")
    out = await _run(harness, client)
    assert out["executed"][0]["reason"] == "submission outcome unknown"
    assert any("submission outcome unknown" in r for r in harness["rec"].kill)


@pytest.mark.asyncio
async def test_a_rejected_order_is_not_cancelled_again(harness):
    client = FakeClient(fill={"filled": 0, "status": "REJECTED", "average_price": 0.0})
    out = await _run(harness, client)
    assert out["executed"][0]["status"] == "pending_or_unfilled"
    assert client.cancelled == []                           # already terminal
    assert harness["store"]["u1"]["count"] == 0


@pytest.mark.asyncio
async def test_an_unfilled_live_order_is_cancelled_and_reconciled(harness):
    client = FakeClient(fill={"filled": 0, "status": "OPEN", "average_price": 0.0})
    out = await _run(harness, client)
    assert out["executed"][0]["status"] == "pending_or_unfilled"
    assert client.cancelled == ["OID1"]


# ---------------------------------------------------------------- partial fills

@pytest.mark.asyncio
async def test_a_partial_fill_protects_only_the_quantity_actually_held(harness):
    armed: list[int] = []

    async def arm(client, uid, **kw):
        armed.append(kw["qty"])
        return FakeArm()

    harness["patch"](harness["protection"], "arm_position", arm)
    client = FakeClient(fill={"filled": 75, "status": "PARTIALLY FILLED", "average_price": 18.2})
    out = await _run(harness, client, rows=[_signal_row(quantity=150)])
    entry = out["executed"][0]
    assert entry["status"] == "executed"
    assert entry["quantity"] == 75                          # held
    assert entry["requested_quantity"] == 150               # asked for
    assert armed == [75]                                    # protection follows the fill
    assert client.cancelled == ["OID1"]                     # remainder pulled


# ---------------------------------------------------------------- protection

@pytest.mark.asyncio
async def test_a_position_that_cannot_be_protected_is_closed(harness):
    harness["patch"](harness["protection"], "arm_position", _async(FakeArm(protected=False)))
    closed = []

    async def sell(client, symbol, exchange, qty):
        closed.append(qty)
        return True, "closed"

    harness["patch"](execution, "_sell_and_verify", sell)
    client = FakeClient()
    out = await _run(harness, client)
    assert out["executed"][0]["status"] == "entry_closed_protection_failure"
    assert closed == [75]
    assert harness["store"]["u1"]["count"] == 0


@pytest.mark.asyncio
async def test_an_unprotected_position_that_cannot_be_closed_trips_the_kill_switch(harness):
    harness["patch"](harness["protection"], "arm_position", _async(FakeArm(protected=False)))
    harness["patch"](execution, "_sell_and_verify", _async((False, "sell rejected")))
    client = FakeClient()
    out = await _run(harness, client)
    assert out["executed"][0]["status"] == "critical_unprotected"
    assert any("unprotected position" in r for r in harness["rec"].kill)


# ---------------------------------------------------------------- policy gates

@pytest.mark.asyncio
async def test_auto_execute_off_is_manual_signals_only(harness):
    harness["patch"](harness["state"], "get_config", lambda uid: FakeUniversal(auto_execute=False))
    client = FakeClient()
    out = await _run(harness, client)
    assert out["status"] == "manual"
    assert out["mode"] == "signals_only"
    assert out["executed"] == []
    assert client.placed == []


@pytest.mark.asyncio
async def test_a_broker_lot_size_disagreeing_with_the_plan_is_refused(harness):
    harness["patch"](execution, "_find_contract", _async((
        "NFO",
        {"instrument_type": "CE", "strike": 25000.0, "expiry": EXPIRY, "lot_size": 50, "instrument_token": 1},
    )))
    client = FakeClient()
    out = await _run(harness, client)
    assert out["executed"][0]["reason"] == "broker contract lot size mismatch"
    assert client.placed == []


@pytest.mark.asyncio
async def test_a_broker_expiry_disagreeing_with_the_plan_is_refused(harness):
    harness["patch"](execution, "_find_contract", _async((
        "NFO",
        {"instrument_type": "CE", "strike": 25000.0, "expiry": "2026-09-03", "lot_size": 75, "instrument_token": 1},
    )))
    client = FakeClient()
    out = await _run(harness, client)
    assert out["executed"][0]["reason"] == "broker contract expiry mismatch"
    assert client.placed == []


@pytest.mark.asyncio
async def test_a_live_ask_that_would_resize_the_ticket_is_refused(harness):
    """Same ticket: Auto must not silently buy fewer lots than the board showed."""
    harness["patch"](execution, "_fresh_quote", _async(
        {"ask": 40.0, "bid": 39.9, "volume": 5000.0, "oi": 50000.0},
    ))
    client = FakeClient()
    out = await _run(harness, client, rows=[_signal_row(quantity=150)])
    assert out["executed"][0]["reason"] == "live premium would change the ticket quantity"
    assert client.placed == []


@pytest.mark.asyncio
async def test_the_daily_trade_limit_persists_across_scans(harness):
    client = FakeClient()
    await _run(harness, client, max_trades=1)
    assert harness["store"]["u1"]["count"] == 1
    again = await _run(harness, FakeClient(), max_trades=1)
    assert again["status"] == "daily_limit"


@pytest.mark.asyncio
async def test_a_broker_contract_disagreeing_with_the_plan_is_refused(harness):
    """The broker's contract is authoritative; a mismatch is never traded."""
    client = FakeClient()
    out = await _run(harness, client, rows=[_signal_row(strike=24000.0)])
    assert out["executed"][0]["reason"] == "broker contract strike mismatch"
    assert client.placed == []


@pytest.mark.asyncio
async def test_a_contract_outside_the_expiry_policy_is_refused(harness):
    far = (NOW.date() + timedelta(days=60)).isoformat()
    harness["patch"](execution, "_find_contract", _async((
        "NFO",
        {"instrument_type": "CE", "strike": 25000.0, "expiry": far, "lot_size": 75, "instrument_token": 1},
    )))
    client = FakeClient()
    out = await _run(harness, client, rows=[_signal_row(expiry=far)])
    assert out["executed"][0]["reason"] == "contract outside configured expiry policy"
    assert client.placed == []


@pytest.mark.asyncio
async def test_a_direction_mismatch_is_refused_before_any_broker_call(harness):
    client = FakeClient()
    out = await _run(harness, client, rows=[_signal_row(option_type="PE")])
    assert out["executed"][0]["reason"] == "option direction mismatch"
    assert client.placed == []


@pytest.mark.asyncio
async def test_a_stale_signal_is_refused(harness):
    row = _signal_row()
    row["signal"]["timestamp"] = (NOW - timedelta(hours=2)).isoformat()
    client = FakeClient()
    out = await _run(harness, client, rows=[row])
    assert "stale" in out["executed"][0]["reason"]
    assert client.placed == []


@pytest.mark.asyncio
async def test_illiquid_quotes_are_refused(harness):
    harness["patch"](execution, "_fresh_quote", _async({"ask": 18.0, "bid": 17.9, "volume": 10.0, "oi": 50000.0}))
    client = FakeClient()
    out = await _run(harness, client)
    assert out["executed"][0]["reason"] == "option liquidity below configured minimum"
    assert client.placed == []


@pytest.mark.asyncio
async def test_a_lot_costing_more_than_the_risk_budget_is_refused(harness):
    harness["patch"](execution, "_fresh_quote", _async({"ask": 500.0, "bid": 499.0, "volume": 5000.0, "oi": 50000.0}))
    client = FakeClient()
    out = await _run(harness, client)
    assert "premium risk budget" in out["executed"][0]["reason"]
    assert client.placed == []


@pytest.mark.asyncio
async def test_the_underlying_drifting_past_the_tolerance_is_refused(harness):
    class Drifted(FakeClient):
        async def get_quote(self, symbols):
            return {s: {"last_price": 25200.0} for s in symbols}       # +0.8%

    client = Drifted()
    out = await _run(harness, client)
    assert "underlying moved" in out["executed"][0]["reason"]
    assert client.placed == []


# --------------------------------------------------------------------------
# daily-budget accounting and state persistence
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_refusals_do_not_consume_the_daily_trade_budget(harness):
    """Two illiquid candidates must not spend a two-trade day."""
    harness["patch"](execution, "_fresh_quote", _async({"ask": 18.0, "bid": 17.9, "volume": 10.0, "oi": 50000.0}))
    rows = [_signal_row(), dict(_signal_row(), underlying="BANKNIFTY"), dict(_signal_row(), underlying="FINNIFTY")]
    out = await _run(harness, FakeClient(), rows=rows, max_trades=2)
    # Every row was examined and refused; none was skipped by an exhausted budget.
    assert len(out["executed"]) == 3
    assert {e["reason"] for e in out["executed"]} == {"option liquidity below configured minimum"}
    assert harness["store"]["u1"]["count"] == 0


@pytest.mark.asyncio
async def test_a_refusal_before_a_tradable_candidate_still_leaves_room(harness):
    """The regression: a leading refusal used to eat one of the two slots."""
    quotes = iter([
        {"ask": 18.0, "bid": 17.9, "volume": 10.0, "oi": 50000.0},        # illiquid
        {"ask": 18.0, "bid": 17.9, "volume": 5000.0, "oi": 50000.0},      # tradable
    ])

    async def quote(*a, **k):
        return next(quotes)

    harness["patch"](execution, "_fresh_quote", quote)
    rows = [_signal_row(), dict(_signal_row(), underlying="BANKNIFTY")]
    out = await _run(harness, FakeClient(), rows=rows, max_trades=1)
    statuses = [e["status"] for e in out["executed"]]
    assert statuses == ["blocked", "executed"]
    assert harness["store"]["u1"]["count"] == 1


@pytest.mark.asyncio
async def test_a_failed_state_write_stops_trading_instead_of_raising_the_cap(harness):
    """The count did not persist, so the next tick would trade past the limit."""
    def explode(uid, state):
        raise RuntimeError("database unavailable")

    harness["patch"](execution, "_save_state", explode)
    out = await _run(harness, FakeClient())
    entry = out["executed"][0]
    assert entry["status"] == "executed_count_not_persisted"
    assert entry["protected"] is True                      # the position is real and armed
    assert entry["quantity"] == 75
    assert any("trade count did not persist" in r for r in harness["rec"].kill)


@pytest.mark.asyncio
async def test_a_broker_option_type_disagreeing_with_the_signal_is_refused(harness):
    harness["patch"](execution, "_find_contract", _async((
        "NFO",
        {"instrument_type": "PE", "strike": 25000.0, "expiry": EXPIRY, "lot_size": 75, "instrument_token": 1},
    )))
    client = FakeClient()
    out = await _run(harness, client)
    assert out["executed"][0]["reason"] == "broker contract option type mismatch"
    assert client.placed == []


@pytest.mark.asyncio
async def test_orb_uses_the_authoritative_account_daily_loss_read(harness):
    """ORB must pass `uid=` so the breaker uses the account read.

    The positions cascade in `live_safety` was inert for USD-denominated
    positions -- it contributed 0.00 for every one and reported "clear" against
    any threshold. ORB escaped that only because it passes `uid`, which routes to
    `daily_realized_pnl_strict`. Pin it so a future edit cannot drop the uid and
    silently inherit the weaker path.
    """
    seen: list[dict] = []

    def record(positions, idem=None, **kwargs):
        seen.append(kwargs)
        return FakeDecision()

    harness["patch"](harness["safety"], "assert_safe_to_trade", record)
    await _run(harness, FakeClient())
    assert seen, "the safety gate was never consulted"
    for call in seen:
        assert call.get("check_daily_loss") is True
        assert call.get("uid") == "u1"
