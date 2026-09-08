"""Past ORB fires stay on the board the way SuperTrend history does."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from app.services.nifty_orb_lifecycle import (
    fired_row_key,
    merge_retained_signals,
    remember_fired_signals,
    ticket_fingerprint,
)

IST = ZoneInfo("Asia/Kolkata")
NOW_MS = int(datetime(2026, 8, 21, 16, 0, tzinfo=IST).timestamp() * 1000)


def _signal(ts="2026-08-21T10:30:00+05:30", status="signal", underlying="NIFTY"):
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
    signal = {"direction": "LONG", "timestamp": ts, "reason": "ORB high break"}
    return {
        "status": status,
        "underlying": underlying,
        "signal": signal,
        "trade": plan,
        "ticket_fingerprint": ticket_fingerprint(plan, signal),
    }


def test_a_quiet_rescan_keeps_the_morning_fire_as_ended():
    live = [{"status": "watching", "underlying": "NIFTY", "signal": {"direction": "NONE", "reason": "outside entry window"}}]
    prior = [_signal()]
    out = merge_retained_signals(live, prior, now_ms=NOW_MS)
    ended = [r for r in out if r["status"] == "ended"]
    assert len(ended) == 1
    assert ended[0]["ticket_fingerprint"] == prior[0]["ticket_fingerprint"]
    assert "auto_block" not in ended[0]


def test_the_live_ticket_is_not_duplicated_as_history():
    live = [_signal()]
    out = merge_retained_signals(live, [_signal()], now_ms=NOW_MS)
    assert [r["status"] for r in out] == ["signal"]


def test_a_new_fire_keeps_the_old_one_as_past():
    morning = _signal(ts="2026-08-21T10:30:00+05:30")
    later = _signal(ts="2026-08-21T11:15:00+05:30")
    out = merge_retained_signals([later], [morning], now_ms=NOW_MS)
    statuses = {r["status"] for r in out}
    assert statuses == {"signal", "ended"}
    assert fired_row_key(morning) != fired_row_key(later)


def test_aged_out_fires_are_dropped():
    old = _signal(ts="2026-01-01T10:30:00+05:30")
    live = [{"status": "watching", "underlying": "NIFTY", "signal": {"direction": "NONE"}}]
    out = merge_retained_signals(live, [old], now_ms=NOW_MS)
    assert [r["status"] for r in out] == ["watching"]


def test_remember_round_trips_through_the_store(monkeypatch):
    store: dict[str, str] = {}

    def get_config(key):
        return store.get(key)

    def set_config(key, value):
        store[key] = value

    monkeypatch.setattr("app.services.db.get_config", get_config)
    monkeypatch.setattr("app.services.db.set_config", set_config)

    first = remember_fired_signals("u1", [_signal()], now_ms=NOW_MS)
    assert first[0]["status"] == "signal"
    quiet = [{"status": "watching", "underlying": "NIFTY", "signal": {"direction": "NONE", "reason": "outside entry window"}}]
    second = remember_fired_signals("u1", quiet, now_ms=NOW_MS)
    assert any(r["status"] == "ended" for r in second)
    assert any(r.get("ticket_fingerprint") == first[0]["ticket_fingerprint"] for r in second)
    from app.services.nifty_orb_lifecycle import fired_replay_is_warm
    assert fired_replay_is_warm("u1") is True


def test_session_walk_recovers_a_morning_fire_after_the_window():
    from tests.engines.test_nifty_orb_options import orb_session
    from app.engines.nifty_orb_options import StrategyConfig
    from app.services.nifty_orb_scanner import session_fire_transitions
    bars = orb_session("LONG")
    cfg = StrategyConfig()
    as_of = datetime(2026, 8, 18, 15, 40, tzinfo=IST)
    fires = session_fire_transitions(bars, cfg, as_of=as_of)
    assert fires
    assert fires[0].direction == "LONG"


def test_session_walk_recovers_yesterdays_fire_after_midnight():
    """The overnight board used to walk only *today*, which has no bars yet."""
    from tests.engines.test_nifty_orb_options import orb_session
    from app.engines.nifty_orb_options import StrategyConfig
    from app.services.nifty_orb_scanner import session_fire_transitions
    bars = orb_session("LONG")
    cfg = StrategyConfig()
    as_of = datetime(2026, 8, 19, 2, 32, tzinfo=IST)
    fires = session_fire_transitions(bars, cfg, as_of=as_of)
    assert fires
    assert fires[0].direction == "LONG"


def test_session_walk_keeps_an_or_break_that_failed_later_gates():
    """A choppy RANGE break still printed. Hiding it is why the dock stayed empty."""
    from tests.engines.test_nifty_orb_options import _opening_range_bars, _bar
    from app.engines.nifty_orb_options import StrategyConfig, generate_signal
    from app.services.nifty_orb_scanner import session_fire_transitions
    rows = _opening_range_bars()
    price = 24000.0
    steps = [40, -30] * 11 + [42]
    for index, step in enumerate(steps):
        price += step
        last = index == len(steps) - 1
        rows.append(_bar(len(rows), price - step, price, 3000 if last else 1000))
    probe = generate_signal(rows, StrategyConfig())
    assert probe.direction == "NONE"
    as_of = datetime(2026, 8, 18, 15, 40, tzinfo=IST)
    fires = session_fire_transitions(rows, StrategyConfig(), as_of=as_of)
    assert fires
    assert fires[0].direction == "LONG"
    assert fires[0].reason.startswith("ORB high break")


def test_reconstructed_ended_row_reuses_the_stored_ticket():
    reconstructed = {
        "status": "ended",
        "underlying": "NIFTY",
        "signal": {"direction": "LONG", "timestamp": "2026-08-21T10:30:00+05:30", "reason": "ORB high break"},
        "trade": None,
    }
    stored = _signal()
    out = merge_retained_signals([reconstructed], [stored], now_ms=NOW_MS)
    ended = [r for r in out if r["status"] == "ended"]
    assert len(ended) == 1
    assert ended[0]["trade"]["quantity"] == 75
    assert ended[0]["ticket_fingerprint"]


def test_history_bar_limit_covers_fifteen_sessions():
    from app.services.nifty_orb_scanner import history_bar_limit
    # 09:15–15:30 is 75 five-minute bars. 240 used to cover ~3 sessions.
    assert history_bar_limit(5) >= 15 * 75
    assert history_bar_limit(5) <= 2000


def test_warm_replay_does_not_rewalk_yesterday():
    """After persist is warm, reconstruction is today-only; persist holds older fires."""
    from tests.engines.test_nifty_orb_options import orb_session
    from app.engines.nifty_orb_options import StrategyConfig
    from app.services.nifty_orb_scanner import session_fire_transitions
    bars = orb_session("LONG")
    cfg = StrategyConfig()
    since = datetime(2026, 8, 19, 0, 0, tzinfo=IST)
    as_of = datetime(2026, 8, 19, 2, 32, tzinfo=IST)
    assert session_fire_transitions(bars, cfg, as_of=as_of, since=since) == []


def test_five_minute_history_window_is_weeks_not_months():
    from app.services.exchanges.kite.client import _historical_days_needed
    # 15 sessions of 5m used to request 205 days via n_bars/6.
    assert _historical_days_needed("5minute", 1155) <= 30
    assert _historical_days_needed("5minute", 1155) >= 15
    assert _historical_days_needed("60minute", 2000) >= 300