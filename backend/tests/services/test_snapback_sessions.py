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
    assert trade.spot_entry == pytest.approx(fill)
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


@pytest.mark.asyncio
@pytest.mark.parametrize('settings,move', [
    ({'hold_days': 2, 'premium_stop_pct': 99}, .999),
    ({'hold_days': 3, 'premium_stop_pct': 10}, 1.10),
    ({'hold_days': 3, 'short_leg_delta': .3, 'premium_stop_pct': 99}, .999),
    ({'hold_days': 3, 'exit_mode': 'mean_touch', 'premium_stop_pct': 99}, .90),
    ({'hold_days': 3, 'premium_trail_pct': 10, 'premium_stop_pct': 99}, .97),
    ({'hold_days': 2, 'runner_mult': 1.01, 'premium_stop_pct': 99}, .97),
    ({'hold_days': 3, 'hedge_mode': 'index_futures', 'premium_stop_pct': 99}, .999),
    ({'hold_days': 6, 'exit_on_regime_flip': 1, 'premium_stop_pct': 99}, .999),
])
async def test_runner_matches_canonical_daily_exits_and_costs(replay_tape, monkeypatch, settings, move):
    from dataclasses import replace
    from app.engines.snapback import to_bars
    from app.engines.snapback.backtest import replay
    from app.services.snapback_replay import cost_for
    cfg = replace(snapback.get_config(), **settings)
    monkeypatch.setattr(snapback, 'get_config', lambda uid=None: cfg)
    original = ohlcv_store.get_candles
    rows = {sym: original(sym, '1d', limit=900) for sym in ('MOTILALOFS', 'NIFTY')}
    for sym, tape in rows.items():
        for day in ('2026-09-07', '2026-09-08', '2026-09-09', '2026-09-10', '2026-09-11'):
            tape.append(candle(day, tape[-1]['close'] * (move if sym == 'MOTILALOFS' else 1.20 if cfg.exit_on_regime_flip else .9985)))
    def get(sym, resolution, limit=500, since=None, until=None):
        return [dict(r) for r in rows[sym] if (since is None or r['time'] >= since)
                and (until is None or r['time'] < until)][-limit:] if resolution == '1d' else []
    monkeypatch.setattr(ohlcv_store, 'get_candles', get)
    runner = sim.SimulationRunner()
    runner._config = sim.SimConfig(date='2026-09-04', end_date='2026-09-11', instruments=['MOTILALOFS'],
                                   strategies=['snapback'], speed=10_000_000, friction_mode='realistic')
    runner._speed = 10_000_000
    await runner._run_loop()
    actual = runner._stats.trades[0]
    tapes = {sym: to_bars(daily_sessions.closed_daily_candles(tape, datetime(2026,9,11,16,tzinfo=IST)))
             for sym, tape in rows.items()}
    expected = next(t for t in replay(tapes, replace(cfg, sizing_mode='LOTS', lots=actual.lots),
                         cost=cost_for(runner, 'MOTILALOFS')).trades if t.entry_day == '2026-09-04')
    if cfg.exit_on_regime_flip:
        assert expected.reason == 'regime_flip'
    assert actual.entry_price == pytest.approx(expected.fill_in)
    assert actual.strike == expected.strike
    assert actual.pnl_usd == pytest.approx(expected.net, abs=.01)
    assert actual.fees == expected.costs
    assert actual.premium_model['sessions'] == expected.held_days
    if expected.reason != 'tape_ended':
        assert actual.exit_reason == expected.reason.upper()
        assert actual.exit_price == pytest.approx(expected.fill_out)
    # Rewinding before the first close removes ALL later valuations and exits.
    runner._apply_seek(datetime(2026,9,4,12,tzinfo=IST).timestamp())
    actual = runner._stats.trades[0]
    assert actual.status == 'OPEN'
    assert actual.exit_price is None
    assert actual.pnl_usd == 0
    assert actual.premium_model['sessions'] == 0
    assert actual.bars_held == 0


def test_history_contract_and_quantity_match_next_open_outcome(replay_tape):
    from dataclasses import replace
    from app.engines.snapback import to_bars
    from app.engines.snapback.backtest import replay
    tapes = {s: to_bars(daily_sessions.closed_daily_candles(ohlcv_store.get_candles(s, '1d', limit=900),
                              datetime(2026,9,4,16,tzinfo=IST))) for s in ('MOTILALOFS', 'NIFTY')}
    trade = replay(tapes, replace(snapback.get_config(), sizing_mode='LOTS', lots=1)).trades[-1]
    row = next(r for r in snapback.recent_signals('u') if r['symbol'] == 'MOTILALOFS' and r['outcome'])
    assert row['contract']['strike'] == trade.strike
    assert row['quantity'] == trade.qty
    assert row['lots'] == trade.lots
    assert row['premium'] == round(trade.fill_in, 2)
    assert row['deployed_inr'] == round(trade.fill_in * trade.qty, 2)


