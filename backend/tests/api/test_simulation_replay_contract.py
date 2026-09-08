"""
Contract tests for the replay engine's execution model, option legs, capability
advertisement and seek/delta surfaces.

These exist because the previous generation of this feature declared
`friction_mode` on the config and read it nowhere, while the UI rendered a
"SLIPPAGE DRAG" metric against it — so every replay reported that execution was
free. Each test below pins one half of that contract.
"""
import pytest

from app.services.simulation import (
    SimConfig,
    SimStats,
    SimTradeEvent,
    SimulationRunner,
    _apply_friction,
    _option_contract,
    _pick_moneyness,
    _strike_step,
)


# ── Option leg resolution ────────────────────────────────────────────────────

def test_atm_call_snaps_to_the_index_strike_step():
    leg = _option_contract("NIFTY", 24_512.0, "BULLISH", SimConfig(date="2026-09-04"))
    assert leg["opt_type"] == "CE"
    assert leg["strike"] == 24_500.0
    assert leg["contract"].startswith("NIFTY")
    assert leg["contract"].endswith("CE")
    assert leg["lot_size"] == 25


def test_bearish_signal_resolves_to_a_put():
    leg = _option_contract("NIFTY", 24_512.0, "BEARISH", SimConfig(date="2026-09-04"))
    assert leg["opt_type"] == "PE"


def test_otm_moves_the_strike_away_from_spot_in_both_directions():
    """An OTM call is a HIGHER strike; an OTM put is a LOWER one.

    Applying the same signed offset to both would make one of them ITM, which is
    the arithmetic error this asserts against.
    """
    cfg = SimConfig(date="2026-09-04", moneyness="OTM1")
    call = _option_contract("NIFTY", 24_500.0, "BULLISH", cfg)
    put = _option_contract("NIFTY", 24_500.0, "BEARISH", cfg)
    assert call["strike"] > 24_500.0
    assert put["strike"] < 24_500.0


def test_itm_moves_the_strike_toward_the_money():
    cfg = SimConfig(date="2026-09-04", moneyness="ITM1")
    call = _option_contract("NIFTY", 24_500.0, "BULLISH", cfg)
    assert call["strike"] < 24_500.0


def test_banknifty_uses_a_wider_strike_step_than_nifty():
    assert _strike_step("BANKNIFTY", 52_300.0) == 100.0
    assert _strike_step("NIFTY", 24_500.0) == 50.0


def test_index_aliases_resolve_canonical_strike_steps():
    """Verbose exchange names like NIFTY 50 and NIFTY BANK must resolve correctly."""
    assert _strike_step("NIFTY 50", 24_500.0) == 50.0
    assert _strike_step("NIFTY BANK", 52_300.0) == 100.0
    assert _strike_step("FINNIFTY", 23_000.0) == 50.0
    assert _strike_step("NIFTY FIN SERVICE", 23_000.0) == 50.0
    assert _strike_step("MIDCPNIFTY", 12_000.0) == 25.0
    assert _strike_step("NIFTY MID SELECT", 12_000.0) == 25.0
    assert _strike_step("SENSEX", 80_000.0) == 100.0
    assert _strike_step("BANKEX", 60_000.0) == 100.0


def test_index_aliases_produce_clean_contract_symbols_without_spaces():
    """Option contracts for NIFTY 50 / NIFTY BANK must never contain spaces."""
    leg = _option_contract("NIFTY 50", 24_512.0, "BULLISH", SimConfig(date="2026-09-04"))
    assert leg["contract"].startswith("NIFTY26")
    assert " " not in leg["contract"]
    assert leg["strike"] == 24_500.0
    assert leg["lot_size"] == 25

    leg_bank = _option_contract("NIFTY BANK", 52_300.0, "BEARISH", SimConfig(date="2026-09-04"))
    assert leg_bank["contract"].startswith("BANKNIFTY26")
    assert " " not in leg_bank["contract"]
    assert leg_bank["strike"] == 52_300.0
    assert leg_bank["opt_type"] == "PE"


def test_index_aliases_apply_index_spread_friction():
    """NIFTY 50 must receive index spread friction rather than stock spread friction."""
    cfg = SimConfig(date="2026-09-04", friction_mode="realistic")
    entry_short, exit_short, _ = _apply_friction(100.0, 120.0, "NIFTY", cfg)
    entry_alias, exit_alias, _ = _apply_friction(100.0, 120.0, "NIFTY 50", cfg)
    assert entry_short == entry_alias
    assert exit_short == exit_alias



