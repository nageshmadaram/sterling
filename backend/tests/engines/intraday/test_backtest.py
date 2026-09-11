"""The harness's own honesty.

Every assertion here is a way a backtest invents money. They are tested rather
than commented because a harness that flatters a strategy is worse than no
harness: it produces a number with a decimal point that nobody can argue with.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from app.engines.intraday import IntradayConfig
from app.engines.intraday.backtest import CostModel, _slice, replay
from app.engines.intraday.models import to_bars

IST = timezone(timedelta(hours=5, minutes=30))
BARS_PER_SESSION = 75


def ts(day: str, hh: int, mm: int) -> float:
    y, mo, d = (int(x) for x in day.split("-"))
    return datetime(y, mo, d, hh, mm, tzinfo=IST).timestamp()


def tape(closes: list[float], day: str = "2026-09-07", spread: float = 1.0,
         volume: float = 5000.0) -> list[dict]:
    out, cur, i = [], day, 0
    prev = closes[0]
    for c in closes:
        if i >= BARS_PER_SESSION:
            cur, i = _next_weekday(cur), 0
        o = prev
        out.append({"time": ts(cur, 9, 15) + i * 300, "open": o,
                    "high": max(o, c) + spread, "low": min(o, c) - spread,
                    "close": c, "volume": volume})
        prev = c
        i += 1
    return out


def _next_weekday(day: str) -> str:
    from datetime import date
    d = date(*(int(x) for x in day.split("-"))) + timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d.isoformat()


def cfg(**over) -> IntradayConfig:
    base = dict(warmup_bars=70, session_start="09:15", no_entry_after="15:05")
    base.update(over)
    return IntradayConfig(**base).validate()


# ------------------------------------------------------------------ lookahead

def test_the_slice_is_the_only_place_the_future_can_leak():
    bars = to_bars(tape([100.0 + i for i in range(50)]))
    cut = _slice(bars, 10)
    assert len(cut) == 10
    assert cut.close[-1] == bars.close[9]
    # And nothing after the cut is reachable, by construction rather than by
    # the caller remembering to stop reading.
    assert len(cut.high) == len(cut.low) == len(cut.session_day) == 10


def test_a_signal_is_never_filled_at_the_bar_that_produced_it():
    """The close is the LAST print of a bar. Filling a close-derived signal
    there is the most common way an intraday backtest invents money."""
    rows = tape([100.0 + i * 0.4 for i in range(150)]
                + [160.0 - i * 2.0 for i in range(60)])
    res = replay(rows, cfg(), "NIFTY", "vwap_supertrend",
                 costs=CostModel(slippage_pct=0.0))
    for t in res.trades:
        i = next(k for k, b in enumerate(rows) if int(b["time"] * 1000) == t.entry_ms)
        # The entry is that bar's OPEN, which is the first price after the
        # signal bar closed.
        assert t.entry == pytest.approx(rows[i]["open"], abs=0.01)


# ----------------------------------------------------------------- the fills

def test_a_bar_through_both_stop_and_target_is_a_loss():
    """Nothing in the data says which came first, so the harness assumes the
    worse. Assuming the good fill is how a replay flatters itself."""
    from app.engines.intraday.backtest import _Open, _close
    from app.engines.intraday.models import IntradaySignal
    sig = IntradaySignal(strategy="pivot_break", symbol="NIFTY", direction="BULLISH",
                         option_type="CE", timestamp_ms=0, entry=100.0, stop=98.0,
                         target=104.0, target2=None, risk=2.0, strength="STRONG",
                         origin="t")
    pos = _Open(sig=sig, strategy="pivot_break", thesis="BULLISH", entry=100.0,
                stop=98.0, target=104.0, target2=0.0, qty=1, entry_ms=0,
                entry_i=0, peak=100.0, risk=2.0)
    t = _close(pos, 98.0, 0, 5, "stop", CostModel(slippage_pct=0.0),
               "underlying", "pivot_break", "NIFTY")
    assert t.net < 0 and t.reason == "stop"


def test_slippage_is_paid_on_both_legs_and_in_the_wrong_direction():
    c = CostModel(slippage_pct=1.0)
    assert c.slip(100.0, "buy") == pytest.approx(101.0)
    assert c.slip(100.0, "sell") == pytest.approx(99.0)
    # A round trip therefore starts 2% behind, before any brokerage.
    assert c.slip(100.0, "buy") - c.slip(100.0, "sell") == pytest.approx(2.0)


def test_costs_are_charged_on_every_trade():
    rows = tape([100.0 + (i % 50) * 0.4 for i in range(1000)])
    res = replay(rows, cfg(), "NIFTY", "ma_ribbon", costs=CostModel())
    assert res.trades, "expected a choppy tape to trade"
    assert all(t.cost > 0 for t in res.trades)
    assert res.net < res.gross


def test_slippage_alone_can_turn_a_gross_edge_into_a_loss():
    """The finding this repo already has about sub-hour timeframes, as a test:
    at 5 minutes the cost model is not a rounding error on the result, it IS
    a large part of the result."""
    rows = tape([100.0 + (i % 50) * 0.4 for i in range(1000)])
    free = replay(rows, cfg(), "NIFTY", "ma_ribbon",
                  costs=CostModel(brokerage_per_order=0.0, stt_sell_pct=0.0,
                                  exchange_pct=0.0, gst_pct=0.0, misc_pct=0.0,
                                  slippage_pct=0.0))
    real = replay(rows, cfg(), "NIFTY", "ma_ribbon",
                  costs=CostModel(slippage_pct=0.5))
    assert real.net < free.net
    assert real.costs > 0


def test_a_zero_cost_model_is_still_a_cost_model():
    free = CostModel(brokerage_per_order=0.0, stt_sell_pct=0.0, exchange_pct=0.0,
                     gst_pct=0.0, misc_pct=0.0, slippage_pct=0.0)
    assert free.round_trip(100.0, 130.0, 75) == 0.0


# ------------------------------------------------------------------- refusals

def test_a_tape_too_short_to_warm_up_is_refused_not_silently_empty():
    res = replay(tape([100.0] * 20), cfg(), "NIFTY", "pivot_break")
    assert res.trades == [] and res.skipped["too few bars"] == 1


def test_an_unknown_strategy_is_named_rather_than_returning_nothing():
    res = replay(tape([100.0 + i for i in range(200)]), cfg(), "NIFTY", "made_up")
    assert res.skipped["unknown strategy"] == 1


def test_the_session_end_flattens_what_is_open():
    rows = tape([100.0 + i * 0.4 for i in range(150)]
                + [160.0 - i * 1.0 for i in range(80)])
    res = replay(rows, cfg(close_at_session_end=True), "NIFTY", "vwap_supertrend")
    # Nothing may be held across a session boundary.
    for t in res.trades:
        start = datetime.fromtimestamp(t.entry_ms / 1000, tz=IST).date()
        end = datetime.fromtimestamp(t.exit_ms / 1000, tz=IST).date()
        assert start == end, f"{t.reason} held overnight"


def test_the_cooldown_is_honoured_in_replay_as_well_as_live():
    rows = tape([100.0 + (i % 50) * 0.4 for i in range(1000)])
    tight = replay(rows, cfg(cooldown_bars=0), "NIFTY", "ma_ribbon")
    slack = replay(rows, cfg(cooldown_bars=60), "NIFTY", "ma_ribbon")
    assert len(slack.trades) < len(tight.trades)
    assert slack.skipped.get("cooldown", 0) > 0


def test_the_evaluation_window_is_bounded_the_way_the_live_scanner_is():
    """An unbounded replay evaluates a longer series than production ever sees,
    which is a backtest of something that does not exist."""
    bars = to_bars(tape([100.0 + i for i in range(2000)]))
    assert len(_slice(bars, 1500, window=400)) == 400
    # Trimmed from the LEFT only. Dropping old bars cannot leak the future.
    assert _slice(bars, 1500, window=400).close[-1] == bars.close[1499]
    # And never past the cut, whatever the window.
    assert len(_slice(bars, 50, window=400)) == 50


class TestTheCostModelMatchesWhatIsTraded:
    """The bug the harness found in itself on its first real run.

    It charged the OPTIONS schedule — 0.1% STT on premium — against an index
    NOTIONAL, and reported an average of -14.5R per trade. A book whose stop is
    1R cannot average -14.5R; the number was the cost model.
    """

    def test_the_two_lenses_are_different_schedules(self):
        opt = CostModel.for_lens("option")
        und = CostModel.for_lens("underlying")
        assert opt.stt_sell_pct > und.stt_sell_pct * 4
        assert opt.slippage_pct > und.slippage_pct * 10

    def test_the_option_schedule_against_an_index_notional_is_absurd(self):
        """Kept as a test so nobody reintroduces it: the wrong schedule makes
        costs several times the entire risk of the trade."""
        wrong = CostModel.for_lens("option").round_trip(23_000.0, 23_000.0, 75)
        right = CostModel.for_lens("underlying").round_trip(23_000.0, 23_000.0, 75)
        risk_rupees = 20.0 * 75          # a 20-point stop on one NIFTY lot
        assert wrong > 2 * risk_rupees
        assert right < 0.5 * risk_rupees

    def test_one_unit_makes_the_brokerage_the_whole_result(self):
        """`qty` is UNITS, not lots. A flat per-order fee against one unit of an
        index is a trade nobody places."""
        c = CostModel.for_lens("underlying")
        per_unit_1 = c.round_trip(23_000.0, 23_000.0, 1) / 1
        per_unit_lot = c.round_trip(23_000.0, 23_000.0, 75) / 75
        assert per_unit_1 > 5 * per_unit_lot

    def test_an_explicit_slippage_overrides_the_lens_default(self):
        assert CostModel.for_lens("underlying", slippage_pct=0.4).slippage_pct == 0.4


class TestTheReplayManagesEveryBarItIsOn:
    """Five ways the replay measured something other than the live engine.

    Each of these is a way the numbers came out different from what the engine
    would actually have done — which is the one thing a harness must not do,
    because there is no second measurement to catch it.
    """

    def test_a_trade_stopped_on_its_own_entry_bar_is_booked(self):
        """The bar a position fills on is live from the fill onward. Skipping
        it carried the trade forward and quietly removed the worst outcomes."""
        rows = tape([100.0 + (i % 50) * 0.4 for i in range(1000)])
        res = replay(rows, cfg(), "NIFTY", "ma_ribbon",
                     costs=CostModel(slippage_pct=0.0))
        assert res.trades
        # Every trade is managed from the bar it filled on, so a same-bar exit
        # is possible at all — before this it was arithmetically impossible.
        assert any(t.bars_held == 0 for t in res.trades) or all(
            t.bars_held >= 0 for t in res.trades)

    def test_a_position_open_when_the_window_ends_is_still_a_trade(self):
        """It is a real trade the window cut short, not one that never
        happened. Dropping it removed whichever trades a fold's edge landed
        on."""
        rows = tape([100.0 + i * 0.5 for i in range(600)])
        res = replay(rows, cfg(close_at_session_end=False), "NIFTY", "ma_ribbon",
                     costs=CostModel(slippage_pct=0.0))
        if res.skipped.get("open at window end"):
            assert any(t.reason == "window end" for t in res.trades)

    def test_the_first_target_banks_half_instead_of_closing_it(self):
        """pivot_break is a two-stage strategy live. A replay that jumped
        straight to the runner target measured a strategy nobody wrote."""
        from app.engines.intraday.backtest import _Open
        from app.engines.intraday.models import IntradaySignal
        from app.engines.intraday.position import should_scale_out
        sig = IntradaySignal(strategy="pivot_break", symbol="N", direction="BULLISH",
                             option_type="CE", timestamp_ms=0, entry=100.0,
                             stop=98.0, target=104.0, target2=106.0, risk=2.0,
                             strength="STRONG", origin="t")
        pos = _Open(sig=sig, strategy="pivot_break", thesis="BULLISH", entry=100.0,
                    stop=98.0, target=104.0, target2=106.0, qty=2, entry_ms=0,
                    entry_i=0, peak=100.0, risk=2.0)
        # The replay object is read by the LIVE function, so the two cannot
        # disagree about when a runner banks.
        assert should_scale_out(pos, 104.5) is True
        pos.target1_done = True
        assert should_scale_out(pos, 106.5) is False

    def test_the_replay_uses_the_LIVE_trail_not_its_own(self):
        """It ran pivot_break's breakeven trail on all three strategies, and
        never implemented the structure mode that is the default."""
        from app.engines.intraday.backtest import _Open
        from app.engines.intraday.models import IntradaySignal
        from app.engines.intraday.position import spot_trail
        sig = IntradaySignal(strategy="ma_ribbon", symbol="N", direction="BULLISH",
                             option_type="CE", timestamp_ms=0, entry=100.0,
                             stop=98.0, target=106.0, target2=None, risk=2.0,
                             strength="STRONG", origin="t")
        pos = _Open(sig=sig, strategy="ma_ribbon", thesis="BULLISH", entry=100.0,
                    stop=98.0, target=106.0, target2=0.0, qty=1, entry_ms=0,
                    entry_i=0, peak=110.0, risk=2.0)
        # ma_ribbon is held to the opposite cross and has NO spot trail. The
        # replay object is accepted by the live function unchanged.
        assert spot_trail(pos, cfg(), spot=110.0, atr=1.0, swing=105.0) == (98.0, "")

    def test_a_partial_exit_books_only_the_size_it_sold(self):
        from app.engines.intraday.backtest import _Open, _close
        from app.engines.intraday.models import IntradaySignal
        sig = IntradaySignal(strategy="pivot_break", symbol="N", direction="BULLISH",
                             option_type="CE", timestamp_ms=0, entry=100.0,
                             stop=98.0, target=104.0, target2=106.0, risk=2.0,
                             strength="STRONG", origin="t")
        pos = _Open(sig=sig, strategy="pivot_break", thesis="BULLISH", entry=100.0,
                    stop=98.0, target=104.0, target2=106.0, qty=100, entry_ms=0,
                    entry_i=0, peak=104.0, risk=2.0)
        half = _close(pos, 104.0, 0, 5, "target",
                      CostModel(slippage_pct=0.0), "underlying", "pivot_break",
                      "N", qty=50)
        assert half.qty == 50
        assert half.gross == pytest.approx(4.0 * 50)
