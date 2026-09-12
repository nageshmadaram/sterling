"""Walk-forward evaluation for the intraday pack.

The whole point is one rule: **the window a configuration is chosen on is never
the window it is judged on.** Everything else here exists to make that rule
impossible to break by accident.

* Each fold selects on its in-sample window ONLY. The out-of-sample window is
  not read, not scored, and not available to the selector — passing it in is an
  error rather than a convention.
* A **purge** gap sits between the two, so a trade opened at the end of the
  in-sample window cannot still be running inside the out-of-sample one. Without
  it the two windows share outcomes and the split is decorative.
* The out-of-sample books of every fold are concatenated into ONE record. That
  concatenation is the result. A per-fold best, reported as the headline, is the
  same overfitting the split was supposed to prevent, wearing a rigorous hat.

The promotion gate at the end is deliberately hard to pass. Nothing in this
repo's history has cleared a deflated Sharpe of 0.5, and a gate that waves
through the first thing tried would be worse than no gate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional, Sequence

import numpy as np

from .backtest import BacktestResult, BacktestTrade, CostModel, replay
from .config import IntradayConfig
from .models import to_bars
from .stats import (Summary, daily_returns, deflated_sharpe, permutation_p_value,
                    sharpe, summarise)


class SelectionContaminated(RuntimeError):
    """Raised when out-of-sample data reaches the selector.

    An exception rather than a warning on purpose: a contaminated run produces
    a number that looks exactly like a clean one.
    """


@dataclass(frozen=True)
class Fold:
    """One in-sample window, a purge gap, and the out-of-sample window after it.

    Indices are half-open bar positions into the same tape, so a fold can be
    checked for overlap by arithmetic rather than by trusting the caller.
    """

    index: int
    is_start: int
    is_end: int
    oos_start: int
    oos_end: int

    def __post_init__(self) -> None:
        if not (self.is_start < self.is_end <= self.oos_start < self.oos_end):
            raise SelectionContaminated(
                f"fold {self.index}: windows overlap or are inverted "
                f"({self.is_start}:{self.is_end} / {self.oos_start}:{self.oos_end})")

    @property
    def purge(self) -> int:
        return self.oos_start - self.is_end


def make_folds(n_bars: int, *, is_bars: int, oos_bars: int,
               purge_bars: int, step: Optional[int] = None) -> list[Fold]:
    """Rolling folds across a tape.

    ``step`` defaults to ``oos_bars``, which makes the out-of-sample windows
    contiguous and non-overlapping — so concatenating them produces one
    continuous out-of-sample record with no bar counted twice. A smaller step
    would reuse bars across folds and quietly inflate the sample.
    """
    if min(is_bars, oos_bars) <= 0 or purge_bars < 0:
        raise ValueError("is_bars and oos_bars must be positive, purge_bars >= 0")
    step = step or oos_bars
    folds: list[Fold] = []
    start = 0
    while True:
        is_end = start + is_bars
        oos_start = is_end + purge_bars
        oos_end = oos_start + oos_bars
        if oos_end > n_bars:
            break
        folds.append(Fold(len(folds), start, is_end, oos_start, oos_end))
        start += step
    return folds


@dataclass
class Candidate:
    """One configuration under test, and what it scored in-sample."""
    label: str
    cfg: IntradayConfig
    summary: Optional[Summary] = None
    score: float = float("-inf")


@dataclass
class FoldResult:
    fold: Fold
    chosen: str
    #: The symbols this fold's IN-SAMPLE window said were worth trading.
    #:
    #: Selecting the universe is a real decision an operator makes, and making
    #: it in-sample and measuring it out-of-sample is the only honest way to
    #: test it. Trading every symbol pays a full round trip on the ones whose
    #: edge is absent, and costs are the binding constraint here.
    universe: list[str] = field(default_factory=list)
    #: Every candidate's in-sample score, kept so a run can be audited for a
    #: selector that was choosing on noise.
    in_sample: dict[str, float] = field(default_factory=dict)
    oos_trades: list[BacktestTrade] = field(default_factory=list)
    oos: Optional[Summary] = None


@dataclass
class WalkForwardReport:
    strategy: str
    symbols: list[str]
    folds: list[FoldResult]
    #: The concatenated out-of-sample book. THIS is the result.
    oos_trades: list[BacktestTrade]
    oos: Summary
    #: How many distinct configurations were tried, across all folds. Feeds the
    #: deflation — a search this size has to pay for itself.
    n_trials: int
    trial_sr_std: float
    dsr: float
    permutation_p: Optional[float]
    per_symbol_net: dict[str, float]
    verdict: "Verdict"

    def as_dict(self) -> dict:
        return {
            "strategy": self.strategy,
            "symbols": self.symbols,
            "folds": [{"fold": f.fold.index, "chosen": f.chosen,
                       "universe": f.universe,
                       "purge_bars": f.fold.purge,
                       "in_sample_scores": f.in_sample,
                       "oos": f.oos.as_dict() if f.oos else None}
                      for f in self.folds],
            "oos": self.oos.as_dict(),
            "n_trials": self.n_trials,
            "trial_sharpe_std": round(self.trial_sr_std, 4),
            "deflated_sharpe": round(self.dsr, 4),
            "permutation_p": self.permutation_p,
            "per_symbol_net": {k: round(v, 2) for k, v in self.per_symbol_net.items()},
            "verdict": self.verdict.as_dict(),
        }


@dataclass
class Verdict:
    """Whether this strategy has earned live execution, and what is missing."""
    promoted: bool
    reasons: list[str]
    checks: dict[str, bool]

    def as_dict(self) -> dict:
        return {"promoted": self.promoted, "reasons": self.reasons,
                "checks": self.checks}


#: The bar the gate sets. Published rather than buried in a conditional so a
#: reader can disagree with the threshold instead of reverse-engineering it.
GATE = {
    "min_oos_trades": 50,
    "min_oos_net": 0.0,
    "min_oos_sharpe": 0.5,
    "min_deflated_sharpe": 0.5,
    "max_permutation_p": 0.05,
    "min_symbols_positive_pct": 60.0,
    "max_drawdown_pct": -35.0,
    #: In R, and it is THIS the gate leans on. The percentage depends on a
    #: capital figure the harness does not size to.
    "max_drawdown_r": -25.0,
}


def apply_portfolio_limits(trades: Sequence[BacktestTrade],
                           cfg: IntradayConfig) -> list[BacktestTrade]:
    """Keep only the trades the ENGINE would actually have been able to open.

    Each symbol is replayed independently, which is right for measuring a
    signal and wrong for measuring a book: it takes every trade on every symbol
    at once, and the live engine cannot. ``max_concurrent_positions`` caps how
    many can be held at a time and ``max_new_trades_per_day`` caps how many can
    be opened in a session.

    The difference is not marginal. Unconstrained, six symbols firing together
    average away each other's variance and produce a Sharpe no single book
    could earn — this harness reported 7.2 before this existed, and a Sharpe of
    7 is not a discovery, it is a portfolio nobody could hold.

    Chronological and first-come: a trade is admitted if there was room when it
    wanted to open. That is exactly what the live gate does.
    """
    room = max(1, int(cfg.max_concurrent_positions))
    per_day = max(1, int(cfg.max_new_trades_per_day))
    kept: list[BacktestTrade] = []
    open_until: list[int] = []          # exit_ms of admitted, still-open trades
    opened_on: dict[int, int] = {}      # IST day -> count opened
    for t in sorted(trades, key=lambda x: x.entry_ms):
        open_until = [e for e in open_until if e > t.entry_ms]
        if len(open_until) >= room:
            continue
        day = (t.entry_ms + 19_800_000) // 86_400_000
        if opened_on.get(day, 0) >= per_day:
            continue
        kept.append(t)
        open_until.append(t.exit_ms)
        opened_on[day] = opened_on.get(day, 0) + 1
    return kept


def score_in_sample(summary: Summary, *, min_trades: int = 10) -> float:
    """The selector's objective: in-sample Sharpe, with a trade-count floor.

    Sharpe rather than net profit, because net rewards a single lucky trade.
    The floor is a refusal, not a penalty: a configuration that fired four times
    across a whole in-sample window has told us nothing, and letting it win on a
    Sharpe computed from four trades is how a walk-forward selects noise.
    """
    if summary is None or summary.trades < min_trades:
        return float("-inf")
    return summary.sharpe


def run(
    tapes: dict[str, Sequence],
    strategy: str,
    grid: Sequence[tuple[str, IntradayConfig]],
    *,
    is_bars: int,
    oos_bars: int,
    purge_bars: int,
    costs: Optional[CostModel] = None,
    capital: float = 100_000.0,
    qty: int = 1,
    lot_sizes: Optional[dict[str, int]] = None,
    prior_sharpes: Sequence[float] = (),
    select_universe: bool = False,
    selector: Callable[[Summary], float] = score_in_sample,
) -> WalkForwardReport:
    """Walk one strategy forward across every symbol's tape.

    ``tapes`` is symbol -> candles. Folds are cut on the SHORTEST tape so every
    symbol contributes the same windows, which is what makes the per-symbol
    consistency check meaningful rather than an artefact of who has more data.

    ``lot_sizes`` is symbol -> units per lot. Pass it: a flat per-order
    brokerage charged against ONE unit of an index makes the brokerage the
    entire result, and the run reports a strategy that nobody could have placed.

    ``prior_sharpes`` carries the in-sample Sharpes of every configuration
    tried EARLIER IN THE SAME SEARCH — a previous timeframe, a previous grid,
    a previous day of looking. Deflation is about how many variants were
    examined before one looked good, and a search spread across several runs
    deflates by exactly as much as the same search inside one run. Omitting it
    reports a deflated Sharpe that is too HIGH, which is the one direction this
    statistic must never be wrong in.
    """
    costs = costs or CostModel()
    symbols = sorted(tapes)
    if not symbols or not grid:
        raise ValueError("need at least one symbol and one candidate configuration")
    n = min(len(tapes[s]) for s in symbols)
    folds = make_folds(n, is_bars=is_bars, oos_bars=oos_bars, purge_bars=purge_bars)
    if not folds:
        raise ValueError(
            f"{n} bars is not enough for {is_bars} in-sample + {purge_bars} purge "
            f"+ {oos_bars} out-of-sample — widen the data or narrow the windows")

    fold_results: list[FoldResult] = []
    all_oos: list[BacktestTrade] = []
    # Seeded with the rest of the search, not just this run's grid.
    trial_sharpes: list[float] = [float(x) for x in prior_sharpes]
    per_symbol: dict[str, float] = {s: 0.0 for s in symbols}

    for fold in folds:
        scores: dict[str, float] = {}
        best_label, best_score = "", float("-inf")
        for label, cfg in grid:
            trades: list[BacktestTrade] = []
            for sym in symbols:
                window = list(tapes[sym])[fold.is_start:fold.is_end]
                trades.extend(replay(window, cfg, sym, strategy, costs=costs,
                                     qty=(lot_sizes or {}).get(sym, qty),
                                     capital=capital).trades)
            s = summarise(apply_portfolio_limits(trades, cfg), capital)
            scores[label] = round(s.sharpe, 3)
            trial_sharpes.append(s.sharpe)
            value = selector(s)
            if value > best_score:
                best_label, best_score = label, value

        result = FoldResult(fold=fold, chosen=best_label, in_sample=scores)
        if best_label:
            chosen_cfg = dict(grid)[best_label]
            # Which symbols the IN-SAMPLE window says are worth the costs. Read
            # from the chosen config's own in-sample book, never from the
            # out-of-sample one — that would be selecting on the answer.
            if select_universe:
                in_sample_by_symbol: dict[str, float] = {}
                for sym in symbols:
                    window = list(tapes[sym])[fold.is_start:fold.is_end]
                    got = replay(window, chosen_cfg, sym, strategy, costs=costs,
                                 qty=(lot_sizes or {}).get(sym, qty),
                                 capital=capital).trades
                    in_sample_by_symbol[sym] = sum(t.net for t in got)
                tradable = [s for s in symbols if in_sample_by_symbol[s] > 0]
                # Never narrow to nothing: a fold where no symbol worked
                # in-sample is a fold with no opinion, not a fold that says
                # "trade none of them and call it flat".
                result.universe = tradable or list(symbols)
            else:
                result.universe = list(symbols)
            for sym in result.universe:
                # The out-of-sample window ONLY. Nothing before `oos_start` is
                # replayed here, so a trade cannot straddle the boundary.
                window = list(tapes[sym])[fold.oos_start:fold.oos_end]
                got = replay(window, chosen_cfg, sym, strategy, costs=costs,
                             qty=(lot_sizes or {}).get(sym, qty),
                             capital=capital).trades
                result.oos_trades.extend(got)
            # The cap is a limit ACROSS symbols, so it applies to the fold's
            # combined book rather than to each symbol's separately.
            result.oos_trades = apply_portfolio_limits(result.oos_trades, chosen_cfg)
            for t in result.oos_trades:
                per_symbol[t.symbol] = per_symbol.get(t.symbol, 0.0) + t.net
            result.oos = summarise(result.oos_trades, capital)
            all_oos.extend(result.oos_trades)
        fold_results.append(result)

    oos = summarise(all_oos, capital)
    rets = daily_returns(all_oos, capital)
    finite = [s for s in trial_sharpes if np.isfinite(s)]
    trial_sr_std = float(np.std(finite, ddof=1)) / np.sqrt(250) if len(finite) > 1 else 0.0
    dsr = deflated_sharpe(rets, n_trials=len(trial_sharpes),
                          trial_sr_std=trial_sr_std)
    # Per-symbol tapes, so every term of the comparison is in the same units
    # as the observed book. Scoring a quantity-weighted multi-symbol result
    # against one symbol's point moves pinned the p-value at its floor.
    bars_by_symbol = {s: to_bars(list(tapes[s])) for s in symbols}
    p = permutation_p_value(all_oos, bars_by_symbol, costs=costs)
    verdict = judge(oos, dsr, p, per_symbol)
    return WalkForwardReport(
        strategy=strategy, symbols=symbols, folds=fold_results,
        oos_trades=all_oos, oos=oos, n_trials=len(trial_sharpes),
        trial_sr_std=trial_sr_std, dsr=dsr, permutation_p=p,
        per_symbol_net=per_symbol, verdict=verdict)


def judge(oos: Summary, dsr: float, p: Optional[float],
          per_symbol: dict[str, float]) -> Verdict:
    """Every gate, with the failures named.

    A verdict that says only "no" makes the next run a guess. Each check
    reports what it wanted and what it got, so the gap is the work.
    """
    positive = sum(1 for v in per_symbol.values() if v > 0)
    pct = (positive / len(per_symbol) * 100.0) if per_symbol else 0.0
    checks = {
        "enough_trades": oos.trades >= GATE["min_oos_trades"],
        "profitable": oos.net > GATE["min_oos_net"],
        "sharpe": oos.sharpe >= GATE["min_oos_sharpe"],
        "deflated_sharpe": dsr >= GATE["min_deflated_sharpe"],
        # A missing p-value is a FAILED check, never a passed one. "Could not
        # test" and "tested and passed" must not look the same from here.
        "beats_random_timing": p is not None and p <= GATE["max_permutation_p"],
        "consistent_across_symbols": pct >= GATE["min_symbols_positive_pct"],
        "survivable_drawdown": oos.max_drawdown_r >= GATE["max_drawdown_r"],
    }
    reasons: list[str] = []
    if not checks["enough_trades"]:
        reasons.append(f"{oos.trades} out-of-sample trades, needs "
                       f"{GATE['min_oos_trades']}")
    if not checks["profitable"]:
        # Two different failures, and conflating them is misleading. Costs
        # eating a real edge is a cost problem; a gross that was never positive
        # is the SIGNAL, and no cost model will fix it.
        if oos.gross_positive:
            reasons.append(f"out-of-sample net {oos.net:,.0f} after costs — "
                           f"the edge was there ({oos.gross:,.0f} gross) and "
                           f"{oos.cost_share_pct}% of it went to costs")
        else:
            reasons.append(f"out-of-sample net {oos.net:,.0f}: gross was "
                           f"{oos.gross:,.0f} BEFORE any cost, so the entries "
                           "lose on their own and no cost model fixes that")
    if not checks["sharpe"]:
        reasons.append(f"out-of-sample Sharpe {oos.sharpe} < {GATE['min_oos_sharpe']}")
    if not checks["deflated_sharpe"]:
        reasons.append(f"deflated Sharpe {dsr:.3f} < {GATE['min_deflated_sharpe']} — "
                       "the result does not survive how many variants were tried")
    if not checks["beats_random_timing"]:
        reasons.append("timing does not beat random entries of identical exposure"
                       if p is not None else
                       "too few out-of-sample trades to test the timing at all")
    if not checks["consistent_across_symbols"]:
        reasons.append(f"only {pct:.0f}% of symbols profitable, needs "
                       f"{GATE['min_symbols_positive_pct']:.0f}%")
    if not checks["survivable_drawdown"]:
        reasons.append(f"max drawdown {oos.max_drawdown_r}R is worse than "
                       f"{GATE['max_drawdown_r']}R")
    return Verdict(promoted=all(checks.values()), reasons=reasons, checks=checks)
