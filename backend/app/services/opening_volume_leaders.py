"""Kite runtime adapter for the opening-volume leader engine."""

from __future__ import annotations

import asyncio
import time as monotonic_time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from app.engines.option_contracts import Bar
from app.engines.opening_volume_decision import build_opening_decision
from app.engines.opening_volume_leaders import (
    ChaseState,
    EntryPhase,
    IST,
    STRATEGY_CONTRACT,
    LeaderDirection,
    LeaderSignal,
    LeaderTier,
    LiquidityState,
    OpeningVolumeConfig,
    evaluate_leader,
    rank_leaders,
)

_HISTORY_CACHE_TTL_SECONDS = 45.0
_DAILY_CACHE_TTL_SECONDS = 300.0
_HISTORY_CALL_SPACING_SECONDS = 0.36
_INSTRUMENT_MASTER_LIMIT = 100_000
_OPTION_QUOTE_BATCH = 400
_DAILY_CONTEXT_LIMIT = 50
_FNO_OPTION_TYPES = {"CE", "PE"}
_history_cache: dict[tuple[str, str, int, int, datetime], tuple[float, list[Bar]]] = {}
_daily_cache: dict[tuple[str, str, int, date], tuple[float, list[Bar]]] = {}
_META_CACHE: dict[str, tuple[float, Any]] = {}
_META_CACHE_TTL_S = 300.0
_KITE_INDEX_ALIASES = {
    "NIFTY": [("NSE", "NIFTY 50"), ("NSE", "NIFTY FIFTY")],
    "NIFTY 50": [("NSE", "NIFTY 50")],
    "BANKNIFTY": [("NSE", "NIFTY BANK")],
    "FINNIFTY": [("NSE", "NIFTY FIN SERVICE")],
    "MIDCPNIFTY": [("NSE", "NIFTY MID SELECT")],
    "SENSEX": [("BSE", "SENSEX")],
    "BANKEX": [("BSE", "BANKEX")],
}


async def _kite_instrument(client: Any, underlying: str) -> Any:
    import time
    from app.schemas.instruments import InstrumentMeta
    from app.services.exchanges import instrument_registry as reg

    cached = _META_CACHE.get(underlying)
    if cached and time.time() - cached[0] < _META_CACHE_TTL_S:
        return cached[1]

    meta = reg.get_instrument(underlying)
    if meta is None:
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
                meta = InstrumentMeta(
                    underlying=underlying,
                    quote_currency="INR",
                    contract_multiplier=1.0,
                    tick_size=float(exact.get("tick_size") or 0.05),
                    strike_step=1.0,
                    has_options=True,
                    exchange="zerodha",
                    exchange_currency="INR",
                    index_name=str(exact.get("tradingsymbol") or underlying),
                    zerodha_token=int(exact.get("instrument_token") or 0),
                    zerodha_symbol=f"{exchange}:{exact.get('tradingsymbol') or underlying}",
                    lot_size=int(exact.get("lot_size") or 1),
                )
                break
        if meta is None:
            raise RuntimeError(f"No Kite instrument matches {underlying} (tried {', '.join(tried)})")
    _META_CACHE[underlying] = (time.time(), meta)
    return meta



class _HistoricalPacer:
    """Space Kite historical requests so a concurrent scan cannot burst 429s."""

    def __init__(self, spacing_seconds: float) -> None:
        self._spacing_seconds = spacing_seconds
        self._lock = asyncio.Lock()
        self._next_at = 0.0

    async def wait(self) -> None:
        async with self._lock:
            now = monotonic_time.monotonic()
            if now < self._next_at:
                await asyncio.sleep(self._next_at - now)
                now = monotonic_time.monotonic()
            self._next_at = now + self._spacing_seconds


_historical_pacer = _HistoricalPacer(_HISTORY_CALL_SPACING_SECONDS)


def _as_ist(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=IST)
    return value.astimezone(IST)


