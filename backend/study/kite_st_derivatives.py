"""Derivatives and confluence scan-sources, measured.

These are two of the four shipped `scan_source` values and neither has ever been
measured. `kite_st_best.py` and `kite_st_sizing.py` deliberately covered only the
SPOT path, so this closes the stated gap rather than leaving it as an assertion.

Two measurements, because the honest answer has two halves:

1. STRUCTURAL, and it needs no market data at all. Derivatives-source runs the
   SuperTrends on the CONTRACT'S OWN premium series, and all three lines need
   `cfg.warmup` = 21 bars before any of them is valid. A weekly option does not
   live long enough for that to happen early in its life. Exactly WHEN in a
   contract's life the engine can first speak is arithmetic on the calendar, and
   it decides whether the mode is tradeable at all.

2. EMPIRICAL, on the only real option premium tape in this repository:
   `data/nifty_options/raw.json` — 25 NIFTY contracts, 1-minute bars, four
   sessions into the 2026-09-01 expiry. Small and single-expiry, so it cannot
   settle profitability. It CAN settle the operational question that matters
   more: how often the premium actually confirms the spot signal, which is what
   `confluence` is entirely made of. A mode that confirms almost nothing is a
   no-op regardless of its edge.

What this deliberately does NOT claim: any P&L number for derivatives-source.
Expired option premium is not fetchable from the broker (the instrument dump
carries live contracts only, so an expired token cannot be resolved), which is
why the mode has stayed unmeasured for so long. One expiry of one index over four
days is a demonstration, not evidence.

Run:  python -m study.kite_st_derivatives
"""
from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import datetime

import numpy as np

from app.domain.models import Candle
from app.engines.sterling_kite_engine.config import SterlingKiteEngineConfig
from app.engines.sterling_kite_engine.regime import compute_regime, entry_transitions

RAW = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "nifty_options", "raw.json")
OUT_JSON = os.path.join(os.path.dirname(__file__), "kite_st_derivatives.json")

BARS_PER_SESSION = {"1m": 375, "5m": 75, "15m": 25, "1H": 6}


# ── 1. Structural: when can the engine first speak about a contract? ─────────

def warmup_feasibility(cfg: SterlingKiteEngineConfig) -> list[dict]:
    """Sessions of a contract's life consumed by warmup, per timeframe.

    A NIFTY weekly lists ~5 sessions before expiry in the regime the engine
    trades; a monthly runs far longer. The question is what fraction of the
    contract's TRADEABLE life is spent before the indicators are valid — and,
    since an option's theta accelerates into expiry, whether the surviving window
    is the cheap half of its life or the expensive one.
    """
    out = []
    for tf, per_session in BARS_PER_SESSION.items():
        need = cfg.warmup
        sessions_to_warm = need / per_session
        for label, life in (("weekly (5 sessions)", 5), ("monthly (22 sessions)", 22)):
            usable = max(0.0, life - sessions_to_warm)
            out.append({
                "timeframe": tf, "contract": label,
                "warmup_bars": need,
                "sessions_to_warm": round(sessions_to_warm, 2),
                "tradeable_sessions": round(usable, 2),
                "tradeable_pct": round(100 * usable / life, 1),
                # Where the usable window sits: 1.0 = the whole of it is in the
                # final half of the contract's life.
                "share_in_back_half": round(
                    min(1.0, max(0.0, (life / 2 - sessions_to_warm) / max(usable, 1e-9)))
                    if usable > 0 else 0.0, 2),
            })
    return out


# ── 2. Empirical, on the one real premium tape ──────────────────────────────

def load_premium_tape() -> tuple[dict, list, dict]:
    with open(RAW) as fh:
        raw = json.load(fh)

    def to_candles(rows) -> list[Candle]:
        out = []
        for r in rows:
            ts = int(datetime.fromisoformat(r[0]).timestamp() * 1000)
            out.append(Candle(timestamp_ms=ts, open=float(r[1]), high=float(r[2]),
                              low=float(r[3]), close=float(r[4]),
                              volume=float(r[5]) if len(r) > 5 else 0.0))
        return out

    contracts = {k: to_candles(v) for k, v in raw["options"].items()}
    spot = to_candles(raw["spot_candles"])
    meta = {"expiry": raw["expiry"], "spot": raw["spot"], "atm": raw["atm"]}
    return contracts, spot, meta


