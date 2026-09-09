"""Run the shipped engine over the calibration data. End-to-end proof.

Does not silently widen the DTE window. The 26 Aug sample sits at DTE 34–103,
outside the shipped 0–14 rule; that run is labelled ``window=out``.
"""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from dataclasses import replace

from app.engines.gamma_move import (Candle, GammaMoveConfig, InstrumentRef, OICandle,
                                    StrikeCandidate, days_to_expiry, expiry_in_window,
                                    find_levels, live_levels, regime_of, replay_contract,
                                    summarise)

OUT = Path(__file__).parent / "out"
IST = timezone(timedelta(hours=5, minutes=30))


def ms(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp() * 1000)


def window_label(expiry: str, today: date, cfg: GammaMoveConfig) -> str:
    return "in" if expiry_in_window(expiry, today, cfg) else "out"


def labelled_replay_config(shipped: GammaMoveConfig,
                           dtes: Sequence[int]) -> tuple[GammaMoveConfig, list[str]]:
    """Research copy of the shipped config, with every widening labelled.

    The 26 Aug candidates.json is a top-3 snapshot inside ±8% of spot, not
    as-of full-chain OI. Attaching that max to every historical bar would
    filter the past with future, partial-chain open interest. The wall gate
    stays off until a per-bar chain exists.
    """
    notes: list[str] = []
    cfg = shipped
    out_of_window = bool(dtes) and any(
        not (shipped.expiry_dte_min <= d <= shipped.expiry_dte_max)
        or (shipped.avoid_expiry_day and d == 0) for d in dtes)
    if out_of_window:
        widened = max(shipped.expiry_dte_max, max(dtes))
        cfg = replace(cfg, expiry_dte_max=widened)
        notes.append(
            f"window=out sample DTE {min(dtes)}-{max(dtes)}; "
            f"shipped expiry_dte_max={shipped.expiry_dte_max}; "
            f"replay widened to {widened} and labelled — not a production-window result")
    else:
        notes.append(f"window=in shipped expiry_dte_max={shipped.expiry_dte_max}")
    if cfg.require_chain_max_oi:
        cfg = replace(cfg, require_chain_max_oi=False)
    notes.append(
        "wall_gate=skipped — no as-of full-chain OI; candidates.json is a "
        "top-3 snapshot, not a per-bar chain")
    return cfg, notes


def _bar_day(iso: str) -> date:
    return datetime.fromisoformat(str(iso)).astimezone(IST).date()


def main() -> None:
    opt = json.loads((OUT / "bars.json").read_text())
    spot = json.loads((OUT / "spot_daily.json").read_text())

    shipped = GammaMoveConfig(enabled=True, max_premium_at_risk_inr=200_000,
                              capital_inr=2_000_000)
    dtes: list[int] = []
    for rec in opt.values():
        meta = rec.get("meta") or {}
        bars = rec.get("bars") or []
        if not bars:
            continue
        dte = days_to_expiry(str(meta.get("expiry") or ""), _bar_day(bars[0]["date"]))
        if dte is not None:
            dtes.append(dte)

    cfg, notes = labelled_replay_config(shipped, dtes)
    for note in notes:
        print(note)

    results, considered, n_in, n_out = [], 0, 0, 0
    for sym, rec in list(opt.items()):
        meta = rec["meta"]
        name = meta["underlying"]
        if name not in spot:
            continue
        sc = [Candle(ts_ms=ms(b["date"]), open=b["open"], high=b["high"], low=b["low"],
                     close=b["close"]) for b in spot[name]]
        if len(sc) < 60:
            continue
        levels = find_levels(sc, pivot_lookback=cfg.pivot_lookback,
                             cluster_pct=cfg.level_cluster_pct,
                             min_touches=cfg.min_level_touches,
                             window=cfg.level_lookback_days)
        bars = [OICandle(ts_ms=ms(b["date"]), open=b["open"], high=b["high"], low=b["low"],
                         close=b["close"], volume=b["volume"], oi=b["oi"])
                for b in rec["bars"] if (b.get("oi") or 0) > 0 and (b.get("close") or 0) > 0]
        if len(bars) < 60:
            continue
        today = datetime.fromtimestamp(bars[0].ts_ms / 1000, IST).date()
        dte = days_to_expiry(meta["expiry"], today)
        label = window_label(meta["expiry"], today, shipped)
        if label == "in":
            n_in += 1
        else:
            n_out += 1
        px = None
        for c in sc:
            if c.ts_ms <= bars[-1].ts_ms and c.close > 0:
                px = float(c.close)
        if px is None:
            px = sc[-1].close
        kind = "resistance" if meta["option_type"] == "CE" else "support"
        near = [l for l in live_levels(levels, px, cfg.level_proximity_pct) if l.kind == kind]
        considered += 1
        if not near:
            continue
        inst = InstrumentRef(instrument_id=str(meta["token"]), tradingsymbol=sym,
                             option_type=meta["option_type"], strike=meta["strike"],
                             expiry=meta["expiry"], lot_size=meta["lot_size"],
                             tick_size=meta["tick_size"])
        cand = StrikeCandidate(
            underlying=name, level=near[0], instrument=inst,
            oi=int(meta["oi"]), days_to_expiry=int(dte if dte is not None else 0),
            spot=px, premium=meta["ltp"], chain_oi_max=None)
        reg = regime_of(sc, cfg)
        regimes = {b["date"][:10]: reg for b in rec["bars"]}
        results.append(replay_contract(cand, bars, cfg, regime_by_day=regimes,
                                       spot_candles=sc))

    print(f"contracts considered: {considered}")
    print(f"passed the level gate: {len(results)}")
    print(f"window counts: in={n_in} out={n_out}")
    s = summarise(results)
    for k, v in s.items():
        print(f"  {k}: {v}")
    refusals: dict[str, int] = {}
    for r in results:
        for e in r["events"]:
            if e["kind"] == "refused":
                refusals[e["reason"]] = refusals.get(e["reason"], 0) + 1
    print("refusal reasons:", dict(sorted(refusals.items(), key=lambda kv: -kv[1])[:5]))


if __name__ == "__main__":
    main()
