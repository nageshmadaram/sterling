"""
Tests for Market Replay Simulation endpoints and service.
"""
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from app.services.simulation import simulation_runner, SimState, SimConfig, SimStatus, SimSignalEvent


@pytest.fixture(autouse=True)
async def reset_simulation():
    """Ensure simulation runner is stopped before/after each test."""
    await simulation_runner.stop()
    yield
    await simulation_runner.stop()


def test_simulation_initial_status():
    status = simulation_runner.status
    assert status.state == SimState.IDLE
    assert status.bars_played == 0
    assert status.stats.signals_fired == 0


@pytest.mark.asyncio
async def test_start_and_stop_simulation():
    config = SimConfig(
        date="2026-08-28",
        start_time="09:15:00",
        end_time="15:30:00",
        speed=10.0,
        resolution="5m",
        instruments=["NIFTY"],
    )
    status = await simulation_runner.start(config)
    assert status.state in (SimState.RUNNING, SimState.LOADING)
    assert status.config is not None
    assert status.config.date == "2026-08-28"

    # Stop simulation
    stop_status = await simulation_runner.stop()
    assert stop_status.state == SimState.IDLE


@pytest.mark.asyncio
async def test_pause_and_resume():
    config = SimConfig(
        date="2026-08-28",
        start_time="09:15:00",
        end_time="15:30:00",
        speed=10.0,
        instruments=["NIFTY"],
    )
    await simulation_runner.start(config)
    # Manually transition to running if still loading in test
    simulation_runner._state = SimState.RUNNING

    pause_status = await simulation_runner.pause()
    assert pause_status.state == SimState.PAUSED

    resume_status = await simulation_runner.resume()
    assert resume_status.state == SimState.RUNNING

    await simulation_runner.stop()


def test_set_speed():
    from app.services.simulation import SimConfig
    simulation_runner._config = SimConfig(date="2026-09-07", speed=1.0)
    status = simulation_runner.set_speed(15.0)
    assert simulation_runner._speed == 15.0
    assert status.config is not None
    assert status.config.speed == 15.0
    assert simulation_runner._config.speed == 15.0

    # Bounds check
    status = simulation_runner.set_speed(6000.0)
    assert simulation_runner._speed == 5000.0
    assert status.config.speed == 5000.0

    status = simulation_runner.set_speed(0.1)
    assert simulation_runner._speed == 0.5
    assert status.config.speed == 0.5


@pytest.mark.asyncio
async def test_auto_restart_on_duplicate_start():
    config1 = SimConfig(date="2026-08-28", speed=5.0)
    await simulation_runner.start(config1)
    assert simulation_runner.status.config.date == "2026-08-28"

    config2 = SimConfig(date="2026-08-29", speed=10.0)
    status = await simulation_runner.start(config2)
    assert status.config.date == "2026-08-29"

    await simulation_runner.stop()


@pytest.mark.asyncio
async def test_step_and_seek_controls():
    config = SimConfig(date="2026-08-28", start_time="09:15:00", end_time="15:30:00", speed=10.0, instruments=["NIFTY"])
    await simulation_runner.start(config)
    simulation_runner._state = SimState.RUNNING
    simulation_runner._start_epoch = 1787889000
    simulation_runner._end_epoch = 1787911500
    simulation_runner._current_sim_epoch = 1787889000.0

    status = simulation_runner.step_bars(5)
    assert simulation_runner._seek_requested_epoch == 1787889000.0 + (5 * 300)

    jump_status = simulation_runner.jump_start()
    assert simulation_runner._seek_requested_epoch == 1787889000.0

    await simulation_runner.stop()


