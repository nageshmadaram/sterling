import pytest

from app.engines.sterling_kite_engine.config import SterlingKiteEngineConfig
from app.engines.sterling_kite_engine.schemas import EngineConfigModel
from app.services.kite_engine import state
from tests.engines.sterling_kite_engine.canonical_double import CanonicalBrokerDouble


def test_config_store_roundtrip():
    state.reset("u1")
    assert state.get_config("u1").trail_target == "fast"  # default
    cfg = EngineConfigModel(trail_target="slow", strike_moneyness=["ATM", "ITM1"], auto_execute=True)
    state.set_config("u1", cfg)
    got = state.get_config("u1")
    assert got.trail_target == "slow" and got.strike_moneyness == ["ATM", "ITM1"] and got.auto_execute


def test_engine_enabled_false_persists_across_memory_reset(monkeypatch):
    monkeypatch.setattr(state, "db", _FakeDB())
    state.reset("engine_off")
    state.set_config("engine_off", EngineConfigModel(engine_enabled=False))
    state._config.pop("engine_off", None)

    got = state.get_config("engine_off")

    assert got.engine_enabled is False


def test_activity_log_ring_and_status():
    state.reset("u2")
    assert state.activity("u2") == []
    state.log("u2", "scan_start", "scanning")
    state.log("u2", "order_placed", "BUY NIFTY...")
    evs = state.activity("u2")
    assert [e.kind for e in evs] == ["scan_start", "order_placed"]
    assert evs[0].ts_ms > 0

    state.set_scanning("u2", True)
    assert state.status("u2").scanning
    state.mark_scan_done("u2", signal_count=3, next_in_s=300)
    s = state.status("u2")
    assert not s.scanning and s.signal_count == 3
    assert s.next_scan_ms > s.last_scan_ms


@pytest.mark.asyncio
async def test_scan_user_logs_and_marks_status(monkeypatch):
    import numpy as np
    from app.domain.models import Candle
    from app.engines.sterling_kite_engine.regime import compute_regime, entry_transitions
    from app.services.kite_engine import service

    def _candles(path):
        c = np.asarray(path, float); o = np.concatenate([[c[0]], c[:-1]])
        return [Candle(timestamp_ms=i * 3_600_000, open=float(o[i]), high=float(max(o[i], c[i]) + 1),
                       low=float(min(o[i], c[i]) - 1), close=float(c[i]), volume=1.0) for i in range(len(c))]

    cfg = SterlingKiteEngineConfig()
    full = _candles(list(np.linspace(300, 150, 60)) + list(np.linspace(150, 600, 80)))
    o = np.array([x.open for x in full], float); h = np.array([x.high for x in full], float)
    l = np.array([x.low for x in full], float); c = np.array([x.close for x in full], float)
    r = compute_regime(o, h, l, c, cfg); longs, _ = entry_transitions(r)
    idx = int(np.where(longs)[0][0])
    trimmed = full[: idx + 1]

    class FakeClient:
        async def search_instruments(self, q, exch, limit=0):
            if exch in ("NFO", "BFO"):
                return [{"name": "ACME", "tradingsymbol": "ACME300CE", "instrument_type": "CE",
                         "strike": 300, "expiry": "2099-01-01", "lot_size": 50}]
            return [{"tradingsymbol": "ACME", "instrument_token": 1, "exchange": "NSE"}]
        async def get_candles(self, inst, resolution, limit):
            return trimmed if inst.zerodha_token == 1 else _candles(list(np.linspace(100, 101, 30)))

    state.reset("u3")
    state.set_config("u3", state.get_config("u3").model_copy(update={"engine_enabled": True}))
    count = await service.scan_user(FakeClient(), "u3", interval_s=120)
    kinds = [e.kind for e in state.activity("u3")]
    assert "scan_start" in kinds and "scan_done" in kinds
    st = state.status("u3")
    assert not st.scanning and st.signal_count == count and st.next_scan_ms > 0


class _FakeDB:
    """In-memory stand-in for app.services.db config storage, so persistence is
    testable even when the real SQLite layer is unavailable in the test env."""
    def __init__(self):
        self.store = {}
    def get_config(self, key, default=""):
        return self.store.get(key, default)
    def set_config(self, key, value):
        self.store[key] = value


