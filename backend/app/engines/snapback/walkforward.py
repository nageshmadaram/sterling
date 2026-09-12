"""Walk-forward evaluation and the promotion gate.

One rule: **the window a configuration is chosen on is never the window it is
judged on.** Everything else exists to make that impossible to break by accident.

* Folds are cut on the CALENDAR, not on each symbol's own bar index. Fifteen
  instruments share one market; splitting each by its own bar count puts one
  symbol's June in another's out-of-sample window.
* A **purge** gap sits between the windows, at least as long as the holding
  period, so a trade opened at the end of the in-sample window cannot still be
  running inside the out-of-sample one.
* The out-of-sample books of every fold are concatenated into ONE record. That
  concatenation is the result. A per-fold best reported as the headline is the
  same overfitting the split was meant to prevent, wearing a rigorous hat.

The gate is deliberately hard. Two of its checks are the ones that have failed
every strategy this repo has measured:

* the **deflated Sharpe**, which prices in how many variants were tried;
* the **entry-date permutation**, which asks whether the timing did anything a
  random entry of identical exposure would not have.

And one is specific to buying premium: the **break-even VRP margin**. A book can
be profitable at a modelled vol and still be worthless, because the market does
not sell options at realised vol. If the break-even multiple does not clear the
top of the 1.15-1.30 band the market actually charges, the edge is inside the
modelling error and the gate says so.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Mapping, Optional, Sequence

import numpy as np

from app.engines.common.stats import (TRADING_DAYS, deflated_sharpe,
                                      max_drawdown, sharpe)

from .backtest import CostModel, Result, Trade, replay
from .config import SnapbackConfig
from .models import Bars, ist_day
from .pricing import VRP_BAND, break_even_vrp
from .strategy import entry_indices, features


class SelectionContaminated(RuntimeError):
    """Raised when out-of-sample data reaches the selector.

    An exception rather than a warning: a contaminated run produces a number
    that looks exactly like a clean one.
    """


@dataclass(frozen=True)
class Fold:
    index: int
    is_start: str        # inclusive IST day
    is_end: str          # exclusive
    oos_start: str       # inclusive
    oos_end: str         # exclusive

    def __post_init__(self) -> None:
        if not (self.is_start < self.is_end <= self.oos_start < self.oos_end):
            raise SelectionContaminated(
                f"fold {self.index}: windows overlap or are inverted "
                f"({self.is_start}..{self.is_end} / {self.oos_start}..{self.oos_end})")


def calendar(tapes: Mapping[str, Bars]) -> list[str]:
    """Every session any symbol traded, ascending. The folds are cut on this."""
    days: set[str] = set()
    for bars in tapes.values():
        days.update(ist_day(float(t)) for t in bars.time)
    return sorted(days)


def make_folds(days: Sequence[str], *, is_days: int, oos_days: int,
               purge_days: int) -> list[Fold]:
    """Rolling folds with non-overlapping out-of-sample windows.

    The step is ``oos_days``, so concatenating the out-of-sample windows yields
    one continuous record with no session counted twice. A smaller step would
    reuse sessions across folds and quietly inflate the sample.
    """
    if min(is_days, oos_days) <= 0 or purge_days < 0:
        raise ValueError("is_days and oos_days must be positive, purge_days >= 0")
    folds: list[Fold] = []
    start = 0
    while True:
        is_end = start + is_days
        oos_start = is_end + purge_days
        oos_end = oos_start + oos_days
        if oos_end > len(days):
            break
        folds.append(Fold(len(folds), days[start], days[is_end],
                          days[oos_start], days[oos_end - 1] + "~"))
        start += oos_days
    return folds


def slice_tapes(tapes: Mapping[str, Bars], start: str, end: str, *,
                warmup_bars: int = 0) -> dict[str, Bars]:
    """Every symbol's tape restricted to ``[start, end)``, plus its warm-up.

    ``warmup_bars`` extends the slice BACKWARDS only. Indicators need history to
    be defined at all, and a window shorter than the warm-up produces a tape the
    rule can never fire on — which is silence, not a result. Trades are still
    confined to ``[start, end)`` by the caller's ``entry_window``, so the extra
    sessions can only ever inform an indicator, never host a trade.

    Nothing is ever extended FORWARDS. That is the one direction that would be
    lookahead, and keeping the asymmetry in one function is what makes it
    checkable.
    """
    out: dict[str, Bars] = {}
    for sym, bars in tapes.items():
        days = np.array([ist_day(float(t)) for t in bars.time])
        m = (days >= start) & (days < end)
        if m.sum() == 0:
            continue
        if warmup_bars > 0:
            first = int(np.flatnonzero(m)[0])
            m[max(first - int(warmup_bars), 0):first] = True
        out[sym] = Bars(bars.time[m], bars.open[m], bars.high[m], bars.low[m],
                        bars.close[m], bars.volume[m])
    return out


# ------------------------------------------------------------------ scoring

@dataclass
class Book:
    """A set of trades and everything computed from it, day-clustered."""

    trades: list[Trade]
    capital: float
    n_days: int = 0
    mean_day_return: float = 0.0
    t_stat: float = 0.0
    ci: tuple[float, float] = (0.0, 0.0)
    win_rate: float = 0.0
    net: float = 0.0
    gross: float = 0.0
    costs: float = 0.0
    sharpe: float = 0.0
    max_dd_pct: float = 0.0
    #: Share of capital one position deploys. The Sharpe, the drawdown and the
    #: total return are properties of the strategy AND this number, so it
    #: travels with them. Quoting a return without it is quoting leverage.
    allocation_pct: float = 0.0
    #: Compounded return of the equity curve over the period, as a percentage.
    total_return_pct: float = 0.0
    per_year: dict[str, float] = field(default_factory=dict)
    per_symbol_net: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "trades": len(self.trades), "n_days": self.n_days,
            "allocation_pct": round(self.allocation_pct, 2),
            "mean_day_return_pct": round(self.mean_day_return * 100, 3),
            "t_stat": round(self.t_stat, 3),
            "ci_pct": [round(self.ci[0] * 100, 2), round(self.ci[1] * 100, 2)],
            "win_rate": round(self.win_rate * 100, 2),
            "net": round(self.net, 2), "gross": round(self.gross, 2),
            "costs": round(self.costs, 2),
            "sharpe": round(self.sharpe, 3),
            "max_drawdown_pct": round(self.max_dd_pct, 2),
            "total_return_pct": round(self.total_return_pct, 2),
            "per_year_pct": {k: round(v * 100, 2) for k, v in self.per_year.items()},
            "per_symbol_net": {k: round(v, 2) for k, v in self.per_symbol_net.items()},
        }


def summarise(trades: Sequence[Trade], capital: float, *,
              hold_days: int, allocation_pct: float = 10.0,
              seed: int = 11) -> Book:
    b = Book(list(trades), capital)
    if not trades:
        return b
    by: dict[str, list[float]] = {}
    for t in trades:
        by.setdefault(t.entry_day, []).append(t.ret)
    days = sorted(by)
    x = np.array([float(np.mean(by[d])) for d in days])
    b.n_days = len(x)
    b.mean_day_return = float(x.mean())
    b.win_rate = float(np.mean([t.net > 0 for t in trades]))
    b.net = float(sum(t.net for t in trades))
    b.gross = float(sum(t.gross for t in trades))
    b.costs = float(sum(t.costs for t in trades))

    # A moving-block bootstrap, because holds overlap: a ten-day trade entered on
    # consecutive sessions shares nine of its ten days, and an i.i.d. resample
    # of those treats one market episode as ten independent ones.
    if len(x) >= 20:
        rng = np.random.default_rng(seed)
        block = max(int(hold_days), 5)
        nb = int(math.ceil(len(x) / block))
        hi = max(len(x) - block + 1, 1)
        means = np.empty(2000)
        for k in range(2000):
            starts = rng.integers(0, hi, size=nb)
            means[k] = np.concatenate([x[s:s + block] for s in starts])[:len(x)].mean()
        se = float(means.std(ddof=1))
        b.t_stat = float(b.mean_day_return / se) if se > 0 else 0.0
        b.ci = (float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5)))

    # The equity curve is built from RETURNS and an allocation fraction, not
    # from rupees against a capital figure.
    #
    # Not a refinement — the rupee version was wrong. With lot sizing, one NIFTY
    # lot of a 30-day put is about 21,750 of premium, so against a 1 lakh
    # capital each position was a 22% bet and the harness reported a -92%
    # drawdown that was entirely the mismatch between the sizer and the divisor.
    # Stating the allocation explicitly makes the drawdown a property of the
    # strategy and the position size, which is what it should have been.
    rets = equity_series(trades, allocation_pct=allocation_pct)
    if len(rets):
        b.sharpe = sharpe(rets)
        b.max_dd_pct = max_drawdown(rets)
        b.allocation_pct = float(allocation_pct)
        b.total_return_pct = float((np.prod(1.0 + rets) - 1.0) * 100.0)

    for t in trades:
        y = t.entry_day[:4]
        b.per_year.setdefault(y, 0.0)
        b.per_symbol_net[t.symbol] = b.per_symbol_net.get(t.symbol, 0.0) + t.net
    for y in list(b.per_year):
        sel = [t.ret for t in trades if t.entry_day[:4] == y]
        b.per_year[y] = float(np.mean(sel)) if sel else 0.0
    return b


def equity_series(trades: Sequence[Trade], *,
                  allocation_pct: float) -> np.ndarray:
    """Portfolio return per session, at a stated allocation per position.

    Built from RETURNS and an allocation fraction rather than from rupees
    against a capital figure. That is not a preference: with lot sizing, one
    NIFTY lot of a 30-day put is about 21,750 of premium, so against a 1 lakh
    capital every position was a 22% bet and the harness reported a -92%
    drawdown that was entirely the mismatch between the sizer and the divisor.

    Sessions with no exit are ZEROS, not gaps. Dropping them inflates the Sharpe
    of anything that trades rarely, which is exactly what this strategy does.
    """
    if not trades:
        return np.array([])
    alloc = max(float(allocation_pct), 0.0) / 100.0
    by_exit: dict[str, float] = {}
    for t in trades:
        by_exit[t.exit_day] = by_exit.get(t.exit_day, 0.0) + t.ret
    span = sorted(by_exit)
    days = _day_span(span[0], span[-1], trades)
    return np.array([by_exit.get(d, 0.0) * alloc for d in days])


def _day_span(first: str, last: str, trades: Sequence[Trade]) -> list[str]:
    """Every session between two days, from the trades' own calendar.

    Built from the tape's days rather than from a date range so market holidays
    are not counted as flat trading days — 250 calendar-derived zeros against
    ~250 real sessions halves a Sharpe for no reason anyone would notice.
    """
    days = sorted({t.entry_day for t in trades} | {t.exit_day for t in trades})
    return [d for d in days if first <= d <= last]


# -------------------------------------------------------------- permutation

def permutation_p(tapes: Mapping[str, Bars], cfg: SnapbackConfig, observed: float,
                  *, rounds: int = 300, seed: int = 5,
                  cost: Optional[CostModel] = None,
                  windows: Optional[Sequence[tuple[str, str]]] = None
                  ) -> Optional[float]:
    """Does the ENTRY DATE matter?

    The null is the same book with the same exposure — same symbols, same number
    of entries per symbol, same holding period, same contract rule, same costs —
    entered on RANDOM sessions. A strategy cannot pass this by having been long
    puts through a falling market, because the null was long puts through the
    same falling market.

    ``windows`` restricts BOTH the observed book's entries and the random ones
    to the same IST day ranges. Without it the first version of this drew its
    null from the whole tape while the observed number came from three
    out-of-sample windows, so the two lived in different markets and the
    p-value was comparing 2024 against 2026. It reported 1.0 for a book whose
    own full-sample p-value is 0.003.

    Compared on the day-clustered mean return, which is the same statistic the
    book reports. Comparing on a different one is how a permutation test ends up
    measuring the sign of net profit with a decimal point on it.

    ``None`` when there is too little to permute. The gate treats a missing
    p-value as a FAILED check, never as a passed one.
    """
    #: Per symbol, the SIDES the real book took, and the bars it could have
    #: taken them on. Keeping the side mix fixed is what makes this a test of the
    #: date and only the date.
    from .regime import gate_for
    gate = gate_for(tapes, market_filter=cfg.market_filter,
                    ema_period=cfg.market_ema)
    sides_by_symbol: dict[str, list[str]] = {}
    pools: dict[str, np.ndarray] = {}
    lo = cfg.warmup_bars()
    for sym, bars in tapes.items():
        n = len(bars)
        eligible = np.zeros(n, dtype=bool)
        eligible[lo:max(n - cfg.hold_days - 2, lo)] = True
        if gate is not None:
            # The null has to be drawn from the sessions the STRATEGY could
            # have traded. Drawing random dates from every session while the
            # real book only trades gated ones compares two different markets
            # and calls the difference timing.
            days_all = np.array([ist_day(float(t)) for t in bars.time])
            eligible &= np.array([bool(gate.get(d, False)) for d in days_all])
        if windows:
            # A bar is eligible when its FILL session lands in the window, which
            # is the same rule ``replay`` applies. Comparing the signal session
            # here and the fill session there would let the null draw from a
            # slightly different set of days than the book it is judging.
            days = np.array([ist_day(float(t)) for t in bars.time])
            fill_day = np.concatenate([days[1:], np.array(["9999-99-99"])])
            inside = np.zeros(n, dtype=bool)
            for start, end in windows:
                inside |= (fill_day >= start) & (fill_day < end)
            eligible &= inside
        taken: list[str] = []
        for side in cfg.sides():
            idx = entry_indices(bars, cfg, side, gate)
            if len(idx):
                taken += [side] * int(eligible[idx].sum())
        if taken and int(eligible.sum()) > len(taken):
            sides_by_symbol[sym] = taken
            pools[sym] = np.flatnonzero(eligible)
    if sum(len(v) for v in sides_by_symbol.values()) < 20:
        return None

    rng = np.random.default_rng(seed)
    hits = 0
    done = 0
    for _ in range(int(rounds)):
        fake: dict[str, list[tuple[int, str]]] = {}
        for sym, sides in sides_by_symbol.items():
            bars_at = rng.choice(pools[sym], size=len(sides), replace=False)
            order = rng.permutation(len(sides))
            fake[sym] = sorted((int(bars_at[j]), sides[order[j]])
                               for j in range(len(sides)))
        _, daily = replay(tapes, cfg, cost=cost, entry_override=fake).by_day()
        done += 1
        if len(daily) and float(daily.mean()) >= observed:
            hits += 1
    if done == 0:
        return None
    return round((hits + 1) / (done + 1), 4)


# --------------------------------------------------------------------- gate

@dataclass(frozen=True)
class Gate:
    """Thresholds a book must clear to be promoted.

    Nothing in this repo's history has cleared a deflated Sharpe of 0.5, and a
    gate that waves through the first thing tried would be worse than no gate.
    """

    min_trades: int = 100
    min_days: int = 60
    min_mean_day_return: float = 0.0
    max_permutation_p: float = 0.05
    min_deflated_sharpe: float = 0.5
    #: The break-even VRP must clear the TOP of the market's band, not the
    #: bottom. Clearing only the cheap end means the edge disappears in exactly
    #: the nervous, bid-up conditions an operator most wants it in.
    min_breakeven_vrp: float = VRP_BAND[1]
    min_years_positive_pct: float = 100.0
    max_drawdown_pct: float = -40.0
    #: The day-clustered confidence interval must exclude zero. This is the
    #: check the measured book currently FAILS, and it is here so that fact is
    #: reported rather than discovered later.
    require_ci_above_zero: bool = True


@dataclass
class Verdict:
    promoted: bool
    checks: dict[str, bool]
    reasons: list[str]

    def as_dict(self) -> dict:
        return {"promoted": self.promoted, "checks": dict(self.checks),
                "reasons": list(self.reasons)}


def judge(book: Book, *, permutation: Optional[float], dsr: float,
          breakeven_vrp: Optional[float], gate: Optional[Gate] = None) -> Verdict:
    g = gate or Gate()
    checks: dict[str, bool] = {}
    reasons: list[str] = []

    checks["enough_trades"] = len(book.trades) >= g.min_trades
    if not checks["enough_trades"]:
        reasons.append(f"{len(book.trades)} trades, needs {g.min_trades}")
    checks["enough_days"] = book.n_days >= g.min_days
    if not checks["enough_days"]:
        reasons.append(f"{book.n_days} distinct entry days, needs {g.min_days} — "
                       f"trades on one day are one bet")

    checks["profitable"] = book.mean_day_return > g.min_mean_day_return
    if not checks["profitable"]:
        reasons.append(f"out-of-sample mean {book.mean_day_return * 100:+.2f}% "
                       f"per entry day")

    checks["beats_random_timing"] = (permutation is not None
                                     and permutation <= g.max_permutation_p)
    if not checks["beats_random_timing"]:
        reasons.append("timing does not beat random entries of identical exposure"
                       if permutation is not None
                       else "too few trades to permute — treated as a failure")

    checks["deflated_sharpe"] = dsr >= g.min_deflated_sharpe
    if not checks["deflated_sharpe"]:
        reasons.append(f"deflated Sharpe {dsr:.3f} < {g.min_deflated_sharpe} — "
                       f"the result does not survive how many variants were tried")

    ok_vrp = (breakeven_vrp is not None and np.isfinite(breakeven_vrp)
              and breakeven_vrp >= g.min_breakeven_vrp)
    checks["priced_edge"] = bool(ok_vrp)
    if not ok_vrp:
        reasons.append(
            f"break-even VRP {breakeven_vrp if breakeven_vrp is not None else float('nan'):.2f}"
            f" < {g.min_breakeven_vrp:.2f} — inside what the market charges for "
            f"the option, so the edge is inside the modelling error")

    years = [v for v in book.per_year.values()]
    pos = 100.0 * (sum(1 for v in years if v > 0) / len(years)) if years else 0.0
    checks["consistent_across_years"] = pos >= g.min_years_positive_pct
    if not checks["consistent_across_years"]:
        reasons.append(f"only {pos:.0f}% of calendar years profitable, "
                       f"needs {g.min_years_positive_pct:.0f}%")

    checks["survivable_drawdown"] = book.max_dd_pct >= g.max_drawdown_pct
    if not checks["survivable_drawdown"]:
        reasons.append(f"max drawdown {book.max_dd_pct:.1f}% is worse than "
                       f"{g.max_drawdown_pct:.1f}%")

    if g.require_ci_above_zero:
        checks["mean_proven"] = book.ci[0] > 0
        if not checks["mean_proven"]:
            reasons.append(
                f"day-clustered 95% interval "
                f"[{book.ci[0] * 100:+.2f}%, {book.ci[1] * 100:+.2f}%] includes "
                f"zero — the DIRECTION is established, the SIZE is not")

    return Verdict(all(checks.values()), checks, reasons)


# ---------------------------------------------------------------- the run

@dataclass
class Report:
    """Three books, because they answer three different questions.

    ``selected``   the walk-forward proper: a configuration chosen on each
                   fold's in-sample window and judged on the window after it.
                   This asks whether PARAMETER SELECTION generalises.
    ``fixed_oos``  the shipped configuration, judged on exactly the same
                   out-of-sample windows with nothing selected. This asks
                   whether the RULE generalises, which is a separate question
                   and the one a fixed default actually faces.
    ``full``       the shipped configuration over the whole tape. Optimistic by
                   construction — the parameters were chosen by looking at it —
                   and reported anyway, because it is the number the research
                   produced and hiding it would make the other two look like
                   the whole story.

    Keeping all three is the point. A single headline here would have to pick
    one, and each of them is the wrong one to pick alone.
    """

    folds: list[dict]
    selected: Book
    fixed_oos: Book
    full: Book
    n_trials: int
    trial_sr_std: float
    deflated_sharpe: float
    #: Permutation restricted to the out-of-sample windows, for ``fixed_oos``.
    permutation_p: Optional[float]
    #: Permutation over the whole tape, for ``full``.
    permutation_p_full: Optional[float]
    breakeven_vrp: Optional[float]
    verdict: Verdict

    def as_dict(self) -> dict:
        return {
            "folds": self.folds,
            "oos": self.fixed_oos.as_dict(),
            "selected": self.selected.as_dict(),
            "full_sample": self.full.as_dict(),
            "n_trials": self.n_trials,
            "trial_sharpe_std": round(self.trial_sr_std, 4),
            "deflated_sharpe": round(self.deflated_sharpe, 4),
            "permutation_p": self.permutation_p,
            "permutation_p_full_sample": self.permutation_p_full,
            "breakeven_vrp": (round(self.breakeven_vrp, 3)
                              if self.breakeven_vrp is not None else None),
            "vrp_band": list(VRP_BAND),
            "verdict": self.verdict.as_dict(),
        }


def candidates(base: SnapbackConfig) -> dict[str, SnapbackConfig]:
    """The grid the selector chooses from, in-sample, each fold.

    Small on purpose. Every extra variant raises the bar the deflated Sharpe
    sets, and a grid wide enough to be sure of finding something is wide enough
    to find it by accident.
    """
    from dataclasses import replace
    out: dict[str, SnapbackConfig] = {}
    for stretch in (1.0, 1.5, 2.0):
        for hold in (5, 10):
            for delta in (0.55, 0.70):
                out[f"s{stretch}_h{hold}_d{delta}"] = replace(
                    base, min_stretch_atr=stretch, hold_days=hold,
                    target_delta=delta)
    return out


def run(tapes: Mapping[str, Bars], base: SnapbackConfig, *,
        is_days: int = 200, oos_days: int = 60, purge_days: Optional[int] = None,
        cost: Optional[CostModel] = None, gate: Optional[Gate] = None,
        permutation_rounds: int = 300) -> Report:
    """Walk forward, and judge the FIXED rule on the out-of-sample windows.

    The default windows are 200 in-sample and 60 out, not 250 and 120. With
    three years of sessions the wider split yields three folds, and a selector
    choosing among twelve variants on three folds is choosing on noise — fold 0
    picked a variant that then returned -35% out of sample on six trades. Eight
    narrower folds use the same tape and give the out-of-sample record enough
    trades to say anything.
    """
    days = calendar(tapes)
    grid = candidates(base)
    purge = purge_days if purge_days is not None else max(
        c.hold_days for c in grid.values()) + 5
    folds = make_folds(days, is_days=is_days, oos_days=oos_days, purge_days=purge)
    if not folds:
        raise ValueError(f"{len(days)} sessions is not enough for one fold "
                         f"({is_days} in-sample + {purge} purge + {oos_days} out)")

    fold_rows: list[dict] = []
    selected_trades: list[Trade] = []
    fixed_trades: list[Trade] = []
    trial_sharpes: list[float] = []
    windows: list[tuple[str, str]] = []
    alloc = base.premium_pct_of_capital

    warm = max(c.warmup_bars() for c in grid.values())
    for fold in folds:
        is_tapes = slice_tapes(tapes, fold.is_start, fold.is_end,
                               warmup_bars=warm)
        scores: dict[str, float] = {}
        best, best_score = None, -math.inf
        for label, cfg in grid.items():
            book = summarise(
                replay(is_tapes, cfg, cost=cost,
                       entry_window=(fold.is_start, fold.is_end)).trades,
                cfg.capital_inr, hold_days=cfg.hold_days, allocation_pct=alloc)
            # Selected on the day-clustered mean, which is the statistic the
            # gate judges. Selecting on one number and judging on another is how
            # a selector ends up optimising something nobody reports.
            score = book.mean_day_return if len(book.trades) >= 10 else -math.inf
            scores[label] = round(score, 5)
            # PER-PERIOD, because that is what the deflation formula wants.
            # ``sharpe()`` returns an annualised figure, and handing the spread
            # of annualised trial Sharpes to a per-period statistic over-deflates
            # by sqrt(250) — which drives the deflated Sharpe to exactly zero for
            # any book at all and looks like a verdict rather than a unit error.
            trial_sharpes.append(book.sharpe / math.sqrt(TRADING_DAYS))
            if score > best_score:
                best, best_score = label, score
        oos_tapes = slice_tapes(tapes, fold.oos_start, fold.oos_end,
                                warmup_bars=warm)
        window = (fold.oos_start, fold.oos_end)
        windows.append(window)

        fixed = replay(oos_tapes, base, cost=cost, entry_window=window)
        fixed_trades.extend(fixed.trades)
        fixed_book = summarise(fixed.trades, base.capital_inr,
                               hold_days=base.hold_days, allocation_pct=alloc)

        sel_book = None
        if best is not None:
            sel = replay(oos_tapes, grid[best], cost=cost, entry_window=window)
            selected_trades.extend(sel.trades)
            sel_book = summarise(sel.trades, grid[best].capital_inr,
                                 hold_days=grid[best].hold_days,
                                 allocation_pct=alloc)
        fold_rows.append({
            "fold": fold.index,
            "in_sample": [fold.is_start, fold.is_end],
            "out_of_sample": [fold.oos_start, fold.oos_end],
            "purge_days": purge,
            "chosen": best,
            "in_sample_scores": scores,
            "oos_selected": sel_book.as_dict() if sel_book else None,
            "oos_fixed": fixed_book.as_dict(),
        })

    selected = summarise(selected_trades, base.capital_inr,
                         hold_days=base.hold_days, allocation_pct=alloc)
    fixed_oos = summarise(fixed_trades, base.capital_inr,
                          hold_days=base.hold_days, allocation_pct=alloc)
    full = summarise(replay(tapes, base, cost=cost).trades, base.capital_inr,
                     hold_days=base.hold_days, allocation_pct=alloc)

    # The deflated Sharpe must deflate the SHARPE — the same allocation-scaled
    # calendar series ``summarise`` reports — and not a per-entry-day return
    # series with a different mean and a different variance. Feeding it the
    # wrong series is a quiet way to deflate the wrong number.
    daily = equity_series(fixed_trades, allocation_pct=alloc)
    n_trials = len(grid) * max(len(fold_rows), 1)
    sr_std = float(np.std(trial_sharpes, ddof=1)) if len(trial_sharpes) > 1 else 0.0
    dsr = deflated_sharpe(daily, n_trials=n_trials, trial_sr_std=sr_std) \
        if len(daily) >= 30 else 0.0

    # The null lives in the SAME windows as the book it is judging.
    p_oos = permutation_p(tapes, base, fixed_oos.mean_day_return,
                          rounds=permutation_rounds, cost=cost, windows=windows)
    p_full = permutation_p(tapes, base, full.mean_day_return,
                           rounds=permutation_rounds, cost=cost)

    from .backtest import returns_at_vrp
    be = break_even_vrp(returns_at_vrp(tapes, base, cost))
    verdict = judge(fixed_oos, permutation=p_oos, dsr=dsr, breakeven_vrp=be,
                    gate=gate)
    return Report(fold_rows, selected, fixed_oos, full, n_trials, sr_std, dsr,
                  p_oos, p_full, be, verdict)


def _daily_from(trades: Sequence[Trade]) -> tuple[np.ndarray, np.ndarray]:
    if not trades:
        return np.array([]), np.array([])
    by: dict[str, list[float]] = {}
    for t in trades:
        by.setdefault(t.entry_day, []).append(t.ret)
    keys = sorted(by)
    return (np.array(keys), np.array([float(np.mean(by[k])) for k in keys]))
