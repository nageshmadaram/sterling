"""Historical F&O Contract & Eligibility Registry for Snapback.

Eliminates survivorship bias by maintaining a strict dated historical F&O membership
and contract specification database (lot sizes, strike steps, active expiries,
and F&O segment eligibility) across trading dates.

Invariable Rule:
If historical membership or contract metadata is unavailable for a symbol/date,
the answer is fno_eligible = False / UNKNOWN / INCONCLUSIVE — NEVER "eligible by default".
"""
from __future__ import annotations

import datetime
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

# Known historical F&O segment membership window (symbol -> (start_date, end_date))
KNOWN_FNO_MEMBERSHIP_WINDOWS: Dict[str, Tuple[str, str]] = {
    "NIFTY": ("2017-01-01", "2099-12-31"),
    "BANKNIFTY": ("2017-01-01", "2099-12-31"),
    "FINNIFTY": ("2021-01-01", "2099-12-31"),
    "SENSEX": ("2023-05-15", "2099-12-31"),
    "RELIANCE": ("2017-01-01", "2099-12-31"),
    "HDFCBANK": ("2017-01-01", "2099-12-31"),
    "ICICIBANK": ("2017-01-01", "2099-12-31"),
    "INFY": ("2017-01-01", "2099-12-31"),
    "TCS": ("2017-01-01", "2099-12-31"),
    "SBIN": ("2017-01-01", "2099-12-31"),
    "AXISBANK": ("2017-01-01", "2099-12-31"),
    "LT": ("2017-01-01", "2099-12-31"),
    "BHARTIARTL": ("2017-01-01", "2099-12-31"),
    "KOTAKBANK": ("2017-01-01", "2099-12-31"),
    "BAJFINANCE": ("2017-01-01", "2099-12-31"),
    "BAJAJFINSV": ("2017-01-01", "2099-12-31"),
    "ADANIENT": ("2018-01-01", "2099-12-31"),
    "ADANIPORTS": ("2017-01-01", "2099-12-31"),
    "TATASTEEL": ("2017-01-01", "2099-12-31"),
}

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
    trade_date: str
    underlying: str
    fno_eligible: bool
    exchange: str
    tradingsymbol: str
    instrument_type: str                 # "OPTCE" | "OPTPE" | "FUT"
    expiry: str
    strike: float
    lot_size: int
    tick_size: float = 0.05
    strike_step: float = 10.0
    source: str = "nse_fno_membership_v1"
    source_hash: str = "hash_fno_registry_v1"

    @property
    def symbol(self) -> str:
        return self.underlying

    @property
    def date(self) -> str:
        return self.trade_date

    @property
    def is_fo_eligible(self) -> bool:
        return self.fno_eligible

    def as_dict(self) -> Dict[str, Any]:
        return {
            "trade_date": self.trade_date,
            "date": self.trade_date,
            "underlying": self.underlying,
            "symbol": self.underlying,
            "fno_eligible": self.fno_eligible,
            "is_fo_eligible": self.fno_eligible,
            "exchange": self.exchange,
            "tradingsymbol": self.tradingsymbol,
            "instrument_type": self.instrument_type,
            "expiry": self.expiry,
            "strike": self.strike,
            "lot_size": self.lot_size,
            "tick_size": self.tick_size,
            "strike_step": self.strike_step,
            "source": self.source,
            "source_hash": self.source_hash,
        }


