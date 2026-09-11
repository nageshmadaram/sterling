"""What a set of trades is worth, and how much of it is luck.

Two numbers matter more than the rest here and both are about the SECOND
question, not the first:

* the **deflated Sharpe ratio**, which asks what is left of a result once you
  account for how many variants were tried to find it;
* the **permutation p-value**, which asks whether the entries' TIMING did
  anything a coin flip with identical exposure would not have.

A strategy that passes neither has a backtest, not an edge. This repo has
already shipped one configuration that looked strong on both raw Sharpe and
out-of-sample profit and cleared neither of these.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np

#: NSE trading days in a year. Used only to annualise, never in a test.
TRADING_DAYS = 250


def daily_returns(trades: Sequence, capital: float) -> np.ndarray:
    """Trade P&L collapsed onto a calendar of daily returns.

    Daily rather than per-trade on purpose: a per-trade Sharpe rewards taking
    more trades for the same total profit, which is precisely the wrong
    incentive for a strategy whose costs scale with trade count.

    Days with no trade are ZEROS, not gaps. Dropping them inflates the Sharpe of
    anything that trades rarely, because the flat days it was exposed to nothing
    stop counting against its volatility.
    """
    if not trades or capital <= 0:
        return np.array([])
    by_day: dict[int, float] = {}
    for t in trades:
        d = int(getattr(t, "exit_ms", 0)) // 86_400_000
        by_day[d] = by_day.get(d, 0.0) + float(getattr(t, "net", 0.0))
    if not by_day:
        return np.array([])
    days = sorted(by_day)
    span = range(days[0], days[-1] + 1)
    return np.array([by_day.get(d, 0.0) for d in span]) / capital


def sharpe(rets: np.ndarray, *, min_obs: int = 30) -> float:
    """Annualised Sharpe. Zero when there is not enough to say anything.

    ``min_obs`` is a refusal, not a floor: a Sharpe from twelve days is a number
    with no information in it, and returning it invites someone to compare it
    with one from two years.
    """
    if len(rets) < min_obs:
        return 0.0
    sd = float(rets.std(ddof=1))
    if sd == 0:
        return 0.0
    return float(rets.mean() / sd * math.sqrt(TRADING_DAYS))


def _phi_inv(p: float) -> float:
    """Inverse standard normal CDF, without a scipy dependency in the hot path."""
    from statistics import NormalDist
    return float(NormalDist().inv_cdf(min(max(p, 1e-12), 1 - 1e-12)))


def _phi(z: float) -> float:
    from statistics import NormalDist
    return float(NormalDist().cdf(z))


def expected_max_sharpe(n_trials: int, trial_sr_std: float) -> float:
    """The Sharpe a ZERO-skill search of ``n_trials`` variants would produce.

    Bailey & López de Prado's SR0. The term that matters is
    ``trial_sr_std`` — the spread of Sharpes ACROSS the variants actually tried.
    Approximating it (with 1/sqrt(n), say) is the mistake that makes a deflated
    Sharpe deflate by the wrong amount, and it is measurable here because the
    sweep that produced the trials is the thing calling this.
    """
    n = max(int(n_trials), 1)
    if n <= 1 or trial_sr_std <= 0:
        return 0.0
    e = 0.5772156649015329          # Euler-Mascheroni
    return float(trial_sr_std * ((1 - e) * _phi_inv(1 - 1.0 / n)
                                 + e * _phi_inv(1 - 1.0 / (n * math.e))))


def deflated_sharpe(rets: np.ndarray, *, n_trials: int,
                    trial_sr_std: float) -> float:
    """Probability the true Sharpe exceeds what a zero-skill search would find.

    Per-period throughout — annualising inside the formula is a common and
    quiet error, because the skew and kurtosis terms are per-period quantities
    and mixing the two scales the statistic by sqrt(250).

    The skew/kurtosis correction is not academic for these three: a trend
    follower's returns are rare large wins against frequent small losses, which
    is exactly the shape that makes a naive Sharpe overstate the book.

    Returns 0.0 when there is not enough data to say anything, which is a
    refusal and not a score of zero.
    """
    n = len(rets)
    if n < 30:
        return 0.0
    sd = float(rets.std(ddof=1))
    if sd == 0:
        return 0.0
    sr = float(rets.mean() / sd)                       # per-period
    sr0 = expected_max_sharpe(n_trials, trial_sr_std)  # per-period
    g3 = _skew(rets)
    g4 = _kurtosis(rets)
    denom = 1.0 - g3 * sr + (g4 - 1.0) / 4.0 * sr * sr
    if denom <= 0:
        return 0.0
    z = (sr - sr0) * math.sqrt(n - 1) / math.sqrt(denom)
    return _phi(z)


def _skew(x: np.ndarray) -> float:
    m = x.mean()
    s = x.std(ddof=0)
    return float(((x - m) ** 3).mean() / s ** 3) if s > 0 else 0.0


def _kurtosis(x: np.ndarray) -> float:
    """NON-excess kurtosis, which is what the DSR formula wants.

    A normal distribution gives 3.0 here, not 0.0. Passing excess kurtosis makes
    the correction term negative for well-behaved returns and the statistic
    larger than it should be — deflation that inflates.
    """
    m = x.mean()
    s = x.std(ddof=0)
    return float(((x - m) ** 4).mean() / s ** 4) if s > 0 else 3.0


def max_drawdown(rets: np.ndarray) -> float:
    """Peak-to-trough on the compounded equity curve, as a percentage."""
    if len(rets) == 0:
        return 0.0
    equity = np.cumprod(1.0 + rets)
    peak = np.maximum.accumulate(equity)
    return float(((equity - peak) / peak).min() * 100.0)


def _size_weighted_r(trades: Sequence) -> float:
    """Mean R per UNIT traded, not per trade record.

    A partial exit is half a position, and counting it as a whole one inflates
    the average by exactly the share of trades that scale out.
    """
    units = sum(float(getattr(t, "qty", 1) or 1) for t in trades)
    if units <= 0:
        return 0.0
    return sum(float(getattr(t, "r", 0.0)) * float(getattr(t, "qty", 1) or 1)
               for t in trades) / units


def drawdown_r(trades: Sequence) -> float:
    """Peak-to-trough of the cumulative R curve, in R.

    Free of the capital assumption the percentage drawdown carries: this
    harness places one lot per trade whatever ``capital`` says, so a percentage
    drawdown measures the assumption as much as the strategy.
    """
    if not trades:
        return 0.0
    curve = np.cumsum([float(getattr(t, "r", 0.0)) for t in trades])
    peak = np.maximum.accumulate(curve)
    return float((curve - peak).min())


def profit_factor(trades: Sequence) -> Optional[float]:
    wins = sum(float(t.net) for t in trades if float(t.net) > 0)
    losses = -sum(float(t.net) for t in trades if float(t.net) < 0)
    if losses <= 0:
        return None if wins <= 0 else float("inf")
    return round(wins / losses, 3)


def permutation_p_value(trades: Sequence, tapes: dict, *, costs=None,
                        rounds: int = 1000, seed: int = 7) -> Optional[float]:
    """Does the TIMING beat random entries of identical exposure?

    The null is deliberately not "no position". It is the same trades — same
    symbol, same side, same size, same holding period, same costs — entered at
    RANDOM times in that symbol's own tape. A strategy cannot pass this simply
    by having been long a market that went up, which is how most intraday
    backtests pass.

    Every term has to be in the same units as the observed result, and the
    earlier version of this was not: it summed the observed book in RUPEES
    (quantity-weighted, across every symbol) and drew the null in POINTS at one
    unit on a single symbol's closes. The null could never reach the observed,
    so the p-value was pinned at ``1/(rounds+1)`` for any profitable book and
    ``1.0`` for any losing one — it was reporting the SIGN of net profit with a
    decimal point on it, and it read like significance.

    Compared on GROSS, not net. Costs are identical on both sides by
    construction — the null takes the same number of trades at the same sizes —
    so including them cancels in expectation while making the null's mean
    strongly negative, which turns the test back into a cost test that any
    break-even book passes. Charging them made a book of exactly ZERO profit
    score p = 0.002.

    ``tapes`` is symbol -> Bars. A trade on a symbol with no tape is dropped
    from both sides of the comparison rather than silently scored against
    another instrument's prices.

    ``None`` when there is too little to permute. That is an answer: a p-value
    from nine trades is noise with a decimal point, and the gate treats a
    missing one as a FAILED check rather than a passed one.
    """
    usable = [t for t in trades
              if getattr(t, "symbol", None) in (tapes or {})
              and len(tapes[t.symbol]) > int(getattr(t, "bars_held", 1)) + 2]
    if len(usable) < 20:
        return None
    rng = np.random.default_rng(seed)
    observed = float(sum(float(getattr(t, "gross", t.net)) for t in usable))

    plans = []
    for t in usable:
        closes = np.asarray(tapes[t.symbol].close, dtype=float)
        hold = max(1, int(getattr(t, "bars_held", 1)))
        sign = 1.0 if getattr(t, "thesis", "BULLISH") == "BULLISH" else -1.0
        plans.append((closes, hold, sign, int(getattr(t, "qty", 1) or 1)))

    hits = 0
    for _ in range(rounds):
        total = 0.0
        for closes, hold, sign, qty in plans:
            i = int(rng.integers(0, len(closes) - hold - 1))
            entry, exit_px = float(closes[i]), float(closes[i + hold])
            total += sign * (exit_px - entry) * qty
        if total >= observed:
            hits += 1
    return round((hits + 1) / (rounds + 1), 4)


@dataclass
class Summary:
    trades: int
    wins: int
    win_rate: Optional[float]
    net: float
    gross: float
    costs: float
    #: What share of the gross edge the costs ate. The number that decides
    #: whether a 5-minute strategy is viable at all.
    #:
    #: ``None`` when gross is not positive, and that is the important case: a
    #: ratio against a negative gross reads as "costs destroyed a profit" when
    #: what actually happened is that there was never a profit. ``gross_positive``
    #: is what tells the two apart, and the gate reads THAT.
    cost_share_pct: Optional[float]
    gross_positive: bool
    sharpe: float
    max_drawdown_pct: float
    profit_factor: Optional[float]
    avg_r: float
    expectancy: float
    #: Peak-to-trough of the cumulative R curve, in R.
    #:
    #: Reported beside the percentage because the percentage is against a
    #: capital figure this harness does not size to — one lot per trade whatever
    #: the capital says — so it measures the assumption as much as the strategy.
    #: R drawdown has no such dependency, and the gate reads THIS one.
    max_drawdown_r: float = 0.0

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def summarise(trades: Sequence, capital: float) -> Summary:
    rets = daily_returns(trades, capital)
    net = round(sum(float(t.net) for t in trades), 2)
    gross = round(sum(float(t.gross) for t in trades), 2)
    costs = round(sum(float(t.cost) for t in trades), 2)
    wins = sum(1 for t in trades if float(t.net) > 0)
    n = len(trades)
    return Summary(
        trades=n, wins=wins,
        win_rate=round(wins / n * 100.0, 2) if n else None,
        net=net, gross=gross, costs=costs,
        cost_share_pct=round(costs / gross * 100.0, 1) if gross > 0 else None,
        gross_positive=gross > 0,
        sharpe=round(sharpe(rets), 3),
        max_drawdown_pct=round(max_drawdown(rets), 2),
        profit_factor=profit_factor(trades),
        avg_r=round(_size_weighted_r(trades), 3) if n else 0.0,
        expectancy=round(net / n, 2) if n else 0.0,
        max_drawdown_r=round(drawdown_r(trades), 2))
