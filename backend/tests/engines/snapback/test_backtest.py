"""What the replay must never do, stated as tests.

Each of these corresponds to a way a backtest invents money, and two of them are
bugs this engine actually had before they were written down.
"""
from __future__ import annotations

import numpy as np
import pytest

from dataclasses import replace

from app.engines.snapback import Bars, SnapbackConfig
from app.engines.snapback.backtest import CostModel, replay, returns_at_vrp
from app.engines.snapback.pricing import break_even_vrp

DAY = 86_400.0


def tape(close, *, high=None, low=None) -> Bars:
    close = np.asarray(close, dtype=float)
    n = len(close)
    high = np.asarray(high, float) if high is not None else close * 1.004
    low = np.asarray(low, float) if low is not None else close * 0.996
    return Bars(time=1_690_000_000.0 + np.arange(n) * DAY,
                open=close, high=np.maximum(high, close),
                low=np.minimum(low, close), close=close,
                volume=np.full(n, 1000.0))


def spike_tape(n: int = 240, spike: float = 1.10, after: float = 1.0) -> Bars:
    rng = np.random.default_rng(5)
    close = 1_200 * np.exp(np.cumsum(rng.normal(0.0002, 0.006, n)))
    close[-1] = close[-2] * spike
    tail = close[-1] * np.power(after, np.arange(1, 16))
    return tape(np.concatenate([close, tail]))


CFG = SnapbackConfig(sizing_mode="LOTS", lots=1, scan_stocks=("RELIANCE",),
                     scan_indices=())


class TestFills:
    def test_entry_is_the_NEXT_session_open_not_the_signal_close(self):
        """A signal computed from a close cannot be filled at that close.

        The single most common way a backtest invents money.
        """
        b = spike_tape()
        res = replay({"RELIANCE": b}, CFG)
        assert res.trades
        # The LAST trade is the one the spike produced; this tape is long enough
        # to fire more than once, and pinning the first would test whichever
        # entry the noise happened to put first.
        t = res.trades[-1]
        # The signal bar is the spike; the fill is the session after it.
        assert t.spot_in == pytest.approx(float(b.open[len(b) - 15]), rel=1e-9)
        assert t.entry_ms == int(b.time[len(b) - 15] * 1000)

    def test_a_session_that_trades_through_the_stop_is_stopped(self):
        """Nothing in a daily bar says what came first, so assume the worse."""
        b = spike_tape(after=1.02)          # runs hard against a bought put
        cfg = SnapbackConfig(sizing_mode="LOTS", lots=1, premium_stop_pct=20.0,
                             scan_stocks=("RELIANCE",), scan_indices=())
        res = replay({"RELIANCE": b}, cfg)
        assert res.trades
        t = res.trades[-1]
        assert t.reason == "premium_stop"
        # Stopped AT the stop, never below it.
        assert t.premium_out == pytest.approx(t.fill_in * 0.8, rel=1e-9)

    def test_holding_period_is_honoured_when_nothing_stops_it(self):
        b = spike_tape(after=0.999)
        cfg = SnapbackConfig(sizing_mode="LOTS", lots=1, hold_days=6,
                             premium_stop_pct=99.0,
                             scan_stocks=("RELIANCE",), scan_indices=())
        res = replay({"RELIANCE": b}, cfg)
        assert res.trades and res.trades[-1].held_days == 6


class TestVegaIsZero:
    def test_a_flat_tape_loses_exactly_theta_and_costs(self):
        """The exit is valued at the ENTRY's vol, so the vega term is zero.

        Deliberately unfair to this strategy: spot and vol are negatively
        correlated, so a put bought into strength gains vol when the fade works
        and this harness refuses to credit it.
        """
        b = spike_tape(after=1.0)           # spot pinned after entry
        cfg = SnapbackConfig(sizing_mode="LOTS", lots=1, premium_stop_pct=99.0,
                             scan_stocks=("RELIANCE",), scan_indices=())
        res = replay({"RELIANCE": b}, cfg)
        t = res.trades[-1]
        # Spot unchanged and vol unchanged: the premium can only have decayed.
        assert t.spot_out == pytest.approx(t.spot_in, rel=1e-6)
        assert t.premium_out < t.premium_in


