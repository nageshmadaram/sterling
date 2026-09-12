"""The runtime around the three intraday strategies.

The engine tests prove each rule. These prove the things that only break in the
wiring: a config that will not round-trip, a resample that makes the replay and
the live scan disagree, and a simulation that quietly runs a different strategy
than the one on the board.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.engines.intraday import IntradayConfig
from app.services import intraday as svc

IST = timezone(timedelta(hours=5, minutes=30))


@pytest.fixture(autouse=True)
def _db(tmp_path, monkeypatch):
    monkeypatch.setenv("STERLING_DB_PATH", str(tmp_path / "t.db"))
    from app.services import db
    monkeypatch.setattr(db, "_DB_PATH", str(tmp_path / "t.db"), raising=False)
    db.init()
    svc._state.clear()
    yield
    svc._state.clear()


def _ts(day: str, hh: int, mm: int) -> float:
    y, mo, d = (int(x) for x in day.split("-"))
    return datetime(y, mo, d, hh, mm, tzinfo=IST).timestamp()


#: The window these MECHANICS tests need. The shipped default is the measured
#: afternoon window, which is a finding about the market rather than a property
#: of the scan — so a test of the scan states its own window instead of
#: inheriting one that can move when a measurement does.
ALL_DAY = dict(session_start="09:15", no_entry_after="15:10",
               close_at_session_end=True, exit_after_bars=0,
               stop_widen_mult=1.0, warmup_bars=70)


def _tape_5m(day: str = "2026-09-10", n: int = 120) -> list[dict]:
    """A tape that RISES then falls, so every rule has something to fire on.

    A monotonic tape has its ribbon cross before the window starts and never
    again, which looks exactly like a broken strategy.
    """
    out, price, i_in_day, cur = [], 200.0, 0, day
    turn = n // 2
    for i in range(n):
        if i_in_day >= 75:
            i_in_day = 0
            cur = (datetime(*(int(x) for x in cur.split("-")), tzinfo=IST)
                   + timedelta(days=1)).strftime("%Y-%m-%d")
        o = price
        price += 0.4 if i < turn else -0.6
        out.append({"time": _ts(cur, 9, 15) + i_in_day * 300, "open": o,
                    "high": max(o, price) + 0.5, "low": min(o, price) - 0.5,
                    "close": price, "volume": 5000.0})
        i_in_day += 1
    return out


# --------------------------------------------------------------------- config

class TestConfig:
    def test_an_unset_config_is_the_real_defaults(self):
        cfg = svc.get_config("u1")
        assert cfg.enabled is True
        assert cfg.as_dict() == IntradayConfig().as_dict()

    def test_a_partial_change_leaves_everything_else_alone(self):
        svc.set_config({"vs_target_points": 25.0}, "u1")
        cfg = svc.get_config("u1")
        assert cfg.vs_target_points == 25.0
        assert cfg.pb_ema_length == IntradayConfig().pb_ema_length

    def test_an_unknown_field_is_refused_not_dropped(self):
        # A silently ignored setting is worse than an error: the UI has no way
        # to tell that it did not take.
        with pytest.raises(ValueError, match="Unknown"):
            svc.set_config({"pb_ema_lenght": 9}, "u1")

    def test_an_invalid_value_never_becomes_a_trading_config(self):
        with pytest.raises(ValueError):
            svc.set_config({"rb_ema_slow": 5}, "u1")   # must exceed the faster lines
        assert svc.get_config("u1").rb_ema_slow == 55

    def test_stored_but_unreadable_falls_back_with_the_engine_off(self):
        from app.services import db
        db.set_config("intraday_config:u1", "{not json at all")
        assert svc.get_config("u1").enabled is False

    def test_lists_round_trip_as_tuples(self):
        svc.set_config({"scan_indices": ["NIFTY", "FINNIFTY"]}, "u1")
        assert svc.get_config("u1").scan_indices == ("NIFTY", "FINNIFTY")

    def test_auto_execute_is_off_by_default_and_says_why_when_on(self):
        assert IntradayConfig().auto_execute is False
        cfg = IntradayConfig(auto_execute=True).validate()
        assert any("walk-forward" in w for w in cfg.warnings())


# ------------------------------------------------------------------ resampling

def test_one_minute_tape_and_five_minute_tape_agree():
    """The replay reads 1m bars and the live scan reads 5m. Same signal or the
    replay proves nothing about the live engine."""
    five = _tape_5m(n=120)
    one: list[dict] = []
    for b in five:
        for k in range(5):
            one.append({"time": b["time"] + k * 60, "open": b["open"],
                        "high": b["high"], "low": b["low"], "close": b["close"],
                        "volume": b["volume"] / 5.0})
    cfg = IntradayConfig(warmup_bars=70).validate()
    from_five = [e.as_dict() for e in svc.evaluate_symbol(five, cfg, "NIFTY")]
    from_one = [e.as_dict() for e in svc.evaluate_symbol(one, cfg, "NIFTY")]
    assert [e["strategy"] for e in from_five] == [e["strategy"] for e in from_one]
    assert [e["state"] for e in from_five] == [e["state"] for e in from_one]


# ------------------------------------------------------------------- cooldown

def test_the_same_strategy_will_not_re_fire_inside_its_cooldown():
    cfg = IntradayConfig(cooldown_bars=6).validate()
    st = svc.status("u1")
    bar_ms = 1_757_000_000_000
    assert svc._cooldown_ok(st, cfg, "NIFTY", "pivot_break", bar_ms) is None
    svc._record_fire(st, "NIFTY", "pivot_break", bar_ms)
    blocked = svc._cooldown_ok(st, cfg, "NIFTY", "pivot_break", bar_ms + 5 * 60_000)
    assert blocked and "cooling down" in blocked
    # And a different strategy on the same symbol is unaffected.
    assert svc._cooldown_ok(st, cfg, "NIFTY", "ma_ribbon", bar_ms + 60_000) is None
    # Past the cooldown it is allowed again.
    assert svc._cooldown_ok(st, cfg, "NIFTY", "pivot_break",
                            bar_ms + 31 * 60_000) is None


def test_the_daily_cap_is_a_hard_stop():
    cfg = IntradayConfig(max_signals_per_symbol_per_day=2, cooldown_bars=0).validate()
    st = svc.status("u1")
    for i in range(2):
        svc._record_fire(st, "NIFTY", "pivot_break", 1_757_000_000_000 + i * 60_000)
    blocked = svc._cooldown_ok(st, cfg, "NIFTY", "pivot_break", 1_757_000_600_000)
    assert blocked and "cap 2" in blocked


# ------------------------------------------------------------ the replay path

def test_the_simulation_calls_the_same_evaluator_the_live_scan_does():
    """The caller path, not the function. A strategy that fires in a unit test
    and never in the replay is the bug this test exists to catch."""
    from app.services import simulation as sim

    class _Runner:
        _candles: list = []
        _bar_history: dict = {}
        _uid = None

    tape = _tape_5m(n=200)
    runner = _Runner()
    runner._bar_history = {"NIFTY": tape}
    out = sim._intraday_signals_from_bars(runner, "NIFTY", tape[-1]["time"], tape, None)
    assert isinstance(out, list)
    for sdef in out:
        assert sdef["strategy"] in {"pivot_break", "ma_ribbon", "vwap_supertrend"}
        assert sdef["opt_type"] in {"CE", "PE"}
        # The stop and target travel with the signal. The replay must not
        # substitute its generic ATR envelope for a rule-derived stop.
        assert sdef["stop"] is not None and sdef["target"] is not None


def test_a_disabled_engine_emits_nothing_into_the_replay():
    from app.services import simulation as sim

    class _Runner:
        _candles: list = []
        _bar_history: dict = {}
        _uid = None
        _cached_intraday_cfg = IntradayConfig(enabled=False)

    tape = _tape_5m(n=200)
    r = _Runner()
    r._bar_history = {"NIFTY": tape}
    assert sim._intraday_signals_from_bars(r, "NIFTY", tape[-1]["time"], tape, None) == []


def test_a_strategy_turned_off_is_absent_from_the_replay():
    from app.services import simulation as sim

    class _Runner:
        _candles: list = []
        _bar_history: dict = {}
        _uid = None
        _cached_intraday_cfg = IntradayConfig(pb_enabled=False, rb_enabled=False)

    tape = _tape_5m(n=200)
    r = _Runner()
    r._bar_history = {"NIFTY": tape}
    out = sim._intraday_signals_from_bars(r, "NIFTY", tape[-1]["time"], tape, None)
    assert all(s["strategy"] == "vwap_supertrend" for s in out)


# ------------------------------------------------------------------- snapshot

def test_a_snapshot_before_any_scan_is_empty_but_well_formed():
    snap = svc.snapshot("u1")
    assert snap["rows"] == [] and snap["armed"] == 0
    assert snap["strategy"]["id"] == "intraday"
    assert [s["id"] for s in snap["strategy"]["strategies"]] == [
        "pivot_break", "ma_ribbon", "vwap_supertrend"]
    # Nothing here is calibrated, and the payload says so rather than letting
    # the settings page render bare numbers as measurements.
    assert snap["strategy"]["calibrated_fields"] == []
    assert snap["strategy"]["validated"] is False


def _near_expiry() -> str:
    """A weekly-ish expiry relative to TODAY.

    Hardcoding a date makes a greeks test that passes until the date passes,
    then fails for a reason that has nothing to do with the code.
    """
    return (svc.ist_today() + timedelta(days=5)).isoformat()


# ---------------------------------------------------------------- solved delta

class TestSolvedDelta:
    """Kite quotes carry no greeks, so delta is backed out of the traded premium.

    It matters because the premium stop is a spot stop converted by delta. Get
    it wrong high and the stop lands below zero — a position that can never be
    stopped out at all.
    """

    def test_an_atm_contract_solves_near_a_half(self):
        d = svc.solved_delta({"strike": 24800.0, "expiry": _near_expiry(),
                              "option_type": "CE"}, 120.0, 24800.0)
        assert d is not None and 0.35 <= d <= 0.65

    def test_a_premium_too_low_for_its_tenor_refuses_rather_than_guessing(self):
        """A 120-rupee ATM with months to run implies an IV the solver cannot
        reach. None is the honest answer, and the caller falls back."""
        assert svc.solved_delta({"strike": 24800.0, "expiry": "2027-12-31",
                                 "option_type": "CE"}, 120.0, 24800.0) is None

    def test_a_far_otm_contract_solves_small(self):
        d = svc.solved_delta({"strike": 26000.0, "expiry": _near_expiry(),
                              "option_type": "CE"}, 2.0, 24800.0)
        assert d is not None and d < 0.2

    def test_a_premium_below_intrinsic_refuses_rather_than_guessing(self):
        assert svc.solved_delta({"strike": 24800.0, "expiry": _near_expiry(),
                                 "option_type": "CE"}, 0.5, 26000.0) is None

    def test_missing_inputs_refuse(self):
        near = _near_expiry()
        for c, px, spot in (({"strike": 0, "expiry": near,
                              "option_type": "CE"}, 100.0, 24800.0),
                            ({"strike": 24800.0, "expiry": "",
                              "option_type": "CE"}, 100.0, 24800.0),
                            ({"strike": 24800.0, "expiry": near,
                              "option_type": "CE"}, 100.0, 0.0)):
            assert svc.solved_delta(c, px, spot) is None

    def test_the_delta_is_returned_unsigned_for_a_put(self):
        """`premium_stop_for` takes a distance, not a direction — a signed delta
        there would move the stop the wrong way on every PE."""
        d = svc.solved_delta({"strike": 24800.0, "expiry": _near_expiry(),
                              "option_type": "PE"}, 120.0, 24800.0)
        assert d is not None and d > 0


# ------------------------------------------------------------------- history

class TestRecentSignals:
    """The answer to "why is the board empty?".

    Every rule in this pack fires on ONE bar, and the live scan only evaluates
    the last closed one. Outside the session — and for most of any session —
    the honest live answer is "nothing right now", so without a history an
    operator cannot tell a quiet strategy from a broken one.
    """

    def _store(self, monkeypatch, rows: list[dict], symbols=("NIFTY",)):
        from app.services import ohlcv_store
        monkeypatch.setattr(ohlcv_store, "get_candles",
                            lambda sym, res, **kw: rows if sym in symbols else [])

    def test_it_replays_stored_bars_with_no_broker_and_no_live_scan(self, monkeypatch):
        rows = _tape_5m(n=900)
        self._store(monkeypatch, rows)
        svc.set_config(ALL_DAY, "u1")
        svc.clear_history_cache()
        out = svc.recent_signals("u1", sessions=30, symbols=["NIFTY"])
        assert out, "expected the stored tape to have fired something"
        assert all(r["historical"] is True for r in out)
        assert all(r["state"] == "ended" for r in out)

    def test_every_historical_row_carries_its_outcome(self, monkeypatch):
        """"A signal we would have taken" is much less useful than "and here is
        where it came out", and the replay already knows."""
        self._store(monkeypatch, _tape_5m(n=900))
        svc.clear_history_cache()
        out = svc.recent_signals("u1", sessions=30, symbols=["NIFTY"])
        for r in out:
            o = r["outcome"]
            assert o["reason"] and o["exit"] > 0
            assert isinstance(o["r"], float)

    def test_rows_are_newest_first(self, monkeypatch):
        self._store(monkeypatch, _tape_5m(n=900))
        svc.clear_history_cache()
        out = svc.recent_signals("u1", sessions=30, symbols=["NIFTY"])
        stamps = [r["generated_at_ms"] for r in out]
        assert stamps == sorted(stamps, reverse=True)

    def test_a_symbol_with_too_little_history_is_skipped_not_fatal(self, monkeypatch):
        self._store(monkeypatch, _tape_5m(n=10))
        svc.clear_history_cache()
        assert svc.recent_signals("u1", sessions=5, symbols=["NIFTY"]) == []

    def test_the_history_is_cached_rather_than_replayed_per_poll(self, monkeypatch):
        calls: list[int] = []
        rows = _tape_5m(n=900)

        def _get(sym, res, **kw):
            calls.append(1)
            return rows
        from app.services import ohlcv_store
        monkeypatch.setattr(ohlcv_store, "get_candles", _get)
        svc.clear_history_cache()
        svc.recent_signals("u1", sessions=30, symbols=["NIFTY"])
        first = len(calls)
        svc.recent_signals("u1", sessions=30, symbols=["NIFTY"])
        assert len(calls) == first, "a replay on a 5-second poll timer"

    def test_the_signal_ids_match_the_live_scan_s_shape(self, monkeypatch):
        self._store(monkeypatch, _tape_5m(n=900))
        svc.clear_history_cache()
        for r in svc.recent_signals("u1", sessions=30, symbols=["NIFTY"]):
            assert r["signal_id"] == svc.signal_id_for(
                r["strategy"], r["symbol"], r["signal"]["timestamp_ms"])