def test_auto_open_persists_across_memory_reset(monkeypatch):
    # mark_auto_open must survive an in-memory reset by reloading from DB —
    # this is what stops a server restart from dropping the guard.
    monkeypatch.setattr(state, "db", _FakeDB())
    state.reset("persist_u")
    state.mark_auto_open("persist_u", "NIFTY24JUN24000CE")
    assert state.is_auto_open("persist_u", "NIFTY24JUN24000CE")
    # drop ONLY the in-memory cache (simulate restart) — DB row remains
    state._auto_open.pop("persist_u", None)
    assert state.is_auto_open("persist_u", "NIFTY24JUN24000CE")  # rehydrated from DB
    # clear also persists
    state.clear_auto_open("persist_u", "NIFTY24JUN24000CE")
    state._auto_open.pop("persist_u", None)
    assert not state.is_auto_open("persist_u", "NIFTY24JUN24000CE")


def test_reconcile_auto_open_drops_stale_keeps_live(monkeypatch):
    monkeypatch.setattr(state, "db", _FakeDB())
    state.reset("recon_u")
    state.mark_auto_open("recon_u", "NIFTY24JUN24000CE")  # broker confirms this
    state.mark_auto_open("recon_u", "BANKNIFTY24JUN50000PE")  # broker says closed
    after = state.reconcile_auto_open("recon_u", {"NIFTY24JUN24000CE"})
    assert after == {"NIFTY24JUN24000CE"}
    assert state.is_auto_open("recon_u", "NIFTY24JUN24000CE")
    assert not state.is_auto_open("recon_u", "BANKNIFTY24JUN50000PE")
    # reconciled result is persisted (survives memory reset)
    state._auto_open.pop("recon_u", None)
    assert state.auto_open_underlyings("recon_u") == {"NIFTY24JUN24000CE"}


def test_broker_open_slots_emits_symbol_and_prefix():
    from app.services.kite_engine import service
    net = [
        {"tradingsymbol": "NIFTY24JUN24000CE", "quantity": 50},
        {"tradingsymbol": "RELIANCE24JUN3000PE", "quantity": -250},  # short still counts as held
        {"tradingsymbol": "INFY24JUN1500CE", "quantity": 0},  # flat → excluded
    ]
    slots = service._broker_open_slots(net)
    assert "NIFTY24JUN24000CE" in slots and "NIFTY" in slots
    assert "RELIANCE24JUN3000PE" in slots and "RELIANCE" in slots
    assert "INFY24JUN1500CE" not in slots and "INFY" not in slots


@pytest.mark.asyncio
async def test_reconcile_user_auto_open_clears_after_broker_flat():
    from app.services.kite_engine import service
    state.reset("recon_live")
    state.mark_auto_open("recon_live", "NIFTY24JUN24000CE")

    class FlatClient:
        async def get_positions_raw(self):
            return {"net": [], "day": []}  # nothing actually open at the broker

    await service.reconcile_user_auto_open(FlatClient(), "recon_live")
    assert not state.is_auto_open("recon_live", "NIFTY24JUN24000CE")


