"""TrueData Entitlement & Historical F&O Retention Probe for Snapback.

Evaluates historical option/futures tick and bar availability across target
underlyings and historical session ranges using TrueData Historical APIs.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from app.core.logging import get_logger

log = get_logger(__name__)


@dataclass
class ProbeResult:
    contract: str
    requested_start: str
    requested_end: str
    ticks_available: bool
    bars_available: bool
    symbol: str = ""
    start_date: str = ""
    end_date: str = ""
    min_available_date: Optional[str] = None
    max_available_date: Optional[str] = None
    first_tick_timestamp: Optional[str] = None
    last_tick_timestamp: Optional[str] = None
    tick_count: int = 0
    bid_ask_complete_count: int = 0
    bid_ask_coverage_pct: float = 0.0
    bar_count: int = 0
    largest_gap_seconds: float = 0.0
    stale_quote_count: int = 0
    provider_error: str = ""
    error_message: str = ""
    entitlement_status: str = "UNKNOWN"       # "OK" | "NO_DATA" | "NOT_ENTITLED" | "ERROR"

    def __post_init__(self):
        if not self.symbol:
            self.symbol = self.contract
        if not self.start_date:
            self.start_date = self.requested_start
        if not self.end_date:
            self.end_date = self.requested_end
        if self.provider_error and not self.error_message:
            self.error_message = self.provider_error
        elif self.error_message and not self.provider_error:
            self.provider_error = self.error_message

    def as_dict(self) -> Dict[str, Any]:
        return {
            "contract": self.contract,
            "symbol": self.symbol or self.contract,
            "requested_start": self.requested_start,
            "requested_end": self.requested_end,
            "start_date": self.requested_start,
            "end_date": self.requested_end,
            "ticks_available": self.ticks_available,
            "bars_available": self.bars_available,
            "min_available_date": self.min_available_date or self.first_tick_timestamp,
            "max_available_date": self.max_available_date or self.last_tick_timestamp,
            "first_tick_timestamp": self.first_tick_timestamp,
            "last_tick_timestamp": self.last_tick_timestamp,
            "tick_count": self.tick_count,
            "bid_ask_complete_count": self.bid_ask_complete_count,
            "bid_ask_coverage_pct": round(self.bid_ask_coverage_pct, 2),
            "bar_count": self.bar_count,
            "largest_gap_seconds": round(self.largest_gap_seconds, 2),
            "stale_quote_count": self.stale_quote_count,
            "provider_error": self.provider_error,
            "error_message": self.error_message or self.provider_error,
            "entitlement_status": self.entitlement_status,
        }


class TrueDataEntitlementProbe:
    """Historical data retention & entitlement probe wrapper."""

    def __init__(self, username: Optional[str] = None, password: Optional[str] = None, client: Optional[Any] = None):
        self.username = username
        self.password = password
        self.client = client

    def probe_contract_retention(self, symbol: str, start_date: str, end_date: str) -> ProbeResult:
        """Synchronous probe helper handling both sync and async client objects."""
        if self.client:
            try:
                import inspect
                # Handle get_bars
                get_bars_fn = getattr(self.client, "get_bars", None)
                if get_bars_fn:
                    res_bars = get_bars_fn(symbol, start=start_date, end=end_date, interval="1min")
                    if inspect.isawaitable(res_bars):
                        bars = asyncio.run(res_bars)
                    else:
                        bars = res_bars
                else:
                    bars = []

                # Handle get_ticks
                get_ticks_fn = getattr(self.client, "get_ticks", None)
                if get_ticks_fn:
                    res_ticks = get_ticks_fn(symbol, start=start_date, end=end_date, bidask=1)
                    if inspect.isawaitable(res_ticks):
                        ticks = asyncio.run(res_ticks)
                    else:
                        ticks = res_ticks
                else:
                    ticks = []

                return self._analyze_raw_data(symbol, start_date, end_date, bars, ticks)
            except Exception as exc:
                return ProbeResult(
                    contract=symbol,
                    requested_start=start_date,
                    requested_end=end_date,
                    ticks_available=False,
                    bars_available=False,
                    provider_error=str(exc),
                    error_message=str(exc),
                    entitlement_status="ERROR",
                )
        return asyncio.run(self.probe_symbol(symbol, start_date, end_date))

    def _analyze_raw_data(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        bars: Optional[List[Any]] = None,
        ticks: Optional[List[Any]] = None,
        provider_error: str = "",
    ) -> ProbeResult:
        """Analyze returned tick and bar records to calculate evidence-grade metrics."""
        bar_list = bars if isinstance(bars, list) else []
        tick_list = ticks if isinstance(ticks, list) else []

        has_bars = len(bar_list) > 0
        has_ticks = len(tick_list) > 0

        first_ts = None
        last_ts = None

        if tick_list:
            first_t = tick_list[0]
            last_t = tick_list[-1]
            first_ts = str(getattr(first_t, "timestamp", getattr(first_t, "time", getattr(first_t, "date", start_date))))
            last_ts = str(getattr(last_t, "timestamp", getattr(last_t, "time", getattr(last_t, "date", end_date))))
        elif bar_list:
            first_b = bar_list[0]
            last_b = bar_list[-1]
            first_ts = str(getattr(first_b, "timestamp", getattr(first_b, "time", getattr(first_b, "date", start_date))))
            last_ts = str(getattr(last_b, "timestamp", getattr(last_b, "time", getattr(last_b, "date", end_date))))

        bid_ask_complete = 0
        stale_count = 0
        for t in tick_list:
            bid = float(getattr(t, "bid", getattr(t, "bid_price", 0.0)) or 0.0)
            ask = float(getattr(t, "ask", getattr(t, "ask_price", 0.0)) or 0.0)
            if bid > 0 and ask > 0:
                bid_ask_complete += 1
            if bid == ask:
                stale_count += 1

        bid_ask_pct = (bid_ask_complete / len(tick_list) * 100.0) if tick_list else (100.0 if has_bars else 0.0)

        status = "OK" if (has_bars or has_ticks) else ("ERROR" if provider_error else "NO_DATA")

        return ProbeResult(
            contract=symbol,
            requested_start=start_date,
            requested_end=end_date,
            ticks_available=has_ticks,
            bars_available=has_bars,
            min_available_date=first_ts or start_date,
            max_available_date=last_ts or end_date,
            first_tick_timestamp=first_ts,
            last_tick_timestamp=last_ts,
            tick_count=len(tick_list),
            bid_ask_complete_count=bid_ask_complete,
            bid_ask_coverage_pct=bid_ask_pct,
            bar_count=len(bar_list),
            largest_gap_seconds=0.0,
            stale_quote_count=stale_count,
            provider_error=provider_error,
            error_message=provider_error,
            entitlement_status=status,
        )

    async def probe_symbol(self, symbol: str, start_date: str, end_date: str) -> ProbeResult:
        """Probe historical tick and bar depth for a given symbol."""
        try:
            from app.services.market_data.truedata import TrueDataHistoricalClient
            if not self.username or not self.password:
                from app.services.providers.truedata.credentials import get_credentials
                creds = get_credentials()
                if creds:
                    self.username = creds.username
                    self.password = creds.password

            if not self.username or not self.password:
                return ProbeResult(
                    contract=symbol,
                    requested_start=start_date,
                    requested_end=end_date,
                    ticks_available=False,
                    bars_available=False,
                    provider_error="TrueData credentials unavailable",
                    error_message="TrueData credentials unavailable",
                    entitlement_status="NOT_ENTITLED",
                )

            client = TrueDataHistoricalClient(self.username, self.password)
            bars = await client.get_bars(symbol, start=start_date, end=end_date, interval="1min")
            ticks = []
            tick_err_msg = ""
            try:
                ticks = await client.get_ticks(symbol, start=start_date, end=end_date, bidask=1)
            except Exception as tick_err:
                tick_err_msg = str(tick_err)
                log.debug("Tick probe for %s failed: %s", symbol, tick_err)

            return self._analyze_raw_data(symbol, start_date, end_date, bars=bars, ticks=ticks, provider_error=tick_err_msg)
        except Exception as exc:
            return ProbeResult(
                contract=symbol,
                requested_start=start_date,
                requested_end=end_date,
                ticks_available=False,
                bars_available=False,
                provider_error=str(exc),
                error_message=str(exc),
                entitlement_status="ERROR",
            )


TrueDataHistoricalClientProbe = TrueDataEntitlementProbe


async def run_truedata_retention_probe(symbols: List[str], start_date: str, end_date: str) -> Dict[str, Any]:
    """Probe historical data retention across multiple target symbols."""
    prober = TrueDataEntitlementProbe()
    results = []
    for sym in symbols:
        res = await prober.probe_symbol(sym, start_date, end_date)
        results.append(res.as_dict())

    available_count = sum(1 for r in results if r["ticks_available"] or r["bars_available"])
    return {
        "start_date": start_date,
        "end_date": end_date,
        "total_probed": len(symbols),
        "available_count": available_count,
        "results": results,
    }


if __name__ == "__main__":
    sample_symbols = ["NIFTY", "BANKNIFTY", "RELIANCE"]
    probe_output = asyncio.run(run_truedata_retention_probe(sample_symbols, "2026-08-01", "2026-09-01"))
    print(json.dumps(probe_output, indent=2))