def test_an_unavailable_config_store_does_not_start_the_shipped_defaults(monkeypatch):
    """`db.get_config` returns "" both for "nothing stored" and for "the store
    is not reachable". Those must not mean the same thing: the first is the
    real defaults, the second is an engine that has lost its settings and would
    otherwise scan the SHIPPED universe with the operator's choices discarded.
    """
    from app.services import db
    monkeypatch.setattr(db, "is_available", lambda: False)
    cfg = svc.get_config("u1")
    assert cfg.enabled is False


def test_the_history_names_the_contract_each_signal_would_have_bought(monkeypatch):
    from app.services import ohlcv_store
    rows = _tape_5m(n=900)
    monkeypatch.setattr(ohlcv_store, "get_candles", lambda sym, res, **kw: rows)
    svc.set_config(ALL_DAY, "u1")
    svc.clear_history_cache()
    out = svc.recent_signals("u1", sessions=30, symbols=["NIFTY"])
    assert out
    for r in out:
        c = r["contract"]
        assert c is not None, "a row that names the index is not naming a trade"
        assert c["option_type"] in {"CE", "PE"}
        assert c["strike"] % 50 == 0          # NIFTY's published step
        assert c["estimated"] is True
        assert c["symbol"].startswith("NIFTY ")


def test_the_history_scans_one_instrument_once_however_it_is_spelled(monkeypatch):
    from app.services import ohlcv_store
    rows = _tape_5m(n=900)
    monkeypatch.setattr(ohlcv_store, "get_candles", lambda sym, res, **kw: rows)
    svc.clear_history_cache()
    once = svc.recent_signals("u1", sessions=30, symbols=["NIFTY"])
    svc.clear_history_cache()
    twice = svc.recent_signals("u1", sessions=30, symbols=["NIFTY", "NIFTY 50"])
    assert len(once) == len(twice)
    assert len({r["signal_id"] for r in twice}) == len(twice)