@dataclass(frozen=True)
class LiveLeaderScanConfig:
    symbols: tuple[str, ...] = ()
    scan_all_stocks: bool = True
    include_watch: bool = False
    include_weak: bool = False
    max_candidates: int = 250
    concurrency: int = 3
    history_calendar_days: int = 45
    sector_by_symbol: dict[str, str] | None = None

    def validate(self) -> LiveLeaderScanConfig:
        if self.max_candidates < 1 or self.max_candidates > 500:
            raise ValueError("max_candidates must be between 1 and 500")
        if self.concurrency < 1 or self.concurrency > 8:
            raise ValueError("concurrency must be between 1 and 8")
        if self.history_calendar_days < 30 or self.history_calendar_days > 60:
            raise ValueError("history_calendar_days must be between 30 and 60")
        if not self.scan_all_stocks and not self.symbols:
            raise ValueError("select symbols or enable scan_all_stocks")
        if self.sector_by_symbol is not None:
            for symbol, sector in self.sector_by_symbol.items():
                if not str(symbol).strip() or not str(sector).strip():
                    raise ValueError("sector_by_symbol requires non-empty symbols and sectors")
        return self


def _normalize_requested_symbols(symbols: Sequence[str]) -> list[str]:
    return list(
        dict.fromkeys(symbol.strip().upper() for symbol in symbols if symbol.strip())
    )


async def _instrument_masters(client) -> tuple[list[dict], list[dict]]:
    nfo_rows, nse_rows = await asyncio.gather(
        client.search_instruments("", "NFO", limit=_INSTRUMENT_MASTER_LIMIT),
        client.search_instruments("", "NSE", limit=_INSTRUMENT_MASTER_LIMIT),
    )
    return list(nfo_rows or []), list(nse_rows or [])


def _discover_fno_equity_symbols_from_rows(
    nfo_rows: Sequence[dict],
    nse_rows: Sequence[dict],
) -> list[str]:
    """Return current single-stock F&O underlyings from Kite's instrument masters.

    The NFO dump alone also contains index options.  Requiring an exact NSE cash
    equity row removes those indices and any stale/non-cash derivative names
    without relying on a hardcoded symbol list.
    """

    option_underlyings = {
        str(row.get("name") or "").strip().upper()
        for row in nfo_rows or []
        if str(row.get("instrument_type") or "").strip().upper() in _FNO_OPTION_TYPES
        and str(row.get("name") or "").strip()
    }
    cash_equities = {
        str(row.get("tradingsymbol") or "").strip().upper()
        for row in nse_rows or []
        if str(row.get("instrument_type") or "").strip().upper() == "EQ"
        and str(row.get("tradingsymbol") or "").strip()
    }
    symbols = sorted(option_underlyings & cash_equities)
    if not symbols:
        raise RuntimeError(
            "Kite instrument masters returned no NSE cash equities with NFO options"
        )
    return symbols


async def _discover_fno_equity_symbols(client) -> list[str]:
    nfo_rows, nse_rows = await _instrument_masters(client)
    return _discover_fno_equity_symbols_from_rows(nfo_rows, nse_rows)


def _resolve_universe_from_rows(
    nfo_rows: Sequence[dict],
    nse_rows: Sequence[dict],
    config: LiveLeaderScanConfig,
) -> tuple[list[str], dict[str, object]]:
    available = _discover_fno_equity_symbols_from_rows(nfo_rows, nse_rows)
    available_set = set(available)
    if config.scan_all_stocks:
        requested = available
        source = "kite_nfo_options_intersect_nse_equities"
    else:
        requested = _normalize_requested_symbols(config.symbols)
        source = "explicit_current_fno_equities"
    unsupported = [symbol for symbol in requested if symbol not in available_set]
    if unsupported:
        raise ValueError(
            "symbols are not current NSE cash equities with NFO options: "
            + ", ".join(sorted(unsupported))
        )
    selected = requested[: config.max_candidates]
    return selected, {
        "source": source,
        "available_fno_equity_count": len(available),
        "requested_count": len(requested),
        "selected_count": len(selected),
        "truncated": len(selected) < len(requested),
        "symbols": selected,
    }


async def _resolve_universe(
    client,
    config: LiveLeaderScanConfig,
) -> tuple[list[str], dict[str, object]]:
    nfo_rows, nse_rows = await _instrument_masters(client)
    return _resolve_universe_from_rows(nfo_rows, nse_rows, config)


