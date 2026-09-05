"""Execution-path tests for the Kite directional auto-exec callback.

These drive the real ``service._make_place_cb`` callback with a fake Kite client
so they exercise the vehicle routing, the P0 stop-direction fix, the deep-ITM /
futures resolution, the entry filters, and the drawdown breaker — the integration
the unit-level golden test cannot reach.
"""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.services.kite_engine import market_hours

_IST = ZoneInfo("Asia/Kolkata")

from app.engines.sterling_kite_engine.schemas import (
    AlignmentChip, EngineConfigModel, EngineSignalRow, OptionLeg,
)
from app.services import db, live_safety
from app.services.exchanges.kite import constants as K
from app.services.kite_engine import order_journal, positions, service, state
from app.services.kite_engine.universe import UniverseItem

UID = "test-dir-exec"


class FakeClient:
    #: Default capital: enough that risk sizing is never the gate. These tests are
    #: about direction, vehicle and the entry filters, but at the old ₹100,000 a
    #: single NIFTY lot (₹2,250 of risk) already broke the 1% budget — so every one
    #: of them would be refused by the risk cap for reasons unrelated to what it
    #: asserts. Tests that need a small account pass ``balance`` explicitly.
    DEFAULT_BALANCE = 5_000_000.0

    def __init__(self, instruments=None, balance: float | None = None,
                 ltp_by_symbol: dict | None = None, ltp_missing: bool = False):
        self.gtt_calls = []
        self.opt_placed = []
        self.fut_placed = []
        self._instruments = instruments or []
        self.balance = self.DEFAULT_BALANCE if balance is None else float(balance)
        self.ltp_by_symbol = ltp_by_symbol or {}
        self.ltp_missing = ltp_missing

    async def order_margins(self, orders):
        return [{"total": 1000 * o["quantity"]} for o in orders]

    async def get_margins(self, seg=None):
        return {"available": {"live_balance": self.balance}}

    async def place_order_option(self, sym, side, size, **kw):
        self.opt_placed.append({"sym": sym, "side": side, "size": size, **kw})
        return {"order_id": "OPT1"}

    async def place_order_future(self, sym, side, size, **kw):
        self.fut_placed.append({"sym": sym, "side": side, "size": size, **kw})
        return {"order_id": "FUT1"}

    async def place_gtt(self, **kw):
        self.gtt_calls.append(kw)
        return {"trigger_id": 999}

    async def search_instruments(self, q, exch, limit=0):
        return self._instruments

    async def get_ltp(self, syms):
        # ``ltp_by_symbol`` lets a test price one instrument differently — a futures
        # contract does not trade at the same price as an option leg, and the whole
        # point of the futures translation is that it does not trade at spot either.
        if self.ltp_missing:
            return {}
        return {s: {"last_price": self.ltp_by_symbol.get(s.split(":")[-1], 520.0)}
                for s in syms}


def _bear_row(source="spot"):
    """A bear (PE-buying) signal — the case the P0 bug mis-handled."""
    return EngineSignalRow(
        underlying="NIFTY 50", token=256265, exchange="NFO", regime="BEAR",
        alignment=AlignmentChip(fast=-1, mid=-1, slow=-1),
        direction="short", option_type="PE",
        legs=[OptionLeg(moneyness="ATM", option_type="PE", option_symbol="NIFTY2562625000PE",
                        strike=25000, expiry=_future_expiry(10), lot_size=75,
                        premium_spot=120.0, premium_sl=90.0, token=111)],
        spot=25000.0, stop_loss=25200.0, score=85.0, timestamp_ms=1_700_000_000_000,
        source=source, adx=30.0, atr_pct=60.0)


def _future_expiry(days: int = 10) -> str:
    return (date.today() + timedelta(days=days)).isoformat()


@pytest.mark.parametrize("balance", [0, 100, float("nan")])
def test_fixed_lot_mode_never_bypasses_available_capital(balance):
    client = FakeClient(balance=balance)
    opened = _run(EngineConfigModel(risk_sizing=False), _bear_row(), client)
    assert opened == [] and client.opt_placed == []


@pytest.mark.parametrize("stop", [120, 130, float("nan"), float("inf")])
def test_fixed_lot_mode_requires_finite_protective_stop(stop):
    client = FakeClient()
    row = _bear_row()
    row.legs[0].premium_sl = stop
    opened = _run(EngineConfigModel(risk_sizing=False), row, client)
    assert opened == [] and client.opt_placed == []


def _spot_row_no_premium():
    """A realistic SPOT-source row: attach_strikes produces legs with NO premium
    data (premium_spot/premium_sl unset), so the auto-exec must delta-translate the
    underlying ST trail into a premium stop. This is the D1 case."""
    return EngineSignalRow(
        underlying="NIFTY 50", token=256265, exchange="NFO", regime="BULL",
        alignment=AlignmentChip(fast=1, mid=1, slow=1),
        direction="long", option_type="CE",
        legs=[OptionLeg(moneyness="ATM", option_type="CE", option_symbol="NIFTY26FUT25000CE",
                        strike=25000, expiry=_future_expiry(10), lot_size=75, token=222)],
        spot=25000.0, stop_loss=24800.0, score=85.0, timestamp_ms=1_700_000_000_000,
        source="spot", adx=30.0, atr_pct=60.0)