class TestTheScanCatchesUp:
    """Every rule in this pack fires on ONE bar.

    A scan cycle that lands late — a slow instrument dump, a rate limit, a
    restart — skipped that bar entirely and the signal on it was never seen.
    The replay found it and the live engine did not, which is the same
    divergence as a second implementation with none of the visibility.
    """

    def test_looking_back_recovers_signals_a_late_cycle_would_miss(self):
        rows = _tape_5m(n=900)
        misses, catches = 0, 0
        for end in range(300, len(rows), 3):     # a scan landing every 3rd bar
            tape = rows[:end]
            misses += sum(1 for e in svc.evaluate_symbol(
                tape, IntradayConfig(**ALL_DAY).validate(), "NIFTY",
                catchup=1) if e.signal)
            catches += sum(1 for e in svc.evaluate_symbol(
                tape, IntradayConfig(**{**ALL_DAY, "catchup_bars": 4}).validate(),
                "NIFTY", catchup=None) if e.signal)
        assert catches > misses

    def test_a_quiet_newest_bar_does_not_erase_a_signal_behind_it(self):
        """Which is the whole point of looking back."""
        rows = _tape_5m(n=900)
        cfg = IntradayConfig(**{**ALL_DAY, "catchup_bars": 6}).validate()
        for end in range(400, len(rows), 7):
            got = svc.evaluate_symbol(rows[:end], cfg, "NIFTY")
            newest_only = svc.evaluate_symbol(rows[:end], cfg, "NIFTY", catchup=1)
            fired_now = {e.strategy for e in newest_only if e.signal}
            fired_back = {e.strategy for e in got if e.signal}
            assert fired_now <= fired_back

    def test_one_row_per_strategy_however_far_it_looks_back(self):
        rows = _tape_5m(n=900)
        cfg = IntradayConfig(**{**ALL_DAY, "catchup_bars": 8}).validate()
        got = svc.evaluate_symbol(rows, cfg, "NIFTY")
        assert len(got) == len({e.strategy for e in got})

    def test_the_replay_evaluates_every_bar_so_it_never_looks_back(self):
        """Looking back there would re-report a signal the loop already
        reported on the bar it fired, as though it were new."""
        from app.services import simulation as sim
        import inspect
        src = inspect.getsource(sim._intraday_signals_from_bars)
        assert "catchup=1" in src