def _bar_from_kite(row: Sequence[Any]) -> Bar:
    if len(row) < 5:
        raise ValueError("Kite historical row has fewer than five OHLC fields")
    try:
        timestamp = datetime.fromisoformat(
            str(row[0]).replace("+0530", "+05:30")
        )
    except ValueError as exc:
        raise ValueError("invalid Kite historical timestamp") from exc
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=IST)
    else:
        timestamp = timestamp.astimezone(IST)
    return Bar(
        timestamp=timestamp,
        open=float(row[1]),
        high=float(row[2]),
        low=float(row[3]),
        close=float(row[4]),
        volume=float(row[5]) if len(row) > 5 else 0.0,
    )


async def _history(
    client,
    *,
    uid: str,
    symbol: str,
    token: int,
    as_of: datetime,
    history_calendar_days: int,
) -> list[Bar]:
    observed_at = _as_ist(as_of)
    # A new minute makes one more candle causally available.  Including the
    # completion boundary prevents a cache hit at 09:17 from reusing a 09:16
    # snapshot that could not yet contain the completed 09:16 bar.
    completed_through = observed_at.replace(second=0, microsecond=0)
    key = (uid, symbol, token, history_calendar_days, completed_through)
    cached = _history_cache.get(key)
    now_mono = monotonic_time.monotonic()
    if cached and now_mono - cached[0] < _HISTORY_CACHE_TTL_SECONDS:
        return cached[1]

    start = observed_at - timedelta(days=history_calendar_days)
    await _historical_pacer.wait()
    payload = await client.get_historical(
        token,
        "minute",
        start.strftime("%Y-%m-%d 09:00:00"),
        observed_at.strftime("%Y-%m-%d %H:%M:%S"),
    )
    bars: list[Bar] = []
    for row in (payload or {}).get("candles", []) or []:
        try:
            bars.append(_bar_from_kite(row))
        except (IndexError, TypeError, ValueError):
            continue
    bars.sort(key=lambda bar: bar.timestamp)
    _history_cache[key] = (monotonic_time.monotonic(), bars)
    expired = [
        cache_key
        for cache_key, (created_at, _) in _history_cache.items()
        if monotonic_time.monotonic() - created_at >= _HISTORY_CACHE_TTL_SECONDS
    ]
    for cache_key in expired:
        _history_cache.pop(cache_key, None)
    return bars


async def _daily_market_context(
    client,
    *,
    uid: str,
    symbol: str,
    token: int,
    signal: LeaderSignal,
    as_of: datetime,
) -> dict[str, object]:
    """Fetch causal daily evidence for the visible 50-DMA and 52-week fields."""

    if not callable(getattr(client, "get_historical", None)):
        raise RuntimeError("Kite daily-history method unavailable")
    observed_at = _as_ist(as_of)
    key = (uid, symbol, token, observed_at.date())
    cached = _daily_cache.get(key)
    now_mono = monotonic_time.monotonic()
    if cached and now_mono - cached[0] < _DAILY_CACHE_TTL_SECONDS:
        rows = cached[1]
    else:
        await _historical_pacer.wait()
        start = observed_at - timedelta(days=400)
        payload = await client.get_historical(
            token,
            "day",
            start.strftime("%Y-%m-%d 00:00:00"),
            observed_at.strftime("%Y-%m-%d %H:%M:%S"),
        )
        rows = []
        for raw in (payload or {}).get("candles", []) or []:
            try:
                bar = _bar_from_kite(raw)
            except (IndexError, TypeError, ValueError):
                continue
            # A provider's current daily candle is still forming.  It cannot
            # enter the moving average or 52-week history for this snapshot.
            if bar.timestamp.date() < signal.session_date:
                rows.append(bar)
        rows.sort(key=lambda bar: bar.timestamp)
        _daily_cache[key] = (now_mono, rows)
        for cache_key, (created_at, _) in list(_daily_cache.items()):
            if now_mono - created_at >= _DAILY_CACHE_TTL_SECONDS:
                _daily_cache.pop(cache_key, None)

    last_year = rows[-252:]
    sma_50 = (
        sum(bar.close for bar in rows[-50:]) / 50.0 if len(rows) >= 50 else None
    )
    high_candidates = [bar.high for bar in last_year]
    low_candidates = [bar.low for bar in last_year]
    high_candidates.append(signal.session_high)
    low_candidates.append(signal.session_low)
    high_52w = max(high_candidates) if high_candidates else None
    low_52w = min(low_candidates) if low_candidates else None
    if sma_50 is None or signal.direction is LeaderDirection.NEUTRAL:
        trend_aligned: bool | None = None
    elif signal.direction is LeaderDirection.UP:
        trend_aligned = signal.current_price >= sma_50
    else:
        trend_aligned = signal.current_price <= sma_50
    distance_from_high = (
        (signal.current_price / high_52w - 1.0) * 100.0
        if high_52w and high_52w > 0
        else None
    )
    return {
        "status": "available" if len(rows) >= 50 else "insufficient_history",
        "daily_session_count": len(rows),
        "sma_50": sma_50,
        "trend_50dma_aligned": trend_aligned,
        "high_52w": high_52w,
        "low_52w": low_52w,
        "distance_from_52w_high_pct": distance_from_high,
        "source": "Kite daily candles; current/forming daily bar excluded",
    }