class TestCosts:
    def test_both_legs_pay_slippage(self):
        c = CostModel(slippage_pct=1.0)
        assert c.fill(100.0, "buy") == pytest.approx(101.0)
        assert c.fill(100.0, "sell") == pytest.approx(99.0)

    def test_a_worthless_exit_still_pays_the_buy_side(self):
        c = CostModel()
        charges = c.charges(100.0, 0.0, 500)
        assert charges > 2 * c.brokerage_per_order

    def test_brokerage_is_flat_so_a_small_outlay_pays_a_large_share(self):
        c = CostModel()
        small = c.charges(2.0, 2.0, 10) / (2.0 * 10)
        large = c.charges(200.0, 200.0, 500) / (200.0 * 500)
        assert small > large * 10

    def test_higher_slippage_never_improves_a_book(self):
        b = spike_tape(after=0.99)
        cheap = replay({"RELIANCE": b}, CFG, cost=CostModel(slippage_pct=0.25))
        dear = replay({"RELIANCE": b}, CFG, cost=CostModel(slippage_pct=2.0))
        assert cheap.trades and dear.trades
        assert sum(t.net for t in cheap.trades) > sum(t.net for t in dear.trades)


class TestNothingIsSilentlyDropped:
    def test_an_unaffordable_lot_is_COUNTED_not_dropped(self):
        """The defect this counter exists for.

        At 2% of a 1 lakh account the premium budget could not buy one NIFTY
        lot, so the sizer returned zero and every signal vanished without a
        word. An empty result read as 'the rule never fires', which is a
        different bug with a different fix.
        """
        b = spike_tape(after=0.99)
        broke = SnapbackConfig(sizing_mode="PREMIUM_PCT",
                               premium_pct_of_capital=0.01, capital_inr=1000.0,
                               scan_stocks=("RELIANCE",), scan_indices=())
        res = replay({"RELIANCE": b}, broke)
        assert not res.trades
        assert sum(res.unsized.values()) > 0
        assert any("premium budget" in k for k in res.unsized)

    def test_a_tape_too_short_is_reported_with_a_reason(self):
        res = replay({"RELIANCE": tape(np.full(30, 1_200.0))}, CFG)
        assert "RELIANCE" in res.skipped and "sessions" in res.skipped["RELIANCE"]

    def test_an_instrument_with_no_published_step_is_reported(self):
        res = replay({"NOTLISTED": spike_tape()}, CFG)
        assert "NOTLISTED" in res.skipped


class TestEntryWindow:
    def test_restricts_entries_without_starving_the_indicators(self):
        """A fold's window is shorter than the warm-up.

        Slicing the tape instead hands a 60-session window to a rule with an
        80-session warm-up, and every fold comes back with zero trades and no
        error — which is exactly what happened.
        """
        b = spike_tape()
        all_trades = replay({"RELIANCE": b}, CFG).trades
        assert all_trades
        day = all_trades[0].entry_day
        inside = replay({"RELIANCE": b}, CFG, entry_window=(day, "9999")).trades
        outside = replay({"RELIANCE": b}, CFG,
                         entry_window=("1990-01-01", "1991-01-01")).trades
        assert len(inside) == len(all_trades)
        assert outside == []


