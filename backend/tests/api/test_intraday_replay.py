"""The intraday pack inside a replay, through ``_evaluate_bar``.

The caller path, not the function. A strategy that fires in a unit test and
never in the replay is the bug this file exists to catch — and it nearly
happened: the replay seeds FIFTY warmup bars at its own resolution, and a 55
EMA on 5-minute candles cannot be computed from ten of them. The failure would
have looked exactly like "the rule is strict".
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

IST = timezone(timedelta(hours=5, minutes=30))


def _ts(day: str, hh: int, mm: int) -> int:
    y, mo, d = (int(x) for x in day.split("-"))
    return int(datetime(y, mo, d, hh, mm, tzinfo=IST).timestamp())


def _turning_tape(days: list[str], start: float = 300.0) -> list[dict]:
    """Two sessions up, then a session that turns over.

    The turn has to happen INSIDE the replayed window. A tape that is already
    falling when the replay starts has its ribbon cross in the warmup, and the
    freshness rule then correctly refuses every bar of it — which looks like
    silence and is not.
    """
    out: list[dict] = []
    price = start
    for d, day in enumerate(days):
        last = d == len(days) - 1
        for i in range(75):
            o = price
            price += (-1.2 if last else 0.25)
            out.append({"time": _ts(day, 9, 15) + i * 300, "open": o,
                        "high": max(o, price) + 0.4, "low": min(o, price) - 0.4,
                        "close": price, "volume": 9000.0})
    return out


@pytest.fixture
def replay(tmp_path, monkeypatch):
    monkeypatch.setenv("STERLING_DB_PATH", str(tmp_path / "t.db"))
    from app.services import db, ohlcv_store
    monkeypatch.setattr(db, "_DB_PATH", str(tmp_path / "t.db"), raising=False)
    monkeypatch.setattr(ohlcv_store, "_DB_PATH", str(tmp_path / "t.db"), raising=False)
    db.init()
    ohlcv_store.init_ohlcv_table()

    from app.services import intraday
    from app.services.simulation import SimConfig, simulation_runner
    intraday._state.clear()

    r = simulation_runner
    r._bar_history = {}
    r._last_fired = {}
    r._active_until_bar = {}
    r._in_session_bars = {}
    r._candles = []
    r._bars_played = 0
    r._stats.signals_fired = 0
    r._stats.events = []
    r._stats.trades = []
    # Mechanics, not the shipped window: this asserts the replay CALLS the
    # engine and keeps its stop, which is true whatever hours are configured.
    from app.engines.intraday import IntradayConfig
    r._cached_intraday_cfg = IntradayConfig(
        session_start="09:15", no_entry_after="15:10",
        close_at_session_end=True, exit_after_bars=0,
        stop_widen_mult=1.0).validate()
    r._config = SimConfig(date="2026-09-10", strategy="all", strategies=["all"])
    yield r
    r._stats.events = []
    r._cached_intraday_cfg = None


def _play(runner, tape: list[dict], symbol: str = "NIFTY") -> None:
    for bar in tape:
        dt = datetime.fromtimestamp(bar["time"], tz=IST)
        runner._evaluate_bar({**bar, "symbol": symbol}, dt)


def test_the_replay_fires_the_pack_and_keeps_its_own_stop(replay):
    """The tape is only in the STORE — the replay's own 50-bar warmup is not
    enough for a 55 EMA, and the pack must read the rest rather than sit mute."""
    from app.services import ohlcv_store
    tape = _turning_tape(["2026-09-08", "2026-09-09", "2026-09-10"])
    ohlcv_store.upsert_candles("NIFTY", "5m", tape)

    # Replay only the last session; the two before it exist only in the store.
    _play(replay, tape[150:])

    ours = [ev for ev in replay._stats.events
            if ev.strategy in {"pivot_break", "ma_ribbon", "vwap_supertrend"}]
    assert ours, "the intraday pack produced nothing in a replay"
    for ev in ours:
        assert ev.direction in {"BULLISH", "BEARISH"}
        assert ev.stop is not None and ev.target is not None
        # The rule's own stop survived. The replay's generic 1.5×ATR envelope
        # would put the stop a fixed distance from the close on every row.
        if ev.direction == "BEARISH":
            assert ev.stop > ev.entry and ev.target < ev.entry
        else:
            assert ev.stop < ev.entry and ev.target > ev.entry


def test_a_disabled_engine_is_silent_in_the_replay(replay):
    from app.engines.intraday import IntradayConfig
    from app.services import ohlcv_store
    replay._cached_intraday_cfg = IntradayConfig(enabled=False)
    tape = _turning_tape(["2026-09-08", "2026-09-09", "2026-09-10"])
    ohlcv_store.upsert_candles("NIFTY", "5m", tape)
    _play(replay, tape[150:])
    assert not [ev for ev in replay._stats.events
                if ev.strategy in {"pivot_break", "ma_ribbon", "vwap_supertrend"}]


def test_the_replay_never_reads_a_bar_from_the_future(replay):
    """The store holds the whole session; the tape handed to the strategies must
    stop at the replay clock. This is the lookahead bug in its purest form."""
    from app.services import simulation as sim
    from app.services import ohlcv_store
    from app.services.intraday import get_config
    tape = _turning_tape(["2026-09-08", "2026-09-09", "2026-09-10"])
    ohlcv_store.upsert_candles("NIFTY", "5m", tape)

    clock = tape[200]["time"]
    bars = sim._intraday_tape(replay, "NIFTY", clock, [], get_config(None))
    assert bars, "expected the store to supply the warmup"
    assert max(sim._bar_epoch_seconds(b) for b in bars) <= clock
