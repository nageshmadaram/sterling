"""Realtime multi-underlying scanner for the independent NIFTY ORB family."""
from __future__ import annotations

import asyncio
import time as _time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import Any

from app.engines.nifty_orb_options import Bar, OptionContract, StrategyConfig, build_trade_plan, generate_signal, is_monthly_expiry, select_option
from app.services.nifty_orb_options import _bar, get_config
from app.services.providers.truedata.orb_provider import TrueDataOrbProvider

_IST = timezone(timedelta(hours=5, minutes=30))
_BAR_CACHE_TTL_S = 4.0
#: Instruments per `/quote` request. Kite caps a quote call at 500; staying
#: under it keeps a full option chain to one or two round trips without ever
#: silently dropping the tail of a large chain.
_QUOTE_BATCH = 250


class _MinSpacing:
    """Enforce a minimum interval between calls, across all concurrent callers.

    Deliberately not a token bucket. A bucket with capacity N lets N callers
    fire simultaneously, and Kite counts that burst against the same per-second
    ceiling -- which is exactly what happened here: `scan_user` gathers every
    configured underlying at once, so eight live signals meant eight concurrent
    quote requests and five came back "Too many requests". Holding the lock
    across the sleep is what makes the spacing strict rather than advisory.
    """

    def __init__(self, interval_s: float) -> None:
        self._interval = interval_s
        self._lock = asyncio.Lock()
        self._next = 0.0

    async def wait(self) -> None:
        async with self._lock:
            now = _time.monotonic()
            if now < self._next:
                await asyncio.sleep(self._next - now)
            self._next = max(now, self._next) + self._interval


#: Kite allows 3 quote requests a second. One batched call per chain means this
#: costs a single interval per firing signal, not one per strike.
_QUOTE_PACER = _MinSpacing(1.0 / 3.0)
#: Instrument metadata is stable for a session; re-searching it every tick would
#: spend the broker's rate limit on an answer that does not change.
_META_CACHE_TTL_S = 900.0
_meta_cache: dict[str, tuple[float, object]] = {}
_option_cache: dict[tuple, tuple[float, list[OptionContract]]] = {}
_bar_cache: dict[tuple[str, str, str], tuple[float, list[Bar]]] = {}


def _cache_put(cache: dict, key, value, ttl: float = _BAR_CACHE_TTL_S) -> None:
    """Store an entry and drop every expired one.

    The runner ticks every five seconds for the life of the process, and the
    option-cache key carries the session date, so without eviction these grow
    without bound -- one dead entry per underlying per user per day, forever.
    """
    now = datetime.now().timestamp()
    for stale in [k for k, (stamp, _) in cache.items() if now - stamp >= ttl]:
        cache.pop(stale, None)
    cache[key] = (now, value)


#: BSE derivatives settle on BFO; everything else on NFO. SENSEX and BANKEX are
#: the BSE index underlyings ORB can trade.
_BSE_UNDERLYINGS = {"SENSEX", "BANKEX"}


def _option_exchange(option_symbol: str, underlying: str) -> str:
    return "BFO" if underlying.upper() in _BSE_UNDERLYINGS else "NFO"


def _canonical(symbol: str) -> str:
    return {"NIFTY 50": "NIFTY", "NIFTY BANK": "BANKNIFTY", "FINNIFTY": "FINNIFTY", "NIFTY FIN SERVICE": "FINNIFTY"}.get(symbol.strip().upper(), symbol.strip().upper())


def _truedata_symbol(underlying: str) -> str:
    return {"NIFTY": "NIFTY 50", "BANKNIFTY": "NIFTY BANK", "FINNIFTY": "NIFTY FIN SERVICE"}.get(underlying, underlying)


#: Kite tradingsymbol and exchange for indices whose canonical ORB name differs.
#: Searching NSE for "FINNIFTY" or "SENSEX" finds nothing -- the dump lists them
#: as "NIFTY FIN SERVICE" (NSE) and "SENSEX" (BSE) under instrument_type INDICES.
_KITE_INDEX_ALIASES: dict[str, tuple[str, ...]] = {
    "NIFTY": (("NSE", "NIFTY 50"),),
    "BANKNIFTY": (("NSE", "NIFTY BANK"),),
    "FINNIFTY": (("NSE", "NIFTY FIN SERVICE"),),
    "MIDCPNIFTY": (("NSE", "NIFTY MID SELECT"),),
    "SENSEX": (("BSE", "SENSEX"),),
    "BANKEX": (("BSE", "BANKEX"),),
}


