"""The Gamma Move scan: levels, then strikes, then the trigger.

The ordering is the whole reason this engine can run at all. A naive scan --
"pull 15-minute open-interest candles for every strike of every F&O stock" -- is
roughly 150 names times 10 strikes = 1,500 historical requests against a budget
of about 3 per second, so ten minutes per cycle. It cannot run on a 15-minute
cadence and it would starve every other consumer of the same budget.

But the level filter and the expiry window both run on data that is already
cached or costs one bulk quote for the entire universe, and together they discard
most of the universe. So:

    Stage A  levels      ~150 daily-candle requests, ONCE per trading day
    Stage B  strikes     1-4 bulk /quote calls TOTAL (500 instruments each)
    Stage C  trigger     one 15-minute request per surviving contract, ~25

Stage C is then about nine seconds of the historical budget per bar instead of
ten minutes. Reordering these stages is a performance regression that will not
look like one in review, which is why it is written down here.
"""
from __future__ import annotations

import asyncio
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from app.core.logging import get_logger
from app.engines.gamma_move import (Candle, GammaMoveConfig, GammaMoveStrategy,
                                    GammaSignal, InstrumentRef, OICandle, SpotLevel,
                                    StrikeCandidate, find_levels, live_levels,
                                    option_type_for, regime_of, select_expiry)
from app.services.gamma_move import (ist_today, nfo_dump, stock_underlyings,
                                     to_instrument_ref)

log = get_logger(__name__)
_IST = timezone(timedelta(hours=5, minutes=30))

_HISTORICAL_RATE = 2.6
_QUOTE_RATE = 0.8
_QUOTE_BATCH = 400


class Pacer:
    def __init__(self, rate: float):
        self._gap = 1.0 / max(rate, 0.01)
        self._next = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            if now < self._next:
                await asyncio.sleep(self._next - now)
                now = time.monotonic()
            self._next = now + self._gap


_levels_cache: dict[tuple[str, str], tuple[dict, dict, dict]] = {}
_scan_stats: dict[str, dict] = {}


def scan_stats(uid: str) -> dict:
    return _scan_stats.get(uid) or {}


def _ms(iso: str) -> int:
    try:
        return int(datetime.fromisoformat(str(iso)).timestamp() * 1000)
    except (ValueError, TypeError):
        return 0


def _to_candles(rows: list, oi: bool = False) -> list:
    out = []
    for r in rows or []:
        if isinstance(r, dict):
            d = r
        elif len(r) >= 6:
            d = {"date": r[0], "open": r[1], "high": r[2], "low": r[3],
                 "close": r[4], "volume": r[5], "oi": r[6] if len(r) > 6 else 0}
        else:
            continue
        kw = dict(ts_ms=_ms(d["date"]), open=float(d["open"]), high=float(d["high"]),
                  low=float(d["low"]), close=float(d["close"]))
        if oi:
            out.append(OICandle(**kw, volume=int(d.get("volume") or 0),
                                oi=int(d.get("oi") or 0)))
        else:
            out.append(Candle(**kw, volume=int(d.get("volume") or 0)))
    return out


async def _historical(client, pacer: Pacer, token: int, interval: str,
                      frm: str, to: str, oi: bool) -> list:
    await pacer.wait()
    try:
        raw = await client.get_historical(int(token), interval, frm, to, False, oi)
    except Exception as exc:                                       # noqa: BLE001
        log.debug("gamma_move historical %s failed: %s", token, exc)
        return []
    rows = raw.get("candles") if isinstance(raw, dict) else raw
    return rows or []


async def _quote_batched(client, pacer: Pacer, keys: list) -> dict:
    out: dict = {}
    for i in range(0, len(keys), _QUOTE_BATCH):
        await pacer.wait()
        try:
            out.update(await client.get_quote(keys[i:i + _QUOTE_BATCH]) or {})
        except Exception as exc:                                   # noqa: BLE001
            log.warning("gamma_move quote batch at %s failed: %s", i, exc)
    return out


def _quote_of(quotes: dict, key: str) -> dict:
    return quotes.get(key) or quotes.get(key.split(":", 1)[-1]) or {}


def _spread_pct(q: dict) -> float | None:
    depth = q.get("depth") or {}
    bids = depth.get("buy") or []
    asks = depth.get("sell") or []
    bid = ask = 0.0
    if bids:
        bid = float((bids[0] or {}).get("price") or 0.0)
    if asks:
        ask = float((asks[0] or {}).get("price") or 0.0)
    if bid <= 0 or ask <= 0:
        return None
    mid = (bid + ask) / 2.0
    if mid <= 0:
        return None
    return (ask - bid) / mid * 100.0