@pytest.mark.asyncio
async def test_simulation_default_instruments_never_fabricate_missing_warmup(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock
    from app.services import simulation, ohlcv_store
    monkeypatch.setattr(simulation, "_hydrate_missing_candles", AsyncMock())
    monkeypatch.setattr(simulation, "_load_recorded_signals", lambda *a, **kw: [])
    monkeypatch.setattr(ohlcv_store, "get_candles", lambda *a, **kw: [])
    config = SimConfig(
        date="2026-09-03",
        start_time="09:15:00",
        end_time="09:20:00",
        speed=10.0,
        strategy="all",
        instruments=[],
        resolution="5m",
    )
    await simulation_runner.start(config)
    for _ in range(50):
        if simulation_runner.status.state == SimState.IDLE and "KOTAKBANK" in simulation_runner._bar_history:
            break
        await asyncio.sleep(0.1)

    # Verify high-liquidity stock symbols are present in simulation
    assert "KOTAKBANK" in simulation_runner._bar_history
    assert "ADANIPORTS" in simulation_runner._bar_history
    assert "AXISBANK" in simulation_runner._bar_history
    assert "BAJFINANCE" in simulation_runner._bar_history

    # Missing real history cannot be replaced with generated warmup/session bars.
    assert simulation_runner._bar_history["KOTAKBANK"] == []
    assert simulation_runner.status.state == SimState.IDLE
    assert simulation_runner.status.bars_total == 0
    assert "No real candles" in simulation_runner._status_message

    # Verify kite signal responses can format rows for these stocks
    res = simulation_runner.get_kite_signals_response()
    assert "rows" in res
    assert isinstance(res["rows"], list)

    await simulation_runner.stop()


def test_evaluate_bar_supertrend_cooldown_and_no_flood():
    from datetime import datetime, timezone
    simulation_runner._bar_history = {}
    simulation_runner._last_fired = {}
    simulation_runner._active_until_bar = {}
    simulation_runner._config = SimConfig(date="2026-08-28", strategy="supertrend", strategies=["supertrend"])
    simulation_runner._candles = []
    simulation_runner._bars_played = 0
    simulation_runner._stats.signals_fired = 0
    simulation_runner._stats.events = []
    simulation_runner._stats.trades = []

    # Feed 25 steadily rising bars
    base_time = datetime(2026, 8, 28, 9, 15, tzinfo=timezone.utc)
    for i in range(25):
        price = 24000.0 + (i * 20.0)
        bar = {
            "symbol": "NIFTY",
            "open": price - 5.0,
            "high": price + 15.0,
            "low": price - 10.0,
            "close": price + 10.0,
            "volume": 50000,
        }
        dt = base_time
        simulation_runner._evaluate_bar(bar, dt)

    st_events = [ev for ev in simulation_runner._stats.events if ev.strategy == "supertrend"]
    # Should only fire transition/pullback signals with cooldown, not a signal on every single bar (25)
    assert len(st_events) <= 3


def test_evaluate_bar_supertrend_requires_warmup_and_no_early_spike():
    """Verify that SuperTrend requires >= 22 bars for Triple Alignment and does NOT fire at bar 6 (09:40:00)."""
    from datetime import datetime, timezone
    simulation_runner._bar_history = {}
    simulation_runner._last_fired = {}
    simulation_runner._active_until_bar = {}
    simulation_runner._config = SimConfig(date="2026-08-28", strategy="supertrend", strategies=["supertrend"])
    simulation_runner._candles = []
    simulation_runner._bars_played = 0
    simulation_runner._stats.signals_fired = 0
    simulation_runner._stats.events = []
    simulation_runner._stats.trades = []

    # Feed 6 bars (up to 09:40) across 5 instruments
    base_time = datetime(2026, 8, 28, 9, 15, tzinfo=timezone.utc)
    for sym in ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "RELIANCE"]:
        for i in range(6):
            bar = {
                "symbol": sym,
                "open": 100.0 + i,
                "high": 105.0 + i,
                "low": 95.0 + i,
                "close": 102.0 + i,
                "volume": 10000,
            }
            simulation_runner._evaluate_bar(bar, base_time)

    # In previous buggy code, bar 6 (09:40) fired 5 simultaneous trades across all 5 instruments.
    # Canonical logic requires >= 22 bars so 0 signals should fire.
    st_events = [ev for ev in simulation_runner._stats.events if ev.strategy == "supertrend"]
    assert len(st_events) == 0


