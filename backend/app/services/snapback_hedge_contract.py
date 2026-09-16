"""Hedge future selection.

The hedge must outlive the option it hedges. Picking a near-month future before the
40-60 DTE option is chosen can leave an open position unhedged at futures expiry —
an exposure nobody decided to take. No compatible contract is INCONCLUSIVE, never a
silent fallback to the nearest one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Dict, Iterable, Optional

log = logging.getLogger(__name__)


class HedgeContractError(Exception):
    """No future can hedge this option."""


@dataclass(frozen=True)
class HedgeContract:
    tradingsymbol: str
    expiry: date
    lot_size: int
    instrument_token: int
    exchange: str = "NFO"


def _get(row: Any, key: str, default=None):
    if isinstance(row, dict):
        return row.get(key, default)
    return getattr(row, key, default)


def _expiry(row: Any) -> Optional[date]:
    raw = _get(row, "expiry") or _get(row, "expiry_date")
    if isinstance(raw, date):
        return raw
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw)[:10]).date()
    except Exception:
        return None


def select_hedge_future(
    chain: Iterable[Any],
    *,
    option_expiry: date,
    name: str,
    exchange: str = "NFO",
) -> HedgeContract:
    """Nearest liquid future whose expiry is on or after the option's expiry."""
    candidates = []
    for row in chain or []:
        if str(_get(row, "instrument_type", "") or "").upper() not in ("FUT", ""):
            continue
        if name and str(_get(row, "name", "") or "").upper() not in (name.upper(), ""):
            continue
        expiry = _expiry(row)
        if expiry is None or expiry < option_expiry:
            continue
        lot = int(_get(row, "lot_size", 0) or 0)
        token = int(_get(row, "instrument_token", 0) or _get(row, "token", 0) or 0)
        symbol = str(_get(row, "tradingsymbol", "") or "")
        if not symbol or lot <= 0 or token <= 0:
            continue
        candidates.append((expiry, HedgeContract(
            tradingsymbol=symbol, expiry=expiry, lot_size=lot,
            instrument_token=token, exchange=exchange,
        )))

    if not candidates:
        raise HedgeContractError(
            f"INCONCLUSIVE_HEDGE_CONTRACT: no {name} future expires on or after "
            f"{option_expiry.isoformat()}"
        )

    candidates.sort(key=lambda item: item[0])
    return candidates[0][1]
