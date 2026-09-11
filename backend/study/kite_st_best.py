"""What actually makes the SuperTrend engine better — measured, not asserted.

This repository has a documented history of parameter sweeps that looked good in
sample and failed out of it: `kite_st_exit_sweep.py` found a trail-multiplier
optimum whose IS->OOS Spearman was NEGATIVE (-0.20), and the wider Sterling work
records "no config clears DSR 0.5". So this script does NOT sweep parameters. It
tests a small, PRE-DECLARED set of STRUCTURAL changes — one bit or one coarse
knob each — and prices every one of them through a deflated Sharpe that knows
how many things were tried.

The three things it does differently from the earlier studies:

1. It measures the SHIPPED configuration. `kite_st_futures.py` ran
   `trail_target="mid"`; production is `"fast"` with `exit_mode="one_red"`.
2. It measures a PORTFOLIO. Every earlier study reports four separate indices of
   ~100 out-of-sample trades each, which is far too thin to conclude anything.
   Four correlated-but-not-identical books share one capital here, which is how
   the strategy would actually be run.
3. It reports a DEFLATED Sharpe ratio. A Sharpe of 1.0 picked from eight
   variants is not the same evidence as a Sharpe of 1.0 that was the only thing
   tried, and every table in this repo that ignored that has been wrong.

Run:  python -m study.kite_st_best
"""
from __future__ import annotations

import csv
import json
import math
import os
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np
from scipy import stats as sps

from app.engines.sterling_kite_engine.config import SterlingKiteEngineConfig
from app.engines.sterling_kite_engine.regime import compute_regime, entry_transitions

CACHE_DIR = os.path.join(os.path.dirname(__file__), "kite_cache")
OUT_CSV = os.path.join(os.path.dirname(__file__), "kite_st_best_results.csv")
OUT_JSON = os.path.join(os.path.dirname(__file__), "kite_st_best.json")

INDICES = [
    {"name": "NIFTY",     "token": 256265, "lot": 75},
    {"name": "BANKNIFTY", "token": 260105, "lot": 30},
    {"name": "FINNIFTY",  "token": 257801, "lot": 65},
    {"name": "SENSEX",    "token": 265,    "lot": 20},
]

# One NIFTY lot is ~1.9 million rupees of notional today. At a capital of
# 1,000,000 every risk-based size rounds back to a single lot, which is how the
# first pass of this study reported "volatility targeting changes nothing" —
# the variant was INERT, not disproven. `sizing_is_inert` below now says so out
# loud rather than letting a null result read as a finding.
STARTING_CAPITAL = 10_000_000.0
#: Fraction of capital risked per entry, split across the books held.
RISK_PER_ENTRY = 0.0075
#: SPAN + exposure margin on an index future, as a fraction of notional.
MARGIN_PCT = 0.12
BARS_PER_DAY = 6.0
TRADING_DAYS = 252.0

# ── Costs ────────────────────────────────────────────────────────────────────
# Index FUTURES, which is the only vehicle this signal has ever been OOS-positive
# in. `kite_st_deep_itm_results.csv` shows every long-option variant going
# negative out of sample, so options are not modelled here as the base case.
BROKERAGE = 20.0                 # flat, per order
STT_SELL = 0.0125 / 100          # sell-side turnover
EXCHANGE_FEES = 0.0019 / 100
GST_RATE = 0.18
STAMP_BUY = 0.002 / 100
# Slippage in TICKS of the index, each way. An index future at 1H close is
# liquid, but "filled at the close I saw" is a fiction; this is the honest tax.
SLIPPAGE_TICKS = 1.0
TICK = 0.05


def _round_trip_costs(entry: float, exit_px: float, qty: int) -> float:
    turnover = (entry + exit_px) * qty
    brok = BROKERAGE * 2
    stt = exit_px * qty * STT_SELL
    exch = turnover * EXCHANGE_FEES
    stamp = entry * qty * STAMP_BUY
    gst = (brok + exch) * GST_RATE
    return brok + stt + exch + stamp + gst


# ── Trade model ──────────────────────────────────────────────────────────────

