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
    symbol: str
    start_date: str
    end_date: str
    ticks_available: bool
    bars_available: bool
    min_available_date: Optional[str] = None
    max_available_date: Optional[str] = None
    error_message: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "ticks_available": self.ticks_available,
            "bars_available": self.bars_available,
            "min_available_date": self.min_available_date,
            "max_available_date": self.max_available_date,
            "error_message": self.error_message,
        }


class TrueDataEntitlementProbe:
    """Historical data retention & entitlement probe wrapper."""

    def __init__(self, username: Optional[str] = None, password: Optional[str] = None):
        self.username = username
        self.password = password

    async def probe_symbol(self, symbol: str, start_date: str, end_date: str) -> ProbeResult:
        """Probe historical tick and bar depth for a given symbol."""
        try:
            from app.services.market_data.truedata import TrueDataHistoricalClient
            if not self.username or not self.password:
                # If credentials not passed directly, try retrieving from stored provider credentials
                from app.services.providers.truedata.credentials import get_credentials
                creds = get_credentials()
                if creds:
                    self.username = creds.username
                    self.password = creds.password

            if not self.username or not self.password:
                return ProbeResult(
                    symbol=symbol,
                    start_date=start_date,
                    end_date=end_date,
                    ticks_available=False,
                    bars_available=False,
                    error_message="TrueData credentials unavailable",
                )

            client = TrueDataHistoricalClient(self.username, self.password)
            # Fetch sample bar data to check availability
            bars = await client.get_bars(symbol, interval="1min", start_time=start_date, end_time=end_date)
            has_bars = bool(bars and len(bars) > 0)

            # Probe tick availability
            has_ticks = False
            try:
                ticks = await client.get_ticks(symbol, start_time=start_date, end_time=end_date, bidask=1)
                has_ticks = bool(ticks and len(ticks) > 0)
            except Exception as tick_err:
                log.debug("Tick probe for %s failed: %s", symbol, tick_err)

            return ProbeResult(
                symbol=symbol,
                start_date=start_date,
                end_date=end_date,
                ticks_available=has_ticks,
                bars_available=has_bars,
                min_available_date=start_date if (has_bars or has_ticks) else None,
                max_available_date=end_date if (has_bars or has_ticks) else None,
            )
        except Exception as exc:
            return ProbeResult(
                symbol=symbol,
                start_date=start_date,
                end_date=end_date,
                ticks_available=False,
                bars_available=False,
                error_message=str(exc),
            )


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
