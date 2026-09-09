"""Fetch stored history and drive the engine's replay over it.

Fetching only. Every decision is made by ``app.engines.gamma_move.replay``, which
drives the same strategy object the live runner drives -- so a replay result is
evidence about the shipped code rather than about a parallel implementation.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from app.core.logging import get_logger
from app.engines.gamma_move import (Candle, GammaMoveConfig, InstrumentRef, OICandle,
                                    SpotLevel, StrikeCandidate, days_to_expiry,
                                    find_levels, live_levels, option_type_for,
                                    regime_of, replay_contract, summarise)
from app.services.gamma_move import get_config, ist_today, nfo_dump, to_instrument_ref

log = get_logger(__name__)
_IST = timezone(timedelta(hours=5, minutes=30))


def cfg_for_unscanned_chain(cfg: GammaMoveConfig) -> GammaMoveConfig:
    """Wall gate needs a full as-of chain. Without one, skip it and say so.

    ``is_chain_wall(..., chain_oi_max=None, required=True)`` returns True, so
    leaving the flag on would silently pass every strike. Study replay already
    labels this; live ``replay_symbol`` must do the same.
    """
    if cfg.require_chain_max_oi:
        return replace(cfg, require_chain_max_oi=False)
    return cfg


async def replay_symbol(uid: str, tradingsymbol: str, *, days: int = 60,
                        cfg: Optional[GammaMoveConfig] = None) -> dict:
    """Replay one option contract over the last ``days`` of its own history."""
    cfg = cfg or get_config()
    from app.services.exchanges.kite import accounts
    from app.services.gamma_move_scanner import Pacer, _historical, _to_candles

    acct = accounts.get_active(uid)
    if not acct:
        raise RuntimeError("No active Kite account")
    client = await accounts.acquire_client(acct)

    rows = await nfo_dump(uid)
    row = next((r for r in rows if str(r.get("tradingsymbol")) == tradingsymbol), None)
    if row is None:
        raise ValueError(f"{tradingsymbol} is not in the current NFO instrument dump. "
                         "Expired contracts leave the dump, so only live contracts "
                         "can be replayed.")
    inst = to_instrument_ref(row)
    underlying = str(row.get("name") or "").upper()

    today = ist_today()
    frm = (today - timedelta(days=days)).isoformat()
    to = today.isoformat()
    pacer = Pacer(2.6)

    bars = [b for b in _to_candles(
        await _historical(client, pacer, int(inst.instrument_id), cfg.trigger_timeframe,
                          frm, to, True), oi=True) if b.close > 0]
    if len(bars) < 40:
        raise ValueError(f"only {len(bars)} usable bars for {tradingsymbol}")

    eq = await client.search_instruments(underlying, "NSE", limit=20)
    spot_token = next((int(r["instrument_token"]) for r in eq
                       if r.get("tradingsymbol") == underlying), None)
    spot_candles: list[Candle] = []
    if spot_token:
        spot_candles = _to_candles(await _historical(
            client, pacer, spot_token, cfg.level_timeframe,
            (today - timedelta(days=400)).isoformat(), to, False))

    if not spot_candles:
        raise ValueError(f"no spot history for {underlying}; the level filter — which is "
                         "where this strategy's measured edge lives — cannot be applied")

    spot = spot_candles[-1].close
    levels = find_levels(spot_candles, pivot_lookback=cfg.pivot_lookback,
                         cluster_pct=cfg.level_cluster_pct,
                         min_touches=cfg.min_level_touches,
                         window=cfg.level_lookback_days)
    kind = "resistance" if inst.option_type == "CE" else "support"
    near = [l for l in live_levels(levels, spot, cfg.level_proximity_pct) if l.kind == kind]
    if not near:
        nearest = min((l.distance_pct(spot) for l in levels if l.kind == kind),
                      default=None)
        return {"tradingsymbol": tradingsymbol, "skipped": True,
                "reason": (f"{underlying} is not within {cfg.level_proximity_pct}% of a "
                           f"{kind} level" +
                           (f" (nearest is {nearest:.2f}% away)" if nearest else "")),
                "summary": summarise([])}

    dte = days_to_expiry(inst.expiry, today)
    if dte is None:
        return {"tradingsymbol": tradingsymbol, "skipped": True,
                "reason": f"unparseable expiry {inst.expiry!r}",
                "summary": summarise([])}
    cand = StrikeCandidate(underlying=underlying, level=near[0], instrument=inst,
                           oi=0, days_to_expiry=dte, spot=spot,
                           premium=bars[-1].close)
    cfg = cfg_for_unscanned_chain(cfg)
    regime = regime_of(spot_candles, cfg)
    regimes = {datetime.fromtimestamp(b.ts_ms / 1000, _IST).strftime("%Y-%m-%d"): regime
               for b in bars}
    result = replay_contract(cand, bars, cfg, regime_by_day=regimes,
                             spot_candles=spot_candles)
    result["summary"] = summarise([result])
    result["level"] = {"price": near[0].price, "kind": near[0].kind,
                       "touches": near[0].touches}
    # The level is the one confirmed today, held fixed across the window. It is
    # not what a live scan would have seen on every past bar, so this replay is
    # indicative rather than a clean walk-forward.
    result["caveats"].append(
        "the level is today's, held fixed across the window — a clean walk-forward "
        "would rediscover it bar by bar")
    result["caveats"].append(
        "wall_gate=skipped — no as-of full-chain OI; require_chain_max_oi was "
        "turned off rather than silently passing every strike")
    return result
