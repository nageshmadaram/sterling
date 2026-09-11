"""Runtime for the three intraday strategies.

Config persistence, the 5-minute candle fetch, and the scan that turns a tape
into board rows. Every rule lives in ``app.engines.intraday`` and is reachable
from here without a broker object, so the simulation runs the same code the live
scan does.

Nothing in this module places an order. These three strategies are not
walk-forward validated, so ``auto_execute`` defaults off and the runner has no
execution path at all — arming is the operator's, through the shared order
route every other engine already uses.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from app.core.logging import get_logger
from app.engines.intraday import (CONTRACT_VERSION, DESCRIPTORS, IntradayConfig,
                                  STRATEGY_ID, STRATEGY_KEYS, TUPLE_FIELDS,
                                  descriptor, evaluate_all, resample, to_bars)

log = get_logger(__name__)

_IST = timezone(timedelta(hours=5, minutes=30))
_CONFIG_KEY = "intraday_config"
#: Enough 5-minute bars for a 55 EMA plus the prior session the pivots need.
#: Two sessions is 150 bars; 400 covers a long weekend and a half-day.
LOOKBACK_BARS = 400
_CANDLE_TTL_S = 45.0

__all__ = ["get_config", "set_config", "descriptor", "snapshot", "scan_once",
           "status", "evaluate_symbol", "ist_now_ms", "STRATEGY_ID"]


def ist_now_ms() -> int:
    return int(datetime.now(_IST).timestamp() * 1000)


def ist_today() -> date:
    return datetime.now(_IST).date()


# ------------------------------------------------------------------ config

def get_config(uid: str | None = None) -> IntradayConfig:
    """The configuration in force.

    Two fallbacks, deliberately different. **Nothing stored** gives the real
    defaults — a default is what applies when nobody has said otherwise.
    **Stored but invalid** gives defaults with the engine OFF, because a config
    that will not validate must never become a trading config.
    """
    key = f"{_CONFIG_KEY}:{uid}" if uid else _CONFIG_KEY
    try:
        from app.services import db
        # `db.get_config` returns "" both for "nothing stored" and for "the
        # store is not reachable", and those must not mean the same thing here:
        # the first is the real defaults, the second is an engine that has lost
        # its settings and would otherwise start scanning the SHIPPED universe
        # with the operator's own choices silently discarded.
        if hasattr(db, "is_available") and not db.is_available():
            log.warning("%s: config store unavailable; running with defaults OFF",
                        STRATEGY_ID)
            return IntradayConfig(enabled=False)
        raw = db.get_config(key)
        if not raw and uid:
            raw = db.get_config(_CONFIG_KEY)
    except Exception:
        log.warning("%s: config store unavailable; running with defaults OFF", STRATEGY_ID)
        return IntradayConfig(enabled=False)
    if not raw:
        return IntradayConfig()
    try:
        stored = json.loads(raw) if isinstance(raw, str) else raw
        known = IntradayConfig.field_names()
        merged = {**IntradayConfig().as_dict(),
                  **{k: v for k, v in dict(stored).items() if k in known}}
        for name in TUPLE_FIELDS:
            if isinstance(merged.get(name), list):
                merged[name] = tuple(merged[name])
        # Canonicalise BEFORE validating: a stored config written before the
        # de-aliasing existed holds "NIFTY" and "NIFTY 50" as two instruments,
        # and validation would now refuse names that are merely spelled the
        # store's way rather than the option chain's.
        return IntradayConfig(**merged).canonical().validate()
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        log.error("Stored %s config is invalid (%s); running with defaults OFF",
                  STRATEGY_ID, exc)
        return IntradayConfig(enabled=False)


def set_config(values: dict[str, Any], uid: str | None = None) -> IntradayConfig:
    """Persist a partial change. Validation is the engine's, not a second copy."""
    current = get_config(uid).as_dict()
    unknown = sorted(set(values) - set(current))
    if unknown:
        raise ValueError(f"Unknown {STRATEGY_ID} config fields: {', '.join(unknown)}")
    current.update(values)
    for name in TUPLE_FIELDS:
        if isinstance(current.get(name), list):
            current[name] = tuple(current[name])
    cfg = IntradayConfig(**current).canonical().validate()
    from app.services import db
    db.set_config(f"{_CONFIG_KEY}:{uid}" if uid else _CONFIG_KEY,
                  json.dumps(cfg.as_dict(), separators=(",", ":")))
    return cfg