def _closed_daily(candles: list, today: date) -> list:
    if not candles:
        return candles
    last_day = datetime.fromtimestamp(candles[-1].ts_ms / 1000, _IST).date()
    if last_day == today and len(candles) > 1:
        return candles[:-1]
    return candles


async def scan_levels(uid: str, cfg: GammaMoveConfig, client, *,
                      today: Optional[date] = None) -> tuple[dict, dict, dict]:
    day = (today or ist_today()).isoformat()
    key = (uid, day)
    if key in _levels_cache:
        return _levels_cache[key]

    rows = await nfo_dump(uid)
    names = stock_underlyings(rows, cfg)
    eq = await client.search_instruments("", "NSE", limit=1_000_000)
    tokens = {str(r.get("tradingsymbol")): int(r.get("instrument_token") or 0)
              for r in eq if r.get("segment") == "NSE"}

    pacer = Pacer(_HISTORICAL_RATE)
    to_d = today or ist_today()
    frm_d = to_d - timedelta(days=max(cfg.level_lookback_days * 2, 200))
    frm, to = frm_d.isoformat(), to_d.isoformat()

    levels, spots, regimes, skipped = {}, {}, {}, 0
    for name in names:
        token = tokens.get(name)
        if not token:
            skipped += 1
            continue
        candles = _to_candles(
            await _historical(client, pacer, token, cfg.level_timeframe, frm, to, False))
        if len(candles) < cfg.pivot_lookback * 2 + 10:
            skipped += 1
            continue
        hist = _closed_daily(candles, to_d)
        if len(hist) < cfg.pivot_lookback * 2 + 10:
            skipped += 1
            continue
        levels[name] = find_levels(hist, pivot_lookback=cfg.pivot_lookback,
                                   cluster_pct=cfg.level_cluster_pct,
                                   min_touches=cfg.min_level_touches,
                                   window=cfg.level_lookback_days)
        spots[name] = hist[-1].close
        regimes[name] = regime_of(hist, cfg)
    if skipped:
        log.info("gamma_move stage A: %s of %s underlyings had no usable daily history",
                 skipped, len(names))
    _levels_cache.clear()
    _levels_cache[key] = (levels, spots, regimes)
    return levels, spots, regimes


async def scan_strikes(uid: str, cfg: GammaMoveConfig, client, levels: dict,
                       spots: dict, regimes: dict, *,
                       today: Optional[date] = None) -> list[StrikeCandidate]:
    day = today or ist_today()
    rows = await nfo_dump(uid)

    names = list(levels.keys())
    eq_keys = [f"NSE:{n}" for n in names]
    live_quotes = await _quote_batched(client, Pacer(_QUOTE_RATE), eq_keys) if eq_keys else {}
    for name in names:
        q = _quote_of(live_quotes, f"NSE:{name}")
        ltp = float(q.get("last_price") or 0.0)
        if ltp > 0:
            spots[name] = ltp

    wanted: list[tuple[str, SpotLevel]] = []
    for name, lvls in levels.items():
        spot = spots.get(name) or 0.0
        for lv in live_levels(lvls, spot, cfg.level_proximity_pct):
            if cfg.regime_enabled:
                want = option_type_for(lv)
                reg = regimes.get(name, "unknown")
                if reg == "unknown" or ((reg == "up") != (want == "CE")):
                    continue
            wanted.append((name, lv))
    if not wanted:
        return []

    by_name: dict[str, list] = {}
    wanted_names = {w[0] for w in wanted}
    for r in rows:
        if r.get("segment") != "NFO-OPT":
            continue
        n = str(r.get("name") or "").upper()
        if n in wanted_names:
            by_name.setdefault(n, []).append(r)

    pool: list[tuple[str, SpotLevel, InstrumentRef, bool]] = []
    for name, lv in wanted:
        expiry = select_expiry([str(r.get("expiry") or "")[:10] for r in by_name.get(name, [])],
                               day, cfg)
        if not expiry:
            continue
        want = option_type_for(lv)
        for r in by_name.get(name, []):
            if str(r.get("expiry") or "")[:10] != expiry:
                continue
            if str(r.get("instrument_type")) != want:
                continue
            strike = float(r.get("strike") or 0)
            near = (lv.price > 0
                    and abs(strike - lv.price) / lv.price * 100 <= cfg.strike_window_pct)
            pool.append((name, lv, to_instrument_ref(r), near))
    if not pool:
        return []

    quotes = await _quote_batched(client, Pacer(_QUOTE_RATE),
                                  [f"NFO:{i.tradingsymbol}" for _, _, i, _ in pool])

    from app.engines.gamma_move import days_to_expiry
    from app.engines.gamma_move.selection import is_chain_wall, spot_through_or_at_strike

    chain_max: dict[tuple[str, str, str], int] = {}
    for name, lv, inst, _near in pool:
        q = _quote_of(quotes, f"NFO:{inst.tradingsymbol}")
        oi = int(float(q.get("oi") or 0))
        key_c = (name, inst.expiry[:10], inst.option_type)
        if oi > chain_max.get(key_c, 0):
            chain_max[key_c] = oi

    best: dict[tuple[str, float, str], StrikeCandidate] = {}
    for name, lv, inst, near in pool:
        if not near:
            continue
        q = _quote_of(quotes, f"NFO:{inst.tradingsymbol}")
        oi = int(float(q.get("oi") or 0))
        premium = float(q.get("last_price") or 0.0)
        volume = int(q.get("volume") or 0)
        if (oi < cfg.min_option_oi or volume < cfg.min_option_volume
                or premium < cfg.min_option_premium):
            continue
        spread = _spread_pct(q)
        if spread is not None and cfg.max_spread_pct > 0 and spread > cfg.max_spread_pct:
            continue
        dte = days_to_expiry(inst.expiry, day)
        if dte is None:
            continue
        spot = spots.get(name) or 0.0
        if cfg.require_spot_through_strike and not spot_through_or_at_strike(
                spot, inst.strike, inst.option_type, cfg.level_proximity_pct):
            continue
        wall = chain_max.get((name, inst.expiry[:10], inst.option_type))
        if not is_chain_wall(oi, wall, required=cfg.require_chain_max_oi):
            continue
        key = (name, lv.price, inst.option_type)
        cand = StrikeCandidate(underlying=name, level=lv, instrument=inst, oi=oi,
                               days_to_expiry=dte, spot=spot, premium=premium,
                               chain_oi_max=wall)
        cur = best.get(key)
        if cur is None or (oi, -abs(inst.strike - lv.price)) > \
                (cur.oi, -abs(cur.instrument.strike - lv.price)):
            best[key] = cand

    out = sorted(best.values(), key=lambda c: -c.oi)
    if len(out) > cfg.max_candidates:
        log.info("gamma_move stage B: %s candidates found, watching the top %s by open "
                 "interest", len(out), cfg.max_candidates)
        out = out[:cfg.max_candidates]
    return out


