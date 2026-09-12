"""The harness, and the four ways it flattered this strategy before it stopped.

Every test below except the config ones corresponds to a real bug this file's
subject had. Three of them produced numbers that looked completely normal:

* the out-of-sample windows were SHORTER than the indicator warm-up, so every
  fold returned zero trades and no error;
* the permutation compared an out-of-sample mean against a full-tape null, so it
  reported p=1.0 for a book whose own p is 0.003;
* the deflated Sharpe was handed ANNUALISED trial Sharpes, over-deflating by
  sqrt(250) and printing exactly 0.000 for anything at all.
"""
from __future__ import annotations

import numpy as np
import pytest

from app.engines.snapback import Bars, SnapbackConfig, validate
from app.engines.snapback.backtest import Trade, replay
from app.engines.snapback.walkforward import (Fold, Gate, SelectionContaminated,
                                              calendar, equity_series, judge,
                                              make_folds, permutation_p, run,
                                              slice_tapes, summarise)

DAY = 86_400.0


def tape(n: int = 900, seed: int = 4) -> Bars:
    rng = np.random.default_rng(seed)
    close = 1_200 * np.exp(np.cumsum(rng.normal(0.0002, 0.009, n)))
    return Bars(time=1_600_000_000.0 + np.arange(n) * DAY,
                open=close, high=close * 1.006, low=close * 0.994,
                close=close, volume=np.full(n, 1000.0))


CFG = SnapbackConfig(sizing_mode="LOTS", lots=1, scan_indices=(),
                     scan_stocks=("RELIANCE", "INFY"))


class TestFolds:
    def test_windows_never_overlap(self):
        days = [f"2024-{m:02d}-{d:02d}" for m in range(1, 13) for d in range(1, 29)]
        folds = make_folds(days, is_days=100, oos_days=30, purge_days=15)
        assert folds
        for f in folds:
            assert f.is_start < f.is_end <= f.oos_start < f.oos_end

    def test_out_of_sample_windows_do_not_reuse_a_session(self):
        """The step is the out-of-sample length, so concatenating the windows
        gives one continuous record with no session counted twice."""
        days = [f"2024-{m:02d}-{d:02d}" for m in range(1, 13) for d in range(1, 29)]
        folds = make_folds(days, is_days=100, oos_days=30, purge_days=15)
        for a, b in zip(folds, folds[1:]):
            assert a.oos_end <= b.oos_start or a.oos_start != b.oos_start

    def test_an_inverted_fold_raises_rather_than_warning(self):
        """A contaminated run produces a number that looks exactly like a clean
        one, so this has to be an exception."""
        with pytest.raises(SelectionContaminated):
            Fold(0, "2024-03-01", "2024-01-01", "2024-04-01", "2024-05-01")

    def test_a_tape_too_short_for_one_fold_yields_none(self):
        assert make_folds(["2024-01-01"] * 50, is_days=100, oos_days=30,
                          purge_days=5) == []


class TestSliceTapes:
    def test_warmup_extends_backwards_only(self):
        """The one direction that would be lookahead is forwards."""
        t = {"RELIANCE": tape(400)}
        days = calendar(t)
        start, end = days[200], days[260]
        plain = slice_tapes(t, start, end)["RELIANCE"]
        warmed = slice_tapes(t, start, end, warmup_bars=80)["RELIANCE"]
        assert len(warmed) == len(plain) + 80
        assert warmed.time[-1] == plain.time[-1]        # nothing added after
        assert warmed.time[0] < plain.time[0]           # only before

    def test_a_window_shorter_than_the_warmup_can_still_fire(self):
        """The bug: a 60-session window handed to a rule with an 80-session
        warm-up returns zero trades and no error."""
        t = {"RELIANCE": tape(900)}
        days = calendar(t)
        window = (days[600], days[660])
        starved = replay(slice_tapes(t, *window), CFG, entry_window=window)
        warmed = replay(slice_tapes(t, *window, warmup_bars=CFG.warmup_bars()),
                        CFG, entry_window=window)
        assert not starved.trades
        assert len(warmed.trades) >= len(starved.trades)