def configured_underlyings(cfg: StrategyConfig) -> list[str]:
    values: list[str] = []
    for raw in cfg.scan_indices or ():
        s = _canonical(str(raw))
        if s and s not in values:
            values.append(s)
    if cfg.scan_stock_contracts:
        selected = [_canonical(str(x)) for x in (cfg.scan_stocks or ())]
        if cfg.scan_all_stocks:
            try:
                from app.services.kite_engine.stock_registry import CURATED_STOCK_NAMES
                selected = list(CURATED_STOCK_NAMES)
            except Exception:
                pass
        for s in selected:
            if s and s not in values:
                values.append(s)
    if not values and cfg.underlying:
        values.append(_canonical(cfg.underlying))
    return values


async def _kite_instrument(client, underlying: str):
    """Resolve an underlying to the InstrumentMeta `get_candles` needs.

    This used to hand `get_candles` a plain `"NSE:SYMBOL"` string. The Kite
    client reads `instrument.zerodha_token`, so every underlying failed with
    `'str' object has no attribute 'zerodha_token'` -- the whole scan returned
    errors for every symbol. An unresolvable symbol now says which symbol and
    why, instead of surfacing an AttributeError.
    """
    from app.services.exchanges import instrument_registry as reg
    from app.services.nifty_orb_universe_runtime import _stock_meta

    cached = _meta_cache.get(underlying)
    if cached and datetime.now().timestamp() - cached[0] < _META_CACHE_TTL_S:
        return cached[1]

    meta = reg.get_instrument(underlying)
    if meta is None:
        # Try the equity name first, then any aliased index tradingsymbol. An
        # index is a legitimate ORB underlying, so INDICES is accepted alongside EQ.
        attempts = [("NSE", underlying), *_KITE_INDEX_ALIASES.get(underlying, ())]
        tried: list[str] = []
        for exchange, tradingsymbol in attempts:
            tried.append(f"{exchange}:{tradingsymbol}")
            try:
                rows = await client.search_instruments(tradingsymbol, exchange, limit=20)
            except Exception:
                continue
            exact = next(
                (r for r in (rows or [])
                 if str(r.get("tradingsymbol") or "").upper() == tradingsymbol.upper()
                 and str(r.get("instrument_type") or "").upper() in {"EQ", "INDICES", ""}),
                None,
            )
            if exact is not None:
                meta = _stock_meta(underlying, exact, exchange=exchange)
                break
        if meta is None:
            raise RuntimeError(f"No Kite instrument matches {underlying} (tried {', '.join(tried)})")
    _cache_put(_meta_cache, underlying, meta, _META_CACHE_TTL_S)
    return meta


def _completed_bars(bars: list[Bar], interval_minutes: int, now: datetime | None = None) -> list[Bar]:
    """Drop the currently-forming candle; keep a candle exactly at its close time."""
    now_ist = (now or datetime.now(_IST)).astimezone(_IST)
    interval = max(1, int(interval_minutes))
    out: list[Bar] = []
    for bar in bars:
        ts = bar.timestamp.astimezone(_IST) if bar.timestamp.tzinfo else bar.timestamp.replace(tzinfo=_IST)
        ts = ts.replace(second=0, microsecond=0)
        close_time = ts + timedelta(minutes=interval)
        if close_time <= now_ist:
            out.append(bar)
    return out


async def _kite_bars_for_underlying(uid: str, underlying: str, interval: str) -> list[Bar]:
    from app.services.exchanges.kite import accounts
    acct = accounts.get_active(uid)
    if not acct:
        raise RuntimeError("No active Kite account")
    key = (uid, underlying, interval)
    cached = _bar_cache.get(key)
    if cached and datetime.now().timestamp() - cached[0] < _BAR_CACHE_TTL_S:
        return _completed_bars(cached[1], int(interval.rstrip("m")))
    client = await accounts.acquire_client(acct)
    rows = await client.get_candles(await _kite_instrument(client, underlying), interval, limit=240)
    bars = [_bar({"timestamp_ms": r.timestamp_ms, "open": r.open, "high": r.high, "low": r.low, "close": r.close, "volume": r.volume}) for r in rows]
    _cache_put(_bar_cache, key, bars)
    return _completed_bars(bars, int(interval.rstrip("m")))


