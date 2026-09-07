"""Lifecycle tests for ORB restart recovery, ticket identity, and disarm."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.services import nifty_orb_lifecycle as life
from app.services.kite_engine import positions

IST = ZoneInfo("Asia/Kolkata")


def test_ticket_fingerprint_is_stable_for_manual_and_auto():
    plan = {
        "quantity": 75,
        "stop_premium": 14.0,
        "target_premium": 26.0,
        "underlying_entry": 25000.0,
        "contract": {
            "symbol": "NIFTY26AUG25000CE",
            "option_type": "CE",
            "strike": 25000,
            "expiry": "2026-08-27",
            "lot_size": 75,
        },
    }
    signal = {"direction": "LONG", "timestamp": "2026-08-25T10:30:00+05:30"}
    a = life.ticket_fingerprint(plan, signal)
    b = life.ticket_fingerprint(dict(plan), dict(signal))
    assert a == b
    assert "NIFTY26AUG25000CE" in a
    assert "LONG" in a
    fields = life.ticket_fields(plan)
    assert set(fields) == set(life.SAME_TICKET_FIELDS)
    row = {"status": "signal", "trade": plan, "signal": signal}
    stamped = life.attach_ticket(dict(row))
    assert stamped["ticket_fingerprint"] == a
    assert stamped["ticket"]["quantity"] == 75
    assert stamped["ticket"]["symbol"] == "NIFTY26AUG25000CE"


def test_fingerprint_moves_when_lot_or_underlying_entry_moves():
    """SAME_TICKET_FIELDS must all participate in identity — a silent drift
    in lot_size or underlying_entry used to keep the same fingerprint."""
    plan = {
        "quantity": 150,
        "stop_premium": 14.0,
        "target_premium": 26.0,
        "underlying_entry": 25000.0,
        "contract": {
            "symbol": "NIFTY26AUG25000CE",
            "option_type": "CE",
            "strike": 25000,
            "expiry": "2026-08-27",
            "lot_size": 75,
        },
    }
    signal = {"direction": "LONG", "timestamp": "2026-08-25T10:30:00+05:30"}
    base = life.ticket_fingerprint(plan, signal)
    fields = life.ticket_fields(plan)
    for key in life.SAME_TICKET_FIELDS:
        assert str(fields[key] or "") in base

    lot = dict(plan)
    lot["contract"] = {**plan["contract"], "lot_size": 50}
    assert life.ticket_fingerprint(lot, signal) != base

    entry = dict(plan)
    entry["underlying_entry"] = 25010.0
    assert life.ticket_fingerprint(entry, signal) != base


def test_manual_mode_response_is_signals_only():
    out = life.manual_mode_response()
    assert out["status"] == "manual"
    assert out["mode"] == "signals_only"
    assert out["executed"] == []
    assert "Same signal" in out["message"]


def test_preview_auto_refusal_matches_execute_scan_reasons():
    """Manual board shows the same refusal Auto would emit (no broker needed)."""
    from datetime import timedelta
    from app.engines.nifty_orb_options import StrategyConfig

    now = datetime(2026, 8, 25, 10, 30, tzinfo=IST)
    cfg = StrategyConfig(enabled=True, interval_minutes=5, max_trades_per_day=2)
    row = {
        "status": "signal",
        "trade": {
            "quantity": 75,
            "stop_premium": 14.0,
            "target_premium": 26.0,
            "contract": {
                "symbol": "NIFTY26AUG25000CE",
                "option_type": "CE",
                "strike": 25000,
                "expiry": "2026-08-27",
                "lot_size": 75,
            },
        },
        "signal": {"direction": "LONG", "timestamp": now.isoformat()},
    }
    assert life.preview_auto_refusal(row, cfg, now=now, filled_today=0, max_trades=2) is None

    pe = dict(row)
    pe["trade"] = {**row["trade"], "contract": {**row["trade"]["contract"], "option_type": "PE"}}
    assert life.preview_auto_refusal(pe, cfg, now=now) == "option direction mismatch"

    stale = dict(row)
    stale["signal"] = {"direction": "LONG", "timestamp": (now - timedelta(hours=2)).isoformat()}
    reason = life.preview_auto_refusal(stale, cfg, now=now)
    assert reason and "stale" in reason

    assert life.preview_auto_refusal(row, cfg, now=now, filled_today=2, max_trades=2) == "daily trade limit reached"

    early = now.replace(hour=9, minute=20)
    assert life.preview_auto_refusal(row, cfg, now=early) == "outside entry window"

    saturday = datetime(2026, 8, 29, 10, 30, tzinfo=IST)
    assert life.preview_auto_refusal(row, cfg, now=saturday) == "market_closed"

    far = dict(row)
    far["trade"] = {**row["trade"], "contract": {**row["trade"]["contract"], "expiry": "2026-12-31"}}
    assert life.preview_auto_refusal(far, cfg, now=now) == "contract outside configured expiry policy"

    thin = dict(row)
    thin["trade"] = {**row["trade"], "contract": {**row["trade"]["contract"], "volume": 10, "open_interest": 50_000}}
    assert life.preview_auto_refusal(thin, cfg, now=now) == "option liquidity below configured minimum"


def test_untagged_kite_positions_are_not_orb_positions(monkeypatch):
    """Empty vehicle used to match every leftover kite row."""
    positions.reset("u1")
    positions.register(
        positions.OpenPosition(
            uid="u1",
            symbol="SBIN",
            exchange="NSE",
            qty=1,
            vehicle="",
            status=positions.OPEN,
        )
    )
    positions.register(
        positions.OpenPosition(
            uid="u1",
            symbol="NIFTY26AUG25000CE",
            exchange="NFO",
            qty=75,
            vehicle="otm_options",
            order_id="ORB-1",
            status=positions.OPEN,
        )
    )
    held = {p.symbol for p in life.orb_open_positions("u1")}
    assert "NIFTY26AUG25000CE" in held
    assert "SBIN" not in held


def test_recover_trade_state_lists_open_orb_underlyings(monkeypatch):
    store = {}

    def fake_state(uid):
        return store.setdefault(uid, {"date": "2026-08-25", "count": 1, "signals": []})

    def fake_save(uid, st):
        store[uid] = st

    pos = positions.OpenPosition(
        uid="u1",
        symbol="NIFTY26AUG25000CE",
        exchange="NFO",
        qty=75,
        vehicle="otm_options",
        underlying="NIFTY",
        expiry="2026-08-27",
        status=positions.OPEN,
        token=11,
    )
    monkeypatch.setattr(life, "orb_open_positions", lambda uid: [pos])
    monkeypatch.setattr("app.services.nifty_orb_execution._state", fake_state)
    monkeypatch.setattr("app.services.nifty_orb_execution._save_state", fake_save)

    report = life.recover_trade_state("u1")
    assert report["status"] == "recovered"
    assert report["count"] == 1
    assert report["underlyings"] == ["NIFTY"]
    assert store["u1"]["recovered_open"][0]["symbol"] == "NIFTY26AUG25000CE"


@pytest.mark.asyncio
async def test_disarm_cancels_gtt_and_closes_registry(monkeypatch):
    positions.reset("u1")
    positions.register(
        positions.OpenPosition(
            uid="u1",
            symbol="NIFTY26AUG25000CE",
            exchange="NFO",
            qty=75,
            gtt_id=99,
            vehicle="otm_options",
            status=positions.OPEN,
        )
    )
    cancelled = []

    async def cancel(client, gtt_id):
        cancelled.append(gtt_id)
        return "cancelled"

    monkeypatch.setattr("app.services.kite_engine.protective_stop.cancel_stop_result", cancel)
    monkeypatch.setattr("app.services.kite_engine.state.log", lambda *a, **k: None)

    out = await life.disarm_position(object(), "u1", symbol="NIFTY26AUG25000CE", reason="test")
    assert out["status"] == "disarmed"
    assert cancelled == [99]
    assert positions.get("u1", "NIFTY26AUG25000CE").status == positions.CLOSED


@pytest.mark.asyncio
async def test_square_off_expired_sells_then_disarms(monkeypatch):
    positions.reset("u1")
    positions.register(
        positions.OpenPosition(
            uid="u1",
            symbol="NIFTY26AUG25000CE",
            exchange="NFO",
            qty=75,
            vehicle="otm_options",
            expiry="2026-08-25",
            status=positions.OPEN,
        )
    )
    sold = []

    async def sell(client, symbol, exchange, qty):
        sold.append((symbol, qty))
        return True, "closed"

    monkeypatch.setattr("app.services.nifty_orb_execution._sell_and_verify", sell)
    monkeypatch.setattr("app.services.kite_engine.state.log", lambda *a, **k: None)

    async def disarm(client, uid, *, symbol, reason="disarmed"):
        positions.close(uid, symbol, reason=reason)
        return {"status": "disarmed", "symbol": symbol}

    monkeypatch.setattr(life, "disarm_position", disarm)
    today = datetime(2026, 8, 25, 15, 0, tzinfo=IST)
    out = await life.square_off_expired(object(), "u1", today=today)
    assert sold == [("NIFTY26AUG25000CE", 75)]
    assert out["squared"][0]["sold"] is True