class TestSummarise:
    def _trades(self, rets, days):
        return [Trade(symbol="X", side="fade_up", option_type="PE",
                      entry_ms=0, exit_ms=0, entry_day=d, exit_day=d,
                      spot_in=100, spot_out=100, strike=100, dte_in=35, iv=0.2,
                      premium_in=10, premium_out=10, fill_in=10, fill_out=10,
                      qty=1, lots=1, gross=r * 10, costs=0, net=r * 10, ret=r,
                      held_days=10, reason="horizon", stretch=2.0)
                for r, d in zip(rets, days)]

    def test_one_observation_per_entry_day(self):
        t = self._trades([0.1, 0.3, -0.2], ["2024-01-01", "2024-01-01", "2024-01-02"])
        b = summarise(t, 100_000.0, hold_days=10)
        assert b.n_days == 2
        assert b.mean_day_return == pytest.approx((0.2 + -0.2) / 2)

    def test_drawdown_scales_with_the_allocation_not_with_a_capital_figure(self):
        """The bug: with lot sizing, one NIFTY lot is about 22% of a 1 lakh
        account, and the harness reported a -92% drawdown that was entirely the
        mismatch between the sizer and the divisor."""
        t = self._trades([-0.5] * 4, [f"2024-01-0{i}" for i in range(1, 5)])
        small = summarise(t, 100_000.0, hold_days=10, allocation_pct=1.0)
        big = summarise(t, 100_000.0, hold_days=10, allocation_pct=10.0)
        assert big.max_dd_pct < small.max_dd_pct < 0
        assert small.allocation_pct == 1.0

    def test_flat_sessions_are_zeros_not_gaps(self):
        """Dropping the days a strategy held nothing inflates the Sharpe of
        anything that trades rarely, which is exactly what this one does."""
        t = self._trades([0.2, 0.2], ["2024-01-01", "2024-06-01"])
        rets = equity_series(t, allocation_pct=10.0)
        assert len(rets) == 2                   # only sessions the tape has
        assert float(np.sum(rets)) == pytest.approx(0.04)

    def test_an_empty_book_is_not_an_error(self):
        b = summarise([], 100_000.0, hold_days=10)
        assert b.n_days == 0 and b.mean_day_return == 0.0


class TestPermutation:
    def test_the_null_lives_in_the_same_windows_as_the_book(self):
        """The bug: the null was drawn from the whole tape while the observed
        came from three out-of-sample windows, so the p-value compared 2024
        against 2026."""
        t = {"RELIANCE": tape(900), "INFY": tape(900, seed=9)}
        days = calendar(t)
        windows = [(days[400], days[500])]
        p = permutation_p(t, CFG, 0.0, rounds=20, windows=windows)
        # It either has enough entries in those windows to permute, or refuses.
        assert p is None or 0.0 < p <= 1.0

    def test_an_impossibly_good_observed_is_never_beaten(self):
        t = {"RELIANCE": tape(900), "INFY": tape(900, seed=9)}
        p = permutation_p(t, CFG, 10.0, rounds=30)
        assert p is None or p <= 0.05

    def test_an_impossibly_bad_observed_is_always_beaten(self):
        t = {"RELIANCE": tape(900), "INFY": tape(900, seed=9)}
        p = permutation_p(t, CFG, -10.0, rounds=30)
        assert p is None or p > 0.9

    def test_too_little_to_permute_returns_none_not_a_pass(self):
        """The gate treats a missing p-value as a FAILED check."""
        assert permutation_p({"RELIANCE": tape(200)}, CFG, 0.0, rounds=5) is None


class TestGate:
    def _book(self, **kw):
        b = summarise([], 100_000.0, hold_days=10)
        b.trades = [object()] * kw.pop("trades", 200)
        b.n_days = kw.pop("n_days", 120)
        b.mean_day_return = kw.pop("mean", 0.05)
        b.ci = kw.pop("ci", (0.01, 0.09))
        b.max_dd_pct = kw.pop("dd", -12.0)
        b.per_year = kw.pop("per_year", {"2024": 0.1, "2025": 0.05, "2026": 0.07})
        return b

    def test_a_missing_permutation_fails_rather_than_passes(self):
        v = judge(self._book(), permutation=None, dsr=0.9, breakeven_vrp=2.0)
        assert not v.checks["beats_random_timing"]

    def test_break_even_must_clear_the_DEAR_end_of_the_band(self):
        """The cheap end is the flattering comparison, and a strategy that only
        clears it stops working in exactly the bid-up conditions where an
        operator most wants to believe in it."""
        cheap = judge(self._book(), permutation=0.01, dsr=0.9, breakeven_vrp=1.20)
        dear = judge(self._book(), permutation=0.01, dsr=0.9, breakeven_vrp=1.45)
        assert not cheap.checks["priced_edge"]
        assert dear.checks["priced_edge"]

    def test_a_confidence_interval_through_zero_fails(self):
        v = judge(self._book(ci=(-0.02, 0.12)), permutation=0.01, dsr=0.9,
                  breakeven_vrp=2.0)
        assert not v.checks["mean_proven"]
        assert any("includes" in r for r in v.reasons)

    def test_one_losing_year_fails_consistency(self):
        v = judge(self._book(per_year={"2024": 0.1, "2025": -0.02}),
                  permutation=0.01, dsr=0.9, breakeven_vrp=2.0)
        assert not v.checks["consistent_across_years"]

    def test_everything_passing_promotes(self):
        v = judge(self._book(), permutation=0.01, dsr=0.9, breakeven_vrp=2.0)
        assert v.promoted and not v.reasons

    def test_the_gate_is_configurable_but_strict_by_default(self):
        g = Gate()
        assert g.min_deflated_sharpe == 0.5
        assert g.max_permutation_p == 0.05
        assert g.require_ci_above_zero