async def _truedata_bars_for_underlying(underlying: str, interval: str) -> list[Bar]:
    from app.core.config import settings
    from app.services.market_data.truedata import TrueDataHistoricalClient
    client = TrueDataHistoricalClient(settings.truedata_username, settings.truedata_password, timeout=settings.truedata_timeout_seconds)
    try:
        bars = await TrueDataOrbProvider(client).bars(_truedata_symbol(underlying), StrategyConfig(interval_minutes=int(interval)))
        return _completed_bars(bars, int(interval))
    finally:
        await client.aclose()


def _expiry_for_mode(eligible: list[tuple], mode: str):
    """Resolve the configured expiry preference using the engine's calendar rule.

    "Weekly is the earliest eligible expiry" was wrong: with the default 0-7 day
    DTE window, the earliest eligible expiry *is* the monthly contract for the
    week that the monthly expires, so a weekly mandate bought a monthly. The rule
    is now :func:`is_monthly_expiry`, shared with the engine and every provider,
    and an unmatched preference returns nothing rather than substituting the
    other bucket.
    """
    if not eligible:
        return None
    mode = mode.strip().lower()
    if mode not in {"nearest", "weekly", "monthly", "any"}:
        raise ValueError("expiry_selection must be nearest, weekly, monthly or any")
    expiries = {exp for exp, _ in eligible}
    if mode in {"nearest", "any"}:
        return min(expiries)
    want_monthly = mode == "monthly"
    matching = [exp for exp in expiries if is_monthly_expiry(exp) is want_monthly]
    return min(matching) if matching else None