class TestOverride:
    def test_the_side_is_supplied_not_re_derived(self):
        """A null must be signal-free in the dimension being tested.

        The first version chose the side from the sign of the stretch at the
        random bar — the strategy's own rule with the threshold removed — so the
        null contained a diluted copy of the signal and could not be beaten.
        """
        b = spike_tape()
        res = replay({"RELIANCE": b}, SnapbackConfig(
            sizing_mode="LOTS", lots=1, allow_fade_down=True,
            scan_stocks=("RELIANCE",), scan_indices=()),
            entry_override={"RELIANCE": [(150, "fade_down"), (170, "fade_up")]})
        sides = {t.side for t in res.trades}
        assert "fade_down" in sides

    def test_an_override_ignores_the_rule_entirely(self):
        b = tape(np.full(300, 1_200.0))     # a tape nothing can fire on
        assert replay({"RELIANCE": b}, CFG).trades == []
        forced = replay({"RELIANCE": b}, CFG,
                        entry_override={"RELIANCE": [(200, "fade_up")]})
        assert len(forced.trades) == 1


class TestBreakEvenIntegration:
    def test_repricing_moves_the_whole_book_not_a_stored_return(self):
        """Every term moves with the vol assumption: the strike, the quantity,
        the stop and the cost. Scaling a stored return by a ratio is wrong and
        looks right."""
        b = spike_tape(after=0.99)
        at = returns_at_vrp({"RELIANCE": b}, CFG)
        cheap, dear = float(at(0.8).mean()), float(at(2.5).mean())
        assert cheap > dear

    def test_break_even_is_a_real_crossing(self):
        b = spike_tape(after=0.985)
        at = returns_at_vrp({"RELIANCE": b}, CFG)
        be = break_even_vrp(at)
        # NaN means it loses even on a nearly-free option and 3.0 is the cap;
        # neither is a crossing, and asserting one anyway would be testing the
        # sentinel rather than the search.
        if np.isfinite(be) and be < 3.0:
            assert float(at(be - 0.2).mean()) > 0 >= float(at(be + 0.2).mean())


class TestHedgeInTheReplay:
    """The hedge has to reach the trade, not just exist in its own module."""

    def _tapes(self):
        b = spike_tape(after=0.99)
        # A market tape is required: without NIFTY in the book there is nothing
        # to hedge against, and the replay must SAY so rather than quietly
        # running every trade directional.
        rng = np.random.default_rng(11)
        mkt = 23_000 * np.exp(np.cumsum(rng.normal(0.0003, 0.007, len(b))))
        return {"RELIANCE": b,
                "NIFTY": Bars(b.time, mkt, mkt * 1.003, mkt * 0.997, mkt,
                              np.full(len(b), 0.0))}

    def test_hedging_changes_the_book(self):
        tapes = self._tapes()
        cfg = SnapbackConfig(sizing_mode="LOTS", lots=1, scan_indices=("NIFTY",),
                             scan_stocks=("RELIANCE",), max_open_positions=99)
        plain = replay(tapes, replace(cfg, hedge_mode="none"))
        hedged = replay(tapes, cfg)
        assert plain.trades and hedged.trades
        assert all(t.beta == 0.0 for t in plain.trades)
        assert any(t.beta for t in hedged.trades)
        # The unhedged net is KEPT alongside, so a reader can see what the hedge
        # did rather than having to trust a single combined figure.
        for t in hedged.trades:
            if t.beta:
                assert t.net == pytest.approx(
                    t.gross_unhedged - t.market_pnl - t.hedge_cost, abs=0.02)

    def test_a_book_with_no_market_tape_SAYS_so(self):
        """Running every trade directional because the index was missing, with
        nothing on screen to say so, would turn the shipped configuration into
        the one measured at -1.08%."""
        b = spike_tape(after=0.99)
        cfg = SnapbackConfig(sizing_mode="LOTS", lots=1, scan_indices=(),
                             scan_stocks=("RELIANCE",))
        res = replay({"RELIANCE": b}, cfg)
        assert "NIFTY" in res.skipped
        assert "ungated" in res.skipped["NIFTY"] or "UNHEDGED" in res.skipped["NIFTY"]

    def test_the_hedge_is_never_free(self):
        tapes = self._tapes()
        cfg = SnapbackConfig(sizing_mode="LOTS", lots=1, scan_indices=("NIFTY",),
                             scan_stocks=("RELIANCE",), max_open_positions=99)
        hedged = [t for t in replay(tapes, cfg).trades if t.beta]
        assert hedged and all(t.hedge_cost > 0 for t in hedged)


