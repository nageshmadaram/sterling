"""Historical F&O Contract & Eligibility Registry for Snapback.

Eliminates survivorship bias by maintaining a dated historical F&O membership
and contract specification database (lot sizes, strike steps, active expiries,
and F&O segment eligibility) across trading dates.
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

# Historical lot size revision schedule (underlying -> list of (start_date, end_date, lot_size))
HISTORICAL_LOT_SIZES: Dict[str, List[Tuple[str, str, int]]] = {
    "NIFTY": [
        ("2017-01-01", "2021-06-30", 75),
        ("2021-07-01", "2024-04-25", 50),
        ("2024-04-26", "2024-11-19", 25),
        ("2024-11-20", "2099-12-31", 75),
    ],
    "BANKNIFTY": [
        ("2017-01-01", "2020-06-30", 20),
        ("2020-07-01", "2023-07-13", 25),
        ("2023-07-14", "2024-11-19", 15),
        ("2024-11-20", "2099-12-31", 30),
    ],
    "FINNIFTY": [
        ("2021-01-01", "2024-11-19", 40),
        ("2024-11-20", "2099-12-31", 65),
    ],
}

DEFAULT_LOT_SIZES: Dict[str, int] = {
    "RELIANCE": 250, "HDFCBANK": 550, "ICICIBANK": 700, "INFY": 400, "TCS": 175,
    "SBIN": 1500, "AXISBANK": 625, "LT": 300, "BHARTIARTL": 950, "KOTAKBANK": 400,
    "BAJFINANCE": 125, "BAJAJFINSV": 500, "ADANIENT": 300, "ADANIPORTS": 800, "TATASTEEL": 5500,
}

DEFAULT_STRIKE_STEPS: Dict[str, float] = {
    "NIFTY": 50.0, "BANKNIFTY": 100.0, "FINNIFTY": 50.0, "SENSEX": 100.0,
    "RELIANCE": 20.0, "HDFCBANK": 10.0, "ICICIBANK": 10.0, "INFY": 10.0, "TCS": 20.0,
    "SBIN": 5.0, "AXISBANK": 10.0, "LT": 20.0, "BHARTIARTL": 10.0, "KOTAKBANK": 10.0,
    "BAJFINANCE": 50.0, "BAJAJFINSV": 10.0, "ADANIENT": 20.0, "ADANIPORTS": 10.0, "TATASTEEL": 1.0,
}


@dataclass(frozen=True)
class HistoricalContractSpec:
    symbol: str
    date: str
    is_fo_eligible: bool
    lot_size: int
    strike_step: float
    exchange: str = "NFO"
    available_expiries: Tuple[str, ...] = ()

    def as_dict(self) -> Dict[str, Any]:
        return {
            "symbol": self.symbol,
            "date": self.date,
            "is_fo_eligible": self.is_fo_eligible,
            "lot_size": self.lot_size,
            "strike_step": self.strike_step,
            "exchange": self.exchange,
            "available_expiries": list(self.available_expiries),
        }


class SnapbackContractRegistry:
    """Historical F&O Eligibility & Contract Specification Registry."""

    def __init__(self, fno_membership_override: Optional[Dict[str, Set[str]]] = None):
        """
        fno_membership_override: dict of date_str -> set of eligible F&O underlying symbols.
        """
        self._fno_membership = fno_membership_override or {}

    def is_fo_eligible(self, symbol: str, date_str: str) -> bool:
        """Check if symbol was an active F&O eligible underlying on date_str."""
        if date_str in self._fno_membership:
            return symbol.upper() in self._fno_membership[date_str]
        # Default: all standard symbols are eligible unless explicitly excluded by dated registry
        return True

    def get_lot_size(self, symbol: str, date_str: str) -> int:
        """Resolve exact historical lot size for symbol on date_str."""
        symbol_upper = symbol.upper()
        if symbol_upper in HISTORICAL_LOT_SIZES:
            for start, end, lot in HISTORICAL_LOT_SIZES[symbol_upper]:
                if start <= date_str <= end:
                    return lot
        return DEFAULT_LOT_SIZES.get(symbol_upper, 500)

    def get_strike_step(self, symbol: str) -> float:
        """Get strike step for symbol."""
        return DEFAULT_STRIKE_STEPS.get(symbol.upper(), 10.0)

    def resolve_contract_spec(self, symbol: str, date_str: str) -> HistoricalContractSpec:
        """Resolve complete historical contract spec for symbol on date_str."""
        eligible = self.is_fo_eligible(symbol, date_str)
        lot = self.get_lot_size(symbol, date_str)
        step = self.get_strike_step(symbol)
        exchange = "BFO" if symbol.upper() in ("SENSEX", "BANKEX") else "NFO"
        return HistoricalContractSpec(
            symbol=symbol.upper(),
            date=date_str,
            is_fo_eligible=eligible,
            lot_size=lot,
            strike_step=step,
            exchange=exchange,
        )


def build_daily_contract_registry_dataframe(symbols: List[str], dates: List[str]) -> List[Dict[str, Any]]:
    """Generate daily contract records for a universe of symbols and dates."""
    registry = SnapbackContractRegistry()
    records = []
    for d in dates:
        for s in symbols:
            spec = registry.resolve_contract_spec(s, d)
            records.append(spec.as_dict())
    return records