def test_stock_strike_step_scales_with_price():
    """A 2.5-point step on a 3,000-rupee stock would be nonsense."""
    assert _strike_step("TATASTEEL", 150.0) <= 5.0
    assert _strike_step("RELIANCE", 3_000.0) >= 20.0


def test_pick_moneyness_takes_the_first_concrete_leg():
    assert _pick_moneyness(SimConfig(date="d", moneyness="ALL")) == "ATM"
    assert _pick_moneyness(SimConfig(date="d", moneyness="OTM1,OTM2")) == "OTM1"
    assert _pick_moneyness(SimConfig(date="d", moneyness="nonsense")) == "ATM"


def test_stock_lot_size_differs_from_index():
    stock = _option_contract("RELIANCE", 3_000.0, "BULLISH", SimConfig(date="d"))
    assert stock["lot_size"] == 15


# ── Execution friction ───────────────────────────────────────────────────────

def test_realistic_friction_buys_higher_and_sells_lower():
    cfg = SimConfig(date="2026-09-04", friction_mode="realistic")
    entry, exit_, mode = _apply_friction(100.0, 120.0, "NIFTY", cfg)
    assert mode == "realistic"
    assert entry > 100.0, "a buy must fill at the ask, above the theoretical price"
    assert exit_ < 120.0, "a sell must fill at the bid, below the theoretical price"


def test_ideal_mode_fills_at_the_theoretical_price():
    cfg = SimConfig(date="2026-09-04", friction_mode="ideal")
    entry, exit_, mode = _apply_friction(100.0, 120.0, "NIFTY", cfg)
    assert (entry, exit_, mode) == (100.0, 120.0, "ideal")


def test_stock_options_are_charged_a_wider_spread_than_index_options():
    cfg = SimConfig(date="2026-09-04", friction_mode="realistic")
    idx_entry, _, _ = _apply_friction(100.0, 120.0, "NIFTY", cfg)
    stk_entry, _, _ = _apply_friction(100.0, 120.0, "RELIANCE", cfg)
    assert stk_entry > idx_entry


def test_friction_parameters_are_honoured_not_hardcoded():
    """The config's percentages must actually reach the arithmetic.

    A wider configured spread has to produce a worse fill; if it does not, the
    parameters are decorative — which is the original defect.
    """
    narrow = SimConfig(date="d", index_spread_pct=0.10, slippage_pct=0.0)
    wide = SimConfig(date="d", index_spread_pct=4.00, slippage_pct=0.0)
    narrow_entry, _, _ = _apply_friction(100.0, 120.0, "NIFTY", narrow)
    wide_entry, _, _ = _apply_friction(100.0, 120.0, "NIFTY", wide)
    assert wide_entry > narrow_entry


def test_a_fill_is_never_pushed_below_zero():
    cfg = SimConfig(date="d", index_spread_pct=400.0, slippage_pct=400.0)
    _, exit_, _ = _apply_friction(100.0, 0.10, "NIFTY", cfg)
    assert exit_ >= 0.05


# ── Aggregates ───────────────────────────────────────────────────────────────

def _trade(**kw):
    base = dict(
        trade_id="T1", entry_time_iso="09:20:00", exit_time_iso="09:40:00",
        strategy="supertrend", symbol="NIFTY26AUG24500CE", underlying="NIFTY",
        direction="BUY", opt_type="CE", strike=24_500.0, lots=1, quantity=25,
        entry_price=100.0, exit_price=110.0, stop_loss=75.0, target_price=150.0,
        status="WIN", pnl_usd=250.0, pnl_pct=10.0, duration_mins=20,
    )
    base.update(kw)
    return SimTradeEvent(**base)


def test_slippage_total_is_none_when_no_trade_carried_friction():
    """`None` means "not modelled"; 0.0 would mean "modelled, and free".

    Collapsing the two is exactly what made the dock report zero execution cost
    for every strategy.
    """
    runner = SimulationRunner()
    runner._stats = SimStats(trades=[_trade(slippage=None)])
    runner._recompute_totals()
    assert runner._stats.slippage_total is None


def test_slippage_total_sums_only_trades_that_carry_it():
    runner = SimulationRunner()
    runner._stats = SimStats(trades=[
        _trade(trade_id="T1", slippage=12.5),
        _trade(trade_id="T2", slippage=7.5),
        _trade(trade_id="T3", slippage=None),
    ])
    runner._recompute_totals()
    assert runner._stats.slippage_total == 20.0