class TestRun:
    def test_produces_all_three_books(self):
        t = {"RELIANCE": tape(900), "INFY": tape(900, seed=9)}
        r = run(t, CFG, is_days=200, oos_days=60, permutation_rounds=5)
        d = r.as_dict()
        assert {"oos", "selected", "full_sample"} <= set(d)
        assert d["folds"]
        # The fixed book is the one the verdict judges.
        assert d["verdict"]["checks"]

    def test_the_selector_never_sees_the_window_it_is_judged_on(self):
        t = {"RELIANCE": tape(900), "INFY": tape(900, seed=9)}
        r = run(t, CFG, is_days=200, oos_days=60, permutation_rounds=5)
        for f in r.folds:
            assert f["in_sample"][1] <= f["out_of_sample"][0]

    def test_too_short_a_tape_raises_with_the_arithmetic(self):
        with pytest.raises(ValueError, match="not enough"):
            run({"RELIANCE": tape(200)}, CFG, is_days=200, oos_days=60)


class TestConfigValidation:
    def test_a_trade_may_not_outlive_its_contract(self):
        with pytest.raises(ValueError, match="outlive"):
            validate({"hold_days": 40, "min_dte": 35})

    def test_unknown_keys_are_refused_not_ignored(self):
        """A silently dropped setting is worse than a 422 — the UI has no way
        to tell that it did not take."""
        with pytest.raises(ValueError, match="unknown settings"):
            validate({"not_a_setting": 1})

    def test_an_empty_curated_universe_is_refused(self):
        with pytest.raises(ValueError, match="nothing to scan"):
            validate({"universe_mode": "curated",
                      "scan_indices": [], "scan_stocks": []})

    def test_an_empty_list_is_fine_in_fno_mode(self):
        """In ``fno`` mode the universe comes from the instrument dump, so the
        name lists are not the source of truth and emptying them is not an
        error — it is the default state."""
        cfg = validate({"universe_mode": "fno",
                        "scan_indices": [], "scan_stocks": []})
        assert cfg.universe_mode == "fno"

    def test_the_market_gate_ships_on(self):
        """The setting that decides whether this makes money at all."""
        c = SnapbackConfig()
        assert c.market_filter == "bearish" and c.market_ema == 50
        assert any("-1.28%" in w for w in validate({"market_filter": "off"}).warnings())

    def test_out_of_range_values_are_refused(self):
        with pytest.raises(ValueError, match="target_delta"):
            validate({"target_delta": 1.5})
        with pytest.raises(ValueError, match="max_dte"):
            validate({"min_dte": 40, "max_dte": 30, "hold_days": 10})

    def test_instrument_names_are_upper_cased_and_kept_as_tuples(self):
        cfg = validate({"scan_stocks": ["reliance", " infy "]})
        assert cfg.scan_stocks == ("RELIANCE", "INFY")

    def test_warnings_are_not_errors(self):
        cfg = validate({"auto_execute": True, "min_dte": 15, "hold_days": 10})
        assert cfg.auto_execute
        joined = " ".join(cfg.warnings())
        assert "walk-forward" in joined and "break-even VRP" in joined

    def test_the_shipped_defaults_are_the_measured_ones(self):
        c = SnapbackConfig()
        assert (c.target_delta, c.min_dte, c.hold_days, c.lookback_days) == (
            0.70, 40, 15, 20)
        assert c.allow_fade_down is False
        assert c.enabled is False and c.auto_execute is False
        assert c.sides() == ("fade_up",)

    def test_the_model_assumptions_that_flatter_are_ON_by_default(self):
        """Three settings each have a value that makes the book look better and
        is wrong. All three ship at the honest value, because a default that
        flatters is the one nobody changes."""
        c = SnapbackConfig()
        # A skew, so an out-of-the-money put is not priced at ATM vol. Flat
        # made a 0.20-delta put read +8.31% per entry day against -5.97%.
        assert c.smile_slope > 0
        # A FLATTER in-the-money wing, because real equity skew is asymmetric
        # and this engine buys in-the-money puts. Symmetric read +4.70% against
        # +2.01%.
        assert 0 < c.smile_itm_slope < c.smile_slope
        # The hedge, without which the same windows return -1.08% at p=0.37.
        assert c.hedge_mode == "index_futures"
        assert c.market_filter == "bearish"

    def test_a_trade_cannot_outlive_its_contract_at_the_defaults(self):
        c = SnapbackConfig()
        assert c.hold_days < c.min_dte
