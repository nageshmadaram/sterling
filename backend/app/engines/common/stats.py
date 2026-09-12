"""What a set of trades is worth, and how much of it is luck.

Two numbers matter more than the rest and both are about the SECOND question:

* the **deflated Sharpe ratio**, which asks what is left of a result once you
  account for how many variants were tried to find it;
* the **maximum drawdown**, which asks what holding it would have felt like.

These were the intraday pack's private module until a second engine needed the
same arithmetic. A second copy of a deflated Sharpe is the worst possible thing
to duplicate: the formula has three easy-to-get-wrong details (per-period rather
than annualised, NON-excess kurtosis, the trial spread measured rather than
approximated) and a copy that gets one of them wrong deflates by the wrong
amount and still returns a plausible number.

``intraday.stats`` re-exports every name here, so both spellings are one
implementation.
"""
from __future__ import annotations

import math
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