def _breadth(signals: Sequence[LeaderSignal]) -> dict[str, object]:
    advances = sum(
        1
        for signal in signals
        if signal.day_change_pct is not None and signal.day_change_pct > 0
    )
    declines = sum(
        1
        for signal in signals
        if signal.day_change_pct is not None and signal.day_change_pct < 0
    )
    unchanged = sum(1 for signal in signals if signal.day_change_pct == 0)
    observed = advances + declines + unchanged
    green_pct = advances / observed * 100.0 if observed else None
    if observed == 0 or advances == declines:
        mood = "neutral"
    elif advances >= declines * 1.5:
        mood = "bullish"
    elif declines >= advances * 1.5:
        mood = "bearish"
    else:
        mood = "neutral"
    if green_pct is None:
        participation = "unknown"
    elif green_pct >= 60.0:
        participation = "strong_green"
    elif green_pct < 40.0:
        participation = "selective"
    else:
        participation = "balanced"
    return {
        "advances": advances,
        "declines": declines,
        "unchanged": unchanged,
        "observed": observed,
        "advance_decline_ratio": advances / declines if declines else None,
        "green_pct": green_pct,
        "mood": mood,
        "participation": participation,
        "mood_rule": "bullish when advances >= 1.5x declines; bearish at the inverse",
    }


def _sector_alignments(
    signals: Sequence[LeaderSignal],
    sector_by_symbol: dict[str, str] | None,
    *,
    ratio: float = 1.5,
) -> dict[str, bool | None]:
    """Evaluate sector tailwind only when an explicit sector map is supplied."""

    if not sector_by_symbol:
        return {signal.symbol: None for signal in signals}
    normalized = {
        str(symbol).strip().upper(): str(sector).strip().upper()
        for symbol, sector in sector_by_symbol.items()
        if str(symbol).strip() and str(sector).strip()
    }
    counts: dict[str, list[int]] = {}
    for signal in signals:
        sector = normalized.get(signal.symbol)
        if sector is None or signal.day_change_pct is None:
            continue
        bucket = counts.setdefault(sector, [0, 0])
        if signal.day_change_pct > 0:
            bucket[0] += 1
        elif signal.day_change_pct < 0:
            bucket[1] += 1
    result: dict[str, bool | None] = {}
    for signal in signals:
        sector = normalized.get(signal.symbol)
        if sector is None or sector not in counts or signal.direction is LeaderDirection.NEUTRAL:
            result[signal.symbol] = None
            continue
        advances, declines = counts[sector]
        if advances + declines == 0:
            result[signal.symbol] = None
        elif signal.direction is LeaderDirection.UP:
            result[signal.symbol] = bool(
                advances > 0 and (declines == 0 or advances >= declines * ratio)
            )
        else:
            result[signal.symbol] = bool(
                declines > 0 and (advances == 0 or declines >= advances * ratio)
            )
    return result