@pytest.mark.asyncio
async def test_auto_exec_one_position_guard():
    from app.engines.sterling_kite_engine.schemas import AlignmentChip, EngineSignalRow, OptionLeg
    from app.services.kite_engine import service, state

    state.reset("g1")
    placed = []

    class C(CanonicalBrokerDouble):
        async def get_margins(self,*a):
            return {"available":{"live_balance":1_000_000}}
        async def place_order_option(self, sym, side, size, **kw):
            placed.append((sym, size))
            return {"order_id": "O-" + sym}

        async def get_ltp(self, keys):
            # A spot-source row carries no premium, so the leg's LTP is what the entry
            # and the delta-implied stop are derived from. Without it stop_px stays 0
            # and auto-exec now (correctly) refuses to open an unprotectable position —
            # which would make this test pass for the wrong reason.
            return {k: {"last_price": 90.0} for k in keys}

    cb = service._make_place_cb(C(), "g1")

    def _row(ts):
        return EngineSignalRow(
            underlying="RELIANCE", token=111, exchange="NFO", regime="BULL",
            alignment=AlignmentChip(fast=1, mid=1, slow=1), direction="long", option_type="CE",
            legs=[OptionLeg(moneyness="ATM", option_type="CE", option_symbol="RELIANCE25JUN3000CE",
                            strike=3000, expiry="2026-06-26", lot_size=250)],
            spot=3010.0, stop_loss=2950.0, score=85.0, timestamp_ms=ts)

    # two DISTINCT fresh signals on the same underlying (different bars) →
    # without the guard this would place twice; with it, only once.
    await cb(_row(1000), None)
    await cb(_row(2000), None)
    assert placed == [("RELIANCE25JUN3000CE", 250)]
    assert state.is_auto_open("g1", "RELIANCE")

    # Clearing the one-position guard is no longer enough on its own: the entry
    # went through canonical execution, so a durable intent exists and nothing
    # has reconciled it. An unresolved order blocks new exposure — in a simulated
    # account as much as a live one, because the rule is about what the system
    # knows, not about whose money it is.
    from app.services.kite_engine import order_journal

    state.clear_auto_open("g1", "RELIANCE")
    await cb(_row(3000), None)
    assert len(placed) == 1
    assert any(e.kind == "order_blocked" and "Unresolved durable order intents" in e.message
               for e in state.activity("g1"))

    # Reconciliation is what lifts that block, and it is more than a broker
    # status: the fill has to reach the position registry. That projection is
    # `test_execution_lifecycle`'s subject, so this test stops at the guard it
    # is named for and asserts only that the blocker is the unresolved intent —
    # not the one-position guard, which was already cleared above.
    unresolved = order_journal.unresolved("g1")
    assert [i.symbol for i in unresolved] == ["RELIANCE25JUN3000CE"]


# ── _update_open_position_trails integration ─────────────────────────────────

@pytest.mark.asyncio
async def test_update_trails_tightens_futures_stop_and_moves_gtt(monkeypatch):
    """_update_open_position_trails updates in-memory stop and calls move_stop."""
    from app.engines.sterling_kite_engine.schemas import (
        AlignmentChip, EngineConfigModel, EngineSignalRow, OptionLeg,
    )
    from app.services.kite_engine import positions, service, state
    from app.services.kite_engine.scanner import scanner

    uid = "trail-test"
    state.reset(uid)
    positions._positions.pop(uid, None)
    state.set_config(uid, EngineConfigModel(auto_execute=True, stop_mode="both"))

    # register an open futures long at stop 24700
    positions.register(positions.OpenPosition(
        uid=uid, symbol="NIFTY26JUNFUT", exchange="NFO", token=5001,
        qty=75, lot_size=75, stop_premium=24700.0, direction="long",
        vehicle="futures", underlying="NIFTY 50",
        status=positions.OPEN, gtt_id=42))

    # inject a fresh scan row at stop 24850 (tighter for long)
    row = EngineSignalRow(
        underlying="NIFTY 50", token=256265, exchange="NFO",
        regime="BULL", alignment=AlignmentChip(fast=1, mid=1, slow=1),
        direction="long", option_type="CE",
        legs=[OptionLeg(moneyness="ATM", option_type="CE",
                        option_symbol="NIFTY26JUN25000CE", strike=25000,
                        expiry="2026-06-26", premium_sl=120.0, token=111)],
        spot=25100.0, stop_loss=24850.0, score=90.0,
        timestamp_ms=1_700_000_000_000)
    us = scanner.snapshot(uid)
    us.rows = [row]

    gtt_moved = []

    class _FakeClient:
        async def get_ltp(self, syms):
            return {s: {"last_price": 25100.0} for s in syms}
        async def modify_gtt(self, tid, **kw):
            gtt_moved.append((tid, kw.get("trigger_values")))
            return {"trigger_id": tid}

    await service._update_open_position_trails(_FakeClient(), uid)

    p = positions.open_positions(uid)[0]
    assert p.stop_premium == 24850.0, "in-memory stop should be tightened"
    assert gtt_moved, "GTT should have been modified"
    assert gtt_moved[0] == (42, [24850.0])


