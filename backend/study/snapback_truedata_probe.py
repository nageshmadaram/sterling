"""TrueData Entitlement & Historical F&O Retention Probe for Snapback.

Evaluates historical option/futures tick and bar availability across target
underlyings and historical session ranges using TrueData Historical APIs.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from app.core.logging import get_logger

log = get_logger(__name__)


def _extract_val(item: Any, keys: Tuple[str, ...], default: Any = None) -> Any:
    """Extract value safely from dict or object instance."""
    if isinstance(item, dict):
        for k in keys:
            if k in item:
                return item[k]
    for k in keys:
        if hasattr(item, k):
            return getattr(item, k)
    return default


@dataclass
class ProbeResult:
    contract: str
    requested_start: str
    requested_end: str
    symbol: str = ""
    start_date: str = ""
    end_date: str = ""
    actual_first_tick: Optional[str] = None
    actual_last_tick: Optional[str] = None
    first_tick_timestamp: Optional[str] = None
    last_tick_timestamp: Optional[str] = None
    min_available_date: Optional[str] = None
    max_available_date: Optional[str] = None
    ticks_available: bool = False
    bars_available: bool = False
    tick_status: str = "UNKNOWN"              # "OK" | "NO_TICKS" | "NOT_ENTITLED" | "CONFIG_MISSING" | "ERROR"
    bar_status: str = "UNKNOWN"               # "OK" | "NO_BARS" | "NOT_ENTITLED" | "CONFIG_MISSING" | "ERROR"
    tick_count: int = 0
    bid_ask_complete_count: int = 0
    bid_ask_coverage_pct: float = 0.0
    bar_count: int = 0
    largest_intraday_gap_seconds: float = 0.0
    largest_interday_gap_seconds: float = 0.0
    largest_gap_seconds: float = 0.0
    stale_quote_count: int = 0
    provider_tick_error: str = ""
    provider_bar_error: str = ""
    error_message: str = ""
    entitlement_status: str = "UNKNOWN"       # "OK" | "BARS_ONLY" | "NO_DATA" | "NOT_ENTITLED" | "CONFIG_MISSING" | "ERROR"

    def __post_init__(self):
        if not self.symbol:
            self.symbol = self.contract
        if not self.start_date:
            self.start_date = self.requested_start
        if not self.end_date:
            self.end_date = self.requested_end
        if self.actual_first_tick and not self.first_tick_timestamp:
            self.first_tick_timestamp = self.actual_first_tick
        elif self.first_tick_timestamp and not self.actual_first_tick:
            self.actual_first_tick = self.first_tick_timestamp
        if self.actual_last_tick and not self.last_tick_timestamp:
            self.last_tick_timestamp = self.actual_last_tick
        elif self.last_tick_timestamp and not self.actual_last_tick:
            self.actual_last_tick = self.last_tick_timestamp
        if not self.min_available_date:
            self.min_available_date = self.actual_first_tick
        if not self.max_available_date:
            self.max_available_date = self.actual_last_tick

        combined_err = (self.provider_tick_error + " " + self.provider_bar_error).strip()
        if combined_err and not self.error_message:
            self.error_message = combined_err
        elif self.error_message and not combined_err:
            self.provider_tick_error = self.error_message

    def as_dict(self) -> Dict[str, Any]:
        return {
            "contract": self.contract,
            "symbol": self.symbol or self.contract,
            "requested_start": self.requested_start,
            "requested_end": self.requested_end,
            "start_date": self.requested_start,
            "end_date": self.requested_end,
            "actual_first_tick": self.actual_first_tick,
            "actual_last_tick": self.actual_last_tick,
            "first_tick_timestamp": self.first_tick_timestamp,
            "last_tick_timestamp": self.last_tick_timestamp,
            "min_available_date": self.min_available_date,
            "max_available_date": self.max_available_date,
            "ticks_available": self.ticks_available,
            "bars_available": self.bars_available,
            "tick_status": self.tick_status,
            "bar_status": self.bar_status,
            "tick_count": self.tick_count,
            "bid_ask_complete_count": self.bid_ask_complete_count,
            "bid_ask_coverage_pct": round(self.bid_ask_coverage_pct, 2),
            "bar_count": self.bar_count,
            "largest_intraday_gap_seconds": round(self.largest_intraday_gap_seconds, 2),
            "largest_interday_gap_seconds": round(self.largest_interday_gap_seconds, 2),
            "largest_gap_seconds": round(self.largest_gap_seconds or max(self.largest_intraday_gap_seconds, self.largest_interday_gap_seconds), 2),
            "stale_quote_count": self.stale_quote_count,
            "provider_tick_error": self.provider_tick_error,
            "provider_bar_error": self.provider_bar_error,
            "error_message": self.error_message,
            "entitlement_status": self.entitlement_status,
        }


def resolve_truedata_credentials(user_id: Optional[str] = None) -> Tuple[Optional[str], Optional[str]]:
    """Resolve active TrueData username and password across multi-tenant DB and env fallbacks."""
    # 1. Try truedata_service from app.services.providers.truedata
    try:
        from app.services import db
        db.init()
        from app.services.providers import truedata as truedata_service
        truedata_service.bootstrap()
        if user_id:
            acct = truedata_service.get_active(user_id)
            if not acct:
                accts = truedata_service.list_credentials(user_id)
                if accts:
                    acct = accts[0]
            if acct and acct.username and acct.password:
                return acct.username, acct.password
        from app.services.providers.truedata import credentials
        for a in credentials._credentials.values():
            if a.username and a.password:
                return a.username, a.password
    except Exception as exc:
        log.debug("truedata_service credential resolution notice: %s", exc)

    # 2. Direct read-only query from SQLite database file if DB service is degraded or locked
    for db_path in ["backend/sterling_paper.db", "sterling_paper.db"]:
        try:
            import sqlite3
            from app.core.security import decrypt
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            cur = conn.cursor()
            if user_id:
                cur.execute("SELECT username, password_enc FROM truedata_credentials WHERE user_id = ? AND username != '' LIMIT 1", (user_id,))
            else:
                cur.execute("SELECT username, password_enc FROM truedata_credentials WHERE username != '' ORDER BY is_active DESC LIMIT 1")
            row = cur.fetchone()
            conn.close()
            if row and row[0] and row[1]:
                return row[0], decrypt(row[1])
        except Exception:
            pass

    # 3. Environment variables / Settings fallback
    import os
    try:
        from app.core.config import settings
        if settings.truedata_username and settings.truedata_password:
            return settings.truedata_username, settings.truedata_password
    except Exception:
        pass

    u = os.environ.get("TRUEDATA_USERNAME", "")
    p = os.environ.get("TRUEDATA_PASSWORD", "")
    if u and p:
        return u, p

    return None, None


class TrueDataEntitlementProbe:
    """Historical data retention & entitlement probe wrapper (Runner 1.1)."""

    def __init__(self, username: Optional[str] = None, password: Optional[str] = None, client: Optional[Any] = None, user_id: Optional[str] = None):
        self.username = username
        self.password = password
        self.client = client
        self.user_id = user_id

    def probe_contract_retention(self, symbol: str, start_date: str, end_date: str) -> ProbeResult:
        """Synchronous probe helper handling both sync and async client objects."""
        if self.client:
            try:
                import inspect
                bars = []
                bar_err = ""
                get_bars_fn = getattr(self.client, "get_bars", None)
                if get_bars_fn:
                    try:
                        res_bars = get_bars_fn(symbol, start=start_date, end=end_date, interval="1min")
                        bars = asyncio.run(res_bars) if inspect.isawaitable(res_bars) else res_bars
                    except Exception as e:
                        bar_err = str(e)

                ticks = []
                tick_err = ""
                get_ticks_fn = getattr(self.client, "get_ticks", None)
                if get_ticks_fn:
                    try:
                        res_ticks = get_ticks_fn(symbol, start=start_date, end=end_date, bidask=1)
                        ticks = asyncio.run(res_ticks) if inspect.isawaitable(res_ticks) else res_ticks
                    except Exception as e:
                        tick_err = str(e)

                return self._analyze_raw_data(
                    symbol,
                    start_date,
                    end_date,
                    bars=bars,
                    ticks=ticks,
                    provider_tick_error=tick_err,
                    provider_bar_error=bar_err,
                )
            except Exception as exc:
                return ProbeResult(
                    contract=symbol,
                    requested_start=start_date,
                    requested_end=end_date,
                    ticks_available=False,
                    bars_available=False,
                    tick_status="ERROR",
                    bar_status="ERROR",
                    provider_tick_error=str(exc),
                    provider_bar_error=str(exc),
                    error_message=str(exc),
                    entitlement_status="ERROR",
                )
        return asyncio.run(self.probe_symbol(symbol, start_date, end_date, user_id=self.user_id))

    async def resolve_provider_symbols(self, search_term: str, segment: str = "fo", allexpiry: bool = True) -> List[str]:
        """Discover exact provider-recognized symbol names via get_all_symbols API."""
        if not self.username or not self.password:
            u, p = resolve_truedata_credentials(self.user_id)
            self.username = u
            self.password = p

        if not self.username or not self.password:
            log.warning("Cannot resolve symbols: TrueData credentials unavailable")
            return []

        try:
            from app.services.market_data.truedata import TrueDataHistoricalClient
            client = self.client or TrueDataHistoricalClient(self.username, self.password)
            res = await client.get_all_symbols(segment=segment, search=search_term, allexpiry=allexpiry)
            records = []
            if isinstance(res, dict):
                if "Records" in res and isinstance(res["Records"], list):
                    records = res["Records"]
                elif "records" in res and isinstance(res["records"], list):
                    records = res["records"]
            elif isinstance(res, list):
                records = res

            symbols = []
            for r in records:
                if isinstance(r, dict):
                    sym = r.get("symbol") or r.get("Symbol") or r.get("tradingsymbol")
                elif isinstance(r, (list, tuple)) and len(r) > 0:
                    sym = r[0]
                elif isinstance(r, str):
                    sym = r
                else:
                    sym = None
                if sym and str(sym) not in symbols:
                    symbols.append(str(sym))
            return symbols
        except Exception as exc:
            log.warning("Symbol resolution for '%s' failed: %s", search_term, exc)
            return []

    def _analyze_raw_data(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        bars: Optional[List[Any]] = None,
        ticks: Optional[List[Any]] = None,
        provider_tick_error: str = "",
        provider_bar_error: str = "",
        decision_timestamp: Optional[str] = None,
        max_allowed_quote_age_ms: float = 5000.0,
    ) -> ProbeResult:
        """Analyze returned tick and bar records to calculate evidence-grade metrics."""
        bar_list = bars if isinstance(bars, list) else []
        tick_list = ticks if isinstance(ticks, list) else []

        has_bars = len(bar_list) > 0
        has_ticks = len(tick_list) > 0

        first_ts = None
        last_ts = None
        dts: List[datetime] = []

        # Parse ticks
        bid_ask_complete = 0
        stale_count = 0
        
        # Parse decision datetime if supplied
        decision_dt = None
        if decision_timestamp:
            try:
                cleaned_dec = decision_timestamp.replace("T", " ").split("+")[0].split("Z")[0]
                decision_dt = datetime.strptime(cleaned_dec, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                pass

        for t in tick_list:
            ts_val = _extract_val(t, ("timestamp", "time", "date", "datetime"))
            dt = None
            if ts_val:
                ts_str = str(ts_val)
                if not first_ts:
                    first_ts = ts_str
                last_ts = ts_str
                try:
                    cleaned_ts = ts_str.replace("T", " ").split("+")[0].split("Z")[0]
                    if len(cleaned_ts) == 10:
                        dt = datetime.strptime(cleaned_ts, "%Y-%m-%d")
                    elif "." in cleaned_ts:
                        dt = datetime.strptime(cleaned_ts, "%Y-%m-%d %H:%M:%S.%f")
                    else:
                        dt = datetime.strptime(cleaned_ts, "%Y-%m-%d %H:%M:%S")
                    dts.append(dt)
                except ValueError:
                    pass

            bid = float(_extract_val(t, ("bid", "bid_price", "bidprice"), 0.0) or 0.0)
            ask = float(_extract_val(t, ("ask", "ask_price", "askprice"), 0.0) or 0.0)
            if bid > 0 and ask > 0:
                bid_ask_complete += 1

            # Quote staleness: age relative to decision time or previous tick timestamp gap
            if dt and decision_dt:
                quote_age_ms = (decision_dt - dt).total_seconds() * 1000.0
                if quote_age_ms > max_allowed_quote_age_ms:
                    stale_count += 1
            elif bid > 0 and ask > 0 and bid == ask:
                stale_count += 1

        # Parse bars if ticks missing
        if not dts and bar_list:
            for b in bar_list:
                ts_val = _extract_val(b, ("timestamp", "time", "date", "datetime"))
                if ts_val:
                    ts_str = str(ts_val)
                    if not first_ts:
                        first_ts = ts_str
                    last_ts = ts_str
                    try:
                        cleaned_ts = ts_str.replace("T", " ").split("+")[0].split("Z")[0]
                        if len(cleaned_ts) == 10:
                            dt = datetime.strptime(cleaned_ts, "%Y-%m-%d")
                        elif "." in cleaned_ts:
                            dt = datetime.strptime(cleaned_ts, "%Y-%m-%d %H:%M:%S.%f")
                        else:
                            dt = datetime.strptime(cleaned_ts, "%Y-%m-%d %H:%M:%S")
                        dts.append(dt)
                    except ValueError:
                        pass

        # Calculate gaps
        largest_intraday_gap = 0.0
        largest_interday_gap = 0.0
        if len(dts) >= 2:
            sorted_dts = sorted(dts)
            for i in range(len(sorted_dts) - 1):
                t1, t2 = sorted_dts[i], sorted_dts[i + 1]
                gap_sec = (t2 - t1).total_seconds()
                if t1.date() == t2.date():
                    largest_intraday_gap = max(largest_intraday_gap, gap_sec)
                else:
                    largest_interday_gap = max(largest_interday_gap, gap_sec)

        # CRITICAL RULE: Bars-only does NOT equal bid/ask coverage!
        # If tick_list is empty, bid_ask_coverage_pct MUST be 0.0.
        if has_ticks and len(tick_list) > 0:
            bid_ask_pct = (bid_ask_complete / len(tick_list)) * 100.0
        else:
            bid_ask_pct = 0.0
            bid_ask_complete = 0

        # Status resolution: distinguish CONFIG_MISSING from NOT_ENTITLED
        if "credentials unavailable" in provider_tick_error.lower() or "credentials unavailable" in provider_bar_error.lower():
            tick_status = "CONFIG_MISSING"
            bar_status = "CONFIG_MISSING"
            entitlement_status = "CONFIG_MISSING"
        else:
            tick_status = "OK" if has_ticks else ("NOT_ENTITLED" if "403" in provider_tick_error or "Forbidden" in provider_tick_error or "401" in provider_tick_error else ("ERROR" if provider_tick_error else "NO_DATA"))
            bar_status = "OK" if has_bars else ("NOT_ENTITLED" if "403" in provider_bar_error or "Forbidden" in provider_bar_error or "401" in provider_bar_error else ("ERROR" if provider_bar_error else "NO_DATA"))

            if has_ticks:
                entitlement_status = "OK"
            elif has_bars:
                entitlement_status = "BARS_ONLY"
            elif tick_status == "NOT_ENTITLED" or bar_status == "NOT_ENTITLED":
                entitlement_status = "NOT_ENTITLED"
            elif provider_tick_error or provider_bar_error:
                entitlement_status = "ERROR"
            else:
                entitlement_status = "NO_DATA"

        return ProbeResult(
            contract=symbol,
            requested_start=start_date,
            requested_end=end_date,
            actual_first_tick=first_ts,
            actual_last_tick=last_ts,
            ticks_available=has_ticks,
            bars_available=has_bars,
            tick_status=tick_status,
            bar_status=bar_status,
            tick_count=len(tick_list),
            bid_ask_complete_count=bid_ask_complete,
            bid_ask_coverage_pct=bid_ask_pct,
            bar_count=len(bar_list),
            largest_intraday_gap_seconds=largest_intraday_gap,
            largest_interday_gap_seconds=largest_interday_gap,
            largest_gap_seconds=max(largest_intraday_gap, largest_interday_gap),
            stale_quote_count=stale_count,
            provider_tick_error=provider_tick_error,
            provider_bar_error=provider_bar_error,
            entitlement_status=entitlement_status,
        )

    async def probe_symbol(self, symbol: str, start_date: str, end_date: str, user_id: Optional[str] = None) -> ProbeResult:
        """Probe historical tick and bar depth for a given symbol."""
        try:
            from app.services.market_data.truedata import TrueDataHistoricalClient
            if not self.username or not self.password:
                u, p = resolve_truedata_credentials(user_id or self.user_id)
                self.username = u
                self.password = p

            if not self.username or not self.password:
                return ProbeResult(
                    contract=symbol,
                    requested_start=start_date,
                    requested_end=end_date,
                    ticks_available=False,
                    bars_available=False,
                    tick_status="CONFIG_MISSING",
                    bar_status="CONFIG_MISSING",
                    provider_tick_error="TrueData credentials unavailable",
                    provider_bar_error="TrueData credentials unavailable",
                    error_message="TrueData credentials unavailable (CONFIG_MISSING)",
                    entitlement_status="CONFIG_MISSING",
                )

            client = self.client or TrueDataHistoricalClient(self.username, self.password)
            bars = []
            bar_err_msg = ""
            try:
                bars = await client.get_bars(symbol, start=start_date, end=end_date, interval="1min")
            except Exception as b_err:
                bar_err_msg = str(b_err)
                log.debug("Bar probe for %s failed: %s", symbol, b_err)

            ticks = []
            tick_err_msg = ""
            try:
                ticks = await client.get_ticks(symbol, start=start_date, end=end_date, bidask=1)
            except Exception as tick_err:
                tick_err_msg = str(tick_err)
                log.debug("Tick probe for %s failed: %s", symbol, tick_err)

            return self._analyze_raw_data(
                symbol,
                start_date,
                end_date,
                bars=bars,
                ticks=ticks,
                provider_tick_error=tick_err_msg,
                provider_bar_error=bar_err_msg,
            )
        except Exception as exc:
            return ProbeResult(
                contract=symbol,
                requested_start=start_date,
                requested_end=end_date,
                ticks_available=False,
                bars_available=False,
                tick_status="ERROR",
                bar_status="ERROR",
                provider_tick_error=str(exc),
                provider_bar_error=str(exc),
                error_message=str(exc),
                entitlement_status="ERROR",
            )


TrueDataHistoricalClientProbe = TrueDataEntitlementProbe


async def run_truedata_retention_probe(symbols: List[str], start_date: str, end_date: str, user_id: Optional[str] = None) -> Dict[str, Any]:
    """Probe historical data retention across multiple target symbols."""
    prober = TrueDataEntitlementProbe(user_id=user_id)
    results = []
    for sym in symbols:
        res = await prober.probe_symbol(sym, start_date, end_date)
        results.append(res.as_dict())

    available_count = sum(1 for r in results if r["ticks_available"] or r["bars_available"])
    tick_available_count = sum(1 for r in results if r["ticks_available"])
    return {
        "start_date": start_date,
        "end_date": end_date,
        "total_probed": len(symbols),
        "available_count": available_count,
        "tick_available_count": tick_available_count,
        "results": results,
    }


if __name__ == "__main__":
    sample_symbols = ["NIFTY-I", "BANKNIFTY-I", "RELIANCE-I"]
    probe_output = asyncio.run(run_truedata_retention_probe(sample_symbols, "2026-08-01", "2026-09-01"))
    print(json.dumps(probe_output, indent=2))