def _playbook_context(
    signal: LeaderSignal,
    breadth: dict[str, object],
) -> dict[str, object]:
    mood = str(breadth["mood"])
    if mood == "neutral":
        breadth_alignment = "neutral"
        recommended_risk_pct = 0.5
    elif (
        mood == "bullish"
        and signal.direction is LeaderDirection.UP
        or mood == "bearish"
        and signal.direction is LeaderDirection.DOWN
    ):
        breadth_alignment = "aligned"
        recommended_risk_pct = 1.0
    else:
        breadth_alignment = "against"
        recommended_risk_pct = 0.0

    blockers: list[str] = []
    cautions: list[str] = []
    if signal.direction is LeaderDirection.NEUTRAL:
        blockers.append("neutral opening candle")
    if signal.liquidity_state is LiquidityState.FAIL:
        blockers.append("Layer-1 liquidity failed")
    elif signal.liquidity_state is LiquidityState.UNKNOWN:
        cautions.append("Layer-1 turnover history unavailable")
    if signal.tier in {LeaderTier.WEAK, LeaderTier.WATCH}:
        blockers.append("below the documented SPURT leader tier")
    elif signal.tier is LeaderTier.SPURT:
        cautions.append("SPURT is probe-only; primary confirmation requires STRONG+")
    if not signal.orb_aligned:
        blockers.append("no aligned ORB break")
    elif not signal.orb_fresh:
        blockers.append("ORB break is older than five minutes")
    if signal.chase_state is ChaseState.CHASE:
        blockers.append("price is more than 1% beyond the ORB reference")
    elif signal.chase_state is ChaseState.CAUTION:
        cautions.append("price is 0.5%–1% beyond the ORB reference")
    if signal.stop_too_wide:
        cautions.append("opening-range stop exceeds 1.5%; halve size or skip")
        recommended_risk_pct = min(recommended_risk_pct, 0.5)
    if signal.third_day_repeat is True:
        blockers.append("third consecutive opening-volume leader day")
    elif signal.third_day_repeat is None:
        cautions.append("repeat-day status unavailable from supplied history")
    if breadth_alignment == "against":
        blockers.append("signal direction is against market breadth")
    elif breadth_alignment == "neutral":
        cautions.append("neutral breadth caps documented risk at 0.5%")
    if breadth.get("reliable") is False:
        cautions.append("breadth coverage is below 90% of the selected universe")

    if signal.entry_phase in {
        EntryPhase.NO_NEW_ENTRY,
        EntryPhase.EXIT,
        EntryPhase.FLAT,
        EntryPhase.CLOSED,
    }:
        blockers.append(f"{signal.entry_phase.value.replace('_', ' ')} time window")
    elif signal.entry_phase is EntryPhase.MANAGE:
        cautions.append("manage window: no full-size fresh chase")
        recommended_risk_pct = min(recommended_risk_pct, 0.5)
    elif signal.entry_phase is EntryPhase.DECAY:
        cautions.append("decay window: fresh entries are half-size maximum")
        recommended_risk_pct = min(recommended_risk_pct, 0.5)

    if blockers:
        known_gate_status = "blocked"
        recommended_risk_pct = 0.0
    elif cautions:
        known_gate_status = "caution"
    else:
        known_gate_status = "passes_known_gates"
    return {
        "known_gate_status": known_gate_status,
        "known_gate_blockers": blockers,
        "known_gate_cautions": cautions,
        "breadth_alignment": breadth_alignment,
        "recommended_risk_pct": recommended_risk_pct,
        "primary_gate_complete": False,
        "unverified_private_gates": [
            "ORION score >=55",
            "ORION conviction >=5/7",
            "ORION hidden amber/LATE predicates",
        ],
        "entry_reference": "09:15 ORB boundary",
        "staged_entry_pct": [30, 30, 40],
        "first_scale_r_multiple": [1.5, 2.0],
        "daily_loss_cap_r": 2.0,
        "weekly_loss_cap_r": 4.0,
        "max_open_positions": 2,
    }


def _parse_expiry(value: object) -> date | None:
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


