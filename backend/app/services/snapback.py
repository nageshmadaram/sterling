"""Runtime for Snapback: config, the daily candle fetch, and the scan.

Every rule lives in ``app.engines.snapback`` and is reachable from here without
a broker object, so the replay that produced the numbers and the live scan run
the same code.

Nothing in this module places an order. Snapback has not cleared the
walk-forward gate — it passes six of nine checks and misses on two sample-size
facts — so ``auto_execute`` defaults off and there is no execution path here.
Arming is the operator's, through the shared order route every engine uses.

**This engine scans DAILY bars.** That is the single biggest difference from
every other engine here, and it has one practical consequence worth stating: a
signal is produced by a session's CLOSE, so the scan is worth running once after
the close and once before the next open, not every five minutes. The catch-up
window exists because a rule that fires on one bar is a rule whose signal is
lost forever if the scan that would have seen it did not run.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from app.core.logging import get_logger
from app.engines.option_contracts import canonical, spec_for
from app.engines.snapback import (CONTRACT_VERSION, SnapbackConfig, STRATEGY_ID,
                                  TUPLE_FIELDS, descriptor, evaluate, evaluate_at,
                                  to_bars)
from app.engines.snapback.contracts import lots_for, moneyness_label
from app.engines.snapback.pricing import bs_delta, bs_price

log = get_logger(__name__)

_IST = timezone(timedelta(hours=5, minutes=30))
_CONFIG_KEY = "snapback_config"
#: Daily bars fetched per instrument. The binding input is a 20-session EMA plus
#: a 60-session margin for the seed to wash out; 400 covers that with room and
#: is one request per instrument.
LOOKBACK_BARS = 400
_CANDLE_TTL_S = 900.0
#: Closed sessions the scan looks back over. A daily rule fires on ONE bar, and
#: a scan that does not run on the day it fired never sees it again.
CATCHUP_SESSIONS = 3

__all__ = ["get_config", "set_config", "descriptor", "snapshot", "scan_once",
           "status", "evaluate_symbol", "recent_signals", "ist_now_ms",
           "STRATEGY_ID"]


def ist_now_ms() -> int:
    return int(datetime.now(_IST).timestamp() * 1000)


def ist_today() -> date:
    return datetime.now(_IST).date()


# ------------------------------------------------------------------ config

def get_config(uid: str | None = None) -> SnapbackConfig:
    """The configuration in force.

    Three outcomes, deliberately different. **Nothing stored** gives the real
    defaults. **Stored but invalid** gives defaults with the engine OFF, because
    a config that will not validate must never become a trading config. **Store
    unreachable** also gives OFF — an engine that has lost its settings must not
    start scanning the shipped universe with the operator's choices discarded.
    """
    key = f"{_CONFIG_KEY}:{uid}" if uid else _CONFIG_KEY
    raw: Any = None
    try:
        from app.services import db
        if hasattr(db, "is_available") and not db.is_available():
            log.warning("%s: config store unavailable; defaults OFF", STRATEGY_ID)
            return SnapbackConfig(enabled=False)
        raw = db.get_config(key)
        if not raw and uid:
            raw = db.get_config(_CONFIG_KEY)
    except Exception:                                              # noqa: BLE001
        log.warning("%s: config store unavailable; defaults OFF", STRATEGY_ID)
        return SnapbackConfig(enabled=False)
    if not raw:
        return SnapbackConfig()
    try:
        from app.engines.snapback import validate
        stored = json.loads(raw) if isinstance(raw, str) else raw
        known = set(SnapbackConfig().as_dict())
        return validate({k: v for k, v in dict(stored).items() if k in known})
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        log.error("Stored %s config is invalid (%s); defaults OFF", STRATEGY_ID, exc)
        return SnapbackConfig(enabled=False)


def set_config(values: dict[str, Any], uid: str | None = None) -> SnapbackConfig:
    """Persist a partial change. Validation is the engine's, not a second copy."""
    from app.engines.snapback import validate
    cfg = validate(dict(values), base=get_config(uid))
    from app.services import db
    db.set_config(f"{_CONFIG_KEY}:{uid}" if uid else _CONFIG_KEY,
                  json.dumps(cfg.as_dict(), separators=(",", ":")))
    return cfg