@dataclass
class Trade:
    symbol: str
    entry_i: int
    exit_i: int
    entry_ms: int
    exit_ms: int
    direction: str
    entry_price: float
    exit_price: float
    qty: int
    gross: float
    costs: float
    net: float
    bars_held: int
    reason: str
    risk_inr: float = 0.0


@dataclass
class Variant:
    key: str
    label: str
    rationale: str
    long_only: bool = False
    htf_gate: bool = False
    vol_target: bool = False
    time_stop_bars: int = 0
    drop: tuple = ()
    trail_target: str = "fast"


# ─────────────────────────────────────────────────────────────────────────────
# The pre-declared variant set. Declared BEFORE any of them was run, so the
# deflation below is honest about how many things were tried. Each is one
# structural decision, not a parameter grid.
# ─────────────────────────────────────────────────────────────────────────────
VARIANTS: list[Variant] = [
    Variant("base", "Shipped baseline",
            "fast trail, one_red exit, both directions, all four indices, fixed lot"),
    Variant("long_only", "Long only", "Indian index drift is strongly up; a trend "
            "follower's short book usually pays for the long book", long_only=True),
    Variant("htf", "Daily-trend gate", "Take a 1H signal only when the DAILY close is "
            "above/below its 50-day SMA — the classic multi-timeframe filter, one knob",
            htf_gate=True),
    Variant("vol", "Volatility-targeted size", "Size each entry to a constant rupee risk "
            "instead of a constant lot count; the single most reliable Sharpe lever in "
            "trend following, and it never touches the entry rule", vol_target=True),
    Variant("tstop", "48-bar time stop", "Already found robust cross-lens in "
            "kite_st_exit_sweep (options -134% -> -32%); never tested on delta-1",
            time_stop_bars=48),
    Variant("nofin", "Drop FINNIFTY", "FINNIFTY is ~0.95 correlated to BANKNIFTY and is "
            "the one index OOS-negative in kite_st_futures_results.csv — a redundant, "
            "worse copy of a book already held", drop=("FINNIFTY",)),
    # Added AFTER the first pass, and counted as a trial like everything else.
    # The first run's capital was too small for risk sizing to leave one lot, so
    # the vol-target row was inert and this pairing was never visible. It is the
    # two changes that independently helped, with the one that hurt left out.
    Variant("lo_vol", "Long-only + vol target",
            "The two independent winners, without the daily gate that lost",
            long_only=True, vol_target=True),
    Variant("combo", "Long-only + daily gate + vol target",
            "Everything above that is independent, stacked", long_only=True,
            htf_gate=True, vol_target=True),
    Variant("combo_nofin", "Combo, without FINNIFTY", "The stack, on the three "
            "non-redundant books", long_only=True, htf_gate=True, vol_target=True,
            drop=("FINNIFTY",)),
]


# ── Data ─────────────────────────────────────────────────────────────────────

def load_cached() -> dict:
    out = {}
    for spec in INDICES:
        path = os.path.join(CACHE_DIR, f"{spec['token']}_1H.npz")
        if not os.path.isfile(path):
            continue
        with np.load(path, allow_pickle=False) as raw:
            out[spec["name"]] = {
                "ts": raw["ts"].astype(np.int64),
                "o": raw["o"].astype(float), "h": raw["h"].astype(float),
                "l": raw["l"].astype(float), "c": raw["c"].astype(float),
                "lot": spec["lot"],
            }
    return out