async def _best_option_payloads(
    client,
    signals: Sequence[LeaderSignal],
    nfo_rows: Sequence[dict],
    *,
    session_date: date,
    spot_prices: dict[str, float],
) -> dict[str, dict[str, object]]:
    """Select and quote ORION's visible nearest-strike directional contract."""

    selections: dict[str, tuple[str, dict, date]] = {}
    for signal in signals:
        if signal.direction is LeaderDirection.NEUTRAL:
            continue
        wanted = "CE" if signal.direction is LeaderDirection.UP else "PE"
        eligible: list[tuple[date, float, dict]] = []
        for row in nfo_rows:
            if (
                str(row.get("name") or "").strip().upper() != signal.symbol
                or str(row.get("instrument_type") or "").strip().upper() != wanted
            ):
                continue
            expiry = _parse_expiry(row.get("expiry"))
            if expiry is None or expiry < session_date:
                continue
            try:
                strike = float(row.get("strike") or 0.0)
            except (TypeError, ValueError):
                continue
            if strike <= 0 or not str(row.get("tradingsymbol") or "").strip():
                continue
            eligible.append((expiry, strike, row))
        if not eligible:
            continue
        nearest_expiry = min(expiry for expiry, _, _ in eligible)
        spot = spot_prices.get(signal.symbol, signal.current_price)
        _, _, selected = min(
            (item for item in eligible if item[0] == nearest_expiry),
            key=lambda item: (
                abs(item[1] - spot),
                item[1],
                str(item[2].get("tradingsymbol") or ""),
            ),
        )
        exchange = str(selected.get("exchange") or "NFO").strip().upper()
        tradingsymbol = str(selected.get("tradingsymbol") or "").strip()
        selections[signal.symbol] = (
            f"{exchange}:{tradingsymbol}",
            selected,
            nearest_expiry,
        )

    quotes: dict[str, dict] = {}
    keys = [key for key, _, _ in selections.values()]
    for index in range(0, len(keys), _OPTION_QUOTE_BATCH):
        try:
            quotes.update(
                await client.get_quote(keys[index : index + _OPTION_QUOTE_BATCH]) or {}
            )
        except Exception:  # noqa: BLE001
            # Option metadata is supporting evidence.  One failed quote batch
            # must neither hide the signals nor erase earlier successful chunks.
            continue

    payloads: dict[str, dict[str, object]] = {}
    for symbol, (key, row, expiry) in selections.items():
        quote = quotes.get(key) or {}
        try:
            premium = float(quote.get("last_price") or 0.0)
            strike = float(row.get("strike") or 0.0)
            lot_size = int(row.get("lot_size") or 0)
        except (TypeError, ValueError):
            premium = 0.0
            strike = 0.0
            lot_size = 0
        depth = quote.get("depth") or {}
        buy = (depth.get("buy") or [{}])[0]
        sell = (depth.get("sell") or [{}])[0]
        try:
            bid = float(buy.get("price") or 0.0)
            ask = float(sell.get("price") or 0.0)
        except (AttributeError, TypeError, ValueError):
            bid = 0.0
            ask = 0.0
        dte = (expiry - session_date).days
        if premium > 0 and lot_size > 0:
            option: dict[str, object] | None = {
                "tradingsymbol": str(row.get("tradingsymbol") or ""),
                "exchange": str(row.get("exchange") or "NFO").upper(),
                "option_type": str(row.get("instrument_type") or "").upper(),
                "strike": strike,
                "expiry": expiry.isoformat(),
                "dte": dte,
                "ltp": premium,
                "bid": bid,
                "ask": ask,
                "lot_size": lot_size,
                "lot_cost": premium * lot_size,
                "premium_stop_price": premium * 0.70,
                "premium_target_price": premium * 1.50,
                "premium_risk_per_lot": premium * lot_size * 0.30,
                "beginner_expiry_warning": dte <= 1,
            }
            status = "quoted"
        else:
            option = None
            status = "quote_unavailable"
        payloads[symbol] = {
            "option": option,
            "option_status": status,
            "option_rule": "nearest strike on the nearest non-expired listed expiry",
        }
    return payloads


async def _live_cash_prices(
    client,
    signals: Sequence[LeaderSignal],
) -> dict[str, float]:
    keys = [f"NSE:{signal.symbol}" for signal in signals]
    if not keys:
        return {}
    try:
        quotes = await client.get_ltp(keys) or {}
    except Exception:  # noqa: BLE001
        return {}
    prices: dict[str, float] = {}
    for signal in signals:
        quote = quotes.get(f"NSE:{signal.symbol}") or {}
        try:
            price = float(quote.get("last_price") or 0.0)
        except (TypeError, ValueError):
            continue
        if price > 0:
            prices[signal.symbol] = price
    return prices


