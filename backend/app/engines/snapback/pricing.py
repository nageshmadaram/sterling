"""The option maths this strategy turns on.

Three things live here and nothing else does:

**1. Black-Scholes, so a strike can be chosen by DELTA rather than by steps.**
Delta is the leverage. A fixed "two strikes in the money" is a different delta at
every vol level and every tenor, so a sweep over strike offsets measures the vol
regime and reports it as a strike effect. Every number this engine publishes is
anchored to a target delta instead, which is the quantity an operator actually
cares about: how much of the underlying's move this contract will capture.

**2. An implied-vol proxy, stated as a MULTIPLE of trailing realised vol.**
No store in this repo holds option price history, so a premium has to be
modelled. Modelling it as a level invites the flat-vol trap this codebase has
already paid for once — a far-OTM wing priced at ATM vol looked like a +455%
edge and turned into -79.5% under a realistic smile. Modelling it as a RATIO to
realised vol does not have that failure mode, because the ratio is the quantity
the market actually quotes around: India VIX has run roughly 1.15-1.30x
subsequent realised NIFTY vol, and that band is the toll a buyer pays.

**3. The break-even VRP.** Given a set of outcomes, the ratio at which the trade
stops paying. This is the single number that decides whether a signal is worth
buying premium for: compare it against the 1.15-1.30 the market charges, and the
difference is the margin. A strategy with a break-even of 1.2 has none.

Everything here is pure: arrays in, arrays out, no broker, no clock.
"""
from __future__ import annotations

import math
from typing import Callable, Optional

import numpy as np
from numpy.typing import NDArray

#: NSE trading days in a year. Used to annualise realised vol, never in a test.
TRADING_DAYS = 250.0

#: The risk-free rate Indian option pricing conventionally uses. It moves the
#: delta-targeted strike by a fraction of one tick at these tenors, so it is a
#: constant rather than a setting — a config field nobody can calibrate is a
#: field that will eventually be set wrong.
RISK_FREE = 0.065

#: What India VIX has historically been worth as a multiple of subsequent
#: realised NIFTY vol. The buyer pays this; the seller collects it.
#:
#: Published as a RANGE, and used as a range. A single number here would get
#: quoted back as a measurement, and this one is a market regularity with real
#: dispersion, not a fit.
VRP_BAND: tuple[float, float] = (1.15, 1.30)


try:                                    # pragma: no cover - import shape only
    from scipy.special import ndtr as _ndtr
except Exception:                       # pragma: no cover - fallback path
    _ndtr = None


def _norm_cdf(x: NDArray[np.float64]) -> NDArray[np.float64]:
    """Standard normal CDF.

    scipy's ``ndtr`` when it is there, and an erf fallback when it is not. The
    difference is not cosmetic: a permutation test re-prices the whole book
    three hundred times, and ``np.vectorize(math.erf)`` is a Python loop wearing
    an array's clothes — it made the run slow enough that nobody would finish it.
    """
    a = np.asarray(x, dtype=float)
    if _ndtr is not None:
        return np.asarray(_ndtr(a), dtype=float)
    return 0.5 * (1.0 + np.vectorize(math.erf)(a / math.sqrt(2.0)))


def _norm_ppf(p: float) -> float:
    from statistics import NormalDist
    return float(NormalDist().inv_cdf(min(max(float(p), 1e-9), 1 - 1e-9)))


def realized_vol(close: NDArray[np.float64], window: int) -> NDArray[np.float64]:
    """Annualised close-to-close vol over a trailing window.

    ``out[i]`` uses returns up to and including bar ``i`` and NOTHING after it.
    NaN until the window fills — a partially-filled window returned as a number
    is the quiet way a backtest starts trading on three observations.
    """
    close = np.asarray(close, dtype=float)
    n = len(close)
    out = np.full(n, np.nan)
    if n < window + 1 or window < 2:
        return out
    r = np.diff(np.log(np.maximum(close, 1e-12)))
    c1 = np.concatenate(([0.0], np.cumsum(r)))
    c2 = np.concatenate(([0.0], np.cumsum(r * r)))
    for i in range(window, n):
        a, b = i - window, i
        s1 = c1[b] - c1[a]
        s2 = c2[b] - c2[a]
        var = (s2 - s1 * s1 / window) / (window - 1)
        out[i] = math.sqrt(max(var, 0.0)) * math.sqrt(TRADING_DAYS)
    return out