# ------------------------------------------------------------------- state

@dataclass
class ScanState:
    """What the last scan found, per user. In memory by design — these rows are
    a view of a tape stored elsewhere, and persisting them would create a second
    source of truth for one fact."""

    rows: list[dict] = field(default_factory=list)
    scanning: bool = False
    last_scan_ms: int = 0
    last_error: Optional[str] = None
    scanned: int = 0
    failures: list[str] = field(default_factory=list)
    candles: dict[int, tuple[float, list]] = field(default_factory=dict)
    signals: dict[str, dict] = field(default_factory=dict)


_state: dict[str, ScanState] = {}


def status(uid: str) -> ScanState:
    st = _state.get(uid)
    if st is None:
        st = _state[uid] = ScanState()
    return st


def signal_id_for(symbol: str, side: str, bar_ms: int) -> str:
    return f"{STRATEGY_ID}:{side}:{canonical(symbol)}:{int(bar_ms)}"


# ---------------------------------------------------------------- evaluate

class UnreadableCandles(RuntimeError):
    """Candles arrived and none of them parsed.

    An exception rather than an empty list, because the two are indistinguishable
    downstream and mean opposite things. A shape change in the feed once made
    every one of 200 instruments produce an empty tape, and the scan reported
    "nothing fired" — which is what a quiet market looks like.
    """


def evaluate_symbol(candles, cfg: SnapbackConfig, symbol: str) -> list:
    """Signals on the last few CLOSED sessions of one instrument."""
    bars = to_bars(candles)
    if len(bars) == 0:
        if candles:
            raise UnreadableCandles(
                f"{len(candles)} candles arrived and none parsed "
                f"(first is {type(candles[0]).__name__})")
        return []
    return evaluate(bars, cfg, canonical(symbol), catchup_bars=CATCHUP_SESSIONS)


def _drop_forming(candles: list) -> list:
    """Drop today's session while it is still trading.

    Every rule here is stated on a CLOSE. A forming daily bar makes a signal
    appear and disappear through the session, which is the live-repaint bug this
    repo has already fixed once elsewhere.
    """
    if not candles:
        return candles
    from app.engines.snapback.models import _epoch_seconds
    last = _epoch_seconds(candles[-1])
    if last is None:
        return list(candles)
    if datetime.fromtimestamp(last, tz=_IST).date() >= ist_today():
        # 15:30 IST is the close. Before it, today's bar is still forming.
        now = datetime.now(_IST)
        if (now.hour, now.minute) < (15, 30):
            return list(candles[:-1])
    return list(candles)


def resolve_universe(cfg: SnapbackConfig, *, nfo, bfo, equities) -> list:
    """The instruments this scan covers.

    ``curated`` defers to the shared builder, so Snapback scans exactly what
    every other engine scans. ``fno`` builds its own list from the dump, because
    ``build_universe`` applies the fourteen-name high-liquidity registry BEFORE
    anyone downstream can widen it — pointing this engine at it and asking for
    the F&O list returned eighteen instruments and no signals.

    Liquidity is deliberately NOT a name-level filter in ``fno`` mode. What
    decides whether a trade is fillable is the spread and the open interest on
    the CONTRACT, and both are measured per contract during the scan. A static
    list of liquid underlyings answers a different question and answers it out
    of date: between July and September 2026 this rule fired ninety times across
    the F&O list and not once on those fourteen names.

    Indices come first so ``max_universe`` trims the tail of the stock list
    rather than a random slice.
    """
    from app.engines.option_contracts import spec_for
    from app.services.kite_engine.universe import (UniverseItem, build_universe,
                                                   select_scan_universe)
    built = build_universe(nfo_instruments=nfo, bfo_instruments=bfo,
                           equities=equities)
    if cfg.universe_mode == "curated":
        return list(select_scan_universe(
            built, indices=cfg.scan_indices, stocks=cfg.scan_stocks,
            all_stocks=False, stock_contracts=cfg.scan_stock_contracts))

    by_symbol: dict[str, dict] = {}
    for e in equities or ():
        sym = str(e.get("tradingsymbol", "") if isinstance(e, dict)
                  else getattr(e, "tradingsymbol", "") or "")
        if sym and sym not in by_symbol:
            by_symbol[sym] = e if isinstance(e, dict) else e.__dict__

    out: list = [i for i in built if i.is_index]
    seen: set[str] = {canonical(i.name) for i in out}
    if cfg.scan_stock_contracts:
        stocks: list = []
        for rows, option_exchange in ((nfo, "NFO"), (bfo, "BFO")):
            for r in rows or ():
                d = r if isinstance(r, dict) else getattr(r, "__dict__", {})
                if d.get("instrument_type") not in ("CE", "PE"):
                    continue
                name = str(d.get("name") or "").strip().upper()
                if not name or name in seen or spec_for(name) is None:
                    continue
                equity = by_symbol.get(name)
                if not equity:
                    # No spot listing means no daily tape, and the rule is
                    # stated on the underlying's closes.
                    continue
                seen.add(name)
                stocks.append(UniverseItem(
                    name=name, tradingsymbol=name,
                    token=int(equity.get("instrument_token", 0) or 0),
                    exchange=str(equity.get("exchange", "NSE")),
                    option_exchange=option_exchange))
        stocks.sort(key=lambda i: i.name)
        out += stocks
    return out[:max(int(cfg.max_universe), 1)]


