"""Sizing: the lever, measured — and the two things the engine gets wrong about it.

`kite_st_best.py` established that the SIGNAL has real timing (portfolio p=0.0006
against random entries of identical exposure) and that the short book is worth
nothing. It also showed that the strategy's drawdown is about a fifth of
buy-and-hold's at the same position size. That headroom is the asset. This script
is about spending it.

Four questions, none of which the earlier studies asked:

1. COMPOUNDING. Every study in this repo sizes off a fixed starting capital, so a
   7.5-year result is really "what one constant-size book did". Production sizes
   off `available_capital`, which moves. Those are different strategies and they
   have different drawdowns.
2. THE RISK FRACTION. Production ships `risk_pct = 1.0`. Nothing measured it.
   This prints the whole frontier rather than picking a winner off it — choosing
   the maximum of a curve like this is exactly the overfitting that gave the
   earlier trail-multiplier sweep a NEGATIVE in-sample-to-out-of-sample
   correlation.
3. CONCURRENCY. Four indices that move together can all fire at once. The risk
   budget is per entry, so simultaneous entries stack. What is the real worst
   case, and does margin bind before the risk cap does?
4. SIZING SHAPE. Constant rupee risk via ATR, versus inverse-volatility, versus
   the flat lot the earlier studies assumed.

Run:  python -m study.kite_st_sizing
"""
from __future__ import annotations

import csv
import json
import math
import os
from dataclasses import dataclass

import numpy as np

from app.engines.sterling_kite_engine.config import SterlingKiteEngineConfig
from app.engines.sterling_kite_engine.regime import compute_regime, entry_transitions
from study.kite_st_best import (
    MARGIN_PCT, SLIPPAGE_TICKS, TICK, TRADING_DAYS, _round_trip_costs,
    load_cached, max_drawdown, sharpe,
)

OUT_CSV = os.path.join(os.path.dirname(__file__), "kite_st_sizing_results.csv")
OUT_JSON = os.path.join(os.path.dirname(__file__), "kite_st_sizing.json")

CAPITAL = 10_000_000.0
#: Production ships max_lots = 10 (schemas.py). Kept so the frontier is the one
#: the live engine could actually trade, not an unconstrained one.
MAX_LOTS = 10


@dataclass
class Fill:
    symbol: str
    entry_ms: int
    exit_ms: int
    entry_i: int
    exit_i: int
    entry: float          # after slippage
    exit: float           # after slippage
    lot: int
    stop_dist: float      # underlying points from entry to the initial stop
    atr: float


def long_fills(data: dict, cfg: SterlingKiteEngineConfig) -> list[Fill]:
    """Every long entry/exit the shipped engine produces, WITHOUT a size.

    Sizing is applied afterwards, so every policy below prices the identical set
    of trades. Any difference between them is the sizing, not a different book.
    """
    out: list[Fill] = []
    for sym, a in data.items():
        o, h, l, c, ts, lot = a["o"], a["h"], a["l"], a["c"], a["ts"], a["lot"]
        n = len(c)
        r = compute_regime(o, h, l, c, cfg)
        longs, _ = entry_transitions(r)
        trend = r.trend(cfg.trail_target)
        i = 0
        while i < n:
            if not bool(longs[i]):
                i += 1
                continue
            exit_i = n - 1
            for j in range(i + 1, n):
                if int(trend[j]) != 1:
                    exit_i = j
                    break
            atr = float(r.atr[i]) if r.atr.size > i and not math.isnan(r.atr[i]) else 0.0
            entry_px = float(c[i])
            # The stop the engine would actually be protected by: the trail line
            # at entry. Falling back to 2xATR only when the line is unusable.
            line = float(r.line(cfg.trail_target)[i])
            stop_dist = entry_px - line if 0 < line < entry_px else max(atr * 2.0, entry_px * 0.002)
            out.append(Fill(
                symbol=sym, entry_ms=int(ts[i]), exit_ms=int(ts[exit_i]),
                entry_i=i, exit_i=exit_i,
                entry=entry_px + SLIPPAGE_TICKS * TICK,
                exit=float(c[exit_i]) - SLIPPAGE_TICKS * TICK,
                lot=lot, stop_dist=max(1e-6, stop_dist), atr=atr,
            ))
            i = exit_i + 1
    out.sort(key=lambda f: f.entry_ms)
    return out