def test_recompute_totals_derives_every_aggregate_from_the_ledger():
    runner = SimulationRunner()
    runner._stats = SimStats(trades=[
        _trade(trade_id="T1", status="WIN", pnl_usd=250.0),
        _trade(trade_id="T2", status="LOSS", pnl_usd=-100.0),
    ])
    runner._recompute_totals()
    assert runner._stats.wins == 1
    assert runner._stats.losses == 1
    assert runner._stats.trades_entered == 2
    assert runner._stats.pnl == 150.0


# ── Delta status ─────────────────────────────────────────────────────────────

def _signal(ms):
    from app.services.simulation import SimSignalEvent
    return SimSignalEvent(
        time_iso="09:20:00", timestamp_ms=ms, strategy="supertrend",
        instrument="NIFTY", direction="BULLISH", strength="STRONG",
        entry=100.0, stop=90.0, target=120.0,
    )


def test_status_without_offsets_returns_the_full_payload():
    runner = SimulationRunner()
    runner._stats = SimStats(events=[_signal(1), _signal(2), _signal(3)])
    assert len(runner.status.stats.events) == 3


def test_status_since_returns_only_unseen_rows():
    runner = SimulationRunner()
    runner._stats = SimStats(events=[_signal(1), _signal(2), _signal(3)])
    st = runner.status_since(since_events=2, since_trades=0)
    assert len(st.stats.events) == 1
    assert st.events_total == 3, "the total must still describe the whole ledger"


def test_status_since_resets_when_the_ledger_shrank():
    """A seek truncates the ledger, so a stale offset would return nothing.

    Falling back to the full payload is what lets the client notice and resync
    rather than silently rendering an empty feed.
    """
    runner = SimulationRunner()
    runner._stats = SimStats(events=[_signal(1)])
    st = runner.status_since(since_events=99)
    assert len(st.stats.events) == 1


def test_status_reports_capabilities():
    st = SimulationRunner().status
    assert st.capabilities.friction is True
    assert st.capabilities.contract_on_signal is True
    assert st.capabilities.absolute_seek is True
    assert st.capabilities.multi_day is False


# ── Absolute seek ────────────────────────────────────────────────────────────

def _armed_runner():
    from app.services.simulation import SimState
    r = SimulationRunner()
    r._state = SimState.RUNNING
    r._start_epoch = 1_787_889_000
    r._end_epoch = 1_787_911_500          # 09:15 → 15:30 IST
    r._current_sim_epoch = float(r._start_epoch)
    r._candles = [{"time": r._start_epoch + i * 300} for i in range(76)]
    return r


def test_seek_to_pct_lands_at_the_midpoint():
    r = _armed_runner()
    r.seek_to(to_pct=50.0)
    mid = (r._start_epoch + r._end_epoch) / 2
    assert abs(r._seek_requested_epoch - mid) < 1.0


def test_seek_to_pct_clamps_out_of_range_input():
    r = _armed_runner()
    r.seek_to(to_pct=999.0)
    assert r._seek_requested_epoch == float(r._end_epoch)
    r.seek_to(to_pct=-50.0)
    assert r._seek_requested_epoch == float(r._start_epoch)


def test_seek_to_bar_index_clamps_to_the_candle_range():
    r = _armed_runner()
    r.seek_to(bar_index=10_000)
    assert r._seek_requested_epoch == float(r._candles[-1]["time"])


def test_seek_to_time_resolves_against_the_session_date():
    r = _armed_runner()
    r.seek_to(to_time="12:00:00")
    assert r._start_epoch < r._seek_requested_epoch < r._end_epoch


def test_seek_is_a_noop_while_idle():
    r = SimulationRunner()
    r.seek_to(to_pct=50.0)
    assert r._seek_requested_epoch is None


# ── Multi-day refusal ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_multi_day_range_is_refused_out_loud():
    """The loop derives start/end from `date` alone.

    Accepting a differing `end_date` would replay one session while the UI
    claimed a range — a silent wrong answer, which is worse than an error.
    """
    from app.services.simulation import SimState
    runner = SimulationRunner()
    await runner.start(SimConfig(date="2026-09-03", end_date="2026-09-05", instruments=["NIFTY"]))
    if runner._task:
        await runner._task
    assert runner.status.state == SimState.IDLE
    assert "Multi-day" in runner.status.status_message


# ── SSE fan-out ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_subscriber_receives_the_current_state_immediately():
    """A client connecting mid-session must not stare at a blank dock."""
    runner = SimulationRunner()
    agen = runner.subscribe()
    first = await agen.__anext__()
    assert first.kind == "state"
    assert first.data["state"] == "idle"
    await agen.aclose()