# ---------------------------------------------------------------- contracts

def _pick_by_delta(chain: list, *, spot: float, option_type: str, iv: float,
                   cfg: SnapbackConfig) -> Optional[dict]:
    """The listed strike whose modelled delta is nearest the target.

    Over the REAL chain, not an arithmetic ladder: the engine's own
    ``strike_for_delta`` inverts Black-Scholes to a price, and the exchange
    lists what it lists. Choosing from the chain means a signal can never name a
    strike that is not tradable.

    ``chain_rows_for`` yields DICTS whose option side is spelled "call"/"put"
    and whose expiry key is ``expiry_date``. Reading them with ``getattr`` and
    "CE"/"PE" — as the first version did — matches nothing and every row comes
    back "no listed contract", which reads as an expiry problem rather than a
    shape mismatch.
    """
    call = option_type == "CE"
    want = "call" if call else "put"
    best, best_gap = None, 1e9
    for row in chain:
        if str(row.get("option_type")) != want:
            continue
        dte = int(row.get("dte") or 0)
        if not (cfg.min_dte <= dte <= cfg.max_dte):
            continue
        strike = float(row.get("strike") or 0.0)
        if strike <= 0:
            continue
        d = abs(float(bs_delta(spot, strike, dte / 365.0, iv, call=call)))
        gap = abs(d - cfg.target_delta)
        # Ties break towards the SHORTER expiry: same delta, less premium tied
        # up for the same ten-session horizon.
        if gap < best_gap - 1e-9 or (abs(gap - best_gap) <= 1e-9 and best
                                     and dte < int(best.get("dte") or 999)):
            best_gap = gap
            best = {"symbol": str(row.get("instrument_name") or ""),
                    "strike": strike, "option_type": option_type,
                    "expiry": str(row.get("expiry_date") or ""),
                    "dte": dte,
                    "lot_size": int(row.get("lot_size") or 0),
                    "token": int(row.get("token") or 0),
                    "delta": d if call else -d,
                    "moneyness": moneyness_label(spot, strike, call)}
    return best


async def _contract_for(client, name: str, option_exchange: str, spot: float,
                        option_type: str, iv: float, cfg: SnapbackConfig,
                        chain_cache: dict) -> Optional[dict]:
    from app.services.kite_engine.strikes import chain_rows_for
    try:
        rows = chain_cache.get(option_exchange)
        if rows is None:
            rows = await client.search_instruments("", option_exchange,
                                                   limit=1_000_000)
            chain_cache[option_exchange] = rows
        chain = chain_rows_for(rows, name, ist_today())
        if not chain:
            return None
        pick = _pick_by_delta(chain, spot=spot, option_type=option_type, iv=iv,
                              cfg=cfg)
        if pick:
            pick["exchange"] = option_exchange
        return pick
    except Exception as exc:                                       # noqa: BLE001
        log.debug("%s: contract resolution failed for %s: %s", STRATEGY_ID,
                  name, exc)
        return None