def _signal_payload(
    signal: LeaderSignal,
    breadth: dict[str, object],
    option_payloads: dict[str, dict[str, object]],
    live_prices: dict[str, float],
    daily_contexts: dict[str, dict[str, object]],
    sector_alignments: dict[str, bool | None],
) -> dict:
    payload = signal.to_dict()
    payload["live_price"] = live_prices.get(signal.symbol)
    payload["price_source"] = (
        "kite_live_quote" if signal.symbol in live_prices else "latest_completed_minute"
    )
    playbook = _playbook_context(signal, breadth)
    market_context = daily_contexts.get(
        signal.symbol,
        {
            "status": "unavailable",
            "source": "Kite daily context unavailable",
        },
    )
    decision = build_opening_decision(
        signal,
        breadth_alignment=str(playbook["breadth_alignment"]),
        market_context=market_context,
        sector_alignment=sector_alignments.get(signal.symbol),
    )
    playbook["sterling_gate_complete"] = decision["execution_eligible"]
    payload["playbook"] = playbook
    payload["market_context"] = market_context
    payload["decision"] = decision
    payload.update(
        option_payloads.get(
            signal.symbol,
            {
                "option": None,
                "option_status": "no_listed_contract",
                "option_rule": (
                    "nearest strike on the nearest non-expired listed expiry"
                ),
            },
        )
    )
    return payload