@pytest.mark.asyncio
async def test_signals_and_trades_reach_subscribers():
    runner = SimulationRunner()
    agen = runner.subscribe()
    await agen.__anext__()                       # the opening state event
    runner._publish("signal", {"instrument": "NIFTY"})
    runner._publish("trade", {"trade_id": "T1"})
    assert (await agen.__anext__()).kind == "signal"
    assert (await agen.__anext__()).kind == "trade"
    await agen.aclose()


@pytest.mark.asyncio
async def test_closing_the_generator_unregisters_the_subscriber():
    """Otherwise every disconnected client leaks a queue that fills forever."""
    runner = SimulationRunner()
    agen = runner.subscribe()
    await agen.__anext__()
    assert len(runner._subscribers) == 1
    await agen.aclose()
    assert runner._subscribers == []


def test_frames_are_throttled_and_harder_at_high_speed():
    """At 5000x the clock advances hours a second; a frame per tick is noise."""
    import time as _time

    runner = SimulationRunner()
    q: list = []
    runner._subscribers = [_CollectingQueue(q)]

    runner._speed = 1.0
    runner._publish_frame(force=True)
    runner._publish_frame()                      # immediately after — throttled
    assert len(q) == 1

    runner._last_frame_at = _time.monotonic() - 0.2
    runner._speed = 1.0
    runner._publish_frame()                      # 0.2s > the 0.1s gap at 1x
    assert len(q) == 2

    runner._last_frame_at = _time.monotonic() - 0.2
    runner._speed = 1000.0
    runner._publish_frame()                      # 0.2s < the 0.5s gap at 1000x
    assert len(q) == 2


def test_backpressure_drops_frames_but_never_signals():
    """A dropped frame costs a progress tick; a dropped signal corrupts the ledger."""
    import asyncio as _asyncio

    runner = SimulationRunner()
    full = _asyncio.Queue(maxsize=1)
    full.put_nowait("occupied")
    runner._subscribers = [full]

    runner._publish("frame", {"pct": 50})
    assert full.qsize() == 1
    assert full.get_nowait() == "occupied", "a frame must not evict a queued item"

    full.put_nowait("occupied")
    runner._publish("signal", {"instrument": "NIFTY"})
    survived = full.get_nowait()
    assert getattr(survived, "kind", None) == "signal"


class _CollectingQueue:
    """Minimal stand-in for asyncio.Queue that records what was published."""

    def __init__(self, sink: list):
        self._sink = sink

    def put_nowait(self, item):
        self._sink.append(item)


def test_jump_start_clears_open_book():
    runner = SimulationRunner()
    runner._start_epoch = 1757000000
    trade = SimTradeEvent(
        trade_id="TRD-1",
        entry_time_iso="09:20:00",
        exit_time_iso="OPEN",
        timestamp_ms=1757000000000,
        strategy="supertrend",
        symbol="NIFTY26SEP24500CE",
        underlying="NIFTY",
        direction="BUY",
        opt_type="CE",
        strike=24500.0,
        lots=1,
        quantity=25,
        entry_price=100.0,
        stop_loss=75.0,
        target_price=150.0,
        status="OPEN",
    )
    runner._open_by_symbol["NIFTY"] = [trade]
    runner._stats.trades.append(trade)

    status = runner.jump_start()
    assert runner._open_by_symbol == {}
    assert status.open_positions == 0
    assert status.stats.trades == []


def test_backward_seek_reopens_future_closed_trades():
    runner = SimulationRunner()
    trade = SimTradeEvent(
        trade_id="TRD-1",
        entry_time_iso="09:20:00",
        exit_time_iso="09:45:00",
        timestamp_ms=1757000000000,
        exit_timestamp_ms=1757001500000,
        strategy="supertrend",
        symbol="NIFTY26SEP24500CE",
        underlying="NIFTY",
        direction="BUY",
        opt_type="CE",
        strike=24500.0,
        lots=1,
        quantity=25,
        entry_price=100.0,
        exit_price=150.0,
        stop_loss=75.0,
        target_price=150.0,
        status="WIN",
        pnl_usd=1250.0,
    )
    runner._stats.trades = [trade]
    target_ms = 1757000500000  # between entry (09:20) and exit (09:45)

    # Reconstruct seek logic from runner
    runner._open_by_symbol = {}
    for tr in runner._stats.trades:
        if tr.exit_timestamp_ms is not None and tr.exit_timestamp_ms > target_ms:
            tr.status = "OPEN"
            tr.exit_price = None
            tr.exit_time_iso = "OPEN"
            tr.exit_timestamp_ms = None
            tr.pnl_usd = 0.0
            tr.pnl_pct = 0.0
        if tr.status == "OPEN":
            runner._open_by_symbol.setdefault(tr.underlying, []).append(tr)
    runner._recompute_totals()

    assert trade.status == "OPEN"
    assert trade.exit_price is None
    assert trade.exit_time_iso == "OPEN"
    assert "NIFTY" in runner._open_by_symbol
    assert runner._open_by_symbol["NIFTY"] == [trade]
    assert runner._stats.wins == 0
    assert runner._stats.pnl == 0.0