# ── Sizing policies ──────────────────────────────────────────────────────────

def size_flat(_f: Fill, _equity: float, _risk_pct: float) -> int:
    """One lot, always — what every earlier study in this repo assumed."""
    return 1


def size_atr_risk(f: Fill, equity: float, risk_pct: float) -> int:
    """Constant rupee risk to the engine's OWN stop.

    Not to an invented 2xATR band: the position is protected by the trail line,
    so that is the distance the risk budget should be measured against.
    """
    budget = equity * risk_pct / 100.0
    risk_per_lot = f.stop_dist * f.lot
    return max(1, int(budget // risk_per_lot)) if risk_per_lot > 0 else 1


def size_inverse_vol(f: Fill, equity: float, risk_pct: float) -> int:
    """Size on volatility alone, with no reference to where the stop sits.

    The comparison that matters: if this matches `size_atr_risk`, the stop
    carries no information the ATR did not already have.
    """
    budget = equity * risk_pct / 100.0
    vol_per_lot = max(1e-6, f.atr * 2.0) * f.lot
    return max(1, int(budget // vol_per_lot)) if vol_per_lot > 0 else 1


POLICIES = {
    "flat_1_lot": size_flat,
    "atr_risk": size_atr_risk,
    "inverse_vol": size_inverse_vol,
}


# ── Book ─────────────────────────────────────────────────────────────────────

def run_book(fills: list[Fill], policy, risk_pct: float, *, compound: bool,
             max_lots: int = MAX_LOTS) -> dict:
    """Walk the fills in time order, sizing each one off the equity of the day.

    Compounding is applied at ENTRY time from realised equity only — an open
    position's paper gain cannot fund the next entry, because it is not money yet.
    """
    equity = CAPITAL
    realised = 0.0
    booked: list[tuple[int, float]] = []
    rows = []
    blocked = 0
    for f in fills:
        base = CAPITAL + realised if compound else CAPITAL
        if base <= 0:
            blocked += 1
            continue
        lots = policy(f, base, risk_pct)
        # Margin affordability, then the production ceiling.
        by_margin = int(base / max(1.0, f.entry * f.lot * MARGIN_PCT))
        if by_margin < 1:
            blocked += 1
            continue
        lots = max(1, min(lots, by_margin, max_lots))
        qty = lots * f.lot
        gross = (f.exit - f.entry) * qty
        net = gross - _round_trip_costs(f.entry, f.exit, qty)
        realised += net
        booked.append((f.exit_ms // 86_400_000, net))
        rows.append((f, lots, net))

    if not booked:
        return {"trades": 0}
    by_day: dict[int, float] = {}
    for d, v in booked:
        by_day[d] = by_day.get(d, 0.0) + v
    span = np.arange(min(by_day), max(by_day) + 1)
    pnl = np.array([by_day.get(int(d), 0.0) for d in span])
    eq = CAPITAL + np.cumsum(pnl)
    # Returns on the equity that was actually at work, so a compounding book is
    # not flattered by dividing late rupees by early capital.
    denom = np.maximum(CAPITAL, np.concatenate([[CAPITAL], eq[:-1]])) if compound else CAPITAL
    rets = pnl / denom
    years = (span[-1] - span[0]) / 365.25
    total = float(pnl.sum())
    net_arr = np.array([r[2] for r in rows])
    wins, losses = net_arr[net_arr > 0], net_arr[net_arr <= 0]
    lots_used = [r[1] for r in rows]
    return {
        "trades": len(rows),
        "net": round(total, 0),
        "ret_pct": round(total / CAPITAL * 100, 1),
        "cagr_pct": round(((1 + total / CAPITAL) ** (1 / max(years, 1e-9)) - 1) * 100, 1)
        if total > -CAPITAL else -100.0,
        "pf": round(float(wins.sum() / abs(losses.sum())), 2) if len(losses) and losses.sum() else 0.0,
        "sharpe": round(sharpe(rets), 2),
        "max_dd_pct": round(max_drawdown(eq), 1),
        "lots_mean": round(float(np.mean(lots_used)), 2),
        "lots_max": int(max(lots_used)),
        "at_ceiling_pct": round(100 * sum(1 for x in lots_used if x >= max_lots) / len(lots_used), 1),
        "blocked": blocked,
    }


def concurrency(fills: list[Fill]) -> dict:
    """How many of the four books are open at the same moment.

    The risk budget is per ENTRY. Four indices that move together can all fire on
    the same bar, and then the book carries four times the intended risk at once.
    """
    events = []
    for f in fills:
        events.append((f.entry_ms, 1))
        events.append((f.exit_ms, -1))
    events.sort()
    cur = peak = 0
    hist: dict[int, int] = {}
    last_ms = None
    for ms, delta in events:
        if last_ms is not None and ms > last_ms:
            hist[cur] = hist.get(cur, 0) + (ms - last_ms)
        cur += delta
        peak = max(peak, cur)
        last_ms = ms
    total = sum(hist.values()) or 1
    return {"peak": peak,
            "time_share_pct": {k: round(100 * v / total, 1) for k, v in sorted(hist.items())}}


def main() -> None:
    data = load_cached()
    if not data:
        print("No cached index data in study/kite_cache — run study.kite_data first.")
        return
    cfg = SterlingKiteEngineConfig()        # shipped: fast trail, one_red, long-only
    fills = long_fills(data, cfg)
    print(f"{len(fills)} long entries across {len(data)} indices "
          f"(shipped config, allow_short={cfg.allow_short})\n")

    # ── 1. Does compounding matter? ─────────────────────────────────────────
    print("=== Compounding, at the shipped risk_pct = 1.0 ===")
    print(f"{'policy':<14} {'mode':<10} {'ret%':>9} {'CAGR%':>7} {'PF':>6} {'Sharpe':>7} "
          f"{'maxDD%':>7} {'lots~':>6} {'@cap%':>6}")
    rows = []
    for name, pol in POLICIES.items():
        for compound in (False, True):
            s = run_book(fills, pol, 1.0, compound=compound)
            rows.append({"policy": name, "compound": compound, "risk_pct": 1.0, **s})
            print(f"{name:<14} {'compound' if compound else 'flat cap':<10} "
                  f"{s['ret_pct']:>9.1f} {s['cagr_pct']:>7.1f} {s['pf']:>6.2f} "
                  f"{s['sharpe']:>7.2f} {s['max_dd_pct']:>7.1f} {s['lots_mean']:>6.2f} "
                  f"{s['at_ceiling_pct']:>6.1f}")

    # ── 2. The risk frontier ────────────────────────────────────────────────
    print("\n=== Risk frontier, atr_risk sizing, compounding ===")
    print("Presented whole. Picking the maximum of this curve is how the earlier")
    print("trail sweep got an IS->OOS correlation of -0.20.\n")
    print(f"{'risk_pct':>9} {'ret%':>10} {'CAGR%':>7} {'Sharpe':>7} {'maxDD%':>8} "
          f"{'ret/DD':>7} {'lots~':>6} {'@cap%':>6}")
    frontier = []
    for rp in (0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 5.0):
        s = run_book(fills, size_atr_risk, rp, compound=True)
        ratio = abs(s["ret_pct"] / s["max_dd_pct"]) if s["max_dd_pct"] else 0.0
        frontier.append({"risk_pct": rp, "ret_dd": round(ratio, 2), **s})
        print(f"{rp:>9.2f} {s['ret_pct']:>10.1f} {s['cagr_pct']:>7.1f} {s['sharpe']:>7.2f} "
              f"{s['max_dd_pct']:>8.1f} {ratio:>7.2f} {s['lots_mean']:>6.2f} "
              f"{s['at_ceiling_pct']:>6.1f}")

    # ── 2b. Is the frontier measuring risk_pct, or the lot ceiling? ─────────
    print("\n=== The same frontier with the max_lots ceiling lifted ===")
    print("At risk_pct 1.0 above, 95% of entries sit ON the 10-lot cap, so the curve")
    print("was largely reporting the ceiling rather than the risk fraction.\n")
    print(f"{'risk_pct':>9} {'ret%':>10} {'CAGR%':>7} {'Sharpe':>7} {'maxDD%':>8} "
          f"{'ret/DD':>7} {'lots~':>7}")
    uncapped = []
    for rp in (0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 5.0):
        s = run_book(fills, size_atr_risk, rp, compound=True, max_lots=10_000)
        ratio = abs(s["ret_pct"] / s["max_dd_pct"]) if s["max_dd_pct"] else 0.0
        uncapped.append({"risk_pct": rp, "ret_dd": round(ratio, 2), **s})
        print(f"{rp:>9.2f} {s['ret_pct']:>10.1f} {s['cagr_pct']:>7.1f} {s['sharpe']:>7.2f} "
              f"{s['max_dd_pct']:>8.1f} {ratio:>7.2f} {s['lots_mean']:>7.1f}")

    # ── 3. Concurrency ──────────────────────────────────────────────────────
    con = concurrency(fills)
    print(f"\n=== Concurrency ===")
    print(f"peak simultaneous positions: {con['peak']} of {len(data)} books")
    print("share of time with N open:", con["time_share_pct"])

    # ── 4. Does the stop carry information the ATR does not? ────────────────
    a = run_book(fills, size_atr_risk, 1.0, compound=True)
    b = run_book(fills, size_inverse_vol, 1.0, compound=True)
    print("\n=== Stop-aware vs volatility-only sizing ===")
    print(f"  atr_risk (sizes to the engine's own trail)  Sharpe {a['sharpe']:.2f}  "
          f"maxDD {a['max_dd_pct']:.1f}%  ret {a['ret_pct']:.1f}%")
    print(f"  inverse_vol (sizes to ATR alone)            Sharpe {b['sharpe']:.2f}  "
          f"maxDD {b['max_dd_pct']:.1f}%  ret {b['ret_pct']:.1f}%")

    # ── 5. Budgeting the PORTFOLIO instead of the entry ─────────────────────
    print("\n=== Per-entry risk vs portfolio risk ===")
    print("All four books are open together a quarter of the time, so a per-entry")
    print("budget is really a 4x budget whenever the indices agree — which is")
    print("exactly when they are most correlated.\n")
    print(f"{'budget':<26} {'ret%':>9} {'Sharpe':>7} {'maxDD%':>8} {'ret/DD':>7}")
    shared = []
    for rp in (0.5, 1.0, 2.0):
        per = run_book(fills, size_atr_risk, rp, compound=True, max_lots=10_000)
        # Divide the same budget across the books, which is what "portfolio risk
        # of rp%" actually means when they can all be on at once.
        pool = run_book(fills, size_atr_risk, rp / len(data), compound=True, max_lots=10_000)
        for label, s in ((f"{rp:.2f}% per entry", per),
                         (f"{rp:.2f}% across {len(data)} books", pool)):
            ratio = abs(s["ret_pct"] / s["max_dd_pct"]) if s["max_dd_pct"] else 0.0
            shared.append({"label": label, "ret_dd": round(ratio, 2), **s})
            print(f"{label:<26} {s['ret_pct']:>9.1f} {s['sharpe']:>7.2f} "
                  f"{s['max_dd_pct']:>8.1f} {ratio:>7.2f}")

    with open(OUT_CSV, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    with open(OUT_JSON, "w") as fh:
        json.dump({"compounding": rows, "frontier": frontier, "frontier_uncapped": uncapped,
                   "portfolio_budget": shared, "concurrency": con,
                   "capital": CAPITAL, "max_lots": MAX_LOTS}, fh, indent=2)
    print(f"\n-> {OUT_CSV}")


if __name__ == "__main__":
    main()