def test_bad_candles_are_rejected_and_naive_times_are_ist():
    from app.engines.snapback import to_bars
    valid = {**candle('2026-09-11'), 'time': '2026-09-11T15:30:00'}
    broken = [{**valid, 'open': 'bad'}, {**valid, 'close': float('nan')},
              {**valid, 'low': 101}, {**valid, 'volume': -1}, {**valid, 'high': 99}]
    bars = to_bars(broken + [valid])
    assert len(bars) == 1
    assert bars.time[0] == datetime(2026,9,11,15,30,tzinfo=IST).timestamp()


@pytest.mark.parametrize('missing', [False, True])
def test_only_complete_intraday_sessions_can_be_confirmed(missing):
    start = datetime(2026,9,11,9,15,tzinfo=IST)
    rows = [{**candle('2026-09-11', 100 + i / 100), 'time': (start + timedelta(minutes=i*5)).timestamp()}
            for i in range(75)]
    if missing:
        rows.pop(20)
    before = start.replace(hour=15,minute=29)
    assert daily_sessions.completed_intraday_candle(rows, '5m', before) is None
    daily = daily_sessions.completed_intraday_candle(rows, '5m', before.replace(minute=30))
    if missing:
        assert daily is None
    else:
        assert daily['open'] == 100
        assert daily['close'] == 100.74
        assert daily['volume'] == 75000


@pytest.mark.asyncio
async def test_hedge_only_replay_requires_market_even_with_filter_off(replay_tape, monkeypatch):
    from dataclasses import replace
    cfg = replace(snapback.get_config(), market_filter='off', hedge_mode='index_futures')
    monkeypatch.setattr(snapback, 'get_config', lambda uid=None: cfg)
    original = ohlcv_store.get_candles
    monkeypatch.setattr(ohlcv_store, 'get_candles', lambda sym, *a, **kw: [] if sym == 'NIFTY' else original(sym, *a, **kw))
    runner = await run('2026-09-04', instruments=['MOTILALOFS'])
    assert runner._stats.trades == []
    assert 'hedged entry blocked' in runner._strategy_notes['snapback']


@pytest.mark.asyncio
async def test_complete_intraday_tape_supplies_missing_daily_close(replay_tape, monkeypatch):
    original = ohlcv_store.get_candles
    dc = original('MOTILALOFS', '1d', limit=1)[0]
    start = datetime(2026,9,4,9,15,tzinfo=IST)
    intraday = [{**dc, 'time': (start + timedelta(minutes=5*i)).timestamp(), 'volume': 1000 / 75}
                for i in range(75)]
    def get(sym, resolution, limit=500, since=None, until=None):
        rows = (original(sym, resolution, limit=limit, since=since, until=until)
                if resolution == '1d' else intraday if sym == 'MOTILALOFS' else [])
        return [r for r in rows if (since is None or r['time'] >= since) and
                (until is None or r['time'] < until) and not
                (sym == 'MOTILALOFS' and resolution == '1d' and r['time'] == dc['time'])]
    monkeypatch.setattr(ohlcv_store, 'get_candles', get)
    runner = await run('2026-09-04', instruments=['MOTILALOFS'])
    trade = runner._stats.trades[0]
    assert trade.premium_model['sessions'] == 1
    assert any(b.get('daily_candle') for b in runner._candles if b['symbol'] == 'MOTILALOFS')


@pytest.mark.asyncio
async def test_short_daily_replay_can_fill_open_without_revealing_close(replay_tape):
    runner = sim.SimulationRunner()
    runner._config = sim.SimConfig(date='2026-09-04', end_time='12:00:00',
                                   strategies=['snapback'], instruments=['MOTILALOFS'], speed=10_000_000)
    runner._speed = 10_000_000
    await runner._run_loop()
    assert len(runner._stats.trades) == 1
    trade = runner._stats.trades[0]
    assert trade.status == 'OPEN'
    assert trade.premium_model['sessions'] == 0
    assert all(b.get('daily_candle') is None for b in runner._candles)


@pytest.mark.asyncio
async def test_midday_start_still_shows_confirmed_close(replay_tape):
    runner = sim.SimulationRunner()
    runner._config = sim.SimConfig(date='2026-09-03', start_time='12:00:00',
                                   strategies=['snapback'], instruments=['MOTILALOFS'], speed=10_000_000)
    runner._speed = 10_000_000
    await runner._run_loop()
    assert runner._stats.trades == []
    assert any(e.strength == 'CONFIRMED' for e in runner._stats.events)
    assert all(b['daily_observation'] == 'close' for b in runner._candles)


def test_history_universe_respects_index_selection_and_stock_switch(monkeypatch):
    from dataclasses import replace
    monkeypatch.setattr(ohlcv_store, 'get_status', lambda: [
        {'symbol': s, 'resolution': '1d'} for s in ('NIFTY', 'BANKNIFTY', 'RELIANCE')])
    cfg = SnapbackConfig(scan_indices=('NIFTY',), scan_stock_contracts=False)
    assert snapback._history_universe(cfg) == ('NIFTY',)
    assert snapback._history_universe(replace(cfg, scan_stock_contracts=True)) == ('NIFTY', 'RELIANCE')