async def _quote_for(client, contract: dict, cfg: SnapbackConfig) -> Optional[dict]:
    """The contract's own premium and book, so an armed row can be sized.

    Fetched during the scan and not at arm time: a row without a premium cannot
    be sized, cannot be stopped and cannot be bought, and discovering that after
    clicking Buy is the wrong moment.
    """
    key = f"{contract['exchange']}:{contract['symbol']}"
    try:
        quotes = await client.get_quote([key])
    except Exception as exc:                                       # noqa: BLE001
        return {"premium": None, "blockers": [f"quote unavailable: {exc}"]}
    q = (quotes or {}).get(key) or {}
    depth = (q.get("depth") or {})
    bid = float((depth.get("buy") or [{}])[0].get("price") or 0.0)
    ask = float((depth.get("sell") or [{}])[0].get("price") or 0.0)
    ltp = float(q.get("last_price") or 0.0)
    mid = (bid + ask) / 2.0 if bid > 0 and ask > 0 else ltp
    spread_pct = ((ask - bid) / mid * 100.0) if mid > 0 and bid > 0 and ask > 0 else None
    blockers: list[str] = []
    if mid <= 0:
        blockers.append("no premium quoted")
    elif mid < cfg.min_option_premium:
        blockers.append(f"premium {mid:.2f} below the {cfg.min_option_premium:g} floor")
    if spread_pct is not None and spread_pct > cfg.max_spread_pct:
        blockers.append(f"spread {spread_pct:.1f}% wider than "
                        f"{cfg.max_spread_pct:g}%")
    oi = float(q.get("oi") or 0.0)
    if cfg.min_option_oi > 0 and oi < cfg.min_option_oi:
        blockers.append(f"open interest {oi:,.0f} below {cfg.min_option_oi:,.0f}")
    return {"premium": mid or None, "bid": bid or None, "ask": ask or None,
            "ltp": ltp or None, "oi": oi or None, "spread_pct": spread_pct,
            "blockers": blockers}


# --------------------------------------------------------------------- row