# ------------------------------------------------------------------- state

@dataclass
class ScanState:
    """What the last scan found, per user. In memory by design.

    These rows are a view of a tape that is already stored elsewhere; persisting
    them would create a second source of truth for the same fact, which is the
    failure this codebase keeps paying for.
    """

    rows: list[dict] = field(default_factory=list)
    scanning: bool = False
    last_scan_ms: int = 0
    last_error: Optional[str] = None
    scanned: int = 0
    failures: list[str] = field(default_factory=list)
    candles: dict[int, tuple[float, list]] = field(default_factory=dict)
    #: (symbol, strategy) -> (last fired bar ms, count today, IST date)
    fired: dict[tuple[str, str], tuple[int, int, str]] = field(default_factory=dict)
    #: Armed rows by ``signal_id``, so an arm request names something the scan
    #: actually produced rather than a payload the client assembled.
    signals: dict[str, dict] = field(default_factory=dict)


_state: dict[str, ScanState] = {}


def status(uid: str) -> ScanState:
    return _state.setdefault(uid, ScanState())


# -------------------------------------------------------------- evaluation

def evaluate_symbol(candles, cfg: IntradayConfig, symbol: str, *,
                    catchup: Optional[int] = None) -> list:
    """Bars to evaluations, at the configured timeframe.

    The resample is here rather than in the strategies so every caller gets the
    same answer: the simulation replays 1-minute tape, the live scan asks the
    broker for 5-minute candles, and both must produce the same signal on the
    same session or the replay proves nothing.

    Looks back over ``catchup_bars`` closed bars rather than only the newest
    one. Every rule here fires on a SINGLE bar, so a scan cycle that lands late
    used to skip that bar's signal entirely — the replay found it and the live
    engine never did. The newest firing bar wins, because an older signal has
    had longer for the tape to move away from it.
    """
    rows = resample_for(candles, cfg)
    # A caller that already evaluates EVERY bar passes 1. The replay and the
    # simulation do; looking back there would re-report a signal they already
    # reported on the bar it fired, as though it were new.
    look = max(1, int(catchup if catchup is not None
                      else getattr(cfg, "catchup_bars", 1)))
    newest: dict[str, object] = {}
    for back in range(min(look, max(1, len(rows) - cfg.warmup_bars)) - 1, -1, -1):
        window = rows[: len(rows) - back] if back else rows
        for ev in evaluate_all(to_bars(window), cfg, symbol):
            # A firing bar replaces a quiet one; a NEWER firing bar replaces an
            # older one. A quiet newest bar never erases a signal found behind
            # it, which is the whole point of looking back.
            prev = newest.get(ev.strategy)
            if prev is None or (ev.signal is not None):
                newest[ev.strategy] = ev
    return [newest[k] for k in cfg.enabled_strategies() if k in newest]


def _cooldown_ok(st: ScanState, cfg: IntradayConfig, symbol: str,
                 strategy: str, bar_ms: int) -> Optional[str]:
    """Whether this strategy may fire again on this symbol, and why not if not."""
    today = ist_today().isoformat()
    last_ms, count, day = st.fired.get((symbol, strategy), (0, 0, today))
    if day != today:
        last_ms, count = 0, 0
    if count >= cfg.max_signals_per_symbol_per_day:
        return f"{count} signals already today (cap {cfg.max_signals_per_symbol_per_day})"
    gap_ms = cfg.cooldown_bars * cfg.timeframe_minutes * 60_000
    if last_ms and (bar_ms - last_ms) < gap_ms:
        return f"cooling down — last signal {(bar_ms - last_ms) // 60000}m ago"
    return None