async def _kite_option_contracts(uid: str, underlying: str, direction: str, cfg: StrategyConfig) -> list[OptionContract]:
    from app.services.exchanges.kite import accounts
    acct = accounts.get_active(uid)
    if not acct:
        raise RuntimeError("No active Kite account")
    wanted = "CE" if direction == "LONG" else "PE"
    cache_key = (uid, underlying, wanted, cfg.expiry_selection, cfg.expiry_dte_min, cfg.expiry_dte_max, cfg.avoid_expiry_day, datetime.now(_IST).date().isoformat())
    cached = _option_cache.get(cache_key)
    if cached and datetime.now().timestamp() - cached[0] < _BAR_CACHE_TTL_S:
        return cached[1]

    client = await accounts.acquire_client(acct)
    rows = []
    for exchange in ("NFO", "BFO"):
        try:
            rows.extend(await client.search_instruments(underlying, exchange, limit=10000))
        except Exception:
            continue
    today = datetime.now(_IST).date()
    eligible = []
    listed_dtes: list[int] = []
    for row in rows:
        if str(row.get("name") or "").upper() != underlying.upper() or str(row.get("instrument_type") or "").upper() != wanted:
            continue
        try:
            exp = datetime.strptime(str(row.get("expiry"))[:10], "%Y-%m-%d").date()
        except (TypeError, ValueError):
            continue
        dte = (exp - today).days
        listed_dtes.append(dte)
        if dte < cfg.expiry_dte_min or dte > cfg.expiry_dte_max or (cfg.avoid_expiry_day and dte == 0):
            continue
        eligible.append((exp, row))

    # An unreachable DTE window and an unlisted underlying both end with no
    # contracts, but only one of them is a setting the trader can fix. Returning
    # [] for both pushed the explanation down to `select_option`, which reports
    # it as a *liquidity* failure -- so a window of 0-0 on a non-expiry day read
    # as "the chain is illiquid" instead of "you asked for today's expiry and
    # there isn't one". Single stocks make it chronic: they are monthly-only, so
    # their nearest expiry can sit ~30 days out and no ceiling of 7 reaches it.
    if not eligible and listed_dtes:
        nearest = min(listed_dtes, key=lambda d: (d < cfg.expiry_dte_min, abs(d)))
        raise ValueError(
            f"No {underlying} {wanted} expiry within DTE {cfg.expiry_dte_min}-{cfg.expiry_dte_max}"
            f"{' (expiry day excluded)' if cfg.avoid_expiry_day else ''}"
            f" -- nearest listed expiry is {nearest} days out"
        )

    selected_expiry = _expiry_for_mode(eligible, cfg.expiry_selection)
    if selected_expiry is None:
        if eligible:
            raise ValueError(
                f"No {underlying} {wanted} expiry in the DTE window matches "
                f"expiry_selection={cfg.expiry_selection!r}"
            )
        return []

    # One batched quote request per chain, not one per strike.
    #
    # `/quote` has always taken a list. Calling it per contract and awaiting each
    # one made a ~100-strike NIFTY chain cost ~100 sequential round trips, so a
    # scan with eight live signals ran 65 seconds against a board that refetches
    # every 5 — the queue grew faster than it drained. Same data, same fields;
    # only the request count changes.
    wanted_rows: list[tuple[str, str, dict]] = []
    for exp, row in eligible:
        if exp != selected_expiry:
            continue
        symbol = str(row.get("tradingsymbol") or "")
        if not symbol:
            continue
        exchange = str(row.get("exchange") or "NFO").upper()
        wanted_rows.append((f"{exchange}:{symbol}", symbol, row))

    # A batch that raises is a transport failure -- a throttle, a dropped
    # connection -- not a statement about the chain. Swallowing it left the
    # chain empty, which `select_option` then reports as "no liquid contracts
    # satisfy expiry and liquidity settings": a sentence that sends you to the
    # liquidity settings to debug a rate limit. Worse, the empty result was
    # cached, so one throttled call blinded every scan for the next few seconds.
    quotes: dict[str, dict] = {}
    for i in range(0, len(wanted_rows), _QUOTE_BATCH):
        chunk = [key for key, _, _ in wanted_rows[i:i + _QUOTE_BATCH]]
        try:
            await _QUOTE_PACER.wait()
            quotes.update(await client.get_quote(chunk) or {})
        except Exception as exc:
            raise ValueError(
                f"{underlying} {wanted} quote request failed "
                f"({len(chunk)} contracts): {exc}"
            ) from exc

    contracts: list[OptionContract] = []
    for key, symbol, row in wanted_rows:
        # A strike the batch did not return has no price. Defaulting it to zero
        # would invent a contract that `select_option` then ranks on fabricated
        # liquidity, so an unquoted strike is dropped instead.
        q = quotes.get(key)
        if not q:
            continue
        try:
            depth = q.get("depth") or {}
            bid = (depth.get("buy") or [{}])[0]
            ask = (depth.get("sell") or [{}])[0]
            contracts.append(OptionContract(
                symbol=symbol,
                strike=float(row.get("strike") or 0),
                expiry=selected_expiry.isoformat(),
                option_type=wanted,
                ltp=float(q.get("last_price") or 0),
                bid=float(bid.get("price") or 0),
                ask=float(ask.get("price") or 0),
                lot_size=int(row.get("lot_size") or 1),
                delta=float(q["delta"]) if q.get("delta") not in (None, "") else None,
                volume=float(q.get("volume") or 0),
                open_interest=float(q.get("oi") or 0),
            ))
        except Exception:
            continue
    _cache_put(_option_cache, cache_key, contracts)
    return contracts


async def _truedata_option_contracts(underlying: str, direction: str, cfg: StrategyConfig) -> list[OptionContract]:
    from app.core.config import settings
    from app.services.market_data.truedata import TrueDataHistoricalClient
    client = TrueDataHistoricalClient(settings.truedata_username, settings.truedata_password, timeout=settings.truedata_timeout_seconds)
    try:
        return await TrueDataOrbProvider(client).option_chain(underlying, cfg.expiry_selection, cfg)
    finally:
        await client.aclose()


async def _truedata_refresh_option(contract: OptionContract, cfg: StrategyConfig) -> tuple[OptionContract, float | None]:
    """Re-validate the selected contract at the TrueData boundary.

    This delegates to :meth:`TrueDataOrbProvider.refresh_contract` rather than
    repeating its checks. The duplicate that lived here applied the same gates in
    a different order, so the same bad tick surfaced a different error depending
    on which caller saw it first -- and either copy could be hardened without the
    other.
    """
    from app.core.config import settings
    from app.services.market_data.truedata import TrueDataHistoricalClient
    client = TrueDataHistoricalClient(settings.truedata_username, settings.truedata_password, timeout=settings.truedata_timeout_seconds)
    try:
        return await TrueDataOrbProvider(client).refresh_contract(contract, cfg)
    finally:
        await client.aclose()


