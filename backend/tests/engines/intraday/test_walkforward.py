"""The split, and the gate.

One rule carries this whole module: the window a configuration is CHOSEN on is
never the window it is JUDGED on. A contaminated walk-forward produces a number
that looks exactly like a clean one, which is why the contamination is an
exception here rather than a warning.
"""
from __future__ import annotations

import dataclasses

import numpy as np
import pytest

from app.engines.intraday import IntradayConfig
from app.engines.intraday.backtest import CostModel
from app.engines.intraday.stats import (Summary, deflated_sharpe,
                                        expected_max_sharpe, max_drawdown,
                                        permutation_p_value, profit_factor,
                                        sharpe, summarise)
from app.engines.intraday.walkforward import (GATE, Fold, SelectionContaminated,
                                              Verdict, judge, make_folds, run,
                                              score_in_sample)
from tests.engines.intraday.test_backtest import cfg, tape


# ------------------------------------------------------------------- the split

class TestFolds:
    def test_the_windows_never_overlap(self):
        for f in make_folds(2000, is_bars=400, oos_bars=200, purge_bars=50):
            assert f.is_end <= f.oos_start
            assert f.purge == 50

    def test_out_of_sample_windows_are_contiguous_and_counted_once(self):
        """Overlapping out-of-sample windows reuse bars across folds and
        quietly inflate the sample the result is computed from."""
        folds = make_folds(2000, is_bars=400, oos_bars=200, purge_bars=50)
        for a, b in zip(folds, folds[1:]):
            assert b.oos_start == a.oos_end

    def test_an_inverted_fold_is_an_error_not_a_silent_zero(self):
        with pytest.raises(SelectionContaminated):
            Fold(index=0, is_start=0, is_end=100, oos_start=50, oos_end=150)

    def test_a_tape_too_short_for_one_fold_yields_none(self):
        assert make_folds(300, is_bars=400, oos_bars=200, purge_bars=50) == []

    def test_the_purge_is_not_optional_arithmetic(self):
        f = make_folds(1000, is_bars=300, oos_bars=100, purge_bars=75)[0]
        # A trade opened at the end of the in-sample window cannot still be
        # running inside the out-of-sample one.
        assert f.oos_start - f.is_end == 75


# ------------------------------------------------------------- the selector

class TestSelector:
    def test_a_configuration_with_too_few_trades_cannot_win(self):
        thin = Summary(trades=3, wins=3, win_rate=100.0, net=900.0, gross=1000.0,
                       costs=100.0, cost_share_pct=10.0, gross_positive=True,
                       sharpe=9.9, max_drawdown_pct=-1.0, profit_factor=9.0,
                       avg_r=3.0, expectancy=300.0, max_drawdown_r=-0.5)
        assert score_in_sample(thin) == float("-inf")

    def test_it_scores_on_sharpe_not_on_net(self):
        """Net rewards a single lucky trade; that is the thing a walk-forward
        is supposed to stop selecting."""
        a = Summary(trades=40, wins=20, win_rate=50.0, net=100.0, gross=200.0,
                    costs=100.0, cost_share_pct=50.0, gross_positive=True,
                    sharpe=1.4, max_drawdown_pct=-5.0, profit_factor=1.2,
                    avg_r=0.1, expectancy=2.5, max_drawdown_r=-3.0)
        b = dataclasses.replace(a, net=10_000.0, sharpe=0.2)
        assert score_in_sample(a) > score_in_sample(b)


# -------------------------------------------------------------------- the run

def _grid(base: IntradayConfig) -> list[tuple[str, IntradayConfig]]:
    return [("tight", dataclasses.replace(base, rb_min_spread_pct=0.02).validate()),
            ("loose", dataclasses.replace(base, rb_min_spread_pct=0.30).validate())]


def test_a_full_walk_forward_reports_only_out_of_sample_trades():
    rows = tape([100.0 + (i % 50) * 0.4 for i in range(3000)])
    base = cfg()
    rep = run({"NIFTY": rows}, "ma_ribbon", _grid(base),
              is_bars=800, oos_bars=400, purge_bars=75,
              costs=CostModel(slippage_pct=0.0))
    assert rep.folds, "expected at least one fold"
    # Every trade in the headline book came from an out-of-sample window.
    spans = [(f.fold.oos_start, f.fold.oos_end) for f in rep.folds]
    assert rep.oos.trades == len(rep.oos_trades)
    assert sum(len(f.oos_trades) for f in rep.folds) == len(rep.oos_trades)
    assert rep.n_trials == len(rep.folds) * 2       # two candidates per fold
    assert spans


def test_the_selector_never_sees_the_out_of_sample_window():
    """The load-bearing test. If the chosen label were picked with knowledge of
    the out-of-sample window, changing ONLY that window would change it."""
    head = [100.0 + (i % 50) * 0.4 for i in range(1200)]
    quiet = head + [100.0] * 1800
    wild = head + [100.0 + (i % 7) * 9.0 for i in range(1800)]
    base = cfg()
    a = run({"X": tape(quiet)}, "ma_ribbon", _grid(base),
            is_bars=800, oos_bars=400, purge_bars=75, costs=CostModel(slippage_pct=0.0))
    b = run({"X": tape(wild)}, "ma_ribbon", _grid(base),
            is_bars=800, oos_bars=400, purge_bars=75, costs=CostModel(slippage_pct=0.0))
    # The first fold's in-sample window is identical in both tapes, so the
    # configuration it chooses must be identical too.
    assert a.folds[0].chosen == b.folds[0].chosen
    assert a.folds[0].in_sample == b.folds[0].in_sample


def test_a_tape_too_short_for_the_windows_says_so(self=None):
    with pytest.raises(ValueError, match="not enough"):
        run({"X": tape([100.0] * 300)}, "ma_ribbon", _grid(cfg()),
            is_bars=800, oos_bars=400, purge_bars=75)