def _item():
    return UniverseItem(name="NIFTY 50", tradingsymbol="NIFTY", token=256265,
                        exchange="INDICES", option_exchange="NFO", is_index=True)


# ── P0 D1: spot-mode signals must get a delta-translated premium stop ─────────

def test_spot_mode_otm_gets_delta_translated_premium_stop():
    """A spot-source leg carries no premium fields, so the callback fetches the leg
    LTP and derives a premium stop from the underlying ST trail. Before the fix,
    stop_premium stayed 0 → no GTT, monitor exit permanently inert, sizing → 1 lot."""
    client = FakeClient()  # get_ltp → 520.0
    cfg = EngineConfigModel()  # defaults: directional_mode off, stop_mode "both"
    open_pos = _run(cfg, _spot_row_no_premium(), client)
    assert len(open_pos) == 1
    p = open_pos[0]
    assert p.direction == "long"
    assert p.entry_premium == pytest.approx(520.0)
    assert p.stop_premium > 0, "spot-mode position must get a real premium stop"
    assert p.initial_stop_premium == pytest.approx(p.stop_premium)
    assert p.entry_delta > 0, "delta-translation context must be stored for trailing"
    assert client.gtt_calls, "a protective GTT must be placed for a spot-mode entry too"
    assert client.gtt_calls[0]["orders"][0]["transaction_type"] == K.TXN_SELL


def _fut_dump():
    return [{
        "name": "NIFTY", "segment": "NFO-FUT", "instrument_type": "FUT",
        "tradingsymbol": "NIFTY26JUNFUT", "expiry": _future_expiry(30),
        "instrument_token": 5001, "lot_size": 75,
    }]


def _run(cfg: EngineConfigModel, row, client, item=None):
    state.reset(UID)
    positions._positions.pop(UID, None)
    live_safety._IDEMPOTENCY_CACHE.clear()   # global cache — isolate tests
    state.set_config(UID, cfg)
    cb = service._make_place_cb(client, UID)
    asyncio.run(cb(row, item or _item()))
    return positions.open_positions(UID)


def test_live_entry_is_reserved_before_send_and_duplicate_signal_cannot_resend():
    prior=db._available; db.init(); order_journal.clear_for_tests(UID)
    try:
        positions.reset(UID); state.reset(UID); state.set_config(UID,EngineConfigModel())
        class LostAck(FakeClient):
            _is_paper=False; _account_id='acct-1'
            calls=0
            async def place_order_option(self,*a,**kw):
                self.calls+=1; self.sent_tag=kw['tag']
                raise TimeoutError('response lost')
        client=LostAck()
        cb=service._make_place_cb(client,UID)
        asyncio.run(cb(_bear_row(),_item()))
        intents=order_journal.unresolved(UID)
        assert client.calls == 1 and len(intents) == 1
        assert intents[0].state == 'UNKNOWN' and intents[0].tag == client.sent_tag
        asyncio.run(cb(_bear_row(),_item()))
        assert client.calls == 1
    finally:
        order_journal.clear_for_tests(UID); positions.reset(UID); db._available=prior


# ── P0 regression: a bear PE BUY must be a LONG-premium position ──────────────

def test_default_bear_pe_is_long_premium_with_sell_stop():
    """directional_mode OFF: a bear PE signal registers direction='long' and its
    protective GTT is a SELL (the P0 fix). Previously it was tagged 'short',
    inverting the GTT into a BUY and the tick exit into ≥."""
    client = FakeClient()
    cfg = EngineConfigModel()  # defaults: directional_mode False
    open_pos = _run(cfg, _bear_row(), client)
    assert len(open_pos) == 1
    p = open_pos[0]
    assert p.direction == "long"           # P0: options are always long-premium
    assert p.vehicle == "otm_options"
    # the option BUY went through, and the GTT leg is a SELL (exit a long)
    assert client.opt_placed and client.opt_placed[0]["side"] == "buy"
    assert client.gtt_calls, "a premium stop GTT should be placed"
    leg = client.gtt_calls[0]["orders"][0]
    assert leg["transaction_type"] == K.TXN_SELL


def _pe_chain():
    """A PE chain spanning ITM (high strikes) for a 25000 spot — enough for the
    delta picker to find a deep (~0.9) strike."""
    out = []
    exp = _future_expiry(10)
    for strike in range(24000, 26600, 200):
        out.append({
            "name": "NIFTY", "tradingsymbol": f"NIFTY26JUN{strike}PE", "instrument_type": "PE",
            "strike": strike, "expiry": exp, "instrument_token": strike, "lot_size": 75,
        })
    return out