async def _option_contracts(uid: str, underlying: str, direction: str, cfg: StrategyConfig) -> list[OptionContract]:
    if cfg.data_source == "kite":
        return await _kite_option_contracts(uid, underlying, direction, cfg)
    return await _truedata_option_contracts(underlying, direction, cfg)


async def scan_underlying(uid: str, underlying: str, cfg: StrategyConfig | None = None) -> dict[str, Any]:
    cfg = cfg or get_config()
    symbol = _canonical(underlying)
    local = StrategyConfig(**{**cfg.__dict__, "underlying": symbol})
    interval = str(cfg.interval_minutes)
    bars = await (_kite_bars_for_underlying(uid, symbol, f"{interval}m") if cfg.data_source == "kite" else _truedata_bars_for_underlying(symbol, interval))
    if not bars:
        return {"underlying": symbol, "status": "no_data", "signal": None, "trade": None}

    now = datetime.now(_IST)
    signal = generate_signal(bars, local, as_of=now)
    result = {
        "underlying": symbol,
        "status": "signal" if signal.direction != "NONE" else "watching",
        "signal": signal.to_dict(),
        "spot": bars[-1].close,
        "interval_minutes": cfg.interval_minutes,
        "data_source": cfg.data_source,
        "trade": None,
    }
    if signal.direction == "NONE":
        return result
    try:
        contracts = await _option_contracts(uid, symbol, signal.direction, cfg)
        option = select_option(bars[-1].close, signal.direction, contracts, cfg)
        quote_age = None
        if cfg.data_source == "truedata":
            option, quote_age = await _truedata_refresh_option(option, cfg)
        plan = build_trade_plan(signal, option, cfg, spot=bars[-1].close, now=now)
        result["trade"] = plan.to_dict()
        # The board and the order ticket both need the venue; deriving it in the
        # client from the symbol would be a second, drifting source of truth.
        result["exchange"] = _option_exchange(option.symbol, symbol)
        result["lot_size"] = option.lot_size
        from app.services.nifty_orb_lifecycle import attach_ticket, preview_auto_refusal
        attach_ticket(result)
        filled_today = 0
        state_block = None
        try:
            from app.services.nifty_orb_execution import _state
            filled_today = int(_state(uid).get("count", 0))
        except Exception:
            # Auto fail-closes when trade-state is down. Do not preview a clean
            # ticket that Auto would never place.
            state_block = "trade state unavailable"
        auto_block = state_block or preview_auto_refusal(
            result, cfg, now=now, filled_today=filled_today, max_trades=cfg.max_trades_per_day,
        )
        if auto_block:
            result["auto_block"] = auto_block
            result["reason"] = auto_block
        if quote_age is not None:
            result["quote_age_s"] = round(quote_age, 2)
    except (ValueError, RuntimeError) as exc:
        result["status"] = "signal_unresolved"
        result["trade_error"] = str(exc)
    return result


async def scan_user(uid: str, cfg: StrategyConfig | None = None) -> dict[str, Any]:
    cfg = cfg or get_config()
    if not cfg.enabled:
        return {"enabled": False, "signals": [], "universe": []}
    universe = configured_underlyings(cfg)
    results = await asyncio.gather(*(scan_underlying(uid, s, cfg) for s in universe), return_exceptions=True)
    rows = []
    for symbol, result in zip(universe, results):
        rows.append({"underlying": symbol, "status": "error", "signal": None, "trade": None, "error": str(result)} if isinstance(result, Exception) else result)
    rows.sort(key=lambda r: (r.get("status") not in {"signal", "signal_unresolved"}, r["underlying"]))
    return {
        "enabled": True,
        "universe": universe,
        "signals": rows,
        "signal_count": sum(1 for r in rows if r.get("status") in {"signal", "signal_unresolved"}),
        "data_source": cfg.data_source,
    }