async def scan_kite_leaders(
    uid: str,
    *,
    as_of: datetime | None = None,
    scan_config: LiveLeaderScanConfig | None = None,
    signal_config: OpeningVolumeConfig | None = None,
) -> dict:
    """Scan current broker-listed F&O equities without placing any orders."""

    scan_config = (scan_config or LiveLeaderScanConfig()).validate()
    signal_config = (signal_config or OpeningVolumeConfig()).validate()
    normalized_uid = str(uid or "").strip()
    if not normalized_uid:
        raise ValueError("authenticated user is required")
    observed_at = _as_ist(as_of or datetime.now(IST))
    if observed_at.weekday() >= 5:
        raise ValueError("opening-volume scans require an NSE weekday session")
    if observed_at.time() < time(9, 16):
        raise ValueError("the 09:15 one-minute candle is not complete until 09:16 IST")

    from app.services.exchanges.kite import accounts

    account = accounts.get_active(normalized_uid)
    if not account:
        raise RuntimeError("no active Kite account")
    client = await accounts.acquire_client(account)
    nfo_rows, nse_rows = await _instrument_masters(client)
    symbols, universe = _resolve_universe_from_rows(nfo_rows, nse_rows, scan_config)
    semaphore = asyncio.Semaphore(scan_config.concurrency)

    async def evaluate(
        symbol: str,
    ) -> tuple[LeaderSignal | None, str | None, int | None]:
        async with semaphore:
            try:
                instrument = await _kite_instrument(client, symbol)
                token = int(instrument.zerodha_token or 0)
                if token <= 0:
                    raise RuntimeError(f"no Kite cash token for {symbol}")
                bars = await _history(
                    client,
                    uid=normalized_uid,
                    symbol=symbol,
                    token=token,
                    as_of=observed_at,
                    history_calendar_days=scan_config.history_calendar_days,
                )
                return (
                    evaluate_leader(
                        symbol,
                        bars,
                        as_of=observed_at,
                        config=signal_config,
                    ),
                    None,
                    token,
                )
            # A single broker/instrument failure must stay isolated so the
            # advisory universe scan can report all other symbols.
            except Exception as exc:  # noqa: BLE001
                return None, str(exc), None

    outcomes = await asyncio.gather(*(evaluate(symbol) for symbol in symbols))
    evaluated: list[LeaderSignal] = []
    tokens: dict[str, int] = {}
    failures: list[dict[str, str]] = []
    for symbol, (signal, error, token) in zip(symbols, outcomes):
        if signal is not None:
            evaluated.append(signal)
            if token is not None:
                tokens[symbol] = token
        else:
            failures.append(
                {"symbol": symbol, "error": error or "unknown evaluation failure"}
            )

    ranked = rank_leaders(evaluated)
    # ORION cards are ORB events, not a dump of every opening-volume
    # classification.  Keep the complete evaluated universe for breadth, but
    # publish a card only after the first later high/low breach exists.  This
    # prevents include_weak=True from returning nearly the entire F&O master.
    event_ranked = [signal for signal in ranked if signal.orb_break_time is not None]
    pending_orb_count = len(ranked) - len(event_ranked)
    leaders = [signal for signal in event_ranked if signal.is_leader]
    watch = [signal for signal in event_ranked if signal.tier is LeaderTier.WATCH]
    weak = [signal for signal in event_ranked if signal.tier is LeaderTier.WEAK]
    breadth = _breadth(evaluated)
    breadth["coverage_pct"] = (
        len(evaluated) / len(symbols) * 100.0 if symbols else 0.0
    )
    breadth["reliable"] = bool(
        symbols and len(evaluated) / len(symbols) >= 0.90
    )
    breadth["source"] = "successfully evaluated current F&O cash equities"
    sector_alignments = _sector_alignments(
        evaluated,
        scan_config.sector_by_symbol,
    )
    candidates = [
        *leaders,
        *(watch if scan_config.include_watch else []),
        *(weak if scan_config.include_weak else []),
    ]
    daily_contexts: dict[str, dict[str, object]] = {}

    async def daily_context(signal: LeaderSignal) -> tuple[str, dict[str, object]]:
        token = tokens.get(signal.symbol)
        if token is None:
            return signal.symbol, {
                "status": "unavailable",
                "source": "Kite cash token unavailable",
            }
        try:
            context = await _daily_market_context(
                client,
                uid=normalized_uid,
                symbol=signal.symbol,
                token=token,
                signal=signal,
                as_of=observed_at,
            )
        except Exception as exc:  # noqa: BLE001
            context = {
                "status": "unavailable",
                "source": "Kite daily context request failed",
                "error_type": type(exc).__name__,
            }
        return signal.symbol, context

    daily_targets = candidates[:_DAILY_CONTEXT_LIMIT]
    daily_contexts.update(
        await asyncio.gather(*(daily_context(signal) for signal in daily_targets))
    )
    for signal in candidates[_DAILY_CONTEXT_LIMIT:]:
        daily_contexts[signal.symbol] = {
            "status": "not_requested",
            "source": (
                f"daily enrichment is capped at the top {_DAILY_CONTEXT_LIMIT} "
                "displayed candidates"
            ),
        }
    if as_of is None:
        live_prices = await _live_cash_prices(client, candidates)
        option_payloads = await _best_option_payloads(
            client,
            candidates,
            nfo_rows,
            session_date=observed_at.date(),
            spot_prices=live_prices,
        )
    else:
        # Current Kite quotes cannot be attached to a historical snapshot
        # without leaking future prices into replay results.
        live_prices = {}
        option_payloads = {
            signal.symbol: {
                "option": None,
                "option_status": "historical_quote_unavailable",
                "option_rule": (
                    "historical option quotes are omitted to prevent look-ahead"
                ),
            }
            for signal in candidates
        }
    return {
        "strategy": STRATEGY_CONTRACT,
        "as_of": observed_at.isoformat(),
        "universe": universe,
        "universe_count": len(symbols),
        "evaluated_count": len(evaluated),
        "event_count": len(event_ranked),
        "pending_orb_count": pending_orb_count,
        "leader_count": len(leaders),
        "watch_count": len(watch),
        "weak_count": len(weak),
        "enrichment": {
            "daily_context_limit": _DAILY_CONTEXT_LIMIT,
            "daily_context_count": len(daily_targets),
            "option_quote_count": len(candidates) if as_of is None else 0,
            "historical_quotes_omitted": as_of is not None,
        },
        "breadth": breadth,
        "leaders": [
            _signal_payload(
                signal,
                breadth,
                option_payloads,
                live_prices,
                daily_contexts,
                sector_alignments,
            )
            for signal in leaders
        ],
        "watch": [
            _signal_payload(
                signal,
                breadth,
                option_payloads,
                live_prices,
                daily_contexts,
                sector_alignments,
            )
            for signal in watch
        ]
        if scan_config.include_watch
        else [],
        "weak": [
            _signal_payload(
                signal,
                breadth,
                option_payloads,
                live_prices,
                daily_contexts,
                sector_alignments,
            )
            for signal in weak
        ]
        if scan_config.include_weak
        else [],
        "failures": failures,
    }