def _record_fire(st: ScanState, symbol: str, strategy: str, bar_ms: int) -> None:
    today = ist_today().isoformat()
    last_ms, count, day = st.fired.get((symbol, strategy), (0, 0, today))
    st.fired[(symbol, strategy)] = (bar_ms, (count + 1) if day == today else 1, today)


def signal_id_for(strategy: str, symbol: str, bar_ms: int) -> str:
    """A signal's identity is its rule, its symbol and the BAR it fired on.

    Stable across rescans of the same bar on purpose: re-running a scan must not
    mint a new id for a setup an operator is already looking at, and the id is
    what the idempotency key is built from — so a double-click cannot become a
    double entry.
    """
    return f"{strategy}:{symbol}:{int(bar_ms)}"


def resample_for(candles, cfg: IntradayConfig) -> list:
    """Bars at the configured timeframe. One place, so every caller agrees."""
    return resample(candles, cfg.timeframe_minutes)


def _row(ev, cfg: IntradayConfig, *, spot: float, contract: Optional[dict],
         cooldown: Optional[str], quote: Optional[dict] = None,
         underlying_token: int = 0) -> dict:
    """One board row. ``state`` is the engine's word, not the UI's."""
    d = ev.as_dict()
    sig = ev.signal
    state = "armed" if (sig and not cooldown) else "watching"
    blockers = list(ev.blockers) + ([cooldown] if cooldown else [])
    return {
        **d,
        "signal_id": (signal_id_for(ev.strategy, ev.symbol, sig.timestamp_ms)
                      if sig else None),
        "state": state,
        "blockers": blockers,
        "strategy_name": DESCRIPTORS.get(ev.strategy, {}).get("name", ev.strategy),
        "spot": round(spot, 2) if spot else None,
        "underlying_token": int(underlying_token or 0),
        "timeframe": cfg.timeframe,
        "contract": contract,
        "quote": quote,
        "generated_at_ms": ist_now_ms(),
    }


# ------------------------------------------------------------------- scan

def _inst(name: str, token: int):
    from app.schemas.instruments import InstrumentMeta
    return InstrumentMeta(underlying=name, tick_size=0.05, strike_step=1.0,
                          exchange_currency="INR", index_name=name,
                          has_options=True, exchange="zerodha", zerodha_token=token)


async def _candles(client, st: ScanState, token: int, name: str,
                   cfg: IntradayConfig) -> list:
    hit = st.candles.get(token)
    if hit and (time.monotonic() - hit[0]) < _CANDLE_TTL_S:
        return hit[1]
    rows = await client.get_candles(_inst(name, token), cfg.timeframe, LOOKBACK_BARS)
    st.candles[token] = (time.monotonic(), rows)
    return rows


def _drop_forming(candles: list, cfg: IntradayConfig) -> list:
    """Drop a bar that has not closed.

    Every rule in this engine is stated on a CLOSE. Evaluating a forming bar
    makes a signal appear and disappear within the same five minutes, which is
    the live-repaint bug this repo has already fixed once elsewhere.
    """
    if not candles:
        return candles
    from app.engines.intraday.models import _epoch_seconds
    last = _epoch_seconds(candles[-1])
    if (datetime.now(timezone.utc).timestamp() - last) < cfg.timeframe_minutes * 60:
        return list(candles[:-1])
    return list(candles)


async def _contract_for(client, name: str, is_index: bool, option_exchange: str,
                        spot: float, direction: str, cfg: IntradayConfig,
                        chain_cache: dict) -> Optional[dict]:
    """The CE/PE this signal would buy, using the shared strike resolver.

    ``None`` is a real answer — an illiquid or unlisted chain — and the row is
    still shown, because a signal whose contract cannot be resolved is exactly
    the thing an operator needs to see rather than have silently dropped.
    """
    from app.services.kite_engine.strikes import chain_rows_for, pick_strike
    try:
        rows = chain_cache.get(option_exchange)
        if rows is None:
            rows = await client.search_instruments("", option_exchange, limit=1_000_000)
            chain_cache[option_exchange] = rows
        chain = chain_rows_for(rows, name, ist_today())
        if not chain:
            return None
        series = cfg.expiry_series_indices if is_index else cfg.expiry_series_stocks
        pick = pick_strike(chain, spot=spot,
                           direction="long" if direction == "BULLISH" else "short",
                           moneyness=cfg.moneyness,
                           expiry_types=tuple(series), today=ist_today())
        if not pick:
            return None
        return {"symbol": pick.option_symbol, "strike": pick.strike,
                "option_type": pick.option_type, "expiry": pick.expiry,
                "dte": pick.dte, "lot_size": pick.lot_size, "token": pick.token,
                "exchange": option_exchange}
    except Exception as exc:
        log.debug("intraday: contract resolution failed for %s: %s", name, exc)
        return None