def test_deep_itm_bear_pe_still_long_premium_sell_stop():
    """deep-ITM vehicle, bear signal: still a long-premium PE BUY with a SELL GTT,
    and it picks a DEEP ITM strike (well above spot for a put), not ATM."""
    client = FakeClient(instruments=_pe_chain())
    cfg = EngineConfigModel(directional_mode=True, vehicle="deep_itm_options",
                            enabled_vehicles=["otm_options", "deep_itm_options"],
                            target_delta=0.90)
    open_pos = _run(cfg, _bear_row(), client)
    assert len(open_pos) == 1
    assert open_pos[0].direction == "long"          # P0 holds for deep-ITM too
    assert open_pos[0].vehicle == "deep_itm_options"
    assert client.opt_placed, "deep-ITM order should be placed"
    sym = client.opt_placed[0]["sym"]
    assert sym.endswith("PE") and sym != "NIFTY2562625000PE"   # a deep strike, not the ATM leg
    # PE delta ~0.9 ⇒ a strike comfortably ABOVE spot (deep ITM for a put)
    strike = int(sym.replace("NIFTY26JUN", "").replace("PE", ""))
    assert strike >= 25000
    if client.gtt_calls:
        assert client.gtt_calls[0]["orders"][0]["transaction_type"] == K.TXN_SELL


# ── futures: two-sided, signal direction carried, BUY-to-cover stop on shorts ──

def test_futures_bear_is_short_with_buy_to_cover_stop():
    client = FakeClient(instruments=_fut_dump())
    cfg = EngineConfigModel(directional_mode=True, vehicle="futures",
                            enabled_vehicles=["otm_options", "deep_itm_options", "futures"],
                            futures_expiry="near")
    open_pos = _run(cfg, _bear_row(), client)
    assert len(open_pos) == 1
    p = open_pos[0]
    assert p.direction == "short"                   # futures DO carry the signal direction
    assert p.vehicle == "futures"
    assert p.symbol == "NIFTY26JUNFUT"              # the actual future, not the option symbol
    assert client.fut_placed and client.fut_placed[0]["side"] == "sell"
    assert not client.opt_placed                    # no option order in futures mode
    # short stop must BUY to cover (upside stop)
    assert client.gtt_calls and client.gtt_calls[0]["orders"][0]["transaction_type"] == K.TXN_BUY


def test_futures_entry_and_stop_are_in_the_futures_price_domain():
    """A future does not trade at spot, and its stop cannot be an index level.

    The entry and the GTT trigger were taken straight from ``row.spot`` and
    ``row.stop_loss`` — both UNDERLYING-domain. On NIFTY the basis is tens of points;
    on a stock future it is larger. That mis-states the recorded entry (and so every
    realized-PnL figure derived from it) and puts the broker's trigger a whole basis
    away from the level intended — in a discount, on the wrong side of the last price
    entirely, where it is rejected or fires immediately.

    Futures track spot ~1:1, so the stop DISTANCE carries over and only the level
    shifts by the basis.
    """
    client = FakeClient(instruments=_fut_dump(),
                        ltp_by_symbol={"NIFTY26JUNFUT": 25_080.0})
    cfg = EngineConfigModel(directional_mode=True, vehicle="futures",
                            enabled_vehicles=["futures"], futures_expiry="near")
    # _bear_row: spot 25,000, underlying trail 25,200 → a 200-point stop, above.
    open_pos = _run(cfg, _bear_row(), client)
    assert len(open_pos) == 1
    p = open_pos[0]
    assert p.entry_premium == pytest.approx(25_080.0), "entry is the future's own price"
    # basis = 25,080 − 25,000 = +80 → stop 25,200 + 80.
    assert p.stop_premium == pytest.approx(25_280.0)
    assert p.stop_premium - p.entry_premium == pytest.approx(200.0), "distance preserved"


def test_futures_position_records_its_expiry_so_the_square_off_can_reach_it():
    """``pos_expiry`` was left blank for futures, so ``_square_off_expiring`` — which
    skips anything without an expiry — never squared one off. Stock futures settle
    physically; riding one into expiry means taking delivery."""
    client = FakeClient(instruments=_fut_dump(),
                        ltp_by_symbol={"NIFTY26JUNFUT": 25_080.0})
    cfg = EngineConfigModel(directional_mode=True, vehicle="futures",
                            enabled_vehicles=["futures"], futures_expiry="near")
    open_pos = _run(cfg, _bear_row(), client)
    assert len(open_pos) == 1
    assert open_pos[0].expiry == _future_expiry(30)


def test_futures_entry_skipped_when_the_contract_cannot_be_priced():
    """No quote means no basis, and no basis means the stop cannot be placed where it
    belongs. Consistent with the option path: auto-exec is unattended, so a position
    it cannot protect is not opened."""
    client = FakeClient(instruments=_fut_dump(), ltp_missing=True)
    cfg = EngineConfigModel(directional_mode=True, vehicle="futures",
                            enabled_vehicles=["futures"], futures_expiry="near")
    assert _run(cfg, _bear_row(), client) == []
    assert not client.fut_placed


def test_futures_unresolved_contract_blocks_order():
    client = FakeClient(instruments=[])  # no FUT rows → cannot resolve
    cfg = EngineConfigModel(directional_mode=True, vehicle="futures",
                            enabled_vehicles=["futures"], futures_expiry="near")
    open_pos = _run(cfg, _bear_row(), client)
    assert open_pos == []
    assert not client.fut_placed and not client.opt_placed


# ── futures opt-in: selecting futures while it's not enabled falls back to options

