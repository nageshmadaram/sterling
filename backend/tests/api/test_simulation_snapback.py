"""Snapback inside the replay, and the four ways a DAILY rule fakes it there.

Snapback fires on a daily CLOSE and fills at the NEXT session's OPEN. The replay
walks intraday bars. Every shortcut between those two facts produces a plausible
number for a strategy nobody wrote:

* evaluating only COMPLETED sessions reads yesterday's close on every bar of
  today, so one setup is emitted seventy-five times and none of them is today's;
* filling at the signal bar's CLOSE books a price the measured rule never pays;
* the shared 30-bar `max_hold_bars` times a fifteen-SESSION position out before
  lunch and stamps it MAX_HOLD;
* the shared trailing ratchet moves a stop the measured rule holds fixed.

Each is pinned below.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from app.services import simulation as sim

IST = timezone(timedelta(hours=5, minutes=30))


def _session_bars(day: datetime, closes: list[float], symbol: str) -> list[dict]:
    """One IST session of 5-minute bars whose closes walk `closes`."""
    out = []
    for i, c in enumerate(closes):
        t = day.replace(hour=9, minute=15, second=0, microsecond=0) + timedelta(minutes=5 * i)
        out.append({
            "symbol": symbol, "time": int(t.timestamp()),
            "open": float(closes[i - 1]) if i else float(c),
            "high": float(c) * 1.001, "low": float(c) * 0.999,
            "close": float(c), "volume": 1000.0,
        })
    return out


class TestTheFormingSession:
    def test_todays_bar_is_built_from_the_session_so_far(self):
        day = datetime(2026, 9, 4, tzinfo=IST)
        bars = _session_bars(day, [100.0, 104.0, 101.0, 107.0], "RELIANCE")
        formed = sim._forming_session(bars, bars[-1]["time"])
        assert formed is not None
        assert formed["open"] == pytest.approx(100.0)
        assert formed["close"] == pytest.approx(107.0)
        assert formed["high"] == pytest.approx(107.0 * 1.001)
        # Stamped at the session close, so it sorts after every completed bar
        # and carries the calendar day the market gate is keyed on.
        assert datetime.fromtimestamp(formed["time"], IST).strftime("%H:%M") == "15:30"

    def test_it_grows_as_the_session_does(self):
        """The whole point: ask the rule about THIS bar, not about yesterday."""
        day = datetime(2026, 9, 4, tzinfo=IST)
        bars = _session_bars(day, [100.0, 110.0, 105.0], "RELIANCE")
        early = sim._forming_session(bars, bars[0]["time"])
        late = sim._forming_session(bars, bars[-1]["time"])
        assert early["close"] == pytest.approx(100.0)
        assert late["close"] == pytest.approx(105.0)
        assert late["high"] > early["high"]

    def test_a_bar_after_the_clock_never_reaches_the_tape(self):
        day = datetime(2026, 9, 4, tzinfo=IST)
        bars = _session_bars(day, [100.0, 500.0], "RELIANCE")
        formed = sim._forming_session(bars, bars[0]["time"])
        assert formed["high"] < 200.0, "a future print leaked into the forming bar"

    def test_yesterdays_bars_do_not_join_todays(self):
        d1 = datetime(2026, 9, 3, tzinfo=IST)
        d2 = datetime(2026, 9, 4, tzinfo=IST)
        bars = _session_bars(d1, [100.0, 90.0], "X") + _session_bars(d2, [200.0, 210.0], "X")
        formed = sim._forming_session(bars, bars[-1]["time"])
        assert formed["low"] > 150.0


class TestTheContract:
    """`_option_contract` picks at-the-money and guesses ~2% of spot. Snapback
    buys a 0.70-delta contract about forty days out. Replaying the rule against
    the wrong leg reports a different strategy's P&L under this one's name."""

    def _leg(self, **kw):
        from app.engines.snapback import SnapbackConfig
        cfg = SnapbackConfig(max_rv_pct=100.0, **kw)
        return sim._snapback_leg("RELIANCE", 1400.0, "PE", 0.22, cfg, "2026-09-04")

    def test_the_strike_follows_the_configured_delta(self):
        leg = self._leg()
        assert leg is not None
        # A 0.70-delta PUT is IN the money: its strike sits ABOVE spot.
        assert leg["strike"] > 1400.0
        assert leg["delta"] == pytest.approx(0.70, abs=0.06)

    def test_it_is_not_the_at_the_money_approximation(self):
        leg = self._leg()
        crude = sim._option_contract("RELIANCE", 1400.0, "BEARISH", None,
                                     sim_date="2026-09-04")
        assert leg["strike"] != crude["strike"]
        assert leg["premium"] > crude["premium"]

    def test_a_lower_delta_is_a_cheaper_contract(self):
        assert self._leg(target_delta=0.30)["premium"] < self._leg()["premium"]

    def test_an_instrument_with_no_published_spec_resolves_nothing(self):
        from app.engines.snapback import SnapbackConfig
        assert sim._snapback_leg("NOT-A-REAL-NAME", 1400.0, "PE", 0.22,
                                 SnapbackConfig(), "2026-09-04") is None


class TestTheStop:
    """Snapback's stop is on the PREMIUM. The replay settles against underlying
    levels, so the translation has to be exact rather than a flat delta."""

    def _leg_and_cfg(self, stop_pct=35.0):
        from app.engines.snapback import SnapbackConfig
        cfg = SnapbackConfig(max_rv_pct=100.0, premium_stop_pct=stop_pct)
        leg = sim._snapback_leg("RELIANCE", 1400.0, "PE", 0.22, cfg, "2026-09-04")
        return leg, cfg

    def test_the_spot_stop_is_where_the_premium_has_given_back_the_stop(self):
        from app.engines.snapback.pricing import bs_price, smile_vol
        leg, cfg = self._leg_and_cfg()
        stop = sim._snapback_spot_stop(leg, 1400.0, cfg)
        at_stop = float(bs_price(stop, leg["strike"], leg["years"],
                                 float(smile_vol(stop, leg["strike"], leg["iv"], 0.0)),
                                 call=False))
        assert at_stop == pytest.approx(leg["premium"] * 0.65, rel=0.02)

    def test_a_put_is_stopped_ABOVE_spot(self):
        leg, cfg = self._leg_and_cfg()
        assert sim._snapback_spot_stop(leg, 1400.0, cfg) > 1400.0

    def test_a_tighter_stop_sits_closer_to_spot(self):
        tight_leg, tight_cfg = self._leg_and_cfg(20.0)
        wide_leg, wide_cfg = self._leg_and_cfg(60.0)
        tight = sim._snapback_spot_stop(tight_leg, 1400.0, tight_cfg)
        wide = sim._snapback_spot_stop(wide_leg, 1400.0, wide_cfg)
        assert 1400.0 < tight < wide


class TestTheHorizon:
    def test_sessions_become_bars_at_the_replays_own_resolution(self):
        r = sim.SimulationRunner()
        r._config = sim.SimConfig(date="2026-09-04", resolution="5m")
        assert r._bars_per_session() == 75
        r._config = sim.SimConfig(date="2026-09-04", resolution="1m")
        assert r._bars_per_session() == 375

    def test_a_position_with_its_own_horizon_outlives_the_config(self):
        """A fifteen-session position must not be stamped MAX_HOLD at bar 30."""
        r = sim.SimulationRunner()
        r._config = sim.SimConfig(date="2026-09-04", max_hold_bars=30,
                                  friction_mode="ideal")
        trade = sim.SimTradeEvent(
            trade_id="TRD-1", strategy="snapback", symbol="X26SEP1500PE",
            underlying="RELIANCE", direction="BUY", opt_type="PE", strike=1500.0,
            lots=1, quantity=500, entry_price=100.0, stop_loss=65.0,
            target_price=140.0, status="OPEN", spot_entry=1400.0,
            spot_stop=1500.0, spot_target=1300.0, spot_hwm=1400.0,
            spot_initial_risk=100.0, spot_initial_stop=1500.0, raw_entry=100.0,
            bars_held=40, max_hold_bars=1125, trails=False,
        )
        r._stats = sim.SimStats(trades=[trade])
        r._open_by_symbol = {"RELIANCE": [trade]}
        bar = {"symbol": "RELIANCE", "open": 1400.0, "high": 1401.0,
               "low": 1399.0, "close": 1400.0, "time": 0}
        r._settle_open_positions(bar, datetime(2026, 9, 4, 11, 0, tzinfo=IST))
        assert trade.status == "OPEN"
        assert trade.exit_reason is None

    def test_a_position_that_does_not_trail_keeps_its_stop(self):
        r = sim.SimulationRunner()
        r._config = sim.SimConfig(date="2026-09-04", friction_mode="ideal")

        def _trade(trails: bool) -> sim.SimTradeEvent:
            return sim.SimTradeEvent(
                trade_id="TRD-1", strategy="snapback", symbol="X26SEP1500PE",
                underlying="RELIANCE", direction="BUY", opt_type="PE",
                strike=1500.0, lots=1, quantity=500, entry_price=100.0,
                stop_loss=65.0, target_price=140.0, status="OPEN",
                spot_entry=1400.0, spot_stop=1500.0, spot_target=1200.0,
                spot_hwm=1400.0, spot_initial_risk=100.0,
                spot_initial_stop=1500.0, raw_entry=100.0,
                max_hold_bars=1125, trails=trails,
            )

        # The underlying falls hard — favourable for a put, so a ratchet would
        # pull the stop down behind it.
        bar = {"symbol": "RELIANCE", "open": 1350.0, "high": 1352.0,
               "low": 1340.0, "close": 1345.0, "time": 0}
        at = datetime(2026, 9, 4, 11, 0, tzinfo=IST)

        trailing = _trade(True)
        r._stats = sim.SimStats(trades=[trailing])
        r._open_by_symbol = {"RELIANCE": [trailing]}
        r._settle_open_positions(bar, at)

        fixed = _trade(False)
        r._stats = sim.SimStats(trades=[fixed])
        r._open_by_symbol = {"RELIANCE": [fixed]}
        r._settle_open_positions(bar, at)

        assert trailing.spot_stop < 1500.0, "the ratchet did not move at all"
        assert fixed.spot_stop == 1500.0, "a fixed stop was trailed"


class TestTheGateCannotBeSkipped:
    def test_a_missing_market_tape_emits_NOTHING_and_says_why(self):
        """`gate_for` answers None for "filter off" and for "no index tape"
        alike. Reading the second as the first is the configuration measured at
        -1.28% per entry day."""
        from app.engines.snapback import SnapbackConfig
        r = sim.SimulationRunner()
        r._candles = []
        gate, note = sim._snapback_market_gate(r, SnapbackConfig(), 1_757_000_000.0)
        assert gate is None
        assert "NIFTY" in note

    def test_the_filter_being_OFF_is_not_an_error(self):
        from app.engines.snapback import SnapbackConfig
        r = sim.SimulationRunner()
        gate, note = sim._snapback_market_gate(
            r, SnapbackConfig(max_rv_pct=100.0, market_filter="off"), 1_757_000_000.0)
        assert gate is None and note == ""


class TestTheStateMachine:
    def test_the_three_states_are_declared(self):
        assert sim.SNAPBACK_STATES == ("WATCHING", "CONFIRMED", "STRONG")

    def test_the_client_is_told_this_rule_is_daily(self):
        """"No trades" and "no setups" are different facts to an operator
        replaying a single session."""
        assert "snapback" in sim.SimCapabilities().daily_strategies

    def test_a_silent_strategy_carries_its_reason(self):
        r = sim.SimulationRunner()
        r._strategy_notes = {"snapback": "NIFTY tape unreadable"}
        assert r.status.strategy_notes["snapback"] == "NIFTY tape unreadable"


def _daily(n: int, *, start: float, drift: float, spike: float = 1.0) -> list[dict]:
    """`n` completed sessions ending at `2026-09-03`, last one spiked."""
    end = datetime(2026, 9, 3, 15, 30, tzinfo=IST)
    out = []
    for i in range(n):
        c = start * (drift ** i)
        if i == n - 1:
            c *= spike
        t = end - timedelta(days=(n - 1 - i))
        out.append({"open": c * 0.998, "high": c * 1.002, "low": c * 0.996,
                    "close": c, "volume": 1000.0, "time": t.timestamp()})
    return out


class TestEndToEnd:
    """Drive the real `_evaluate_bar` and watch the three states happen.

    The unit tests above pin each piece. This is the only one that proves the
    WIRING, which is where every previous version of this kind of adapter broke:
    the pieces were right and nothing called them.
    """

    @pytest.fixture
    def runner(self, monkeypatch):
        from app.engines.snapback import SnapbackConfig

        # A stock that grinds up and then breaks out hard on the last completed
        # session — the setup this rule is for.
        stock = _daily(200, start=1000.0, drift=1.0015, spike=1.10)
        # A market BELOW its own 50-session EMA, so the gate is open.
        market = _daily(200, start=25_000.0, drift=0.9985)

        def _store(symbol, asof_ts, days=400):
            rows = market if str(symbol).upper() == "NIFTY" else stock
            return [dict(b) for b in rows if b["time"] <= float(asof_ts)]

        monkeypatch.setattr(sim, "_store_daily_sessions", _store)
        monkeypatch.setattr(sim, "_snapback_config",
                            lambda: SnapbackConfig(max_rv_pct=100.0, enabled=True, cooldown_days=0,
                                                   hedge_mode="none"))
        r = sim.SimulationRunner()
        r._config = sim.SimConfig(date="2026-09-04", resolution="5m",
                                  strategies=["snapback"], friction_mode="ideal")
        r._stats = sim.SimStats()
        r._open_by_symbol = {}
        r._candles = []
        r._in_session_bars = {}
        return r

    def _play(self, runner, day: datetime, closes: list[float], symbol="RELIANCE"):
        """Play one session. NIFTY rides along, because the market gate needs a
        bar for TODAY and a replay without the index cannot gate anything."""
        stock = _session_bars(day, closes, symbol)
        # Continue the index DOWN from where its completed history left off, so
        # it stays below its own 50-session EMA and the gate is open. An index
        # session pinned at an arbitrary level is a different regime.
        base = 25_000.0 * (0.9985 ** 199)
        index = _session_bars(day, [base * (1 - 0.001 * i) for i in range(len(closes))],
                              "NIFTY")
        for s_bar, i_bar in zip(stock, index):
            runner._candles.append(i_bar)
            runner._candles.append(s_bar)
            at = datetime.fromtimestamp(s_bar["time"], IST)
            runner._evaluate_bar(dict(i_bar), at)
            runner._evaluate_bar(dict(s_bar), at)

    def test_a_replay_without_the_index_says_so_instead_of_going_quiet(self, runner):
        """"No Snapback signals" and "Snapback could not gate anything" are
        different facts, and only one of them is about the market."""
        for b in _session_bars(datetime(2026, 9, 4, tzinfo=IST), [1500.0] * 4,
                               "RELIANCE"):
            runner._candles.append(b)
            runner._evaluate_bar(dict(b), datetime.fromtimestamp(b["time"], IST))
        assert runner._stats.events == []
        assert "NIFTY" in runner.status.strategy_notes.get("snapback", "")

    def test_the_rule_reaches_the_feed_at_all(self, runner):
        # 2026-09-04 continues the breakout, so the rule fires on the forming bar.
        self._play(runner, datetime(2026, 9, 4, tzinfo=IST), [1500.0] * 6)
        kinds = [(e.strategy, e.strength) for e in runner._stats.events]
        assert ("snapback", "WATCHING") in kinds, kinds

    def test_one_setup_is_one_row_not_one_per_bar(self, runner):
        self._play(runner, datetime(2026, 9, 4, tzinfo=IST), [1500.0] * 20)
        watches = [e for e in runner._stats.events
                   if e.strategy == "snapback" and e.strength == "WATCHING"]
        assert len(watches) == 1, f"{len(watches)} rows for one unchanged setup"

    def test_no_position_is_opened_inside_the_signal_session(self, runner):
        self._play(runner, datetime(2026, 9, 4, tzinfo=IST), [1500.0] * 20)
        assert [t for t in runner._stats.trades if t.strategy == "snapback"] == []

    def test_the_fill_is_the_NEXT_sessions_open(self, runner):
        self._play(runner, datetime(2026, 9, 4, tzinfo=IST), [1500.0] * 20)
        open_px = 1477.0
        self._play(runner, datetime(2026, 9, 7, tzinfo=IST), [open_px, 1480.0, 1470.0])
        trades = [t for t in runner._stats.trades if t.strategy == "snapback"]
        assert len(trades) == 1, [t.strategy for t in runner._stats.trades]
        t = trades[0]
        # The first bar of a session opens AT the previous close in this fixture,
        # so pin the thing that matters: the entry is the session's own open and
        # not the signal bar's close.
        assert t.spot_entry == pytest.approx(open_px, abs=0.01)
        assert t.entry_time_iso.startswith("09:15") or "09:15" in t.entry_time_iso
        assert t.opt_type == "PE"

    def test_the_position_carries_snapbacks_own_horizon_and_no_trail(self, runner):
        self._play(runner, datetime(2026, 9, 4, tzinfo=IST), [1500.0] * 20)
        self._play(runner, datetime(2026, 9, 7, tzinfo=IST), [1477.0, 1480.0])
        t = [x for x in runner._stats.trades if x.strategy == "snapback"][0]
        assert t.max_hold_bars == 15 * 75
        assert t.trails is False

    def test_the_leg_is_snapbacks_own_and_not_the_at_the_money_guess(self, runner):
        self._play(runner, datetime(2026, 9, 4, tzinfo=IST), [1500.0] * 20)
        self._play(runner, datetime(2026, 9, 7, tzinfo=IST), [1477.0, 1480.0])
        t = [x for x in runner._stats.trades if x.strategy == "snapback"][0]
        # In the money: a 0.70-delta put strikes ABOVE spot.
        assert t.strike > t.spot_entry
        assert t.entry_price > t.spot_entry * 0.02

    def test_a_setup_that_stops_qualifying_before_the_close_does_not_fill(self, runner):
        """The watch is recorded on EVERY bar, firing or not. Keeping only the
        firing ones would let a setup that died at 15:05 fill the next morning."""
        self._play(runner, datetime(2026, 9, 4, tzinfo=IST),
                   [1500.0] * 6 + [1000.0] * 6)
        self._play(runner, datetime(2026, 9, 7, tzinfo=IST), [1000.0, 1005.0])
        assert [t for t in runner._stats.trades if t.strategy == "snapback"] == []


class TestItPlaysAtSpeed:
    """A correct adapter that hangs the dock is not a shipped feature.

    Two costs are quadratic in the length of the replay and invisible on one
    session: re-querying five hundred daily rows per symbol PER BAR, and
    re-deriving today's bars by scanning the whole candle list each time.
    """

    def test_the_completed_daily_tape_is_read_once_per_session(self, monkeypatch):
        calls: list[tuple] = []
        rows = _daily(200, start=1000.0, drift=1.0015)

        def _store(symbol, asof_ts, days=400):
            calls.append((symbol, asof_ts))
            return [dict(b) for b in rows if b["time"] <= float(asof_ts)]

        monkeypatch.setattr(sim, "_store_daily_sessions", _store)
        r = sim.SimulationRunner()
        day = datetime(2026, 9, 4, tzinfo=IST)
        bars = _session_bars(day, [1500.0] * 30, "RELIANCE")
        for b in bars:
            sim._snapback_daily_tape("RELIANCE", [b], b["time"], r)
        assert len(calls) == 1, f"{len(calls)} store reads for one session"

    def test_a_second_session_reads_again(self, monkeypatch):
        calls: list[tuple] = []
        monkeypatch.setattr(sim, "_store_daily_sessions",
                            lambda symbol, asof_ts, days=400: calls.append(symbol) or [])
        r = sim.SimulationRunner()
        for d in (datetime(2026, 9, 4, tzinfo=IST), datetime(2026, 9, 7, tzinfo=IST)):
            for b in _session_bars(d, [1500.0] * 4, "RELIANCE"):
                sim._snapback_daily_tape("RELIANCE", [b], b["time"], r)
        assert len(calls) == 2

    def test_the_session_buffer_resets_on_a_new_day(self):
        """A buffer that never resets turns yesterday's range into today's."""
        r = sim.SimulationRunner()
        r._config = sim.SimConfig(date="2026-09-04", strategies=["snapback"])
        r._stats = sim.SimStats()
        r._open_by_symbol = {}
        r._candles = []
        r._in_session_bars = {}
        from app.engines.snapback import SnapbackConfig
        import app.services.simulation as m
        original = m._snapback_config
        m._snapback_config = lambda: SnapbackConfig(max_rv_pct=100.0, enabled=True)
        try:
            for d, px in ((datetime(2026, 9, 4, tzinfo=IST), 100.0),
                          (datetime(2026, 9, 7, tzinfo=IST), 200.0)):
                for b in _session_bars(d, [px] * 3, "RELIANCE"):
                    r._evaluate_bar(dict(b), datetime.fromtimestamp(b["time"], IST))
        finally:
            m._snapback_config = original
        held = r._session_bars["RELIANCE"]
        assert len(held) == 3
        assert all(b["close"] == pytest.approx(200.0) for b in held)


class TestTheRunnerReachesTheReplay:
    """The horizon is not the end of the trade when the position is a winner.

    The backtest runs a runner and the replay did not, so the dock reported a
    rule's best trades as MAX_HOLD at the bar count — the exact truncation the
    runner exists to prevent, and worth +0.57pp per entry day out of sample.
    """

    def _trade(self, **kw):
        base = dict(
            trade_id="TRD-1", strategy="snapback", symbol="X26SEP1500PE",
            underlying="RELIANCE", direction="BUY", opt_type="PE", strike=1500.0,
            lots=1, quantity=500, entry_price=100.0, stop_loss=65.0,
            target_price=300.0, status="OPEN", spot_entry=1400.0,
            spot_stop=1500.0, spot_target=1000.0, spot_hwm=1400.0,
            spot_initial_risk=100.0, spot_initial_stop=1500.0, raw_entry=100.0,
            leg_delta=0.7, bars_held=20, max_hold_bars=20, trails=False,
        )
        base.update(kw)
        return sim.SimTradeEvent(**base)

    def _settle(self, trade, close):
        r = sim.SimulationRunner()
        r._config = sim.SimConfig(date="2026-09-04", friction_mode="ideal")
        r._stats = sim.SimStats(trades=[trade])
        r._open_by_symbol = {"RELIANCE": [trade]}
        r._settle_open_positions(
            {"symbol": "RELIANCE", "open": close, "high": close + 1,
             "low": close - 1, "close": close, "time": 0},
            datetime(2026, 9, 4, 11, 0, tzinfo=IST))
        return trade

    def test_without_a_runner_the_horizon_closes_a_winner(self):
        t = self._settle(self._trade(runner_mult=0.0), 1200.0)
        assert t.status != "OPEN"
        assert t.exit_reason == "MAX_HOLD"

    def test_a_position_past_the_multiple_keeps_running(self):
        # Spot far below a 1500 put: worth several times what it cost.
        t = self._settle(self._trade(runner_mult=1.5), 1200.0)
        assert t.status == "OPEN"
        assert t.exit_reason is None
        assert t.runner_peak is not None and t.runner_peak > t.entry_price

    def test_a_position_below_the_multiple_still_closes_on_time(self):
        """The rule must not become 'hold everything longer'."""
        t = self._settle(self._trade(runner_mult=1.5), 1399.0)
        assert t.status != "OPEN"
        assert t.exit_reason == "MAX_HOLD"

    def test_a_runner_that_gives_its_gain_back_is_closed_as_a_runner(self):
        t = self._trade(runner_mult=1.5, runner_trail_pct=20.0)
        self._settle(t, 1200.0)
        assert t.status == "OPEN"
        peak = t.runner_peak
        self._settle(t, 1385.0)
        assert t.status != "OPEN"
        assert t.exit_reason == "RUNNER"
        assert peak is not None

    def test_the_replay_and_the_engine_read_the_SAME_runner_settings(self):
        from app.engines.snapback import SnapbackConfig
        c = SnapbackConfig()
        t = sim.SimTradeEvent(
            trade_id="T", strategy="snapback", symbol="X", underlying="Y",
            direction="BUY", opt_type="PE", strike=1.0, lots=1, quantity=1,
            entry_price=1.0, stop_loss=1.0, target_price=1.0,
            runner_mult=c.runner_mult, runner_trail_pct=c.runner_trail_pct)
        assert (t.runner_mult, t.runner_trail_pct) == (c.runner_mult,
                                                       c.runner_trail_pct)