async def _quote_for(client, contract: dict, cfg: IntradayConfig,
                     spot: float = 0.0) -> Optional[dict]:
    """The contract's own premium and book, so an armed row can be sized.

    A row without a premium cannot be sized, cannot be stopped and cannot be
    bought — so this is fetched during the scan rather than at arm time, where
    an operator would discover it only after clicking Buy.
    """
    key = f"{contract['exchange']}:{contract['symbol']}"
    try:
        quotes = await client.get_quote([key]) or {}
    except Exception as exc:
        log.debug("intraday: quote failed for %s: %s", key, exc)
        return None
    from app.services.gamma_move_scanner import _quote_of, _spread_pct
    q = _quote_of(quotes, key)
    if not q:
        return None
    premium = float(q.get("last_price") or 0.0)
    spread = _spread_pct(q)
    oi = float(q.get("oi") or 0.0)
    volume = float(q.get("volume") or q.get("volume_traded") or 0.0)
    blockers: list[str] = []
    if premium <= 0:
        blockers.append("no traded premium on this contract")
    elif premium < cfg.min_option_premium:
        blockers.append(f"premium {premium:.2f} < {cfg.min_option_premium:g}")
    if spread is not None and cfg.max_spread_pct > 0 and spread > cfg.max_spread_pct:
        blockers.append(f"spread {spread:.1f}% > {cfg.max_spread_pct:g}%")
    if cfg.min_option_oi > 0 and oi < cfg.min_option_oi:
        blockers.append(f"OI {oi:.0f} < {cfg.min_option_oi:g}")
    if cfg.min_option_volume > 0 and volume < cfg.min_option_volume:
        blockers.append(f"volume {volume:.0f} < {cfg.min_option_volume:g}")
    return {"premium": round(premium, 2),
            "spread_pct": round(spread, 2) if spread is not None else None,
            "oi": oi, "volume": volume,
            "delta": solved_delta(contract, premium, spot),
            "blockers": blockers}


def solved_delta(contract: dict, premium: float,
                 spot: float) -> Optional[float]:
    """Delta backed out of the market premium, or ``None`` if it will not solve.

    Kite quotes carry no greeks, so this backs IV out of the traded premium and
    reads delta off it — the same machinery every other option path in this repo
    uses, rather than a second approximation of the same number.

    ``None`` is a real answer and a common one: a premium below intrinsic, a
    contract that has not traded, an expiry that has passed. The caller falls
    back to a fixed percentage stop, which is safer than a delta this did not
    actually solve for.
    """
    try:
        from datetime import date
        from app.services.kite_engine.greeks import black_scholes_greeks, implied_vol
        strike = float(contract.get("strike") or 0.0)
        expiry = str(contract.get("expiry") or "")[:10]
        if premium <= 0 or spot <= 0 or strike <= 0 or not expiry:
            return None
        y, m, d = (int(x) for x in expiry.split("-"))
        dte = max(0.0, (date(y, m, d) - ist_today()).days)
        opt = str(contract.get("option_type") or "CE")
        iv = implied_vol(price=premium, spot=spot, strike=strike, dte_days=dte,
                         option_type=opt)
        if iv <= 0:
            return None
        g = black_scholes_greeks(spot=spot, strike=strike, dte_days=dte, iv=iv,
                                 option_type=opt)
        if not getattr(g, "solved", True):
            return None
        return abs(float(g.delta)) or None
    except Exception:                                              # noqa: BLE001
        return None