def _row(sig, cfg: SnapbackConfig, *, contract: Optional[dict],
         quote: Optional[dict], blocked: Optional[str],
         underlying_token: int) -> dict:
    """One board row. Every price that is MODELLED says so.

    The premium a row shows is the quoted one when there is a quote and the
    modelled one when there is not, and the two are never merged into one
    unlabelled number — this repo has shipped a modelled premium that read as a
    price, and an operator cannot size a trade off a figure whose provenance is
    ambiguous.
    """
    spec = spec_for(sig.symbol)
    lot = int((contract or {}).get("lot_size") or (spec.lot_size if spec else 0))
    quoted = (quote or {}).get("premium")
    modelled = None
    if contract:
        modelled = float(bs_price(sig.entry, float(contract["strike"]),
                                  max(int(contract.get("dte") or cfg.min_dte), 1) / 365.0,
                                  sig.assumed_iv,
                                  call=sig.option_type == "CE"))
    premium = quoted if quoted else modelled
    stop = round(premium * (1.0 - cfg.premium_stop_pct / 100.0), 2) if premium else None
    # The thesis' own objective, priced: what this contract is worth if spot
    # returns to the mean over the holding period. Modelled at the ENTRY's vol
    # like everything else here, so it is a plan and not a forecast — but a
    # target column that is always "—" tells an operator nothing about what the
    # trade is FOR.
    target = None
    if contract and premium:
        years = max(int(contract.get("dte") or cfg.min_dte) - cfg.hold_days, 1) / 365.0
        target = float(bs_price(sig.mean_target, float(contract["strike"]), years,
                                sig.assumed_iv, call=sig.option_type == "CE"))
        target = round(max(target, 0.0), 2)
    # A trail only exists when one is configured. Showing the stop in the trail
    # column because the trail is off would claim a ratchet that is not running.
    trail = (round(premium * (1.0 - cfg.premium_trail_pct / 100.0), 2)
             if premium and cfg.premium_trail_pct > 0 else None)
    # The price at which this trade stops being a horizon trade. Above it the
    # position is held past ``hold_days`` under the give-back ratchet instead of
    # being closed, so an operator reading the row needs the number rather than
    # the rule.
    runner = (round(premium * cfg.runner_mult, 2)
              if premium and cfg.runner_mult > 0 else None)
    lots = 0
    if premium and lot:
        from app.engines.snapback.contracts import Pick
        lots = lots_for(Pick(underlying=sig.symbol, symbol=(contract or {}).get("symbol", ""),
                             option_type=sig.option_type,
                             strike=float((contract or {}).get("strike") or 0.0),
                             lot_size=lot, exchange=(contract or {}).get("exchange", ""),
                             dte=(contract or {}).get("dte"),
                             expiry=(contract or {}).get("expiry"),
                             premium=float(premium), delta=0.0, moneyness="",
                             modelled=quoted is None), cfg)
    return {
        "signal_id": signal_id_for(sig.symbol, sig.side, sig.timestamp_ms),
        "strategy": STRATEGY_ID,
        "side": sig.side,
        "symbol": sig.symbol,
        "state": "watching" if blocked else "armed",
        "reason": blocked,
        "direction": sig.direction,
        "opt_type": sig.option_type,
        "timestamp_ms": sig.timestamp_ms,
        "spot": round(sig.entry, 2),
        "mean_target": round(sig.mean_target, 2),
        "distance_pct": round(sig.distance_pct, 2),
        "stretch": round(sig.stretch, 2),
        "level": round(sig.level, 2),
        "strength": sig.strength,
        "realized_vol_pct": round(sig.realized_vol * 100, 1),
        "assumed_iv_pct": round(sig.assumed_iv * 100, 1),
        "assumed_vrp": cfg.assumed_vrp,
        "hold_days": cfg.hold_days,
        "underlying_token": underlying_token,
        "contract": contract,
        "premium": round(float(premium), 2) if premium else None,
        "premium_is_modelled": bool(premium and quoted is None),
        "modelled_premium": round(modelled, 2) if modelled else None,
        "quote": quote,
        "stop_premium": stop,
        "target_premium": target,
        "trail_premium": trail,
        "runner_premium": runner,
        #: Filled for a replayed row by ``recent_signals``: what the trade
        #: actually did. A history row without an outcome leaves the exit, the
        #: result and the LTP columns empty, which reads as missing data rather
        #: than as a closed trade.
        "outcome": None,
        "lots": lots,
        "quantity": lots * lot if lots and lot else 0,
        "deployed_inr": round(float(premium) * lots * lot, 2)
        if premium and lots and lot else None,
        #: What ONE lot costs. The load-bearing number for this engine, and the
        #: one an operator needs before anything else: a 0.70-delta monthly put
        #: on a single stock is one to two LAKH of premium per lot, so at the
        #: shipped 2%-of-1-lakh budget nothing can ever arm. Reporting only
        #: "cannot afford it" leaves the reader to work out by how much.
        "min_outlay_inr": round(float(premium) * lot, 2)
        if premium and lot else None,
        "reasons": list(sig.reasons),
        "metrics": dict(sig.metrics),
    }


# -------------------------------------------------------------------- scan