def test_futures_not_enabled_falls_back_to_options():
    client = FakeClient(instruments=_fut_dump())
    cfg = EngineConfigModel(directional_mode=True, vehicle="futures",
                            enabled_vehicles=["otm_options"])  # futures NOT enabled
    open_pos = _run(cfg, _bear_row(), client)
    assert len(open_pos) == 1
    assert open_pos[0].vehicle == "otm_options" or open_pos[0].direction == "long"
    assert client.opt_placed and not client.fut_placed


# ── Sterling Value-Flow Navigator gate: fails CLOSED only once gate mode is
# actually active, never for an unrelated user/environment ──────────────────

def test_navigator_gate_check_error_fails_closed_when_gate_mode_active():
    """Once the user's config confirms Navigator gate mode is active, a
    subsequent eligibility-check error must BLOCK the order rather than
    silently let it through."""
    from types import SimpleNamespace
    from unittest.mock import patch

    from app.services.navigator import config_store as navigator_config_store
    from app.services.navigator import service as navigator_service

    fake_record = SimpleNamespace(config=SimpleNamespace(enabled=True, operating_mode="gate"))
    client = FakeClient()
    cfg = EngineConfigModel()
    with patch.object(navigator_config_store, "get", return_value=fake_record), \
         patch.object(navigator_service, "check_execution_eligible", side_effect=RuntimeError("boom")):
        open_pos = _run(cfg, _spot_row_no_premium(), client)
    assert open_pos == []
    assert not client.opt_placed


def test_navigator_config_unavailable_does_not_block_unrelated_orders():
    """A Navigator-side infra hiccup (its tables unavailable, as in this
    test's DB fixture) must fail OPEN — it must never halt the entire
    unrelated Kite auto-exec engine for a user who never touched Navigator."""
    client = FakeClient()
    assert len(_run(EngineConfigModel(), _spot_row_no_premium(), client)) == 1


# ── entry filters ─────────────────────────────────────────────────────────────

def test_adx_filter_blocks_weak_trend():
    client = FakeClient()
    cfg = EngineConfigModel(directional_mode=True, vehicle="otm_options", adx_min=40.0)
    row = _bear_row()  # adx=30 < 40 → blocked
    assert _run(cfg, row, client) == []
    assert not client.opt_placed


def test_adx_filter_allows_strong_trend():
    client = FakeClient()
    cfg = EngineConfigModel(directional_mode=True, vehicle="otm_options", adx_min=20.0)
    assert len(_run(cfg, _bear_row(), client)) == 1   # adx=30 >= 20 → allowed


# ── drawdown breaker (opt-in, fail-safe) ─────────────────────────────────────

def test_breaker_halts_after_drawdown():
    client = FakeClient()
    cfg = EngineConfigModel(directional_mode=True, vehicle="otm_options", wire_risk_infra=True)
    # seed the per-user breaker peak high, then drop the value below halt threshold
    state.drawdown_multiplier(UID, 1_000_000.0)   # peak
    state.drawdown_multiplier(UID, 800_000.0)     # -20% → HALT (>= reset 15%)
    open_pos = _run_keep_breaker(cfg, _bear_row(), client)
    assert open_pos == []
    assert not client.opt_placed


def test_correlation_penalty_downsizes_correlated_entry():
    state.reset(UID)
    # two perfectly-correlated underlyings
    for k in range(60):
        state.feed_correlation(UID, "NIFTY 50", 100.0 + k)
        state.feed_correlation(UID, "NIFTY BANK", 200.0 + 2 * k)
    # correlated with an open position → downsized (< 1.0)
    assert state.correlation_penalty(UID, "NIFTY 50", ["NIFTY BANK"]) < 1.0
    # nothing open → no penalty
    assert state.correlation_penalty(UID, "NIFTY 50", []) == 1.0
    # cold tracker (unknown user) → no penalty
    assert state.correlation_penalty("nobody", "NIFTY 50", ["NIFTY BANK"]) == 1.0


def test_monitor_mode_auto_subscribes_token(monkeypatch):
    """When stop_mode includes 'monitor', the position token is auto-subscribed."""
    subscribed = []

    import app.services.exchanges.kite.ticker_manager as _tm

    async def _fake_subscribe(uid, tokens, mode="ltp"):
        subscribed.extend(tokens)

    monkeypatch.setattr(_tm, "subscribe", _fake_subscribe)

    cfg = EngineConfigModel(auto_execute=True, stop_mode="monitor",
                            directional_mode=True, vehicle="futures",
                            enabled_vehicles=["futures"])
    client = FakeClient(instruments=_fut_dump())
    state.reset(UID)
    positions._positions.pop(UID, None)
    live_safety._IDEMPOTENCY_CACHE.clear()
    state.set_config(UID, cfg)
    cb = service._make_place_cb(client, UID)
    asyncio.run(cb(_bear_row(), _item()))
    open_pos = positions.open_positions(UID)
    assert open_pos, "position should be registered"
    assert open_pos[0].token in subscribed, "position token should be subscribed to ticker"


# ── "both"-mode cross guard ───────────────────────────────────────────────────