async def scan_once(uid: str) -> dict:
    """One universe pass: candles -> evaluations -> rows.

    Symbols are scanned concurrently but bounded, and a symbol that throws is
    recorded as a failure rather than ending the scan — one delisted token must
    not blank the board.
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
    # Cleared UP FRONT, not on success. A scan that throws halfway used to leave
    # the rows it had already armed behind, and an armed row from a scan that
    # did not finish is an invitation to buy a setup nothing has re-checked.
    st.signals = {}
    try:
        from app.services.exchanges.kite import accounts
        from app.services.kite_engine.universe import build_universe, select_scan_universe
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
        universe = select_scan_universe(
            build_universe(nfo_instruments=nfo, bfo_instruments=bfo, equities=nse + bse),
            indices=cfg.scan_indices, stocks=cfg.scan_stocks,
            all_stocks=cfg.scan_all_stocks,
        )
        chain_cache: dict[str, list] = {"NFO": nfo, "BFO": bfo}
        sem = asyncio.Semaphore(6)
        rows: list[dict] = []

        async def one(item) -> None:
            async with sem:
                try:
                    raw = _drop_forming(await _candles(client, st, item.token,
                                                       item.tradingsymbol, cfg), cfg)
                    if not raw:
                        st.failures.append(f"{item.name}: no candles")
                        return
                    evals = evaluate_symbol(raw, cfg, item.name)
                    spot = float(raw[-1].get("close") if isinstance(raw[-1], dict)
                                 else getattr(raw[-1], "close", 0.0) or 0.0)
                    for ev in evals:
                        cooldown = None
                        contract = None
                        quote = None
                        if ev.signal:
                            cooldown = _cooldown_ok(st, cfg, item.name, ev.strategy,
                                                    ev.signal.timestamp_ms)
                            if not cooldown:
                                _record_fire(st, item.name, ev.strategy,
                                             ev.signal.timestamp_ms)
                                contract = await _contract_for(
                                    client, item.name, item.is_index,
                                    item.option_exchange, spot, ev.signal.direction,
                                    cfg, chain_cache)
                                if contract:
                                    quote = await _quote_for(client, contract, cfg,
                                                             spot=spot)
                                    if quote and quote["blockers"]:
                                        # A contract that cannot be traded is not
                                        # an armed row. The reason travels with it
                                        # rather than the row silently vanishing.
                                        cooldown = quote["blockers"][0]
                                else:
                                    cooldown = "no listed contract resolved"
                        row = _row(ev, cfg, spot=spot, contract=contract,
                                   cooldown=cooldown, quote=quote,
                                   underlying_token=int(item.token or 0))
                        rows.append(row)
                except Exception as exc:
                    st.failures.append(f"{item.name}: {exc}")

        await asyncio.gather(*(one(i) for i in universe))
        order = {k: i for i, k in enumerate(STRATEGY_KEYS)}
        rows.sort(key=lambda r: (r["state"] != "armed", order.get(r["strategy"], 9),
                                 r["symbol"]))
        st.rows = rows
        # Armed rows only, and only from THIS scan. A stale armed row is an
        # invitation to buy a setup the tape has already moved past.
        st.signals = {r["signal_id"]: r for r in rows
                      if r["state"] == "armed" and r.get("signal_id")}
        st.scanned = len(universe)
        st.last_scan_ms = ist_now_ms()
    except Exception as exc:
        st.last_error = str(exc)
        log.error("intraday scan failed: %s", exc)
    finally:
        st.scanning = False
    return snapshot(uid)


def snapshot(uid: str) -> dict:
    """Config, what the last scan found, what is held, and why nothing is armed."""
    cfg = get_config(uid)
    st = status(uid)
    armed = [r for r in st.rows if r["state"] == "armed"]
    # The live scan only ever evaluates the LAST closed bar, and every rule here
    # fires on ONE bar. Outside the session — and for most of any session — the
    # honest live answer is "nothing right now", so a board showing only that is
    # blank almost always. The recent history is what makes it readable.
    history: list[dict] = []
    try:
        history = recent_signals(uid)
    except Exception as exc:                                       # noqa: BLE001
        log.debug("intraday: history unavailable for %s: %s", uid, exc)
    positions: list[dict] = []
    record: dict = {}
    mode: dict = {}
    notes: list[dict] = []
    blocker: Optional[str] = None
    try:
        from app.services import intraday_positions as pos_store
        from app.services import intraday_runner as runner
        positions = runner.positions_view(uid)
        record = pos_store.load_record(uid).roll(ist_today().isoformat()).as_dict()
        notes = runner.notes(uid)
        # Read from the account and the shared engine, never from this
        # strategy's own copy — there is no second switch here to go stale.
        mode = {"is_paper": runner.is_paper(uid),
                "auto_execute": runner.auto_execute(uid)}
        blocker = runner.entry_blocker(uid, cfg, "")
    except Exception as exc:                                       # noqa: BLE001
        log.debug("intraday: live view unavailable for %s: %s", uid, exc)
    return {
        "positions": positions,
        "open_positions": len([p for p in positions if p.get("is_open")]),
        "record": record,
        "mode": mode,
        "notes": notes,
        "entry_blocker": blocker,
        "strategy": {**descriptor(), "enabled": cfg.enabled},
        "contract_version": CONTRACT_VERSION,
        "config": cfg.as_dict(),
        "warnings": cfg.warnings(),
        "enabled_strategies": list(cfg.enabled_strategies()),
        "rows": st.rows,
        "history": history,
        "history_sessions": HISTORY_SESSIONS,
        "armed": len(armed),
        "scanned": st.scanned,
        "scanning": st.scanning,
        "failures": st.failures[:20],
        "last_scan_ms": st.last_scan_ms,
        "last_error": st.last_error,
        "generated_at_ms": ist_now_ms(),
    }


# ------------------------------------------------------------------- history

#: Sessions of history the board shows behind the live row.
#:
#: Every rule in this pack fires on ONE bar — a break, a cross, a flip — and the
#: live scan only ever evaluates the last closed bar. So outside market hours,
#: and for most of any session, the honest live answer is "nothing right now",
#: and a board that showed only that would be blank almost always. That is not
#: the strategies being quiet; it is the board asking a question with a very
#: narrow answer.
HISTORY_SESSIONS = 5
_history_cache: dict[str, tuple[float, list[dict]]] = {}
_HISTORY_TTL_S = 300.0


def _trade_row(trade, cfg: IntradayConfig, *, symbol: str) -> dict:
    """One historical signal, with what it would have done next.

    Deliberately the outcome too, not just the entry. "Here is a signal we would
    have taken" is much less useful than "here is one we would have taken and
    here is where it came out", and the second is free: the replay already knows.
    """
    from app.engines.intraday.contracts import canonical, estimated_contract
    direction = str(getattr(trade, "thesis", "BULLISH"))
    bullish = direction == "BULLISH"
    name = canonical(symbol)
    opt = "CE" if bullish else "PE"
    # The board's job is to name a tradable thing. Without this the row said
    # "NIFTY / EQUITY" and quoted the index — describing the thesis and calling
    # it a trade. The strike is arithmetic (spot rounded to the instrument's
    # published step), and the row is flagged `estimated` so it cannot be
    # mistaken for a contract resolved against the broker's real chain.
    contract = estimated_contract(name, trade.entry, opt)
    return {
        "signal_id": signal_id_for(trade.strategy, name, trade.entry_ms),
        "strategy": trade.strategy,
        "strategy_name": DESCRIPTORS.get(trade.strategy, {}).get("name", trade.strategy),
        "symbol": name,
        "state": "ended",
        "blockers": [],
        "spot": round(trade.entry, 2),
        "underlying_token": 0,
        "timeframe": cfg.timeframe,
        "contract": contract,
        "quote": None,
        "historical": True,
        "outcome": {
            "exit": round(trade.exit_price, 2),
            "reason": trade.reason,
            "points": round(trade.gross, 2),
            "r": trade.r,
            "bars_held": trade.bars_held,
            "exit_ms": trade.exit_ms,
        },
        "signal": {
            "strategy": trade.strategy, "symbol": name, "direction": direction,
            "opt_type": opt,
            "timestamp_ms": trade.entry_ms,
            "entry": round(trade.entry, 2), "stop": round(trade.stop, 2),
            "target": round(trade.target, 2), "target2": None,
            "risk": round(abs(trade.entry - trade.stop), 2),
            "rr": round(abs(trade.target - trade.entry) / abs(trade.entry - trade.stop), 2)
            if trade.entry != trade.stop else None,
            "strength": "STRONG", "origin": "replay",
            "reasons": [f"closed {trade.reason} after {trade.bars_held} bars"],
            "metrics": {},
        },
        "generated_at_ms": trade.entry_ms,
    }


def recent_signals(uid: str, *, sessions: int = HISTORY_SESSIONS,
                   symbols: Optional[list[str]] = None) -> list[dict]:
    """What these strategies fired over the last few sessions, from stored bars.

    Reads the OHLCV store, so it works with the market closed, with no broker
    session, and before any live scan has ever run. It replays the SAME
    evaluators the live scan uses — a history built from a second implementation
    would be a history of something else.

    Cached briefly: it is a view of bars that are not changing, and recomputing
    it per poll would put a replay on a 5-second timer.
    """
    from app.engines.intraday.contracts import dedupe
    cfg = get_config(uid)
    raw_names = symbols if symbols is not None else (
        list(cfg.scan_indices) + list(cfg.scan_stocks))
    # One instrument, one row. The store keeps index candles under "NIFTY 50"
    # and options under "NIFTY"; scanning both produced two identical rows for
    # one signal, each inviting a separate trade.
    names = list(dedupe(raw_names))
    if not names:
        return []
    # Keyed on the CONFIG, not just the universe. Keyed on names alone, an
    # operator who changed a threshold saw the old history for five minutes and
    # reasonably concluded the setting did nothing.
    import hashlib
    fingerprint = hashlib.sha1(
        json.dumps(cfg.as_dict(), sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:12]
    key = f"{uid}:{sessions}:{','.join(sorted(names))}:{fingerprint}"
    hit = _history_cache.get(key)
    now = time.monotonic()
    if hit and (now - hit[0]) < _HISTORY_TTL_S:
        return hit[1]

    from app.engines.intraday.backtest import CostModel, replay
    from app.services.ohlcv_store import get_candles
    bars_per_session = max(1, 375 // max(1, cfg.timeframe_minutes))
    # Enough tape for the warmup AND the sessions being shown. Asking for only
    # the visible window would evaluate bars whose indicators are still wrong.
    need = cfg.warmup_bars + bars_per_session * (sessions + 2)
    # Costs OFF for the history view: this shows WHERE the rules fired, and a
    # cost model would quietly turn it into a P&L claim the board is not making.
    free = CostModel(brokerage_per_order=0.0, stt_sell_pct=0.0, exchange_pct=0.0,
                     gst_pct=0.0, misc_pct=0.0, slippage_pct=0.0)
    rows: list[dict] = []
    for name in names:
        try:
            candles = get_candles(name, cfg.timeframe, limit=need) or []
        except Exception as exc:                                   # noqa: BLE001
            log.debug("intraday history: %s unreadable (%s)", name, exc)
            continue
        if len(candles) < cfg.warmup_bars + 5:
            continue
        cutoff = 0
        if candles:
            last = float(candles[-1].get("time") or 0)
            cutoff = int(last - sessions * 86400 * 1.6)   # calendar, not trading
        for strategy in cfg.enabled_strategies():
            try:
                res = replay(candles, cfg, name, strategy, costs=free)
            except Exception as exc:                               # noqa: BLE001
                log.debug("intraday history: %s/%s failed (%s)", name, strategy, exc)
                continue
            for t in res.trades:
                if t.entry_ms // 1000 >= cutoff:
                    rows.append(_trade_row(t, cfg, symbol=name))
    rows.sort(key=lambda r: -int(r["generated_at_ms"]))
    _history_cache[key] = (now, rows)
    return rows


def clear_history_cache() -> None:
    _history_cache.clear()