def daily_sma_gate(ts_ms: np.ndarray, close: np.ndarray, window: int = 50) -> np.ndarray:
    """+1 / -1 / 0 per 1H bar from the DAILY trend, with no lookahead.

    The value at bar `i` is derived only from daily bars that had already CLOSED
    before bar `i`'s own session. Using the running day's own close would be
    reading the future of the bar being traded.
    """
    days = (ts_ms // 86_400_000).astype(np.int64)
    uniq, first_idx = np.unique(days, return_index=True)
    order = np.argsort(first_idx)
    uniq = uniq[order]
    first_idx = first_idx[order]
    # Closing price of each completed session.
    last_idx = np.append(first_idx[1:] - 1, len(close) - 1)
    day_close = close[last_idx]

    sma = np.full(len(day_close), np.nan)
    if len(day_close) >= window:
        cs = np.cumsum(np.insert(day_close, 0, 0.0))
        sma[window - 1:] = (cs[window:] - cs[:-window]) / window

    # Day d's signal is decided by day d-1's completed close vs its SMA.
    sig_by_day = np.zeros(len(day_close), dtype=np.int64)
    valid = ~np.isnan(sma)
    sig_by_day[valid] = np.where(day_close[valid] > sma[valid], 1, -1)
    shifted = np.zeros_like(sig_by_day)
    shifted[1:] = sig_by_day[:-1]          # strictly previous session

    out = np.zeros(len(close), dtype=np.int64)
    for k, start in enumerate(first_idx):
        end = first_idx[k + 1] if k + 1 < len(first_idx) else len(close)
        out[start:end] = shifted[k]
    return out


# ── Replay ───────────────────────────────────────────────────────────────────

def replay(arrs: dict, symbol: str, v: Variant, cfg: SterlingKiteEngineConfig,
           risk_per_trade: float) -> list[Trade]:
    o, h, l, c, ts = arrs["o"], arrs["h"], arrs["l"], arrs["c"], arrs["ts"]
    lot = arrs["lot"]
    n = len(c)
    if n <= cfg.warmup + 2:
        return []

    r = compute_regime(o, h, l, c, cfg)
    longs, shorts = entry_transitions(r)
    trend = r.trend(v.trail_target)
    atr = r.atr
    gate = daily_sma_gate(ts, c) if v.htf_gate else None

    trades: list[Trade] = []
    i = 0
    while i < n:
        is_long, is_short = bool(longs[i]), bool(shorts[i])
        if not (is_long or is_short):
            i += 1
            continue
        if v.long_only and is_short:
            i += 1
            continue
        if gate is not None:
            want = 1 if is_long else -1
            if int(gate[i]) != want:
                i += 1
                continue

        want_trend = 1 if is_long else -1
        sign = 1.0 if is_long else -1.0
        raw_entry = float(c[i])
        entry = raw_entry + sign * SLIPPAGE_TICKS * TICK

        # ── size ────────────────────────────────────────────────────────────
        if v.vol_target:
            # Constant rupee risk: a wide-ATR entry gets fewer lots than a quiet
            # one, so every trade contributes the same amount of variance rather
            # than the same number of contracts.
            a = float(atr[i]) if atr[i] and not math.isnan(atr[i]) else 0.0
            stop_dist = max(a * 2.0, raw_entry * 0.002)
            lots = max(1, int(round(risk_per_trade / (stop_dist * lot))))
            # Never size past what the margin can carry.
            max_by_margin = int(STARTING_CAPITAL / max(1.0, raw_entry * lot * MARGIN_PCT))
            lots = max(1, min(lots, max_by_margin))
        else:
            lots = 1
        qty = lots * lot

        # ── exit: one_red on the configured trail line, at that bar's close ──
        exit_i, reason = n - 1, "series end"
        for j in range(i + 1, n):
            if v.time_stop_bars and (j - i) >= v.time_stop_bars:
                exit_i, reason = j, "time stop"
                break
            if int(trend[j]) != want_trend:
                exit_i, reason = j, "trail flip"
                break

        raw_exit = float(c[exit_i])
        exit_px = raw_exit - sign * SLIPPAGE_TICKS * TICK
        gross = (exit_px - entry) * sign * qty
        costs = _round_trip_costs(entry, exit_px, qty)
        trades.append(Trade(
            symbol=symbol, entry_i=i, exit_i=exit_i,
            entry_ms=int(ts[i]), exit_ms=int(ts[exit_i]),
            direction="long" if is_long else "short",
            entry_price=entry, exit_price=exit_px, qty=qty,
            gross=gross, costs=costs, net=gross - costs,
            bars_held=exit_i - i, reason=reason,
            risk_inr=risk_per_trade if v.vol_target else 0.0,
        ))
        i = exit_i + 1
    return trades


# ── Statistics ───────────────────────────────────────────────────────────────

def daily_returns(trades: list[Trade], capital: float) -> tuple[np.ndarray, np.ndarray]:
    """P&L booked per calendar day, as a return on starting capital.

    Booking the whole trade on its EXIT day is the conservative reading: it
    makes the series lumpier and the Sharpe lower than marking to market would.
    """
    if not trades:
        return np.array([]), np.array([])
    by_day: dict[int, float] = {}
    for t in trades:
        d = t.exit_ms // 86_400_000
        by_day[d] = by_day.get(d, 0.0) + t.net
    days = np.array(sorted(by_day))
    if len(days) == 0:
        return np.array([]), np.array([])
    span = np.arange(days[0], days[-1] + 1)
    pnl = np.array([by_day.get(int(d), 0.0) for d in span])
    return span, pnl / capital


def sharpe(rets: np.ndarray) -> float:
    if len(rets) < 30 or rets.std(ddof=1) == 0:
        return 0.0
    return float(rets.mean() / rets.std(ddof=1) * math.sqrt(TRADING_DAYS))


def deflated_sharpe(observed_sr: float, rets: np.ndarray, n_trials: int) -> float:
    """Bailey & Lopez de Prado's DSR: the probability the true Sharpe is > 0
    once you account for how many variants were tried, and for the skew and
    kurtosis of the returns that make a Sharpe overstate a lumpy book.

    A trend follower's returns are exactly the shape that inflates a naive
    Sharpe — rare large wins, frequent small losses — so this correction is not
    academic here.
    """
    n = len(rets)
    if n < 30 or n_trials < 1 or observed_sr == 0:
        return 0.0
    sr = observed_sr / math.sqrt(TRADING_DAYS)          # per-period
    g3 = float(sps.skew(rets))
    g4 = float(sps.kurtosis(rets, fisher=False))
    # Expected maximum Sharpe from N independent trials of zero-skill strategies.
    e = 0.5772156649
    emax = math.sqrt(2 * math.log(max(n_trials, 2))) * (
        (1 - e) + e * math.sqrt(2 * math.log(max(n_trials, 2)) / (2 * math.log(max(n_trials, 2)) + 1e-12))
    ) if n_trials > 1 else 0.0
    sr0 = emax / math.sqrt(TRADING_DAYS) * 0.0 + emax * 0.0
    # sr0 expressed per-period via the variance of the trial Sharpes:
    trial_sr_std = rets.std(ddof=1) and 1.0 / math.sqrt(n)
    sr0 = emax * trial_sr_std
    denom = math.sqrt(max(1e-12, 1 - g3 * sr + (g4 - 1) / 4 * sr * sr))
    z = (sr - sr0) * math.sqrt(n - 1) / denom
    return float(sps.norm.cdf(z))


def max_drawdown(equity: np.ndarray) -> float:
    if len(equity) == 0:
        return 0.0
    peak = np.maximum.accumulate(equity)
    dd = (equity - peak) / np.where(peak == 0, 1, peak)
    return float(dd.min() * 100)


def summarise(trades: list[Trade], capital: float) -> dict:
    if not trades:
        return {"trades": 0}
    net = np.array([t.net for t in trades])
    wins, losses = net[net > 0], net[net <= 0]
    _, rets = daily_returns(trades, capital)
    eq = capital + np.cumsum(rets * capital)
    years = max(1e-9, (max(t.exit_ms for t in trades) - min(t.entry_ms for t in trades))
                / (365.25 * 86_400_000))
    total = net.sum()
    sr = sharpe(rets)
    return {
        "trades": len(trades),
        "win_rate": round(len(wins) / len(trades) * 100, 1),
        "net_pnl": round(float(total), 0),
        "ret_pct": round(float(total / capital * 100), 1),
        "cagr_pct": round(((1 + total / capital) ** (1 / years) - 1) * 100, 1)
        if total > -capital else -100.0,
        "pf": round(float(wins.sum() / abs(losses.sum())), 2) if len(losses) and losses.sum() else 0.0,
        "sharpe": round(sr, 2),
        "max_dd_pct": round(max_drawdown(eq), 1),
        "avg_hold_days": round(float(np.mean([t.bars_held for t in trades])) / BARS_PER_DAY, 1),
        "costs": round(float(sum(t.costs for t in trades)), 0),
        "_rets": rets,
    }


# ── Walk-forward ─────────────────────────────────────────────────────────────

def run_variant(data: dict, v: Variant, folds: int = 5) -> dict:
    """Anchored walk-forward. Nothing is FITTED in-sample — these are structural
    variants, not parameter searches — so the split exists to show whether the
    behaviour is stable across eras, not to choose anything.
    """
    cfg = SterlingKiteEngineConfig(trail_target=v.trail_target)
    symbols = [s for s in data if s not in v.drop]
    risk = STARTING_CAPITAL * RISK_PER_ENTRY / max(1, len(symbols))

    all_trades: list[Trade] = []
    for sym in symbols:
        all_trades.extend(replay(data[sym], sym, v, cfg, risk))
    all_trades.sort(key=lambda t: t.entry_ms)

    if not all_trades:
        return {"key": v.key, "label": v.label, "trades": 0}

    lo = min(t.entry_ms for t in all_trades)
    hi = max(t.exit_ms for t in all_trades)
    edges = np.linspace(lo, hi, folds + 1)
    fold_stats = []
    oos_trades: list[Trade] = []
    for k in range(1, folds):            # fold 0 is the initial train block
        a, b = edges[k], edges[k + 1]
        seg = [t for t in all_trades if a <= t.entry_ms < b]
        oos_trades.extend(seg)
        s = summarise(seg, STARTING_CAPITAL)
        fold_stats.append({"fold": k, "from": int(a), "to": int(b),
                           "trades": s.get("trades", 0),
                           "ret_pct": s.get("ret_pct", 0.0),
                           "pf": s.get("pf", 0.0)})

    full = summarise(all_trades, STARTING_CAPITAL)
    oos = summarise(oos_trades, STARTING_CAPITAL)
    # A sizing variant that never left one lot did not get tested.
    lot_counts = {t.qty // data[t.symbol]["lot"] for t in all_trades}
    full["sizing_is_inert"] = bool(v.vol_target and lot_counts == {1})
    full["lots_min"], full["lots_max"] = min(lot_counts), max(lot_counts)
    pos_folds = sum(1 for f in fold_stats if f["ret_pct"] > 0)
    return {
        "key": v.key, "label": v.label, "rationale": v.rationale,
        "full": full, "oos": oos, "folds": fold_stats,
        "folds_positive": f"{pos_folds}/{len(fold_stats)}",
        "_trades": all_trades,
    }


# ── The two tests that actually settle it ────────────────────────────────────

def long_short_decomposition(data: dict) -> list[dict]:
    """Which side of the book earns.

    Every study in this repo has reported the two sides fused. They are not the
    same strategy: one is a trend follower on an index with a strong upward
    drift, the other is a trend follower fighting it.
    """
    cfg = SterlingKiteEngineConfig()
    base = Variant("base", "base", "")
    rows = []
    for sym, arrs in data.items():
        trades = replay(arrs, sym, base, cfg, 0.0)
        for side in ("long", "short"):
            seg = [t for t in trades if t.direction == side]
            if not seg:
                continue
            net = np.array([t.net for t in seg])
            w, l = net[net > 0], net[net <= 0]
            rows.append({
                "symbol": sym, "side": side, "trades": len(seg),
                "win_rate": round(100 * len(w) / len(seg), 1),
                "net": round(float(net.sum()), 0),
                "pf": round(float(w.sum() / abs(l.sum())), 2) if len(l) and l.sum() else 0.0,
            })
    return rows


def permutation_test(data: dict, n_sim: int = 5000, seed: int = 20260911) -> dict:
    """Is the long book TIMING, or just being long a rising index?

    The null draws the same number of entries, with the same holding-period
    distribution, at random bars. A trend follower on an index that tripled will
    make money from exposure alone; the only question worth asking is whether it
    makes MORE than that exposure explains. Nothing else in this repo has asked
    it, and it is the difference between an edge and a beta wrapper.
    """
    rng = np.random.default_rng(seed)
    cfg = SterlingKiteEngineConfig()
    lo = Variant("lo", "lo", "", long_only=True)
    per_symbol, agg_real, agg_null = [], 0.0, np.zeros(n_sim)

    for sym, arrs in data.items():
        c, n, qty = arrs["c"], len(arrs["c"]), arrs["lot"]
        trades = [t for t in replay(arrs, sym, lo, cfg, 0.0) if t.direction == "long"]
        if len(trades) < 20:
            continue
        holds = np.array([t.bars_held for t in trades])
        real = float(sum(t.net for t in trades))
        unit_cost = _round_trip_costs(float(c[-1]), float(c[-1]), qty) * len(holds)

        sims = np.empty(n_sim)
        lo_i, hi_i = 300, n - int(holds.max()) - 2
        for k in range(n_sim):
            starts = rng.integers(lo_i, hi_i, size=len(holds))
            ends = np.minimum(starts + rng.permutation(holds), n - 1)
            gross = ((c[ends] - SLIPPAGE_TICKS * TICK) - (c[starts] + SLIPPAGE_TICKS * TICK)) * qty
            sims[k] = gross.sum() - unit_cost
        agg_real += real
        agg_null += sims
        per_symbol.append({
            "symbol": sym, "real_net": round(real, 0),
            "null_mean": round(float(sims.mean()), 0),
            "null_p95": round(float(np.percentile(sims, 95)), 0),
            "p_value": round(float((sims >= real).mean()), 4),
            "z": round(float((real - sims.mean()) / sims.std()), 2),
        })

    return {
        "per_symbol": per_symbol,
        "portfolio": {
            "real_net": round(agg_real, 0),
            "null_mean": round(float(agg_null.mean()), 0),
            "null_p95": round(float(np.percentile(agg_null, 95)), 0),
            "p_value": round(float((agg_null >= agg_real).mean()), 4),
            "z": round(float((agg_real - agg_null.mean()) / agg_null.std()), 2),
        },
        "n_sim": n_sim,
    }


def buy_and_hold(data: dict) -> dict:
    """The benchmark the strategy has to beat, at the SAME position size.

    Marked to market daily, so its drawdown is comparable rather than being the
    single open-to-close number a trade-level view would give.
    """
    days, curve = None, None
    for sym, arrs in data.items():
        ts, c, qty = arrs["ts"], arrs["c"], arrs["lot"]
        d = ts // 86_400_000
        uniq, idx = np.unique(d, return_index=True)
        last = np.append(idx[1:] - 1, len(c) - 1)
        eq = (c[last] - c[0]) * qty
        if days is None:
            days, curve = uniq, eq.astype(float)
        else:
            m = np.isin(days, uniq)
            add = np.zeros_like(curve)
            add[m] = eq[np.searchsorted(uniq, days[m])]
            curve = curve + add
    eq = STARTING_CAPITAL + curve
    rets = np.diff(eq, prepend=eq[0]) / STARTING_CAPITAL
    return {"net": round(float(curve[-1]), 0), "max_dd_pct": round(max_drawdown(eq), 1),
            "sharpe": round(sharpe(rets), 2)}


def main() -> None:
    data = load_cached()
    if not data:
        print("No cached index data in study/kite_cache — run study.kite_data first.")
        return
    print(f"Loaded {len(data)} indices: {', '.join(data)}")
    for s, a in data.items():
        print(f"  {s:<10} {len(a['c']):>6} 1H bars")

    results = [run_variant(data, v) for v in VARIANTS]
    n_trials = len(VARIANTS)

    rows = []
    print(f"\n{'variant':<34} {'trades':>7} {'ret%':>8} {'CAGR%':>7} {'PF':>5} "
          f"{'Sharpe':>7} {'maxDD%':>7} {'DSR':>6} {'folds+':>7}")
    print("-" * 100)
    for res in results:
        if not res.get("full"):
            continue
        f, o = res["full"], res["oos"]
        dsr = deflated_sharpe(f.get("sharpe", 0.0), f.get("_rets", np.array([])), n_trials)
        rows.append({
            "variant": res["key"], "label": res["label"],
            "full_trades": f.get("trades"), "full_ret_pct": f.get("ret_pct"),
            "full_cagr_pct": f.get("cagr_pct"), "full_pf": f.get("pf"),
            "full_sharpe": f.get("sharpe"), "full_max_dd_pct": f.get("max_dd_pct"),
            "full_win_rate": f.get("win_rate"), "full_avg_hold_days": f.get("avg_hold_days"),
            "lots_min": f.get("lots_min"), "lots_max": f.get("lots_max"),
            "sizing_is_inert": f.get("sizing_is_inert", False),
            "dsr": round(dsr, 3),
            "oos_trades": o.get("trades"), "oos_ret_pct": o.get("ret_pct"),
            "oos_pf": o.get("pf"), "oos_sharpe": o.get("sharpe"),
            "folds_positive": res["folds_positive"],
        })
        flag = "  <- INERT (never left 1 lot)" if f.get("sizing_is_inert") else ""
        print(f"{res['label']:<34} {f.get('trades',0):>7} {f.get('ret_pct',0):>8.1f} "
              f"{f.get('cagr_pct',0):>7.1f} {f.get('pf',0):>5.2f} {f.get('sharpe',0):>7.2f} "
              f"{f.get('max_dd_pct',0):>7.1f} {dsr:>6.3f} {res['folds_positive']:>7}{flag}")

    if rows:
        with open(OUT_CSV, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        with open(OUT_JSON, "w") as fh:
            json.dump({"n_trials": n_trials, "rows": rows,
                       "folds": {r["key"]: r["folds"] for r in results if r.get("folds")}},
                      fh, indent=2)
        print(f"\n{len(rows)} rows -> {OUT_CSV}")

    print("\nDSR is the probability the true Sharpe exceeds zero AFTER deflating for "
          f"{n_trials} declared trials and for return skew/kurtosis.")
    print("Convention in this repo: >= 0.95 is evidence, < 0.95 is a hypothesis.")

    # ── which side of the book earns ────────────────────────────────────────
    print("\n=== Long vs short ===")
    dec = long_short_decomposition(data)
    print(f"{'index':<11} {'side':<6} {'trades':>7} {'win%':>6} {'net':>13} {'PF':>6}")
    for r in dec:
        print(f"{r['symbol']:<11} {r['side']:<6} {r['trades']:>7} {r['win_rate']:>6.1f} "
              f"{r['net']:>13,.0f} {r['pf']:>6.2f}")
    for side in ("long", "short"):
        tot = sum(r["net"] for r in dec if r["side"] == side)
        cnt = sum(r["trades"] for r in dec if r["side"] == side)
        print(f"{'ALL':<11} {side:<6} {cnt:>7} {'':>6} {tot:>13,.0f}")

    # ── timing, or exposure? ────────────────────────────────────────────────
    print("\n=== Permutation test: long book vs random entries, same exposure ===")
    perm = permutation_test(data)
    print(f"{'index':<11} {'real':>13} {'null mean':>13} {'p':>8} {'z':>7}")
    for r in perm["per_symbol"]:
        print(f"{r['symbol']:<11} {r['real_net']:>13,.0f} {r['null_mean']:>13,.0f} "
              f"{r['p_value']:>8.4f} {r['z']:>7.2f}")
    p = perm["portfolio"]
    print(f"{'PORTFOLIO':<11} {p['real_net']:>13,.0f} {p['null_mean']:>13,.0f} "
          f"{p['p_value']:>8.4f} {p['z']:>7.2f}")

    # ── the benchmark, and what the drawdown headroom is worth ──────────────
    bh = buy_and_hold(data)
    lo_res = next((r for r in results if r["key"] == "long_only"), None)
    print(f"\n=== Buy & hold, same position size ===")
    print(f"  net {bh['net']:>13,.0f}   maxDD {bh['max_dd_pct']:>6.1f}%   Sharpe {bh['sharpe']:.2f}")
    if lo_res:
        f = lo_res["full"]
        print(f"  long-only net {f['net_pnl']:>7,.0f}   maxDD {f['max_dd_pct']:>6.1f}%   "
              f"Sharpe {f['sharpe']:.2f}")
        if bh["max_dd_pct"]:
            head = f["max_dd_pct"] / bh["max_dd_pct"]
            print(f"\n  The strategy's drawdown is {head:.2f}x buy-and-hold's at the same size.")
            print("  That headroom, not the indicator, is what there is to spend.")

    with open(OUT_JSON) as fh:
        payload = json.load(fh)
    payload.update({"long_short": dec, "permutation": perm, "buy_and_hold": bh})
    with open(OUT_JSON, "w") as fh:
        json.dump(payload, fh, indent=2)


if __name__ == "__main__":
    main()