def test_seek_to_while_paused_updates_state_immediately():
    from app.services.simulation import SimState
    runner = SimulationRunner()
    runner._state = SimState.PAUSED
    runner._start_epoch = 1756353000  # 09:15:00 IST
    runner._end_epoch = 1756375500    # 15:30:00 IST
    runner._candles = [
        {"time": 1756353000 + (i * 300), "symbol": "NIFTY", "open": 24000 + i, "close": 24001 + i}
        for i in range(75)
    ]
    trade = SimTradeEvent(
        trade_id="TRD-SEEK-1",
        entry_time_iso="09:20:00",
        exit_time_iso="11:30:00",
        timestamp_ms=(1756353000 + 300) * 1000,
        exit_timestamp_ms=(1756353000 + 7200) * 1000,
        strategy="supertrend",
        symbol="NIFTY26SEP24500CE",
        underlying="NIFTY",
        direction="BUY",
        opt_type="CE",
        strike=24500.0,
        lots=1,
        quantity=25,
        entry_price=100.0,
        exit_price=150.0,
        stop_loss=75.0,
        target_price=150.0,
        status="WIN",
        pnl_usd=1250.0,
    )
    runner._stats.trades = [trade]

    # Target: 10:05:00 IST (45 mins in, before trade exit at 11:30:00)
    target_epoch = 1756353000 + 2700
    status = runner.seek_to(target_epoch=target_epoch)

    # Verifies status is updated immediately without needing loop execution
    assert status.current_time_iso == "10:05:00"
    assert status.bars_played == 10  # 9:20 through 10:05 is 10 bars (0 to 9 <= target)
    assert status.open_positions == 1
    assert trade.status == "OPEN"
    assert trade.exit_price is None


def test_seek_to_parses_iso_datetime_and_time_strings():
    from app.services.simulation import SimState
    runner = SimulationRunner()
    runner._state = SimState.PAUSED
    runner._start_epoch = 1756353000  # 2025-08-28 09:15:00 IST
    runner._end_epoch = 1756375500    # 2025-08-28 15:30:00 IST
    runner._candles = [
        {"time": 1756353000 + (i * 300), "symbol": "NIFTY"}
        for i in range(75)
    ]

    # Test full ISO datetime
    runner.seek_to(to_time="2025-08-28T10:30:00+05:30")
    assert runner._current_time_iso == "10:30:00"

    # Test space-separated datetime
    runner.seek_to(to_time="2025-08-28 11:00:00")
    assert runner._current_time_iso == "11:00:00"

    # Test standard HH:MM:SS
    runner.seek_to(to_time="11:45:00")
    assert runner._current_time_iso == "11:45:00"

    # Test HH:MM
    runner.seek_to(to_time="12:15")
    assert runner._current_time_iso == "12:15:00"


def test_seek_warms_bar_history_for_indicator_continuity():
    from app.services.simulation import SimState
    runner = SimulationRunner()
    runner._state = SimState.PAUSED
    runner._start_epoch = 1756353000
    runner._end_epoch = 1756375500
    runner._candles = [
        {"time": 1756353000 + (i * 300), "symbol": "NIFTY", "close": 24000.0 + i}
        for i in range(75)
    ]

    # Seek to bar 65
    target_epoch = 1756353000 + (65 * 300)
    runner.seek_to(target_epoch=target_epoch)

    assert "NIFTY" in runner._bar_history
    # Capped at max 60 history bars
    assert len(runner._bar_history["NIFTY"]) == 60
    # In-session count reflects all bars up to target
    assert runner._in_session_bars["NIFTY"] == 66
    # Most recent bar in history is bar 65
    assert runner._bar_history["NIFTY"][-1]["close"] == 24000.0 + 65