def test_both_mode_blocks_second_entry_on_same_underlying():
    """In scan_source='both', once an underlying has a position, a second signal on
    the same underlying (the other mode) is blocked — no double-trading one move."""
    client = FakeClient()
    state.reset(UID)
    positions._positions.pop(UID, None)
    live_safety._IDEMPOTENCY_CACHE.clear()
    state.set_config(UID, EngineConfigModel(scan_source="both"))
    positions.register(positions.OpenPosition(
        uid=UID, symbol="NIFTY-EXISTING", exchange="NFO", token=1, qty=75,
        status=positions.OPEN, underlying="NIFTY 50"))
    cb = service._make_place_cb(client, UID)
    asyncio.run(cb(_bear_row(source="derivatives"), _item()))
    assert not client.opt_placed  # blocked by the cross guard


def test_both_mode_allows_first_entry():
    client = FakeClient()
    assert len(_run(EngineConfigModel(scan_source="both"), _bear_row(source="spot"), client)) == 1


# ── session-time entry gate ───────────────────────────────────────────────────

def test_minutes_to_close():
    assert market_hours.minutes_to_close(datetime(2026, 7, 16, 15, 20, tzinfo=_IST)) == pytest.approx(10.0)
    assert market_hours.minutes_to_close(datetime(2026, 7, 16, 8, 0, tzinfo=_IST)) is None   # pre-open
    assert market_hours.minutes_to_close(datetime(2026, 7, 18, 12, 0, tzinfo=_IST)) is None  # Saturday