async def scan_once(uid: str) -> dict:
    """One universe pass: daily candles -> signals -> rows.

    A symbol that throws is recorded as a failure rather than ending the scan:
    one delisted token must not blank the board.
    """
    cfg = get_config(uid)
    st = status(uid)
    if not cfg.enabled:
        st.rows = []
        st.last_error = "engine disabled"
        return snapshot(uid)
    if st.scanning:
        return snapshot(uid)

    st.scanning = True
    st.failures = []
    st.last_error = None
    # Cleared UP FRONT, not on success: a scan that throws halfway would
    # otherwise leave armed rows behind that nothing has re-checked.
    st.signals = {}
    try:
        from app.services.exchanges.kite import accounts
        from app.services.kite_engine.universe import build_universe, select_scan_universe  # noqa: F401
        acct = accounts.get_active(uid)
        if not acct:
            raise RuntimeError("No active Kite account")
        client = await accounts.acquire_client(acct)
        nfo, bfo, nse, bse = await asyncio.gather(
            client.search_instruments("", "NFO", limit=1_000_000),
            client.search_instruments("", "BFO", limit=1_000_000),
            client.search_instruments("", "NSE", limit=1_000_000),
            client.search_instruments("", "BSE", limit=1_000_000),
        )
        universe = resolve_universe(cfg, nfo=nfo, bfo=bfo, equities=nse + bse)
        chain_cache: dict[str, list] = {"NFO": nfo, "BFO": bfo}
        # Kite's historical endpoint is 3 requests/second. Four concurrent
        # fetchers over a 200-name universe spent their budget on 429
        # retries; three sits under the published limit.
        sem = asyncio.Semaphore(3)
        rows: list[dict] = []

        async def one(item) -> None:
            async with sem:
                try:
                    raw = _drop_forming(await _candles(client, st, item.token,
                                                       item.tradingsymbol))
                    if not raw:
                        st.failures.append(f"{item.name}: no daily candles")
                        return
                    for sig in evaluate_symbol(raw, cfg, item.name):
                        blocked = None
                        contract = await _contract_for(
                            client, item.name, item.option_exchange, sig.entry,
                            sig.option_type, sig.assumed_iv, cfg, chain_cache)
                        quote = None
                        if contract is None:
                            blocked = (f"no listed {sig.option_type} between "
                                       f"{cfg.min_dte} and {cfg.max_dte} days out")
                        else:
                            quote = await _quote_for(client, contract, cfg)
                            if quote and quote["blockers"]:
                                blocked = quote["blockers"][0]
                        row = _row(sig, cfg, contract=contract, quote=quote,
                                   blocked=blocked,
                                   underlying_token=int(item.token or 0))
                        if row["state"] == "armed" and not row["lots"]:
                            need = row.get("min_outlay_inr")
                            budget = cfg.capital_inr * cfg.premium_pct_of_capital / 100
                            row["state"] = "watching"
                            row["reason"] = (
                                f"one lot is "
                                f"{('₹%.0f' % need) if need else 'more'} of premium "
                                f"against a ₹{budget:,.0f} budget "
                                f"({cfg.premium_pct_of_capital:g}% of "
                                f"₹{cfg.capital_inr:,.0f})")
                        rows.append(row)
                except Exception as exc:                           # noqa: BLE001
                    st.failures.append(f"{item.name}: {exc}")

        await asyncio.gather(*(one(i) for i in universe))
        rows.sort(key=lambda r: (r["state"] != "armed", -abs(r["stretch"]),
                                 r["symbol"]))
        st.rows = rows
        st.signals = {r["signal_id"]: r for r in rows if r["state"] == "armed"}
        st.scanned = len(universe)
        st.last_scan_ms = ist_now_ms()
    except Exception as exc:                                       # noqa: BLE001
        st.last_error = str(exc)
        log.error("%s scan failed: %s", STRATEGY_ID, exc)
    finally:
        st.scanning = False
    return snapshot(uid)


async def _candles(client, st: ScanState, token: int, name: str) -> list:
    hit = st.candles.get(token)
    if hit and (time.monotonic() - hit[0]) < _CANDLE_TTL_S:
        return hit[1]
    from app.schemas.instruments import InstrumentMeta
    inst = InstrumentMeta(underlying=name, tick_size=0.05, strike_step=1.0,
                          exchange_currency="INR", index_name=name,
                          has_options=True, exchange="zerodha",
                          zerodha_token=int(token))
    rows = await client.get_candles(inst, "1D", LOOKBACK_BARS)
    st.candles[token] = (time.monotonic(), rows)
    return rows


HISTORY_SESSIONS = 30
_history_cache: dict[str, tuple[float, list[dict]]] = {}
_HISTORY_TTL_S = 300.0


