"""Regressions for live/replay session identity, daily availability and entry wiring."""
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from app.engines.snapback import SnapbackConfig
from app.engines.snapback.models import IST
from app.schemas.market import Candle
from app.services import daily_sessions, ohlcv_store, simulation as sim, snapback


def candle(day, close=100.0, hour=0):
    at = datetime.fromisoformat(day).replace(hour=hour, tzinfo=IST)
    return dict(time=int(at.timestamp()), open=close, high=close * 1.002,
                low=close * .998, close=close, volume=1000)


@pytest.mark.parametrize('shape', ['dict', 'tuple', 'object'])
def test_daily_filter_handles_broker_shapes_and_only_closed_sessions(shape):
    rows = [candle(day) for day in ('2026-09-11', '2026-09-12', '2026-09-13',
                                   '2026-09-14', '2026-09-15', '2026-09-16')]
    if shape == 'tuple':
        rows = [[r[k] for k in ('time', 'open', 'high', 'low', 'close', 'volume')] for r in rows]
    elif shape == 'object':
        rows = [Candle(timestamp_ms=r['time'] * 1000, **{k: v for k, v in r.items() if k != 'time'}) for r in rows]
    asof = datetime(2026, 9, 15, 12, tzinfo=IST)
    out = daily_sessions.closed_daily_candles(rows, asof)
    assert [datetime.fromtimestamp(r['time'], IST).isoformat() for r in out] == ['2026-09-11T15:30:00+05:30']
    after = daily_sessions.closed_daily_candles(rows, asof.replace(hour=15, minute=30))
    assert len(after) == 2


def test_same_daily_session_has_stable_timestamp_and_no_duplicate_volume():
    midnight, broker = candle('2026-09-11'), candle('2026-09-11', hour=9)
    asof = datetime(2026, 9, 14, 18, tzinfo=IST)
    once = daily_sessions.closed_daily_candles([midnight], asof)
    assert daily_sessions.closed_daily_candles([midnight, broker], asof) == once
    assert daily_sessions.closed_daily_candles([broker], asof) == once


def test_live_filter_uses_wall_time_and_not_simulation_clock(monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 9, 14, 18, tzinfo=IST)
    monkeypatch.setattr(snapback, 'datetime', Clock)
    rows = snapback._drop_forming([candle('2026-09-11'), candle('2026-09-14')])
    assert len(rows) == 1
    assert datetime.fromtimestamp(rows[0]['time'], IST).day == 11


@pytest.fixture
def replay_tape(monkeypatch):
    # A smooth tape that breaks its high only on Sep 3, then fills Sep 4.
    dates = []
    day = datetime(2026, 9, 3, tzinfo=IST).date()
    while len(dates) < 350:
        if daily_sessions.is_session_day(day):
            dates.append(day)
        day -= timedelta(days=1)
    dates.reverse()
    stock = [candle(d.isoformat(), 1000 * 1.0015 ** i) for i, d in enumerate(dates)]
    stock[-1] = candle('2026-09-03', stock[-1]['close'] * 1.10)
    fill = stock[-1]['close'] * .99
    stock.append(candle('2026-09-04', fill))
    market = [candle(d.isoformat(), 25000 * .9985 ** i) for i, d in enumerate(dates)]
    market.append(candle('2026-09-04', market[-1]['close'] * .9985))
    cfg = SnapbackConfig(enabled=False, max_rv_pct=100, hedge_mode='none', cooldown_days=0)
    monkeypatch.setattr(snapback, 'get_config', lambda uid=None: cfg)
    monkeypatch.setattr(snapback, '_history_universe', lambda cfg: ('MOTILALOFS',))
    monkeypatch.setattr(sim, '_load_recorded_signals', lambda *a: [])
    monkeypatch.setattr(sim, '_get_scanned_dates', lambda *a: set())
    hydrate = AsyncMock()
    monkeypatch.setattr(sim, '_hydrate_missing_candles', hydrate)
    def get(symbol, resolution, limit=500, since=None, until=None):
        if resolution != '1d':
            return []
        rows = market if symbol == 'NIFTY' else stock
        return [dict(r) for r in rows if (since is None or r['time'] >= since)
                and (until is None or r['time'] < until)][-limit:]
    monkeypatch.setattr(ohlcv_store, 'get_candles', get)
    snapback._history_cache.clear()
    return fill, hydrate


async def run(day, end=None, instruments=None):
    r = sim.SimulationRunner()
    r._config = sim.SimConfig(date=day, end_date=end, instruments=instruments or [],
                              strategies=['snapback'], speed=10_000_000, friction_mode='ideal')
    r._speed = 10_000_000
    await r._run_loop()
    return r