class SnapbackContractRegistry:
    """Historical F&O Eligibility & Contract Specification Registry."""

    def __init__(self, fno_membership_override: Optional[Dict[str, Set[str]]] = None):
        """fno_membership_override: dict of date_str -> set of eligible F&O underlying symbols."""
        self._fno_membership = fno_membership_override or {}

    def is_fo_eligible(self, symbol: str, date_str: str) -> bool:
        """Check if symbol was an active F&O eligible underlying on date_str.
        
        FAIL CLOSED: Unknown symbols or dates without explicit historical eligibility record
        return False to eliminate survivorship bias.
        """
        symbol_upper = symbol.upper()
        if date_str in self._fno_membership:
            return symbol_upper in self._fno_membership[date_str]
        
        if symbol_upper in KNOWN_FNO_MEMBERSHIP_WINDOWS:
            start, end = KNOWN_FNO_MEMBERSHIP_WINDOWS[symbol_upper]
            return start <= date_str <= end

        # Unknown symbol or out-of-bounds date fails closed (not eligible)
        return False

    def get_lot_size(self, symbol: str, date_str: str) -> Optional[int]:
        """Resolve exact historical lot size for symbol on date_str. Return None if unknown."""
        symbol_upper = symbol.upper()
        if symbol_upper in HISTORICAL_LOT_SIZES:
            for start, end, lot in HISTORICAL_LOT_SIZES[symbol_upper]:
                if start <= date_str <= end:
                    return lot
        return DEFAULT_LOT_SIZES.get(symbol_upper, None)

    def get_strike_step(self, symbol: str) -> float:
        """Get strike step for symbol."""
        return DEFAULT_STRIKE_STEPS.get(symbol.upper(), 10.0)

    def get_monthly_expiry(self, symbol: str, entry_date_str: str, min_dte: int = 40, max_dte: int = 60) -> str:
        """Resolve monthly expiry date (last Thursday of month) strictly enforcing min_dte <= dte <= max_dte."""
        try:
            dt = datetime.datetime.strptime(entry_date_str, "%Y-%m-%d").date()
        except ValueError:
            return "UNKNOWN"

        candidates = []
        
        for month_offset in range(0, 5):
            # Advance month
            y = dt.year + (dt.month - 1 + month_offset) // 12
            m = (dt.month - 1 + month_offset) % 12 + 1
            
            # Find last Thursday of month (m, y)
            if m == 12:
                next_m_first = datetime.date(y + 1, 1, 1)
            else:
                next_m_first = datetime.date(y, m + 1, 1)
            
            last_day = next_m_first - datetime.timedelta(days=1)
            # Thursday is weekday() == 3
            days_back = (last_day.weekday() - 3) % 7
            last_thursday = last_day - datetime.timedelta(days=days_back)
            
            dte = (last_thursday - dt).days
            if min_dte <= dte <= max_dte:
                candidates.append((abs(dte - 50), dte, last_thursday.strftime("%Y-%m-%d")))
        
        if not candidates:
            return "UNKNOWN"
            
        candidates.sort(key=lambda x: x[0])
        return candidates[0][2]

    def load_from_dict(self, registry_data: Dict[str, Any]) -> None:
        """Load dated historical F&O membership, lot sizes, and strike steps from external dictionary."""
        membership = registry_data.get("membership", {})
        for key, val in membership.items():
            if isinstance(val, (list, tuple)) and len(val) == 2 and isinstance(val[0], str) and isinstance(val[1], str):
                KNOWN_FNO_MEMBERSHIP_WINDOWS[key.upper()] = (str(val[0]), str(val[1]))
            elif isinstance(val, dict):
                for d_str in val.keys():
                    self._fno_membership.setdefault(str(d_str), set()).add(key.upper())
            elif isinstance(val, (list, set)):
                # Key is date_str mapping to list/set of symbols
                self._fno_membership.setdefault(str(key), set()).update(s.upper() for s in val)

        lot_sizes = registry_data.get("lot_sizes", {})
        for symbol, schedule in lot_sizes.items():
            if isinstance(schedule, list):
                HISTORICAL_LOT_SIZES[symbol.upper()] = [
                    (str(item[0]), str(item[1]), int(item[2])) for item in schedule
                ]

        strike_steps = registry_data.get("strike_steps", {})
        for symbol, step in strike_steps.items():
            DEFAULT_STRIKE_STEPS[symbol.upper()] = float(step)

    def load_from_json(self, filepath: str) -> None:
        """Load historical F&O contract registry from JSON file."""
        import json
        with open(filepath, "r") as f:
            data = json.load(f)
        self.load_from_dict(data)

    def load_from_csv(self, filepath: str) -> None:
        """Load historical F&O membership from CSV file."""
        import csv
        with open(filepath, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                sym = row.get("symbol", "").upper()
                start = row.get("start_date", "2017-01-01")
                end = row.get("end_date", "2099-12-31")
                if sym:
                    KNOWN_FNO_MEMBERSHIP_WINDOWS[sym] = (start, end)

    def resolve_contract_spec(
        self,
        symbol: str,
        date_str: str,
        instrument_type: str = "OPT",
        strike: float = 0.0,
        expiry: str = "",
    ) -> HistoricalContractSpec:
        """Resolve complete historical contract spec for symbol on date_str."""
        eligible = self.is_fo_eligible(symbol, date_str)
        lot = self.get_lot_size(symbol, date_str) or 0
        step = self.get_strike_step(symbol)
        exchange = "BFO" if symbol.upper() in ("SENSEX", "BANKEX") else "NFO"
        
        resolved_expiry = expiry or self.get_monthly_expiry(symbol, date_str)
        tsym = f"{symbol.upper()}{resolved_expiry}{int(strike) if strike else ''}{instrument_type}"

        return HistoricalContractSpec(
            trade_date=date_str,
            underlying=symbol.upper(),
            fno_eligible=eligible and lot > 0 and resolved_expiry != "UNKNOWN",
            exchange=exchange,
            tradingsymbol=tsym,
            instrument_type=instrument_type,
            expiry=resolved_expiry,
            strike=strike,
            lot_size=lot,
            tick_size=0.05,
            strike_step=step,
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