def recent_signals(uid: str, *, sessions: int = HISTORY_SESSIONS) -> list[dict]:
    """What this engine fired over the last few weeks, from STORED bars.

    Works with the market closed, with no broker session, and before any live
    scan has run. It exists for the same reason the intraday pack's does: every
    rule here fires on ONE session and the live scan only looks at the last
    three, so the board is blank almost always — and a blank board cannot be
    told apart from a broken one.

    The contract is ESTIMATED: the strike is computed from the instrument's
    published step and the expiry is left unknown rather than guessed. Every row
    carries ``historical: True`` so nothing downstream can mistake a replayed
    signal for a live one.
    """
    cfg = get_config(uid)
    key = f"{uid}:{sessions}:{cfg.market_filter}:{cfg.min_stretch_atr}"
    hit = _history_cache.get(key)
    if hit and (time.monotonic() - hit[0]) < _HISTORY_TTL_S:
        return hit[1]

    from app.engines.snapback import entry_indices, features, ist_day, Bars
    from app.engines.snapback.regime import MARKET_SYMBOL, gate_for
    from app.services import ohlcv_store
    import numpy as np

    def tape(symbol: str) -> Optional[Bars]:
        rows = ohlcv_store.get_candles(symbol, "1d", limit=900)
        if not rows or len(rows) < cfg.warmup_bars() + cfg.hold_days + 2:
            return None
        a = np.array([[r["time"], r["open"], r["high"], r["low"], r["close"],
                       r.get("volume", 0.0)] for r in rows], dtype=float)
        return Bars(a[:, 0], a[:, 1], a[:, 2], a[:, 3], a[:, 4], a[:, 5])

    names = [canonical(n) for n in _history_universe(cfg)]
    tapes: dict[str, Bars] = {}
    for n in names:
        b = tape(n)
        if b is not None:
            tapes[n] = b
    if not tapes:
        _history_cache[key] = (time.monotonic(), [])
        return []

    gate = gate_for(tapes, market_filter=cfg.market_filter,
                    ema_period=cfg.market_ema)
    market = tapes.get(MARKET_SYMBOL)
    cutoff = ""
    if market is not None and len(market) > sessions:
        cutoff = ist_day(float(market.time[-int(sessions)]))

    # The outcome is computed by the SAME replay the backtest uses, so a
    # history row's exit and a measured trade's exit cannot come from two
    # different rules. Without it the row's exit, result and LTP columns are all
    # empty, which reads as missing data rather than as a closed trade.
    from dataclasses import replace as _replace
    from app.engines.snapback.backtest import replay
    # The position cap is LIFTED for this view, deliberately. The board is
    # showing what each SIGNAL did, not which of them a five-position book would
    # have had room for — with the cap on, 73% of candidates are dropped and
    # their rows come back with an empty exit, which reads as missing data
    # rather than as "the book was full". What a capped portfolio actually earns
    # is the walk-forward harness's question, and it answers it separately.
    # ONE LOT, and the cap lifted. Both for the same reason: this view is what
    # each SIGNAL did, not what the operator's account could afford or what a
    # five-position book had room for. With the account's own premium budget
    # applied, every outcome came back empty — the sizer refused all 881 of
    # them at 2% of a lakh — and an empty exit column reads as missing data.
    book = replay(tapes, _replace(cfg, max_open_positions=10 ** 6,
                                  one_position_per_underlying=False,
                                  sizing_mode="LOTS", lots=1))
    done = {(t.symbol, t.entry_day): t for t in book.trades}

    out: list[dict] = []
    for sym, bars in tapes.items():
        f = features(bars, cfg)
        for side in cfg.sides():
            for i in entry_indices(bars, cfg, side, gate):
                day = ist_day(float(bars.time[int(i)]))
                if cutoff and day < cutoff:
                    continue
                for sig in evaluate_at(bars, cfg, int(i), f):
                    named = _named(sig, sym)
                    row = _row(named, cfg, contract=_estimated(sig, sym, cfg),
                               quote=None, blocked=None, underlying_token=0)
                    # The trade is keyed on the FILL session, one bar after the
                    # signal — the same convention ``Trade.entry_day`` uses.
                    fill_day = (ist_day(float(bars.time[int(i) + 1]))
                                if int(i) + 1 < len(bars) else day)
                    t = done.get((canonical(sym), fill_day))
                    if t is None:
                        # A signal the replay did not trade is NOT a closed
                        # position. Rendering it as one — state "ended", every
                        # outcome column empty — is what "no targets, no exited,
                        # no LTP, nothing" looked like from the board.
                        row["state"] = "watching"
                        row["reason"] = book.untraded.get(
                            (canonical(sym), fill_day),
                            # The newest signal has no fill session yet: this
                            # engine fills at the NEXT session's open, so a
                            # signal on the last stored bar has nothing to
                            # replay. It is the most recent row on the board,
                            # so the message is worth getting right.
                            "signalled on the last stored session — the fill is "
                            "the next session's open"
                            if int(i) + 1 >= len(bars) else
                            "the replay did not take this signal")
                        row["historical"] = True
                        out.append(row)
                        continue
                    # ``tape_ended`` is a trade that is STILL OPEN, not
                    # one that closed: the replay sets it only when the tape
                    # ran out before any exit rule fired. Calling it ended
                    # would put a realised exit on a position the operator
                    # may be holding right now.
                    still_open = t.reason == "tape_ended"
                    row["outcome"] = {
                        "open": bool(still_open),
                        "exit_premium": round(t.fill_out, 2),
                        "entry_premium": round(t.fill_in, 2),
                        "exit_day": t.exit_day,
                        "exit_ms": t.exit_ms,
                        "reason": t.reason,
                        "held_days": t.held_days,
                        "net": round(t.net, 2),
                        "return_pct": round(t.ret * 100, 1),
                        "spot_out": round(t.spot_out, 2),
                        "sessions_left": max(cfg.hold_days - t.held_days, 0),
                        # What the hedge did, kept separate from the net so
                        # a reader can see whether a result came from the
                        # edge or from the hedge being on the right side.
                        "beta": t.beta or None,
                        "market_pnl": round(t.market_pnl, 2) if t.beta else None,
                        "hedge_cost": round(t.hedge_cost, 2) if t.beta else None,
                        "net_unhedged": round(t.gross_unhedged, 2) if t.beta else None,
                    }
                    # A closed trade's "last price" IS what it closed at.
                    row["premium"] = round(t.fill_in, 2)
                    row["stop_premium"] = round(
                        t.fill_in * (1 - cfg.premium_stop_pct / 100), 2)
                    o = row["outcome"]
                    row["state"] = "running" if (o and o["open"]) else "ended"
                    row["historical"] = True
                    row["reason"] = None
                    out.append(row)
    out.sort(key=lambda r: (-r["timestamp_ms"], r["symbol"]))
    _history_cache[key] = (time.monotonic(), out)
    return out