def test_session_gate_blocks_entry_near_close(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(service,"entry_data_block_reason",lambda *a,**kw:"entry_close_buffer")
    monkeypatch.setattr(service.market_hours, "minutes_to_close", lambda *a, **k: 3.0)
    open_pos = _run(EngineConfigModel(block_entry_minutes_before_close=5), _bear_row("spot"), client)
    assert open_pos == [] and not client.opt_placed


def test_session_gate_off_by_default(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(service.market_hours, "minutes_to_close", lambda *a, **k: 1.0)
    # default block=0 → gate inert even 1 min from close
    assert len(_run(EngineConfigModel(), _bear_row("spot"), client)) == 1


# ── option-leg liquidity gate ─────────────────────────────────────────────────

def test_passes_liquidity_predicate():
    assert service._passes_liquidity(None, 5.0, 100)[0] is False  # enabled gate needs evidence
    wide = {"depth": {"buy": [{"price": 90}], "sell": [{"price": 110}]}, "oi": 500}
    ok, why = service._passes_liquidity(wide, 5.0, 100)
    assert not ok and "spread" in why                              # 20% spread
    tight = {"depth": {"buy": [{"price": 99}], "sell": [{"price": 101}]}, "oi": 500}
    assert service._passes_liquidity(tight, 5.0, 100)[0] is True   # 2% spread ok
    thin = {"depth": {"buy": [{"price": 99}], "sell": [{"price": 101}]}, "oi": 50}
    ok, why = service._passes_liquidity(thin, 5.0, 100)
    assert not ok and "OI" in why


class _QuoteClient(FakeClient):
    def __init__(self, quote):
        super().__init__()
        self._q = quote
    async def get_quote(self, syms):
        return {s: self._q for s in syms}


def test_liquidity_gate_blocks_wide_spread():
    client = _QuoteClient({"depth": {"buy": [{"price": 90}], "sell": [{"price": 110}]}, "oi": 500})
    assert _run(EngineConfigModel(max_spread_pct=5.0), _bear_row("spot"), client) == []
    assert not client.opt_placed


# ── INR daily-loss breaker ────────────────────────────────────────────────────

def test_daily_pnl_accumulates_and_resets_per_day():
    state.reset("p1")
    assert state.daily_realized_pnl("p1", day_iso="2026-07-16") == 0.0
    state.record_realized_pnl("p1", -100, day_iso="2026-07-16")
    state.record_realized_pnl("p1", -50, day_iso="2026-07-16")
    assert state.daily_realized_pnl("p1", day_iso="2026-07-16") == -150
    state.record_realized_pnl("p1", -10, day_iso="2026-07-17")  # new day resets
    assert state.daily_realized_pnl("p1", day_iso="2026-07-17") == -10
    assert state.daily_realized_pnl("p1", day_iso="2026-07-16") == 0.0


def test_daily_loss_breaker_halts_entries():
    client = FakeClient(balance=100_000.0)  # small account: 2% of ₹100,000 = ₹2,000
    state.reset(UID)
    positions._positions.pop(UID, None)
    live_safety._IDEMPOTENCY_CACHE.clear()
    state.set_config(UID, EngineConfigModel(max_daily_loss_pct=2.0))  # 2% = ₹2000
    state.record_realized_pnl(UID, -2500)  # today's loss exceeds the budget
    cb = service._make_place_cb(client, UID)
    asyncio.run(cb(_bear_row("spot"), _item()))
    assert not client.opt_placed


def test_daily_loss_breaker_allows_within_budget():
    client = FakeClient()
    state.reset(UID)
    positions._positions.pop(UID, None)
    live_safety._IDEMPOTENCY_CACHE.clear()
    state.set_config(UID, EngineConfigModel(max_daily_loss_pct=5.0))  # 5% = ₹5000
    state.record_realized_pnl(UID, -1000)  # within budget
    cb = service._make_place_cb(client, UID)
    asyncio.run(cb(_bear_row("spot"), _item()))
    assert client.opt_placed


def _run_keep_breaker(cfg, row, client, item=None):
    """Like _run but does NOT reset state (keeps the breaker peak primed)."""
    positions._positions.pop(UID, None)
    state.set_config(UID, cfg)
    cb = service._make_place_cb(client, UID)
    asyncio.run(cb(row, item or _item()))
    return positions.open_positions(UID)


# ── Trail update (_new_trail_for_open) ────────────────────────────────────────

def _make_signal_row(underlying="NIFTY 50", stop_loss=24800.0, symbol="NIFTY2562625000PE",
                     premium_sl=85.0):
    return EngineSignalRow(
        underlying=underlying, token=256265, exchange="NFO",
        regime="BEAR", alignment=AlignmentChip(fast=-1, mid=-1, slow=-1),
        direction="short", option_type="PE",
        legs=[OptionLeg(moneyness="ATM", option_type="PE",
                        option_symbol=symbol, strike=25000, expiry=_future_expiry(10),
                        premium_spot=120.0, premium_sl=premium_sl, token=111)],
        spot=25000.0, stop_loss=stop_loss, score=85.0,
        timestamp_ms=1_700_000_000_000)


def test_new_trail_futures_long_tightens():
    p = positions.OpenPosition(
        uid="tx", symbol="NIFTY26JUNFUT", exchange="NFO", token=1,
        qty=75, stop_premium=24700.0, direction="long", vehicle="futures",
        underlying="NIFTY 50", status=positions.OPEN)
    row = _make_signal_row(stop_loss=24850.0)  # higher → tighter for a long
    new_sl = service._new_trail_for_open(p, [row])
    assert new_sl == 24850.0


def test_new_trail_futures_long_no_update_when_wider():
    p = positions.OpenPosition(
        uid="tx", symbol="NIFTY26JUNFUT", exchange="NFO", token=1,
        qty=75, stop_premium=24900.0, direction="long", vehicle="futures",
        underlying="NIFTY 50", status=positions.OPEN)
    row = _make_signal_row(stop_loss=24700.0)  # lower → would widen — skip
    assert service._new_trail_for_open(p, [row]) is None


def test_new_trail_futures_short_tightens():
    p = positions.OpenPosition(
        uid="tx", symbol="BANKNIFTY25JULFUT", exchange="NFO", token=2,
        qty=15, stop_premium=48500.0, direction="short", vehicle="futures",
        underlying="NIFTY BANK", status=positions.OPEN)
    row = _make_signal_row(underlying="NIFTY BANK", stop_loss=48200.0)  # lower → tighter for short
    new_sl = service._new_trail_for_open(p, [row])
    assert new_sl == 48200.0


def test_new_trail_futures_short_no_update_when_wider():
    p = positions.OpenPosition(
        uid="tx", symbol="BANKNIFTY25JULFUT", exchange="NFO", token=2,
        qty=15, stop_premium=48000.0, direction="short", vehicle="futures",
        underlying="NIFTY BANK", status=positions.OPEN)
    row = _make_signal_row(underlying="NIFTY BANK", stop_loss=48800.0)  # higher → would widen
    assert service._new_trail_for_open(p, [row]) is None


def test_new_trail_futures_translates_the_underlying_level_by_the_entry_basis():
    """The trail arrives as an UNDERLYING level; the position's stop is a FUTURES
    price. Without the basis the two are compared in different units, and on a
    premium basis the underlying level never clears the futures stop — the trail
    silently stops ratcheting for the life of the position."""
    p = positions.OpenPosition(
        uid="tx", symbol="NIFTY26JUNFUT", exchange="NFO", token=1,
        qty=75, stop_premium=24_780.0, direction="long", vehicle="futures",
        underlying="NIFTY 50", status=positions.OPEN,
        entry_premium=25_080.0, entry_spot=25_000.0)     # basis +80
    row = _make_signal_row(stop_loss=24_850.0)
    # 24,850 underlying + 80 basis = 24,930, which tightens 24,780.
    assert service._new_trail_for_open(p, [row]) == pytest.approx(24_930.0)


def test_new_trail_futures_without_an_entry_spot_leaves_the_level_alone():
    """A position written before futures entries were priced in their own domain has
    no entry spot. Subtracting a zero spot from a real entry price would add the whole
    contract price as 'basis' and push the stop tens of thousands of points away."""
    p = positions.OpenPosition(
        uid="tx", symbol="NIFTY26JUNFUT", exchange="NFO", token=1,
        qty=75, stop_premium=24_700.0, direction="long", vehicle="futures",
        underlying="NIFTY 50", status=positions.OPEN,
        entry_premium=25_000.0, entry_spot=0.0)          # legacy row
    row = _make_signal_row(stop_loss=24_850.0)
    assert service._new_trail_for_open(p, [row]) == pytest.approx(24_850.0)


def test_new_trail_otm_options_tightens():
    """OTM option long: stop is a premium floor — tighter = higher floor."""
    p = positions.OpenPosition(
        uid="tx", symbol="NIFTY2562625000PE", exchange="NFO", token=111,
        qty=75, stop_premium=80.0, direction="long", vehicle="otm_options",
        underlying="NIFTY 50", status=positions.OPEN)
    row = _make_signal_row(symbol="NIFTY2562625000PE", premium_sl=90.0)  # higher → tighter floor
    assert service._new_trail_for_open(p, [row]) == 90.0


def test_new_trail_otm_options_no_update_when_lower():
    p = positions.OpenPosition(
        uid="tx", symbol="NIFTY2562625000PE", exchange="NFO", token=111,
        qty=75, stop_premium=95.0, direction="long", vehicle="otm_options",
        underlying="NIFTY 50", status=positions.OPEN)
    row = _make_signal_row(symbol="NIFTY2562625000PE", premium_sl=85.0)
    assert service._new_trail_for_open(p, [row]) is None


def test_new_trail_no_matching_row():
    p = positions.OpenPosition(
        uid="tx", symbol="NIFTY26JUNFUT", exchange="NFO", token=1,
        qty=75, stop_premium=24700.0, direction="long", vehicle="futures",
        underlying="NIFTY 50", status=positions.OPEN)
    row = _make_signal_row(underlying="NIFTY BANK")  # different asset
    assert service._new_trail_for_open(p, [row]) is None


# ── D3: deep-ITM & spot-OTM trail by re-translating the fresh underlying ST level ─

def test_new_trail_deep_itm_retranslates_and_tightens():
    """A deep-ITM position has no premium_sl on the scan row, so the trail must be
    re-derived from the fresh underlying ST level via the stored entry delta. As the
    ST trail ratchets toward spot, the premium stop rises."""
    p = positions.OpenPosition(
        uid="tx", symbol="NIFTY26JUN24000CE", exchange="NFO", token=9,
        qty=75, stop_premium=420.0, initial_stop_premium=420.0, direction="long",
        vehicle="deep_itm_options", underlying="NIFTY 50", status=positions.OPEN,
        entry_premium=520.0, entry_delta=0.9, entry_spot=25000.0, strike=24000.0)
    # bull CE: fresh ST trail rose 24800 → 24900 (closer to spot) → tighter premium stop
    row = _make_signal_row(underlying="NIFTY 50", stop_loss=24900.0)
    new_sl = service._new_trail_for_open(p, [row])
    assert new_sl == pytest.approx(520.0 + 0.9 * (24900.0 - 25000.0))  # 430.0
    assert new_sl > p.stop_premium


def test_new_trail_spot_otm_retranslates_when_symbol_differs():
    """A spot-mode OTM leg's option_symbol drifts as spot moves (ATM re-picks), so
    the by-symbol premium_sl match misses — the trail falls back to delta translation
    off the underlying ST level."""
    p = positions.OpenPosition(
        uid="tx", symbol="NIFTY26JUN25000CE", exchange="NFO", token=9,
        qty=75, stop_premium=420.0, initial_stop_premium=420.0, direction="long",
        vehicle="otm_options", underlying="NIFTY 50", status=positions.OPEN,
        entry_premium=520.0, entry_delta=0.5, entry_spot=25000.0)
    row = EngineSignalRow(
        underlying="NIFTY 50", token=256265, exchange="NFO", regime="BULL",
        alignment=AlignmentChip(fast=1, mid=1, slow=1), direction="long", option_type="CE",
        legs=[OptionLeg(moneyness="ATM", option_type="CE", option_symbol="NIFTY26JUN25100CE",
                        strike=25100, expiry=_future_expiry(10), lot_size=75)],  # no premium_sl
        spot=25050.0, stop_loss=24900.0, score=85.0, timestamp_ms=1_700_000_000_000, source="spot")
    new_sl = service._new_trail_for_open(p, [row])
    assert new_sl == pytest.approx(520.0 + 0.5 * (24900.0 - 25000.0))  # 470.0


# ── Expiry square-off guard ───────────────────────────────────────────────────

def test_is_expiring_predicate():
    today = date(2026, 7, 16)
    assert service._is_expiring("2026-07-17", today, within_days=1) is True   # T-1
    assert service._is_expiring("2026-07-16", today, within_days=1) is True   # today
    assert service._is_expiring("2026-07-10", today, within_days=1) is True   # already past
    assert service._is_expiring("2026-07-20", today, within_days=1) is False  # 4 days out
    assert service._is_expiring("2026-07-17", today, within_days=0) is False  # disabled
    assert service._is_expiring("", today, within_days=1) is False            # unknown
    assert service._is_expiring("garbage", today, within_days=1) is False


def test_square_off_expiring_exits_position(monkeypatch):
    """A held option within the square-off window is market-exited on the next scan
    so it can't ride into expiry unmanaged."""
    monkeypatch.setattr(service, "is_market_open", lambda *a, **k: True)
    import app.services.exchanges.kite.ticker_manager as _tm
    monkeypatch.setattr(_tm, "unsubscribe", lambda uid, tokens: _async_none())
    monkeypatch.setattr("app.services.kite_engine.monitor.state.clear_auto_open",
                        lambda *a, **k: None)

    state.reset(UID)
    positions._positions.pop(UID, None)
    state.set_config(UID, EngineConfigModel(expiry_square_off_days=1))
    positions.register(positions.OpenPosition(
        uid=UID, symbol="NIFTY25JUL24000CE", exchange="NFO", token=42, qty=75,
        entry_premium=100, stop_premium=80, status=positions.OPEN, direction="long",
        vehicle="otm_options", underlying="NIFTY 50", guard_key="NIFTY 50",
        expiry=_future_expiry(0)))  # expires today → within T-1

    client = FakeClient()
    asyncio.run(service._square_off_expiring(client, UID))
    p = positions.get(UID, "NIFTY25JUL24000CE")
    asyncio.run(confirm_exit(UID, 520))
    assert p.status == positions.CLOSED
    assert client.opt_placed and client.opt_placed[0]["side"] == "sell"


def test_square_off_skips_when_not_expiring(monkeypatch):
    monkeypatch.setattr(service, "is_market_open", lambda *a, **k: True)
    state.reset(UID)
    positions._positions.pop(UID, None)
    state.set_config(UID, EngineConfigModel(expiry_square_off_days=1))
    positions.register(positions.OpenPosition(
        uid=UID, symbol="NIFTY25JUL24000CE", exchange="NFO", token=42, qty=75,
        entry_premium=100, stop_premium=80, status=positions.OPEN, direction="long",
        vehicle="otm_options", underlying="NIFTY 50", expiry=_future_expiry(20)))
    client = FakeClient()
    asyncio.run(service._square_off_expiring(client, UID))
    assert positions.get(UID, "NIFTY25JUL24000CE").status == positions.OPEN
    assert not client.opt_placed


def _async_none():
    async def _n():
        return None
    return _n()


# ── Time-stop (mechanics-sweep finding; opt-in) ───────────────────────────────

def _register_aged(bars_ago: int, symbol="NIFTY25JUL24000CE"):
    now_ms = int(datetime.now(_IST).timestamp() * 1000)
    return positions.register(positions.OpenPosition(
        uid=UID, symbol=symbol, exchange="NFO", token=7, qty=75,
        entry_premium=100, stop_premium=80, status=positions.OPEN, direction="long",
        vehicle="otm_options", underlying="NIFTY 50", guard_key="NIFTY 50",
        opened_ms=now_ms - bars_ago * 3_600_000, expiry=_future_expiry(20)))


def test_time_stop_squares_off_aged_position(monkeypatch):
    monkeypatch.setattr(service, "is_market_open", lambda *a, **k: True)
    monkeypatch.setattr(service.market_hours, "is_market_open", lambda *a, **k: True)
    monkeypatch.setattr("app.services.kite_engine.monitor.state.clear_auto_open", lambda *a, **k: None)
    import app.services.exchanges.kite.ticker_manager as _tm
    monkeypatch.setattr(_tm, "unsubscribe", lambda uid, tokens: _async_none())
    state.reset(UID)
    positions._positions.pop(UID, None)
    state.set_config(UID, EngineConfigModel(time_stop_bars=48, expiry_square_off_days=0))
    _register_aged(60)  # 60 bars held ≥ 48
    client = FakeClient()
    asyncio.run(service._time_stop_positions(client, UID))
    p = positions.get(UID, "NIFTY25JUL24000CE")
    asyncio.run(confirm_exit(UID, 520))
    assert p.status == positions.CLOSED
    assert client.opt_placed and client.opt_placed[0]["side"] == "sell"


def test_time_stop_holds_young_position(monkeypatch):
    monkeypatch.setattr(service, "is_market_open", lambda *a, **k: True)
    state.reset(UID)
    positions._positions.pop(UID, None)
    state.set_config(UID, EngineConfigModel(time_stop_bars=48))
    _register_aged(10)  # 10 bars < 48
    client = FakeClient()
    asyncio.run(service._time_stop_positions(client, UID))
    assert positions.get(UID, "NIFTY25JUL24000CE").status == positions.OPEN
    assert not client.opt_placed


def test_time_stop_off_by_default(monkeypatch):
    monkeypatch.setattr(service, "is_market_open", lambda *a, **k: True)
    state.reset(UID)
    positions._positions.pop(UID, None)
    state.set_config(UID, EngineConfigModel())  # time_stop_bars=0 → inert
    _register_aged(999)
    client = FakeClient()
    asyncio.run(service._time_stop_positions(client, UID))
    assert positions.get(UID, "NIFTY25JUL24000CE").status == positions.OPEN
    assert not client.opt_placed


def test_new_trail_deep_itm_no_update_when_wider():
    p = positions.OpenPosition(
        uid="tx", symbol="NIFTY26JUN24000CE", exchange="NFO", token=9,
        qty=75, stop_premium=480.0, initial_stop_premium=420.0, direction="long",
        vehicle="deep_itm_options", underlying="NIFTY 50", status=positions.OPEN,
        entry_premium=520.0, entry_delta=0.9, entry_spot=25000.0)
    # ST trail slipped back (24700 < spot) → would loosen → no update (ratchet)
    row = _make_signal_row(underlying="NIFTY 50", stop_loss=24700.0)
    assert service._new_trail_for_open(p, [row]) is None


@pytest.fixture(autouse=True)
def _valid_entry_data_for_execution_plumbing(monkeypatch):
    """Historical row fixtures isolate execution; session gates tested separately."""
    from app.services.kite_engine import service as execution
    monkeypatch.setattr(execution, "entry_data_block_reason", lambda *a, **kw: "")

from tests.engines.sterling_kite_engine.execution_fixtures import confirm_exit