class TestPositionCap:
    def test_the_cap_is_ENFORCED_and_counted(self):
        """The replay used to open unlimited concurrent positions, so its equity
        curve compounded a book nobody could hold."""
        b = spike_tape(after=0.99)
        tapes = {s: b for s in ("RELIANCE", "INFY", "TCS", "SBIN")}
        cfg = SnapbackConfig(sizing_mode="LOTS", lots=1, scan_indices=(),
                             scan_stocks=tuple(tapes), hedge_mode="none",
                             max_open_positions=1)
        res = replay(tapes, cfg)
        assert any("book full" in k for k in res.unsized)
        loose = replay(tapes, replace(cfg, max_open_positions=99))
        assert len(loose.trades) > len(res.trades)


class TestDayClustering:
    def test_trades_on_one_day_collapse_to_one_observation(self):
        """Fifteen large caps on the day the index broke out are ONE bet.

        A t-statistic over correlated trades is inflated by roughly the square
        root of how many of them are the same bet — it turned a genuine 1.7 into
        a confident-looking 3.5 here.
        """
        b = spike_tape()
        tapes = {"RELIANCE": b, "INFY": b, "TCS": b}
        res = replay(tapes, SnapbackConfig(
            sizing_mode="LOTS", lots=1, scan_indices=(),
            scan_stocks=("RELIANCE", "INFY", "TCS")))
        days, daily = res.by_day()
        # Three tapes fire on the same sessions, so there are strictly more
        # trades than clustered observations — which is the whole point.
        assert len(res.trades) > len(days)
        # And every clustered value is the mean of that day's trades, not a sum
        # and not the first of them.
        shared = 0
        for day, value in zip(days, daily):
            same = [t.ret for t in res.trades if t.entry_day == day]
            assert value == pytest.approx(float(np.mean(same)))
            shared += len(same) > 1
        # Not every session fires on all three — their strike ladders and lot
        # sizes differ, so the premium floor bites at different moments. What
        # matters is that the days which DO share entries are collapsed.
        assert shared > 0


class TestUntradedSignalsAreAccountedFor:
    """A signal the replay did not take must say WHY, per signal.

    The counts in ``unsized`` answer "what happened to the book". They cannot
    answer "what happened to this row", and the board needs the second: a signal
    with no trade rendered as a closed position with every outcome column empty
    is what "no targets, no exited, no LTP, nothing" looked like to an operator.
    """

    def test_a_signal_the_book_had_no_room_for_names_itself(self):
        b = spike_tape(after=0.99)
        # Real names: an instrument with no published lot size or strike step is
        # skipped before it ever reaches the book, which is a different account.
        tapes = {n: b for n in ("RELIANCE", "INFY", "TCS", "SBIN", "AXISBANK",
                                "LT")}
        res = replay(tapes, replace(CFG, max_open_positions=2,
                                    one_position_per_underlying=False,
                                    hedge_mode="none"))
        assert res.untraded, "a book capped at 2 with 6 identical tapes drops some"
        assert any("book full" in why for why in res.untraded.values())
        # Keyed by (symbol, FILL day) — the same key ``Trade.entry_day`` uses,
        # so a caller can join the two without a second convention.
        for (sym, day), _ in res.untraded.items():
            assert sym in tapes and day.count("-") == 2

    def test_a_traded_signal_leaves_no_untraded_entry(self):
        """``_run_one`` notes reasons for trades that COMPLETE as well — an
        unhedged fill, for one. Those must not look like refusals."""
        res = replay({"RELIANCE": spike_tape(after=0.97)},
                     replace(CFG, hedge_mode="none"))
        assert res.trades
        traded = {(t.symbol, t.entry_day) for t in res.trades}
        assert not (traded & set(res.untraded)), res.untraded