#: How much steeper the vol is per unit of log-moneyness away from spot.
#:
#: Equity index and single-stock options carry a PUT SKEW: the further
#: out-of-the-money a put, the higher the vol the market charges for it. Pricing
#: every strike at one ATM vol is the "flat-vol trap", and this repository has
#: already shipped a number from it once — a far-OTM wing looked like a +455%
#: edge under flat vol and became -79.5% under a realistic smile.
#:
#: Additive in vol points: ``sigma(K) = sigma_atm + slope * ln(S/K)``. At
#: slope 1.6 a 25-delta 35-day put on a 12-vol index prices about 4 vol points
#: above ATM, which is the right order for NIFTY. Single stocks are steeper.
#:
#: It is a PARAMETER and it must be swept, not assumed. Any result that depends
#: on it is a result about the assumption.
DEFAULT_SMILE_SLOPE = 1.6


def smile_vol(spot, strike, atm_vol, slope: float = DEFAULT_SMILE_SLOPE,
              floor: float = 0.02, itm_slope: Optional[float] = None):
    """The vol a given STRIKE is priced at, once the skew is applied.

    ``slope`` of 0 reproduces the flat-vol model exactly, which is what makes
    the sweep meaningful: the same code path answers both.

    ``itm_slope`` is the steepness on the OTHER side — strikes ABOVE spot, which
    for a put is in the money. A real equity skew is NOT symmetric: it is steep
    below spot and much flatter above, so one slope applied both ways hands an
    in-the-money put a discount the market does not give. This engine buys
    in-the-money puts, so that discount is a flattering assumption and has to be
    a separate, sweepable number. ``None`` means "same as ``slope``".
    """
    S = np.asarray(spot, dtype=float)
    K = np.asarray(strike, dtype=float)
    v = np.asarray(atm_vol, dtype=float)
    if not slope:
        return v
    ln = np.log(np.maximum(S, 1e-9) / np.maximum(K, 1e-9))
    # ln > 0 is a strike BELOW spot (an out-of-the-money put); ln < 0 is above.
    up = float(slope)
    down = float(slope if itm_slope is None else itm_slope)
    out = v + np.where(ln >= 0, up, down) * ln
    # A linear-in-log-moneyness skew is a local approximation. Extrapolated far
    # it produces vols at a fraction of ATM for a deep in-the-money put and
    # multiples of it for a far wing, neither of which any market quotes. Clamp
    # to a band around ATM so a strike choice cannot be rewarded by an
    # unphysical price.
    return np.clip(out, np.maximum(0.5 * v, float(floor)), 2.0 * v)


def implied_vol_proxy(rv: NDArray[np.float64], vrp: float,
                      floor: float = 0.04) -> NDArray[np.float64]:
    """The vol a premium is modelled at: trailing realised, times the toll.

    The floor is not cosmetic. A dead-quiet fortnight can drive a 20-day
    realised vol near zero, and a Black-Scholes price at a vol near zero is a
    pure-intrinsic option that appears to be free — which turns a data artefact
    into an infinite return.
    """
    return np.maximum(np.asarray(rv, dtype=float) * float(vrp), float(floor))


def bs_price(spot, strike, years, sigma, *, call: bool,
             rate: float = RISK_FREE) -> NDArray[np.float64]:
    """Black-Scholes premium. ``years`` and ``sigma`` are both annualised.

    An expired or zero-vol contract returns INTRINSIC value rather than raising.
    That branch is the one that matters at the end of a hold: the alternative is
    a divide-by-zero deep inside a replay, which surfaces as a NaN P&L three
    layers away from its cause.
    """
    S = np.asarray(spot, dtype=float)
    K = np.asarray(strike, dtype=float)
    T = np.asarray(years, dtype=float)
    v = np.asarray(sigma, dtype=float)
    intrinsic = np.maximum(S - K, 0.0) if call else np.maximum(K - S, 0.0)
    live = (T > 1e-9) & (v > 1e-9) & (S > 0) & (K > 0)
    Tc = np.where(live, T, 1e-9)
    vc = np.where(live, v, 1e-9)
    Sc = np.where(S > 0, S, 1e-9)
    Kc = np.where(K > 0, K, 1e-9)
    d1 = (np.log(Sc / Kc) + (rate + 0.5 * vc * vc) * Tc) / (vc * np.sqrt(Tc))
    d2 = d1 - vc * np.sqrt(Tc)
    disc = np.exp(-rate * Tc)
    value = (Sc * _norm_cdf(d1) - Kc * disc * _norm_cdf(d2)) if call else \
            (Kc * disc * _norm_cdf(-d2) - Sc * _norm_cdf(-d1))
    return np.where(live, value, intrinsic)