def resample(candles: list[Candle], minutes: int) -> list[Candle]:
    """1-minute bars into `minutes`-minute bars, on wall-clock boundaries."""
    buckets: dict[int, list[Candle]] = defaultdict(list)
    step = minutes * 60_000
    for c in candles:
        buckets[c.timestamp_ms // step].append(c)
    out = []
    for k in sorted(buckets):
        grp = buckets[k]
        out.append(Candle(
            timestamp_ms=k * step, open=grp[0].open,
            high=max(g.high for g in grp), low=min(g.low for g in grp),
            close=grp[-1].close, volume=sum(g.volume for g in grp)))
    return out


def transitions(candles: list[Candle], cfg: SterlingKiteEngineConfig):
    o = np.array([c.open for c in candles], float)
    h = np.array([c.high for c in candles], float)
    l = np.array([c.low for c in candles], float)
    c_ = np.array([c.close for c in candles], float)
    r = compute_regime(o, h, l, c_, cfg)
    longs, shorts = entry_transitions(r)
    return r, longs, shorts


def measure_tape(cfg: SterlingKiteEngineConfig) -> dict:
    contracts, spot, meta = load_premium_tape()
    result = {"meta": meta, "n_contracts": len(contracts), "timeframes": []}

    for tf, minutes in (("1m", 1), ("5m", 5), ("15m", 15)):
        spot_bars = resample(spot, minutes) if minutes > 1 else spot
        if len(spot_bars) <= cfg.warmup + 2:
            continue
        _, spot_longs, _ = transitions(spot_bars, cfg)
        spot_entry_idx = set(np.where(spot_longs)[0].tolist())
        spot_times = {spot_bars[i].timestamp_ms for i in spot_entry_idx}

        per_contract, deriv_signals, usable = [], 0, 0
        for name, rows in contracts.items():
            bars = resample(rows, minutes) if minutes > 1 else rows
            if len(bars) <= cfg.warmup + 2:
                per_contract.append({"contract": name, "bars": len(bars), "usable": False})
                continue
            usable += 1
            r, longs, _ = transitions(bars, cfg)
            n = int(longs.sum())
            deriv_signals += n
            # "Running" per the shipped one_red exit: a fresh alignment, then the
            # fast line holding. This is what `is_active` means on a derivatives row.
            fast = r.trend(cfg.trail_target)
            live = np.zeros(len(bars), dtype=bool)
            running = False
            for i in range(len(bars)):
                if longs[i]:
                    running = True
                elif running and int(fast[i]) != 1:
                    running = False
                live[i] = running
            per_contract.append({"contract": name, "bars": len(bars), "usable": True,
                                 "premium_entries": n,
                                 "live_bars_pct": round(100 * float(live.mean()), 1),
                                 "_live_mask": live,
                                 "_times": [b.timestamp_ms for b in bars]})

        # ── confluence: a spot entry that a contract's OWN premium confirms ──
        #
        # The shipped rule is `live = [d for d in drows if d.is_active or
        # d.is_fresh]` — the premium must be CURRENTLY trending up, which is not
        # the same as firing its own entry on that bar. An earlier version of
        # this script required an exact same-bar premium entry and reported zero
        # confirmations at 5m and 15m; that was the measurement being wrong, not
        # the mode. "Running" under the shipped one_red exit means a fresh full
        # alignment happened at some bar j <= t and the fast line has not flipped
        # since.
        confirmed = 0
        spot_entry_sorted = sorted(spot_times)
        confirmations_per_spot = {t: 0 for t in spot_entry_sorted}
        for rec in per_contract:
            if not rec.get("usable"):
                continue
            live_mask = rec.pop("_live_mask")
            times = rec.pop("_times")
            live_at = {times[i] for i in np.where(live_mask)[0].tolist()}
            rec["_live_at"] = live_at
            hits = [t for t in spot_entry_sorted if t in live_at]
            rec["confirms_spot_entries"] = len(hits)
            confirmed += len(hits)
            for t in hits:
                confirmations_per_spot[t] += 1
        spot_with_any = sum(1 for v in confirmations_per_spot.values() if v > 0)

        # ── the PRODUCTION-realistic confirm rate ───────────────────────────
        # The 25-contract number above is not what the engine sees. It resolves
        # `strike_moneyness` — ITM1/ATM/OTM1 by default — so it asks about three
        # strikes on the correct side, not twenty-five. With enough candidates,
        # at least one premium is almost always rising, which would make
        # "confluence confirmed" look like a filter when it is really a coin
        # toss over a wide net.
        atm = meta["atm"]
        step = 50
        want_ce = {f"{atm - step}CE", f"{atm}CE", f"{atm + step}CE"}
        prod_live: dict[int, set] = {}
        for rec in per_contract:
            if not rec.get("usable") or rec["contract"] not in want_ce:
                continue
            prod_live[rec["contract"]] = rec["_live_at"]
        prod_hits = sum(
            1 for t in spot_entry_sorted
            if any(t in live for live in prod_live.values()))
        result_prod = {
            "legs_considered": sorted(prod_live),
            "spot_entries_with_a_confirming_leg": prod_hits,
        }
        result["timeframes"].append({
            "timeframe": tf,
            "spot_bars": len(spot_bars),
            "spot_entries": len(spot_entry_idx),
            "contracts_with_enough_bars": usable,
            "contracts_total": len(contracts),
            "premium_entries_total": deriv_signals,
            "confluence_confirmations": confirmed,
            "spot_entries_with_a_confirming_leg": spot_with_any,
            "mean_confirming_legs_per_spot_entry": round(
                confirmed / max(1, len(spot_entry_sorted)), 2),
            "production_three_legs": result_prod,
            "per_contract": [{k: v for k, v in rec.items() if not k.startswith("_")}
                             for rec in per_contract],
        })
    return result


def main() -> None:
    cfg = SterlingKiteEngineConfig()
    print(f"warmup = {cfg.warmup} bars "
          f"(max of fast {cfg.fast[0]}, mid {cfg.mid[0]}, slow {cfg.slow[0]})\n")

    print("=== 1. Structural: how much of a contract's life is tradeable? ===")
    print("Needs no market data — it is arithmetic on the warmup and the calendar.\n")
    print(f"{'timeframe':<10} {'contract':<22} {'warm(sess)':>11} {'tradeable':>10} "
          f"{'% of life':>10}")
    feas = warmup_feasibility(cfg)
    for r in feas:
        print(f"{r['timeframe']:<10} {r['contract']:<22} {r['sessions_to_warm']:>11.2f} "
              f"{r['tradeable_sessions']:>10.2f} {r['tradeable_pct']:>9.1f}%")

    print("\n=== 2. Empirical, on the only real premium tape in the repo ===")
    if not os.path.isfile(RAW):
        print(f"  {RAW} missing — skipping.")
        return
    res = measure_tape(cfg)
    m = res["meta"]
    print(f"  {res['n_contracts']} NIFTY contracts into the {m['expiry']} expiry, "
          f"spot {m['spot']}, ATM {m['atm']}")
    print(f"\n{'timeframe':<10} {'spot bars':>10} {'spot entries':>13} "
          f"{'premium entries':>16} {'confirmed legs':>15} {'spot entries w/ a leg':>22}")
    for t in res["timeframes"]:
        print(f"{t['timeframe']:<10} {t['spot_bars']:>10} {t['spot_entries']:>13} "
              f"{t['premium_entries_total']:>16} {t['confluence_confirmations']:>15} "
              f"{t['spot_entries_with_a_confirming_leg']:>13} of {t['spot_entries']:<6}")
    print("\n  'confirmed legs' counts (spot entry x contract) pairs where the premium "
          "was live.\n  Confluence emits a row only when at least ONE leg confirms.")
    print(f"\n{'timeframe':<10} {'production legs (ITM1/ATM/OTM1)':<34} "
          f"{'spot entries confirmed':>24}")
    for t in res["timeframes"]:
        pr = t["production_three_legs"]
        print(f"{t['timeframe']:<10} {', '.join(pr['legs_considered']) or '(none resolved)':<34} "
              f"{pr['spot_entries_with_a_confirming_leg']:>15} of {t['spot_entries']:<6}")

    with open(OUT_JSON, "w") as fh:
        json.dump({"warmup": cfg.warmup, "feasibility": feas, "tape": res}, fh, indent=2)
    print(f"\n-> {OUT_JSON}")


if __name__ == "__main__":
    main()
