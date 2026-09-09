"""Pure Black-Scholes option greeks — self-contained (no scipy, no other-engine
imports). Used by the Kite engine detail panel. Inputs use IV as a decimal
(0.18 = 18%), DTE in calendar days; theta is per-day, vega per 1% vol move.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

_R_DEFAULT = 0.065  # India ~risk-free


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


@dataclass(frozen=True)
class Greeks:
    delta: float
    gamma: float
    theta: float  # per calendar day
    vega: float   # per 1% vol move
    #: False when the model could not be evaluated (expired, no vol, bad inputs)
    #: and `delta` is just the intrinsic sign with the rest zeroed. Without this
    #: flag a "no data" answer is shaped exactly like a very confident one —
    #: delta ±1.00, gamma 0 — and every consumer has to re-invent the `iv > 0`
    #: heuristic to tell them apart. Most did not.
    solved: bool = True


def black_scholes_greeks(
    *, spot: float, strike: float, dte_days: float, iv: float,
    option_type: str, rate: float = _R_DEFAULT,
) -> Greeks:
    """Greeks for a European option. ``option_type`` is "CE"/"call" or "PE"/"put".

    ``dte_days`` is fractional on purpose: an option expiring at 15:30 today has
    hours of life left, and passing a whole-day 0 collapses it into the
    degenerate branch below, which reports delta ±1.00 for something that is
    still trading with real gamma. Callers on the display path must pass the
    intraday fraction (see ``detail.dte_years_fraction``).
    """
    is_call = str(option_type).upper().startswith("C")
    t = max(dte_days, 0.0) / 365.0
    # Degenerate: expired or no vol → delta is the intrinsic sign (or ±0.5 ATM), rest ~0.
    if t <= 0 or iv <= 0 or spot <= 0 or strike <= 0:
        if is_call:
            delta = 1.0 if spot > strike else (0.5 if spot == strike else 0.0)
        else:
            delta = -1.0 if spot < strike else (-0.5 if spot == strike else 0.0)
        return Greeks(delta=delta, gamma=0.0, theta=0.0, vega=0.0, solved=False)

    t = max(t, 1e-5)
    sqrt_t = math.sqrt(t)
    sig_rt = max(iv * sqrt_t, 1e-8)
    d1 = (math.log(spot / strike) + (rate + 0.5 * iv * iv) * t) / sig_rt
    d2 = d1 - sig_rt
    pdf = _norm_pdf(d1)

    delta = _norm_cdf(d1) if is_call else _norm_cdf(d1) - 1.0
    gamma = pdf / (spot * sig_rt)
    vega = spot * pdf * sqrt_t / 100.0
    denom_theta = max(2.0 * sqrt_t, 1e-6)
    if is_call:
        theta = (-spot * pdf * iv / denom_theta
                 - rate * strike * math.exp(-rate * t) * _norm_cdf(d2)) / 365.0
    else:
        theta = (-spot * pdf * iv / denom_theta
                 + rate * strike * math.exp(-rate * t) * _norm_cdf(-d2)) / 365.0
    return Greeks(delta=delta, gamma=gamma, theta=theta, vega=vega)


def premium_stop_from_move(
    *, entry_premium: float, delta: float, spot: float, trail_level: float,
) -> float:
    """Translate an underlying SuperTrend level into an option-premium stop.

    First-order (delta) model of the option premium as a linear function of the
    underlying, anchored at the entry: the premium when the underlying sits at the
    ST ``trail_level`` is ``entry_premium + delta × (trail_level − spot)``, floored
    at zero. ``delta`` is SIGNED (positive for a CE, negative for a PE) and ``spot``
    is the entry underlying, so the formula is correct for both option sides AND in
    both regimes:

      * at ENTRY the trail sits against the position, so the term is negative and the
        stop lands below the entry premium (a real protective stop);
      * as the trade works and the trail RATCHETS toward/through the entry spot, the
        term turns positive and the stop rises above the entry premium — i.e. it
        trails into profit — with no live re-quote needed.

    Single source of truth for the spot→premium stop used by the OTM (spot-signal)
    and deep-ITM auto-exec paths, at entry and on every trailing update. Returns 0.0
    for a degenerate (non-positive) entry premium (caller treats 0 as "no stop").
    """
    if entry_premium <= 0:
        return 0.0
    raw_stop = max(0.0, float(entry_premium) + float(delta) * (float(trail_level) - float(spot)))
    if raw_stop <= 0:
        return 0.0
    # Round to nearest 0.05 tick size (multiple of 5 paise, 2 decimals max)
    return round(round(raw_stop / 0.05) * 0.05, 2)


def bs_price(*, spot: float, strike: float, dte_days: float, iv: float,
             option_type: str, rate: float = _R_DEFAULT) -> float:
    """Black-Scholes option premium."""
    is_call = str(option_type).upper().startswith("C")
    t = max(dte_days, 0.0) / 365.0
    if t <= 0 or iv <= 0 or spot <= 0 or strike <= 0:
        return max(0.0, (spot - strike) if is_call else (strike - spot))
    sig_rt = iv * math.sqrt(t)
    d1 = (math.log(spot / strike) + (rate + 0.5 * iv * iv) * t) / sig_rt
    d2 = d1 - sig_rt
    disc = strike * math.exp(-rate * t)
    if is_call:
        return spot * _norm_cdf(d1) - disc * _norm_cdf(d2)
    return disc * _norm_cdf(-d2) - spot * _norm_cdf(-d1)


def implied_vol(*, price: float, spot: float, strike: float, dte_days: float,
                option_type: str, rate: float = _R_DEFAULT) -> float:
    """Back IV out of a market premium. Newton-Raphson (vega-driven) from a
    Brenner–Subrahmanyam seed, converging in a few iterations on the common path;
    falls back to bisection if a step leaves bounds or vega goes flat. Returns 0.0
    if unsolvable (e.g. price below intrinsic). Lets us show greeks after hours."""
    t = max(dte_days, 0.0) / 365.0
    is_call = str(option_type).upper().startswith("C")
    intrinsic = max(0.0, (spot - strike) if is_call else (strike - spot))
    if price <= 0 or t <= 0 or spot <= 0 or strike <= 0 or price < intrinsic - 1e-6:
        return 0.0

    sqrt_t = math.sqrt(t)
    disc = strike * math.exp(-rate * t)
    log_sk = math.log(spot / strike)
    # Brenner–Subrahmanyam closed-form seed (exact ATM, good elsewhere), clamped.
    iv = min(5.0, max(1e-3, math.sqrt(2.0 * math.pi / t) * price / spot))
    for _ in range(12):
        sig_rt = iv * sqrt_t
        d1 = (log_sk + (rate + 0.5 * iv * iv) * t) / sig_rt
        d2 = d1 - sig_rt
        model = (spot * _norm_cdf(d1) - disc * _norm_cdf(d2)) if is_call \
            else (disc * _norm_cdf(-d2) - spot * _norm_cdf(-d1))
        diff = model - price
        if abs(diff) < 1e-6:
            return iv
        vega = spot * _norm_pdf(d1) * sqrt_t            # d(price)/d(iv), raw
        if vega < 1e-8:
            break                                        # flat — bisect instead
        nxt = iv - diff / vega
        if not (1e-4 < nxt < 5.0) or math.isnan(nxt):
            break                                        # left bounds — bisect instead
        iv = nxt

    # Robust fallback: bracketed bisection always converges.
    lo, hi = 1e-3, 5.0
    price_lo = bs_price(spot=spot, strike=strike, dte_days=dte_days, iv=lo,
                        option_type=option_type, rate=rate)
    price_hi = bs_price(spot=spot, strike=strike, dte_days=dte_days, iv=hi,
                        option_type=option_type, rate=rate)
    if price < price_lo:
        return lo if abs(price - price_lo) < 0.05 else 0.0
    if price > price_hi:
        return 0.0

    for _ in range(64):
        mid = 0.5 * (lo + hi)
        if bs_price(spot=spot, strike=strike, dte_days=dte_days, iv=mid,
                    option_type=option_type, rate=rate) > price:
            hi = mid
        else:
            lo = mid
    return 0.5 * (lo + hi)