def bs_delta(spot, strike, years, sigma, *, call: bool,
             rate: float = RISK_FREE) -> NDArray[np.float64]:
    """Signed delta: positive for a call, negative for a put."""
    S = np.asarray(spot, dtype=float)
    K = np.asarray(strike, dtype=float)
    T = np.maximum(np.asarray(years, dtype=float), 1e-9)
    v = np.maximum(np.asarray(sigma, dtype=float), 1e-9)
    d1 = (np.log(np.maximum(S, 1e-9) / np.maximum(K, 1e-9))
          + (rate + 0.5 * v * v) * T) / (v * np.sqrt(T))
    nd1 = _norm_cdf(d1)
    return nd1 if call else nd1 - 1.0


def strike_for_delta(spot, sigma, years, target_delta: float, *, call: bool,
                     step: float, rate: float = RISK_FREE) -> NDArray[np.float64]:
    """The listed strike whose delta is closest to ``target_delta``.

    ``target_delta`` is always the MAGNITUDE, 0 to 1, for both sides — 0.55 means
    a 0.55-delta call or a -0.55-delta put. Asking the caller to remember the
    sign is how a put ladder ends up inverted, and an inverted ladder does not
    error: it quietly buys the opposite end of the chain.

    Inverts the closed form and then rounds to the ladder, rather than searching:
    the ladder step is the only discretisation, and stating it that way makes the
    rounding visible instead of hidden inside a loop's tolerance.
    """
    S = np.asarray(spot, dtype=float)
    v = np.maximum(np.asarray(sigma, dtype=float), 1e-9)
    T = np.maximum(np.asarray(years, dtype=float), 1e-9)
    d = min(max(float(target_delta), 0.01), 0.99)
    d1 = _norm_ppf(d if call else 1.0 - d)
    K = S * np.exp(-(d1 * v * np.sqrt(T) - (rate + 0.5 * v * v) * T))
    step = float(step) if step and step > 0 else 1.0
    return np.maximum(np.round(K / step) * step, step)


def break_even_vrp(returns_at: Callable[[float], NDArray[np.float64]], *,
                   low: float = 0.6, high: float = 3.0,
                   iterations: int = 20) -> float:
    """The VRP multiple at which ``returns_at(vrp).mean()`` crosses zero.

    ``returns_at`` re-prices the whole book at a given multiple; it is a
    callback rather than a table because the entry premium, and therefore the
    quantity, the cost and the return all move together with the vol assumption.
    Scaling a single stored return by a ratio afterwards is wrong, and looks
    right.

    Returns NaN when the book loses even at ``low`` — a trade that cannot pay
    for a nearly-free option has no break-even, and reporting one would suggest
    a threshold exists.
    """
    if float(np.mean(returns_at(low))) < 0:
        return float("nan")
    if float(np.mean(returns_at(high))) > 0:
        return float(high)
    lo, hi = float(low), float(high)
    for _ in range(int(iterations)):
        mid = 0.5 * (lo + hi)
        if float(np.mean(returns_at(mid))) > 0:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def vrp_margin(breakeven: Optional[float]) -> Optional[float]:
    """How much room a break-even has over the TOP of the market's own band.

    Measured against 1.30 rather than 1.15 on purpose. The cheap end of the band
    is the flattering comparison, and a strategy that only clears the cheap end
    is one that stops working in exactly the conditions -- nervous, bid-up
    vol -- where an operator will most want to believe in it.
    """
    if breakeven is None or not np.isfinite(breakeven):
        return None
    return float(breakeven - VRP_BAND[1])