async def scan_triggers(uid: str, cfg: GammaMoveConfig, client,
                        candidates: list[StrikeCandidate], regimes: dict,
                        strategy: GammaMoveStrategy, *,
                        today: Optional[date] = None) -> list[GammaSignal]:
    day = today or ist_today()
    pacer = Pacer(_HISTORICAL_RATE)
    frm = (day - timedelta(days=10)).isoformat()
    to = day.isoformat()
    now_ms = int(datetime.now(_IST).timestamp() * 1000)

    out: list[GammaSignal] = []
    for cand in candidates:
        bars = _to_candles(
            await _historical(client, pacer, int(cand.instrument.instrument_id),
                              cfg.trigger_timeframe, frm, to, True), oi=True)
        bars = [b for b in bars if b.close > 0]
        out.append(strategy.evaluate(cand, bars, now_ms=now_ms, today=day,
                                     regime=regimes.get(cand.underlying, "unknown")))
    return out


async def scan_once(uid: str, cfg: GammaMoveConfig, strategy: GammaMoveStrategy,
                    *, today: Optional[date] = None) -> list[GammaSignal]:
    from app.services.exchanges.kite import accounts
    acct = accounts.get_active(uid)
    if not acct:
        raise RuntimeError("No active Kite account")
    client = await accounts.acquire_client(acct)
    day = today or ist_today()
    started = time.monotonic()

    levels, spots, regimes = await scan_levels(uid, cfg, client, today=day)
    near = sum(1 for n, l in levels.items()
               if live_levels(l, spots.get(n) or 0.0, cfg.level_proximity_pct))
    t_a = time.monotonic()

    candidates = await scan_strikes(uid, cfg, client, levels, spots, regimes, today=day)
    t_b = time.monotonic()

    signals = await scan_triggers(uid, cfg, client, candidates, regimes, strategy, today=day)
    t_c = time.monotonic()

    _scan_stats[uid] = {
        "last_run_ms": int(datetime.now(_IST).timestamp() * 1000),
        "stage_a": {"scanned": len(levels), "near_level": near,
                    "seconds": round(t_a - started, 2)},
        "stage_b": {"candidates": len(candidates), "seconds": round(t_b - t_a, 2)},
        "stage_c": {"watched": len(signals),
                    "armed": sum(1 for s in signals if s.state == "armed"),
                    "historical_requests": len(candidates),
                    "seconds": round(t_c - t_b, 2)},
        "total_seconds": round(t_c - started, 2),
    }
    return signals