def test_evaluate_bar_atm_imbalance_single_trade_window():
    """Verify ATM Premium Imbalance only triggers during 09:15-09:30 and takes at most 1 trade per day."""
    from datetime import datetime, timezone, timedelta
    simulation_runner._bar_history = {}
    simulation_runner._last_fired = {}
    simulation_runner._active_until_bar = {}
    simulation_runner._config = SimConfig(date="2026-08-28", strategy="atm_imbalance", strategies=["atm_imbalance"])
    simulation_runner._stats.signals_fired = 0
    simulation_runner._stats.events = []
    simulation_runner._stats.trades = []

    # Bar 1 at 09:15:00
    t1 = datetime(2026, 8, 28, 9, 15, 0, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    bar1 = {"symbol": "NIFTY", "open": 24000, "high": 24050, "low": 23980, "close": 24020, "volume": 50000}
    simulation_runner._evaluate_bar(bar1, t1)

    # Bar 2 at 09:20:00 (inside open window) -> fires 1st trade
    t2 = datetime(2026, 8, 28, 9, 20, 0, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    bar2 = {"symbol": "NIFTY", "open": 24020, "high": 24060, "low": 24010, "close": 24040, "volume": 60000}
    simulation_runner._evaluate_bar(bar2, t2)

    atm_events = [ev for ev in simulation_runner._stats.events if ev.strategy == "atm_imbalance"]
    assert len(atm_events) == 1

    # Bar 3 at 09:25:00 (still in window, but already traded today -> should NOT fire another)
    t3 = datetime(2026, 8, 28, 9, 25, 0, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    bar3 = {"symbol": "NIFTY", "open": 24040, "high": 24070, "low": 24030, "close": 24060, "volume": 40000}
    simulation_runner._evaluate_bar(bar3, t3)

    atm_events_after = [ev for ev in simulation_runner._stats.events if ev.strategy == "atm_imbalance"]
    assert len(atm_events_after) == 1


def test_evaluate_bar_nifty_orb_window_and_constraints():
    """Replay ORB uses the live engine, not a 4-bar clone."""
    from datetime import timezone, timedelta
    from tests.engines.test_nifty_orb_options import orb_session

    simulation_runner._bar_history = {}
    simulation_runner._last_fired = {}
    simulation_runner._active_until_bar = {}
    simulation_runner._in_session_bars = {}
    simulation_runner._config = SimConfig(date="2026-08-18", strategy="nifty_orb", strategies=["nifty_orb"])
    simulation_runner._stats.signals_fired = 0
    simulation_runner._stats.events = []
    simulation_runner._stats.trades = []

    session = orb_session("LONG")
    for bar_i, b in enumerate(session):
        bar = {
            "symbol": "NIFTY",
            "open": b.open, "high": b.high, "low": b.low, "close": b.close,
            "volume": b.volume,
            "time": int(b.timestamp.timestamp()),
        }
        simulation_runner._evaluate_bar(bar, b.timestamp)
        if bar_i < 3:
            orb_so_far = [ev for ev in simulation_runner._stats.events if ev.strategy == "nifty_orb"]
            assert orb_so_far == []

    orb_events = [ev for ev in simulation_runner._stats.events if ev.strategy == "nifty_orb"]
    assert orb_events
    assert orb_events[0].direction == "BULLISH"
    scan = simulation_runner.get_nifty_orb_signals_response()
    assert scan["signals"]
    row = scan["signals"][0]
    assert row["status"] == "signal"
    assert row["signal"]["direction"] == "LONG"
    assert row["ticket_fingerprint"]
    assert row["auto_block"].startswith("replay")
    assert row["trade"]["contract"]["option_type"] == "CE"


def test_evaluate_bar_bear_to_bearish_short_only():
    """Verify Bear to Bearish only emits BEARISH signals on lower highs breakdown."""
    from datetime import datetime, timezone
    simulation_runner._bar_history = {}
    simulation_runner._last_fired = {}
    simulation_runner._active_until_bar = {}
    simulation_runner._config = SimConfig(date="2026-08-28", strategy="bear_to_bearish", strategies=["bear_to_bearish"])
    simulation_runner._stats.signals_fired = 0
    simulation_runner._stats.events = []
    simulation_runner._stats.trades = []

    base_time = datetime(2026, 8, 28, 10, 0, tzinfo=timezone.utc)
    # Feed rising market: should NOT produce any signals
    for i in range(15):
        price = 100.0 + (i * 2.0)
        bar = {
            "symbol": "BANKNIFTY",
            "open": price - 1.0,
            "high": price + 2.0,
            "low": price - 2.0,
            "close": price + 1.0,
            "volume": 20000,
        }
        simulation_runner._evaluate_bar(bar, base_time)

    b2b_events = [ev for ev in simulation_runner._stats.events if ev.strategy == "bear_to_bearish"]
    assert len(b2b_events) == 0
    for ev in b2b_events:
        assert ev.direction == "BEARISH"


@pytest.fixture
def september_4_recorded_evidence():
    """Real-evidence integration cases require the captured session snapshot."""
    from app.services.simulation import _load_recorded_signals
    sigs = _load_recorded_signals("2026-09-04")
    if not sigs:
        pytest.skip("Real September 4, 2026 Kite signal snapshot unavailable in the test database")
    return sigs


def test_recorded_signals_september_4(september_4_recorded_evidence):
    """Verify ground truth recorded signals for 2026-09-04 return exactly LT (09:15) and SBIN (12:15)."""
    sigs = september_4_recorded_evidence
    assert len(sigs) == 2
    symbols = [s["underlying"] for s in sigs]
    assert symbols == ["LT", "SBIN"]
    assert sigs[0]["time_iso"] == "09:15:00"
    assert sigs[0]["direction"] == "BEARISH"
    assert sigs[1]["time_iso"] == "12:15:00"
    assert sigs[1]["direction"] == "BEARISH"


def test_indicator_warmup_no_boundary_spike():
    """Verify bar 22 does not trigger a spurious SuperTrend signal due to indicator warmup."""
    from datetime import datetime, timezone, timedelta
    simulation_runner._bar_history = {}
    simulation_runner._last_fired = {}
    simulation_runner._active_until_bar = {}
    simulation_runner._recorded_signals = []  # test synthetic indicator logic directly
    simulation_runner._config = SimConfig(date="2026-08-28", strategy="supertrend", strategies=["supertrend"])
    simulation_runner._stats.signals_fired = 0
    simulation_runner._stats.events = []
    simulation_runner._stats.trades = []

    tz_ist = timezone(timedelta(hours=5, minutes=30))
    # Feed 24 flat bars: prices should not generate a crossover at bar 22
    for i in range(24):
        t = datetime(2026, 8, 28, 9, 15, 0, tzinfo=tz_ist) + timedelta(minutes=i * 5)
        bar = {
            "symbol": "NIFTY",
            "open": 24000.0,
            "high": 24010.0,
            "low": 23990.0,
            "close": 24000.0,
            "volume": 10000,
            "time": int(t.timestamp()),
        }
        simulation_runner._evaluate_bar(bar, t)

    st_events = [ev for ev in simulation_runner._stats.events if ev.strategy == "supertrend"]
    assert len(st_events) == 0


@pytest.mark.asyncio
async def test_september_4_replay_emits_only_lt_and_sbin(september_4_recorded_evidence):
    """Verify that simulating 2026-09-04 replays only LT and SBIN and formats accurate Kite legs."""
    config = SimConfig(
        date="2026-09-04",
        start_time="09:15:00",
        end_time="15:30:00",
        speed=50.0,
        strategy="supertrend",
        strategies=["supertrend"],
    )
    await simulation_runner.start(config)

    # Wait for simulation to transition to RUNNING state
    import asyncio
    for _ in range(50):
        if simulation_runner.status.state == SimState.RUNNING:
            break
        await asyncio.sleep(0.05)

    assert simulation_runner.status.state == SimState.RUNNING

    # Fast forward clock to 11:20:00 (user's screenshot time)
    # 2026-09-04 11:20:00 IST
    from datetime import datetime, timezone, timedelta
    tz_ist = timezone(timedelta(hours=5, minutes=30))
    t_1120 = int(datetime(2026, 9, 4, 11, 20, 0, tzinfo=tz_ist).timestamp())
    simulation_runner._seek_requested_epoch = float(t_1120)

    # Wait for seek to apply
    for _ in range(50):
        if simulation_runner._seek_requested_epoch is None and simulation_runner._current_sim_epoch >= t_1120:
            break
        await asyncio.sleep(0.05)

    # At 11:20:00, LT (09:15) should have fired, but SBIN (12:15) has not yet
    assert simulation_runner._stats.signals_fired == 1
    assert simulation_runner._stats.events[0].instrument == "LT"

    # Now step past 12:15:00 (e.g. 12:30:00 IST)
    t_1230 = int(datetime(2026, 9, 4, 12, 30, 0, tzinfo=tz_ist).timestamp())
    simulation_runner._seek_requested_epoch = float(t_1230)
    for _ in range(50):
        if simulation_runner._seek_requested_epoch is None and simulation_runner._current_sim_epoch >= t_1230:
            break
        await asyncio.sleep(0.05)

    # Now both LT and SBIN should have fired, and NO spurious index signals
    assert simulation_runner._stats.signals_fired == 2
    symbols = [ev.instrument for ev in simulation_runner._stats.events]
    assert symbols == ["LT", "SBIN"]

    # Verify Kite signals response formatting contains the real option legs
    kite_resp = simulation_runner.get_kite_signals_response()
    assert len(kite_resp["rows"]) == 2
    underlyings = [r["underlying"] for r in kite_resp["rows"]]
    assert underlyings == ["LT", "SBIN"]

    # Verify executed trades have valid entry and exit timestamps and slippage accounting
    assert len(simulation_runner._stats.trades) == 2
    for tr in simulation_runner._stats.trades:
        assert tr.entry_time_iso != ""
        assert tr.exit_time_iso != ""
        assert len(tr.entry_time_iso.split(":")) == 3
        assert tr.exit_time_iso == "OPEN" or len(tr.exit_time_iso.split(":")) == 3
        assert tr.raw_entry is not None
        if tr.status != "OPEN":
            assert tr.raw_exit is not None
        assert tr.slippage is not None and tr.slippage >= 0
        assert tr.entry_price >= tr.raw_entry
    assert simulation_runner._stats.trades[0].entry_time_iso == "09:15:00"

    await simulation_runner.stop()


@pytest.mark.asyncio
async def test_simulation_ideal_friction_mode(september_4_recorded_evidence):
    """Verify that ideal friction mode executes at raw signal entry/exit with 0 slippage."""
    config = SimConfig(
        date="2026-09-04",
        start_time="09:15:00",
        end_time="15:30:00",
        speed=50.0,
        strategy="supertrend",
        strategies=["supertrend"],
        friction_mode="ideal",
    )
    await simulation_runner.start(config)

    import asyncio
    for _ in range(50):
        if simulation_runner.status.state == SimState.RUNNING:
            break
        await asyncio.sleep(0.05)

    from datetime import datetime, timezone, timedelta
    tz_ist = timezone(timedelta(hours=5, minutes=30))
    t_1230 = int(datetime(2026, 9, 4, 12, 30, 0, tzinfo=tz_ist).timestamp())
    simulation_runner._seek_requested_epoch = float(t_1230)
    for _ in range(50):
        if simulation_runner._seek_requested_epoch is None and simulation_runner._current_sim_epoch >= t_1230:
            break
        await asyncio.sleep(0.05)

    assert len(simulation_runner._stats.trades) == 2
    for tr in simulation_runner._stats.trades:
        assert tr.slippage == 0.0
        assert tr.entry_price == tr.raw_entry
        if tr.status != "OPEN":
            assert tr.exit_price == tr.raw_exit

    await simulation_runner.stop()


def test_gamma_move_snapshot_matches_the_live_board_schema():
    """Simulation must publish `candidates` + `config`, not a private `signals` blob.

    The live adapter reads those keys. A sim payload that only has `signals`
    makes Gamma Move disappear from the board during replay.
    """
    simulation_runner._config = SimConfig(date="2026-08-28")
    simulation_runner._stats.events = [
        SimSignalEvent(
            time_iso="10:15:00", timestamp_ms=1788756300000,
            strategy="gamma_move", instrument="RELIANCE",
            direction="BULLISH", strength="WATCHING",
            entry=1298.0, stop=1280.0, target=1320.0,
            contract="RELIANCE26AUG1300CE", opt_type="CE", strike=1300.0,
            spot=1298.0, premium_entry=53.0, premium_sl=37.0, premium_target=80.0,
        ),
        SimSignalEvent(
            time_iso="10:16:00", timestamp_ms=1788756360000,
            strategy="supertrend", instrument="NIFTY",
            direction="BULLISH", strength="STRONG",
            entry=24000.0, stop=23900.0, target=24200.0,
        ),
    ]
    snap = simulation_runner.get_gamma_move_snapshot()
    assert snap["strategy"]["id"] == "gamma_move"
    assert snap["config"]["require_chain_max_oi"] is True
    assert len(snap["candidates"]) == 1
    row = snap["candidates"][0]
    assert row["state"] == "watching"
    assert row["metrics"] is None
    assert "open-interest" in row["reason"]
    assert any("open-interest" in b for b in snap["blockers"])
    simulation_runner._stats.events = []


def test_gamma_move_watch_needs_enough_history():
    from app.services.simulation import _gamma_move_watch_from_bars
    short = [{"open": 100, "high": 101, "low": 99, "close": 100, "time": i} for i in range(5)]
    assert _gamma_move_watch_from_bars(short, 100.0) is None


def test_asof_symbol_bars_drops_future_prints():
    from app.services.simulation import _asof_symbol_bars
    candles = [
        {"symbol": "RELIANCE", "time": 1, "close": 10},
        {"symbol": "RELIANCE", "time": 2, "close": 11},
        {"symbol": "RELIANCE", "time": 3, "close": 12},
        {"symbol": "TCS", "time": 2, "close": 99},
    ]
    got = _asof_symbol_bars(candles, "RELIANCE", 2)
    assert [b["time"] for b in got] == [1, 2]


def test_adaptive_edge_snapshot_dynamic_ltp():
    """Verify get_adaptive_edge_snapshot dynamically tracks current spot and produces points delta."""
    from app.services.simulation import SimSignalEvent
    simulation_runner._stats.events = [
        SimSignalEvent(
            time_iso="09:15:00",
            timestamp_ms=1788752700000,
            strategy="adaptive_edge",
            instrument="NIFTY",
            direction="BULLISH",
            strength="STRONG",
            entry=23800.0,
            stop=23700.0,
            target=24000.0,
            contract="NIFTY26SEP23800CE",
            opt_type="CE",
            strike=23800.0,
            spot=23800.0,
            premium_entry=150.0,
            premium_sl=100.0,
            premium_target=250.0,
        )
    ]
    # Simulated current price has moved up by 100 points
    simulation_runner._bar_history = {
        "NIFTY": [{"close": 23900.0, "high": 23910.0, "low": 23890.0, "time": 1788753000}]
    }
    snap = simulation_runner.get_adaptive_edge_snapshot()
    assert len(snap["signals"]) == 1
    sig = snap["signals"][0]
    atm_leg = next(l for l in sig["legs"] if l["moneyness"] == "ATM")
    assert atm_leg["entry_premium"] == 150.0
    # Spot moved +100 points for CE -> option LTP should increase (~ +50 points)
    assert atm_leg["ltp"] > atm_leg["entry_premium"]
    pts_gain = atm_leg["ltp"] - atm_leg["entry_premium"]
    assert pts_gain == 50.0


def test_emit_recorded_signal_dynamic_lifecycle():
    """Verify _emit_recorded_signal opens trade as OPEN and manages lifecycle dynamically."""
    from datetime import datetime, timezone, timedelta
    ist = timezone(timedelta(hours=5, minutes=30))

    simulation_runner._stats.trades = []
    simulation_runner._stats.events = []
    simulation_runner._open_by_symbol = {}
    simulation_runner._candles = []
    simulation_runner._bars_played = 0
    simulation_runner._config = SimConfig(
        date="2026-09-07",
        instruments=["NIFTY"],
        strategy="supertrend",
        strategies=["supertrend"],
        friction_mode="ideal",
    )

    rec = {
        "underlying": "NIFTY",
        "direction": "BEARISH",
        "time_iso": "09:15:00",
        "timestamp_ms": 1788752700000,
        "spot": 23800.0,
        "stop_loss": 23850.0,
        "target": 23700.0,
        "strategy": "supertrend",
        "raw_row": {
            "legs": [
                {
                    "moneyness": "ATM",
                    "option_type": "PE",
                    "option_symbol": "NIFTY2690823800PE",
                    "strike": 23800.0,
                    "lot_size": 25,
                    "premium_spot": 100.0,
                    "premium_sl": 75.0,
                    "premium_target": 150.0,
                }
            ]
        },
    }

    # Emit signal at 09:15:00
    simulation_runner._emit_recorded_signal(rec)

    # 1. Trade MUST be opened as OPEN, not immediately closed as a loss!
    assert len(simulation_runner._stats.trades) == 1
    trade = simulation_runner._stats.trades[0]
    assert trade.status == "OPEN"
    assert trade.exit_time_iso == "OPEN"
    assert trade.exit_price is None
    assert trade.pnl_usd == 0.0
    assert "NIFTY" in simulation_runner._open_by_symbol
    assert len(simulation_runner._open_by_symbol["NIFTY"]) == 1

    # 2. Settle on bar 1 (price moves favorably to 23750, neither stop nor target hit)
    dt_bar1 = datetime.fromtimestamp(1788753000, tz=ist)
    bar1 = {"symbol": "NIFTY", "open": 23800.0, "high": 23810.0, "low": 23740.0, "close": 23750.0}
    simulation_runner._settle_open_positions(bar1, dt_bar1)

    assert trade.status == "OPEN"
    assert trade.bars_held == 1
    # Spot dropped 50 points, PE premium increases by 50 * 0.50 = 25 -> mark = 125, pnl = +25 * 25 = +625
    assert trade.pnl_usd == 625.0

    # 3. Settle on bar 2 (price hits target 23700)
    dt_bar2 = datetime.fromtimestamp(1788753300, tz=ist)
    bar2 = {"symbol": "NIFTY", "open": 23750.0, "high": 23760.0, "low": 23690.0, "close": 23700.0}
    simulation_runner._settle_open_positions(bar2, dt_bar2)

    assert trade.status == "WIN"
    assert trade.exit_price is not None
    assert trade.exit_time_iso != "OPEN"
    assert trade.pnl_usd > 0
    assert "NIFTY" not in simulation_runner._open_by_symbol


def test_emit_recorded_signal_strategy_filtering():
    """Verify that _emit_recorded_signal respects cfg.strategies and does not leak unwanted strategies."""
    simulation_runner._config = SimConfig(date="2026-09-07", strategies=["vcp"])
    simulation_runner._stats.events = []
    simulation_runner._stats.trades = []
    simulation_runner._candles = []
    simulation_runner._bars_played = 0

    rec = {
        "underlying": "NIFTY",
        "direction": "BULLISH",
        "time_iso": "09:15:00",
        "timestamp_ms": 1788752700000,
        "spot": 24000.0,
        "stop_loss": 23900.0,
        "target": 24200.0,
        "strategy": "supertrend",
        "raw_row": {},
    }

    # Should NOT emit when filtered for vcp
    simulation_runner._emit_recorded_signal(rec)
    assert len(simulation_runner._stats.events) == 0

    # When filtered for adaptive_edge, maps cleanly to adaptive_edge
    simulation_runner._config = SimConfig(date="2026-09-07", strategies=["adaptive_edge"])
    simulation_runner._emit_recorded_signal(rec)
    assert len(simulation_runner._stats.events) == 1
    assert simulation_runner._stats.events[0].strategy == "adaptive_edge"

    # When filtered for supertrend, emits cleanly as supertrend
    simulation_runner._stats.events = []
    simulation_runner._config = SimConfig(date="2026-09-07", strategies=["supertrend"])
    simulation_runner._emit_recorded_signal(rec)
    assert len(simulation_runner._stats.events) == 1
    assert simulation_runner._stats.events[0].strategy == "supertrend"


def test_evaluate_bar_skips_synthetic_ae_when_recorded_present():
    """Verify that RSI pin-bar heuristics in _evaluate_bar do not fire synthetic AE signals when recorded signals exist for the day."""
    from datetime import datetime, timezone, timedelta
    ist = timezone(timedelta(hours=5, minutes=30))
    simulation_runner._config = SimConfig(date="2026-09-07", strategies=["adaptive_edge"])
    simulation_runner._recorded_signals = [{"underlying": "SENSEX", "timestamp_ms": 1788752700000}]
    simulation_runner._stats.events = []
    simulation_runner._stats.trades = []
    simulation_runner._bar_history = {}

    # Feed 20 bars for LT with oversold RSI (<25) and hammer pin-bar
    t0 = datetime(2026, 9, 7, 10, 0, 0, tzinfo=ist)
    for i in range(20):
        bar = {
            "symbol": "LT",
            "open": 3500.0 - i * 10,
            "high": 3505.0 - i * 10,
            "low": 3480.0 - i * 10,
            "close": 3495.0 - i * 10, # hammer
            "volume": 20000,
        }
        simulation_runner._evaluate_bar(bar, t0 + timedelta(minutes=5 * i))

    # Because recorded signals exist for the day, synthetic AE signals for LT must NOT fire
    ae_events = [ev for ev in simulation_runner._stats.events if ev.strategy == "adaptive_edge"]
    assert len(ae_events) == 0


def test_september_7_recorded_signals_adaptive_edge():
    """Verify that 2026-09-07 recorded signals emit all 6 authentic spot scans when replayed with adaptive_edge."""
    from app.services.simulation import _load_recorded_signals
    sigs = _load_recorded_signals("2026-09-07")
    assert len(sigs) == 6
    underlyings = {s["underlying"] for s in sigs}
    assert underlyings == {"NIFTY 50", "NIFTY BANK", "SENSEX", "BAJAJFINSV", "INFY", "TCS"}

    simulation_runner._config = SimConfig(date="2026-09-07", strategies=["adaptive_edge"])
    simulation_runner._recorded_signals = sigs
    simulation_runner._stats.events = []
    simulation_runner._stats.trades = []
    simulation_runner._open_by_symbol = {}
    simulation_runner._candles = []
    simulation_runner._bars_played = 0

    for s in sigs:
        simulation_runner._emit_recorded_signal(s)

    assert len(simulation_runner._stats.events) == 6
    assert all(ev.strategy == "adaptive_edge" for ev in simulation_runner._stats.events)
    assert len(simulation_runner._stats.trades) == 6
    assert all(tr.strategy == "adaptive_edge" for tr in simulation_runner._stats.trades)
    assert all(tr.status == "OPEN" for tr in simulation_runner._stats.trades)
    assert all(tr.opt_type == "PE" for tr in simulation_runner._stats.trades)

    # Verify Adaptive Edge snapshot formats all 6 signals with spot_scan origin
    snap = simulation_runner.get_adaptive_edge_snapshot()
    snap_sigs = snap.get("signals", [])
    assert len(snap_sigs) == 6
    assert all(s.get("scan_origin") == "spot_scan" for s in snap_sigs)


def test_status_since_preserves_closed_trades_under_delta_polling():
    """Verify status_since returns all trades (with updated status/exit price) so delta polling never misses exits."""
    from app.services.simulation import SimTradeEvent
    trade1 = SimTradeEvent(
        trade_id="TRD-1", entry_time_iso="09:15:00", exit_time_iso="09:20:00",
        timestamp_ms=1, strategy="adaptive_edge", symbol="NIFTY2690823800PE",
        underlying="NIFTY", direction="BUY", opt_type="PE", strike=23800,
        lots=1, quantity=25, entry_price=100, exit_price=120, stop_loss=80,
        target_price=150, status="WIN", pnl_usd=500, pnl_pct=20, duration_mins=5,
    )
    simulation_runner._stats.trades = [trade1]
    st = simulation_runner.status_since(since_events=0, since_trades=1)
    assert len(st.stats.trades) == 1
    assert st.stats.trades[0].status == "WIN"
    assert st.stats.trades[0].exit_price == 120


def test_subscribers_not_dropped_by_evaluate_bar():
    """Verify that an active SSE subscriber queue is retained across _evaluate_bar calls."""
    import asyncio
    from datetime import datetime, timezone, timedelta
    ist = timezone(timedelta(hours=5, minutes=30))

    q = asyncio.Queue(maxsize=100)
    simulation_runner._subscribers = [q]
    simulation_runner._config = SimConfig(date="2026-09-07", strategies=["all"])

    bar = {
        "symbol": "NIFTY",
        "open": 24000.0,
        "high": 24050.0,
        "low": 23950.0,
        "close": 24010.0,
        "volume": 50000,
    }
    simulation_runner._evaluate_bar(bar, datetime(2026, 9, 7, 9, 20, tzinfo=ist))

    assert q in simulation_runner._subscribers
    assert len(simulation_runner._subscribers) == 1
    simulation_runner._subscribers.clear()


def test_settle_open_positions_alias_handling():
    """Verify that an open position recorded under 'NIFTY 50' settles against a bar with symbol 'NIFTY'."""
    from datetime import datetime, timezone, timedelta
    from app.services.simulation import SimTradeEvent
    ist = timezone(timedelta(hours=5, minutes=30))

    simulation_runner._config = SimConfig(date="2026-09-07", strategies=["all"], friction_mode="ideal")
    trade = SimTradeEvent(
        trade_id="TRD-ALIAS-1",
        entry_time_iso="09:15:00",
        exit_time_iso="OPEN",
        timestamp_ms=1788752700000,
        strategy="supertrend",
        symbol="NIFTY2690823800PE",
        underlying="NIFTY 50",
        direction="BUY",
        opt_type="PE",
        strike=23800.0,
        lots=1,
        quantity=25,
        entry_price=100.0,
        exit_price=None,
        stop_loss=75.0,
        target_price=150.0,
        status="OPEN",
        pnl_usd=0.0,
        pnl_pct=0.0,
        duration_mins=0,
        spot_entry=23800.0,
        spot_stop=23850.0,
        spot_target=23700.0,
        bars_held=0,
    )

    simulation_runner._open_by_symbol = {"NIFTY 50": [trade]}
    simulation_runner._stats.trades = [trade]

    # Bar arrives with symbol "NIFTY", hitting target 23700
    dt_bar = datetime(2026, 9, 7, 9, 30, tzinfo=ist)
    bar = {"symbol": "NIFTY", "open": 23750.0, "high": 23760.0, "low": 23690.0, "close": 23700.0}
    simulation_runner._settle_open_positions(bar, dt_bar)

    assert trade.status == "WIN"
    assert trade.exit_price is not None
    assert trade.exit_time_iso == "09:30:00"
    assert trade.pnl_usd > 0
    assert "NIFTY 50" not in simulation_runner._open_by_symbol


def test_kite_signals_synced_with_simulation_events():
    """Verify that get_kite_signals_response returns rows matching events fired during simulation."""
    simulation_runner._config = SimConfig(date="2026-09-08", strategies=["adaptive_edge"])
    simulation_runner._stats.events = [
        SimSignalEvent(
            time_iso="09:15:00",
            timestamp_ms=1788800100000,
            strategy="adaptive_edge",
            instrument="NIFTY",
            direction="BEARISH",
            strength="STRONG",
            entry=57.97,
            stop=43.5,
            target=86.9,
            contract="NIFTY2690823800PE",
            spot=23800.0,
            strike=23800.0,
            opt_type="PE",
            premium_entry=57.97,
            premium_sl=43.5,
            premium_target=86.9,
        )
    ]

    res = simulation_runner.get_kite_signals_response()
    assert len(res["rows"]) == 1
    row = res["rows"][0]
    assert row["underlying"] in ("NIFTY", "NIFTY 50")
    assert row["direction"] == "short"
    assert row["option_type"] == "PE"
    assert len(row["legs"]) > 0
    assert "26SEP" in row["legs"][0]["option_symbol"] or row["legs"][0]["option_symbol"] == "NIFTY2690823800PE"


def test_has_session_view_lifecycle():
    """Verify that has_session_view is True during replay AND while finished session is reviewed, but False when cleared."""
    from app.services.simulation import SimState, SimSignalEvent

    # 1. Initially idle and empty -> False
    simulation_runner.clear()
    assert simulation_runner.has_session_view is False

    # 2. Running -> True
    simulation_runner._state = SimState.RUNNING
    assert simulation_runner.has_session_view is True

    # 3. Finished session (state is IDLE, but _session_complete is True and events present) -> True
    simulation_runner._state = SimState.IDLE
    simulation_runner._session_complete = True
    simulation_runner._stats.events = [
        SimSignalEvent(
            time_iso="09:15:00",
            timestamp_ms=1788800100000,
            strategy="adaptive_edge",
            instrument="NIFTY",
            direction="BEARISH",
            strength="STRONG",
            entry=57.97,
            stop=43.5,
            target=86.9,
        )
    ]
    assert simulation_runner.has_session_view is True

    # 4. User clears session -> False
    simulation_runner.clear()
    assert simulation_runner.has_session_view is False


def test_simulation_adaptive_source_filtering():
    """Verify adaptive_source option ('both', 'ae_model', 'spot_scan') controls AE signals and trades."""
    stock_spot_sig = {
        "underlying": "RELIANCE",
        "direction": "BEARISH",
        "time_iso": "10:00:00",
        "timestamp_ms": 1788752700000,
        "spot": 3000.0,
        "stop_loss": 3050.0,
        "strategy": "supertrend",
        "is_spot_scan": True,
        "source": "spot",
        "raw_row": {
            "underlying": "RELIANCE",
            "direction": "SHORT",
            "spot": 3000.0,
            "scan_origin": "spot_scan",
        },
    }

    # 1. Mode: ae_model -> spot scan signals are skipped
    simulation_runner.clear()
    simulation_runner._config = SimConfig(
        date="2026-09-07",
        strategies=["adaptive_edge"],
        adaptive_source="ae_model",
    )
    simulation_runner._emit_recorded_signal(stock_spot_sig)
    assert len(simulation_runner._stats.events) == 0
    assert len(simulation_runner._stats.trades) == 0

    # 2. Mode: spot_scan -> spot scan signals ARE emitted
    simulation_runner.clear()
    simulation_runner._config = SimConfig(
        date="2026-09-07",
        strategies=["adaptive_edge"],
        adaptive_source="spot_scan",
    )
    simulation_runner._emit_recorded_signal(stock_spot_sig)
    assert len(simulation_runner._stats.events) == 1
    assert simulation_runner._stats.events[0].strategy == "adaptive_edge"
    assert len(simulation_runner._stats.trades) == 1

    snap_spot = simulation_runner.get_adaptive_edge_snapshot()
    assert len(snap_spot["signals"]) == 1
    assert snap_spot["signals"][0]["scan_origin"] == "spot_scan"

    # In spot_scan mode, synthetic/candle AE model evaluation is skipped
    simulation_runner._stats.events = []
    simulation_runner._stats.trades = []
    from datetime import datetime, timezone, timedelta
    ist = timezone(timedelta(hours=5, minutes=30))
    t0 = datetime(2026, 9, 7, 10, 0, 0, tzinfo=ist)
    for i in range(25):
        bar = {
            "symbol": "NIFTY 50",
            "open": 24000.0 - i * 10,
            "high": 24005.0 - i * 10,
            "low": 23980.0 - i * 10,
            "close": 23995.0 - i * 10,
            "volume": 50000,
        }
        simulation_runner._evaluate_bar(bar, t0 + timedelta(minutes=5 * i))
    assert len(simulation_runner._stats.events) == 0

    # 3. Mode: both -> spot scan signals are emitted
    simulation_runner.clear()
    simulation_runner._config = SimConfig(
        date="2026-09-07",
        strategies=["adaptive_edge"],
        adaptive_source="both",
    )
    simulation_runner._emit_recorded_signal(stock_spot_sig)
    assert len(simulation_runner._stats.events) == 1
    assert simulation_runner._stats.events[0].strategy == "adaptive_edge"
    assert len(simulation_runner._stats.trades) == 1