@pytest.mark.asyncio
async def test_full_runner_confirms_board_signal_then_fills_next_open(replay_tape):
    fill, hydrate = replay_tape
    history_before = snapback.recent_signals('u')
    assert any(r['symbol'] == 'MOTILALOFS' for r in history_before)
    r = await run('2026-09-03', '2026-09-04')
    assert r._bars_played == r._bars_total == 8
    events = [e for e in r._stats.events if e.instrument == 'MOTILALOFS']
    assert [(e.strength, e.time_iso) for e in events] == [
        ('CONFIRMED', '2026-09-03T15:30:00'), ('STRONG', '2026-09-04T09:15:00')]
    assert len(r._stats.trades) == 1
    trade = r._stats.trades[0]
    assert trade.spot_entry == pytest.approx(round(fill, 2))
    assert trade.status == 'OPEN'  # daily positions survive session end
    assert trade.bars_held == r._bars_per_session()
    assert all(b['daily_observation'] in ('open', 'close') for b in r._candles)
    assert all(b['high'] == b['low'] == b['close'] for b in r._candles)
    assert 'MOTILALOFS' in hydrate.call_args_list[0].args[0]
    daily_fetch = hydrate.call_args_list[1].args
    assert daily_fetch[3] - daily_fetch[2] >= 1500 * 86400
    snapback._history_cache.clear()
    assert snapback.recent_signals('u') == history_before


@pytest.mark.asyncio
async def test_single_session_replay_seeds_prior_close_and_adds_market_dependency(replay_tape):
    r = await run('2026-09-04', instruments=['MOTILALOFS'])
    assert len(r._stats.trades) == 1
    assert r._stats.trades[0].entry_time_iso == '09:15:00'
    assert any(b['symbol'] == 'NIFTY' for b in r._candles)
    again = await run('2026-09-04', instruments=['MOTILALOFS'])
    assert [e.model_dump() for e in again._stats.events] == [e.model_dump() for e in r._stats.events]


@pytest.mark.asyncio
@pytest.mark.parametrize('day', ['2026-09-13', '2026-09-14'])
async def test_no_replay_signals_or_trades_on_weekend_or_holiday(replay_tape, day):
    r = await run(day)
    assert r._bars_played == 0
    assert r._stats.events == []
    assert r._stats.trades == []


def test_daily_close_does_not_leak_into_open_observation():
    dc = candle('2026-09-03', 999)
    opening = {**candle('2026-09-03', 100), 'time': datetime(2026,9,3,9,15,tzinfo=IST).timestamp(),
               'daily_observation': 'open', 'daily_candle': None}
    closing = {**opening, 'time': datetime(2026,9,3,15,30,tzinfo=IST).timestamp(),
               'daily_observation': 'close', 'daily_candle': dc}
    formed = sim._forming_session([opening, closing], opening['time'])
    assert formed['close'] == 100
    assert sim._forming_session([opening, closing], closing['time'])['close'] == 999


def test_live_evaluation_honors_market_filter(replay_tape):
    rows = ohlcv_store.get_candles('MOTILALOFS', '1d', limit=900)[:-1]
    cfg = SnapbackConfig(enabled=True, max_rv_pct=100, cooldown_days=0)
    assert snapback.evaluate_symbol(rows, cfg, 'MOTILALOFS')
    assert snapback.evaluate_symbol(rows, cfg, 'MOTILALOFS', market_gate={}) == []


@pytest.mark.asyncio
async def test_starting_replay_does_not_clear_live_signal_caches(monkeypatch):
    from unittest.mock import Mock
    reset = Mock()
    monkeypatch.setattr(sim, 'reset_all_engine_signals', reset)
    r = sim.SimulationRunner()
    monkeypatch.setattr(r, '_run_loop', AsyncMock())
    await r.start(sim.SimConfig(date='2026-09-04', strategies=['snapback']))
    await r._task
    reset.assert_not_called()


@pytest.mark.asyncio
async def test_rewind_removes_future_daily_buffers(replay_tape):
    r = await run('2026-09-03', '2026-09-04')
    target = datetime(2026, 9, 3, 9, 15, tzinfo=IST).timestamp()
    r._apply_seek(target)
    assert r._stats.trades == []
    assert all(b['time'] <= target for bars in r._session_bars.values() for b in bars)
    assert r._snapback_filled == {}
    assert r._daily_tape_cache == {}


def test_stored_close_at_replay_clock_is_available_but_future_session_is_not(monkeypatch):
    at = datetime(2026, 9, 11, 15, 30, tzinfo=IST).timestamp()
    rows = [{**candle('2026-09-11'), 'time': at}, candle('2026-09-15', 999)]
    def get(symbol, resolution, limit=500, since=None, until=None):
        return [r for r in rows if r['time'] < until]
    monkeypatch.setattr(ohlcv_store, 'get_candles', get)
    out = sim._store_daily_sessions('MOTILALOFS', at)
    assert len(out) == 1
    assert out[0]['time'] == at
    assert out[0]['close'] == 100