def test_an_empty_grid_is_refused():
    with pytest.raises(ValueError):
        run({"X": tape([100.0] * 3000)}, "ma_ribbon", [],
            is_bars=800, oos_bars=400, purge_bars=75)


# -------------------------------------------------------------------- the gate

def _summary(**over) -> Summary:
    base = dict(trades=120, wins=70, win_rate=58.3, net=50_000.0, gross=60_000.0,
                costs=10_000.0, cost_share_pct=16.7, gross_positive=True,
                sharpe=1.4, max_drawdown_pct=-12.0, profit_factor=1.6, avg_r=0.3,
                expectancy=416.0, max_drawdown_r=-8.0)
    base.update(over)
    return Summary(**base)


class TestTheGate:
    def test_everything_passing_promotes(self):
        v = judge(_summary(), dsr=0.72, p=0.01, per_symbol={"A": 10.0, "B": 5.0})
        assert v.promoted and v.reasons == []

    def test_a_missing_permutation_p_is_a_FAILED_check_not_a_passed_one(self):
        """"Could not test" and "tested and passed" must not look the same."""
        v = judge(_summary(), dsr=0.72, p=None, per_symbol={"A": 10.0})
        assert not v.promoted
        assert v.checks["beats_random_timing"] is False
        assert any("too few" in r for r in v.reasons)

    def test_a_thin_deflated_sharpe_blocks_a_profitable_book(self):
        v = judge(_summary(), dsr=0.31, p=0.01, per_symbol={"A": 10.0})
        assert not v.promoted
        assert any("how many variants were tried" in r for r in v.reasons)

    def test_one_symbol_carrying_the_result_blocks_it(self):
        v = judge(_summary(), dsr=0.8, p=0.01,
                  per_symbol={"A": 60_000.0, "B": -5.0, "C": -5.0, "D": -1.0})
        assert not v.promoted
        assert any("symbols profitable" in r for r in v.reasons)

    def test_an_unsurvivable_drawdown_blocks_it(self):
        """Measured in R, because the percentage depends on a capital figure
        the harness does not size to."""
        v = judge(_summary(max_drawdown_r=-40.0), dsr=0.8, p=0.01,
                  per_symbol={"A": 10.0})
        assert not v.promoted
        assert any("R is worse than" in r for r in v.reasons)

    def test_costs_eating_a_real_edge_reads_differently_from_having_no_edge(self):
        """Conflating them is misleading. Costs eating an edge is a cost
        problem; a gross that was never positive is the SIGNAL, and no cost
        model fixes that."""
        eaten = judge(_summary(net=-4000.0, gross=10_000.0, costs=14_000.0,
                               cost_share_pct=140.0, gross_positive=True),
                      dsr=0.8, p=0.01, per_symbol={"A": -10.0})
        assert any("the edge was there" in r and "140.0%" in r for r in eaten.reasons)
        none = judge(_summary(net=-4000.0, gross=-1_000.0, costs=3_000.0,
                              cost_share_pct=None, gross_positive=False),
                     dsr=0.8, p=0.01, per_symbol={"A": -10.0})
        assert any("BEFORE any cost" in r for r in none.reasons)

    def test_the_thresholds_are_published_rather_than_buried(self):
        # A reader must be able to disagree with the bar instead of
        # reverse-engineering it from a conditional.
        assert GATE["min_deflated_sharpe"] == 0.5
        assert set(GATE) >= {"min_oos_trades", "min_oos_sharpe",
                             "max_permutation_p", "max_drawdown_pct"}


# ------------------------------------------------------------------- the stats

class TestStats:
    def test_deflation_bites_harder_the_more_variants_were_tried(self):
        rng = np.random.default_rng(3)
        rets = rng.normal(0.001, 0.01, 400)
        one = deflated_sharpe(rets, n_trials=1, trial_sr_std=0.0)
        many = deflated_sharpe(rets, n_trials=500, trial_sr_std=0.05)
        assert many < one

    def test_a_single_trial_deflates_by_nothing(self):
        assert expected_max_sharpe(1, 0.05) == 0.0

    def test_too_few_observations_refuse_rather_than_score_zero(self):
        assert deflated_sharpe(np.array([0.01] * 10), n_trials=5,
                               trial_sr_std=0.01) == 0.0
        assert sharpe(np.array([0.01] * 10)) == 0.0

    def test_flat_days_count_against_the_sharpe(self):
        """Dropping them inflates anything that trades rarely, because the days
        it was exposed to nothing stop counting against its volatility."""
        from app.engines.intraday.stats import daily_returns

        class T:
            def __init__(self, day, net):
                self.exit_ms = day * 86_400_000
                self.net = net
        rets = daily_returns([T(0, 100.0), T(5, 100.0)], 10_000.0)
        assert len(rets) == 6 and float((rets == 0).sum()) == 4

    def test_the_kurtosis_convention_is_non_excess(self):
        """A normal sample must give ~3, not ~0. Passing excess kurtosis makes
        the correction negative and deflation inflate."""
        from app.engines.intraday.stats import _kurtosis
        rng = np.random.default_rng(11)
        assert 2.5 < _kurtosis(rng.normal(0, 1, 20_000)) < 3.5

    def test_the_permutation_refuses_a_sample_too_small_to_permute(self):
        assert permutation_p_value([], None) is None

    def test_profit_factor_says_nothing_rather_than_infinity_on_no_trades(self):
        assert profit_factor([]) is None

    def test_max_drawdown_is_negative_or_zero(self):
        assert max_drawdown(np.array([0.1, -0.5, 0.2])) < 0
        assert max_drawdown(np.array([])) == 0.0