@pytest.mark.asyncio
async def test_update_trails_does_not_widen_stop(monkeypatch):
    """_update_open_position_trails never widens an existing stop."""
    from app.engines.sterling_kite_engine.schemas import (
        AlignmentChip, EngineConfigModel, EngineSignalRow, OptionLeg,
    )
    from app.services.kite_engine import positions, service, state
    from app.services.kite_engine.scanner import scanner

    uid = "trail-nowiden"
    state.reset(uid)
    positions._positions.pop(uid, None)
    state.set_config(uid, EngineConfigModel(auto_execute=True, stop_mode="both"))

    positions.register(positions.OpenPosition(
        uid=uid, symbol="NIFTY26JUNFUT", exchange="NFO", token=5001,
        qty=75, lot_size=75, stop_premium=24900.0, direction="long",
        vehicle="futures", underlying="NIFTY 50",
        status=positions.OPEN, gtt_id=43))

    # row has a WORSE (lower) stop than what we already have
    row = EngineSignalRow(
        underlying="NIFTY 50", token=256265, exchange="NFO",
        regime="BULL", alignment=AlignmentChip(fast=1, mid=1, slow=1),
        direction="long", option_type="CE",
        legs=[OptionLeg(moneyness="ATM", option_type="CE",
                        option_symbol="NIFTY26JUN25000CE", strike=25000,
                        expiry="2026-06-26", premium_sl=100.0, token=111)],
        spot=25100.0, stop_loss=24700.0, score=90.0,
        timestamp_ms=1_700_000_000_000)
    us = scanner.snapshot(uid)
    us.rows = [row]

    class _FakeClient:
        async def get_ltp(self, syms):
            return {s: {"last_price": 25100.0} for s in syms}

    await service._update_open_position_trails(_FakeClient(), uid)

    p = positions.open_positions(uid)[0]
    assert p.stop_premium == 24900.0, "stop must not be widened"


@pytest.mark.asyncio
async def test_update_trails_tightens_short_future_downward(monkeypatch):
    """A short future's protective stop sits ABOVE price, so it trails DOWN."""
    from app.engines.sterling_kite_engine.schemas import (
        AlignmentChip, EngineConfigModel, EngineSignalRow,
    )
    from app.services.kite_engine import positions, service, state
    from app.services.kite_engine.scanner import scanner

    uid = "trail-short"
    state.reset(uid)
    positions._positions.pop(uid, None)
    state.set_config(uid, EngineConfigModel(auto_execute=True, stop_mode="both"))

    # short future, protective stop above price at 25200
    positions.register(positions.OpenPosition(
        uid=uid, symbol="NIFTY26JUNFUT", exchange="NFO", token=5001,
        qty=75, lot_size=75, stop_premium=25200.0, direction="short",
        vehicle="futures", underlying="NIFTY 50",
        status=positions.OPEN, gtt_id=44))

    # fresh scan: ST level fell to 25050 → tighter for a short (lower stop)
    row = EngineSignalRow(
        underlying="NIFTY 50", token=256265, exchange="NFO",
        regime="BEAR", alignment=AlignmentChip(fast=-1, mid=-1, slow=-1),
        direction="short", option_type="PE", legs=[],
        spot=24900.0, stop_loss=25050.0, score=90.0,
        timestamp_ms=1_700_000_000_000)
    us = scanner.snapshot(uid)
    us.rows = [row]

    gtt_moved = []

    class _FakeClient:
        async def get_ltp(self, syms):
            return {s: {"last_price": 24900.0} for s in syms}
        async def modify_gtt(self, tid, **kw):
            gtt_moved.append((tid, kw.get("trigger_values")))
            return {"trigger_id": tid}

    await service._update_open_position_trails(_FakeClient(), uid)

    p = positions.open_positions(uid)[0]
    assert p.stop_premium == 25050.0, "short stop should tighten downward"
    assert gtt_moved and gtt_moved[0] == (44, [25050.0])


@pytest.fixture(autouse=True)
def _valid_entry_data_for_execution_plumbing(monkeypatch):
    """Historical row fixtures isolate execution; session gates tested separately."""
    from app.services.kite_engine import service as execution
    monkeypatch.setattr(execution, "entry_data_block_reason", lambda *a, **kw: "")