def _history_universe(cfg: SnapbackConfig) -> tuple[str, ...]:
    """Names to replay. In ``fno`` mode the store decides, because a name with
    no stored tape has no history to show whatever the dump says."""
    if cfg.universe_mode == "curated":
        return cfg.universe()
    from app.engines.option_contracts import spec_for
    from app.services import ohlcv_store
    try:
        rows = ohlcv_store.get_status()
    except Exception:                                              # noqa: BLE001
        return cfg.universe()
    names = {str(r.get("symbol")) for r in rows
             if str(r.get("resolution")) == "1d"}
    have = tuple(sorted(n for n in names if spec_for(n) is not None))
    return have[:max(int(cfg.max_universe), 1)] or cfg.universe()


def _named(sig, symbol: str):
    from dataclasses import replace as _replace
    return _replace(sig, symbol=canonical(symbol))


def _estimated(sig, symbol: str, cfg: SnapbackConfig) -> Optional[dict]:
    """The contract a replayed signal would have bought, as far as it is known.

    The expiry is None ON PURPOSE. It is determined by a calendar this function
    does not have, and a date computed from a rule would be wrong for part of
    the history it is labelling.
    """
    from app.engines.snapback.contracts import pick_for
    pick = pick_for(_named(sig, symbol), cfg, dte=cfg.min_dte)
    if pick is None:
        return None
    d = pick.as_dict()
    d["expiry"] = None
    return d


def clear_history_cache() -> None:
    _history_cache.clear()


def snapshot(uid: str) -> dict:
    """Config, what the last scan found, and every reason nothing is armed."""
    cfg = get_config(uid)
    st = status(uid)
    armed = [r for r in st.rows if r["state"] == "armed"]
    from app.services.snapback_validation import auto_execution_blocker
    return {
        "strategy": {**descriptor(), "enabled": cfg.enabled},
        "config": cfg.as_dict(),
        "contract_version": CONTRACT_VERSION,
        "rows": st.rows,
        "armed": len(armed),
        "scanned": st.scanned,
        "scanning": st.scanning,
        "last_scan_ms": st.last_scan_ms,
        "last_error": st.last_error,
        "failures": list(st.failures[:20]),
        "warnings": cfg.warnings(),
        "auto_execution_blocker": auto_execution_blocker(),
        "catchup_sessions": CATCHUP_SESSIONS,
    }
