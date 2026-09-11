"""Kite-only ATM/ITM/OTM option strike and expiry-series resolver.

Expiry classification is derived from the dates actually listed in the instrument
chain. This avoids weekday assumptions because exchange expiry days and holiday
adjustments can change.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import List, Literal, Optional, Sequence

Moneyness = Literal[
    "ATM", "ITM1", "ITM2", "ITM3", "ITM4", "ITM5",
    "ITM10", "ITM15", "ITM20",
    "OTM1", "OTM2", "OTM3", "OTM4", "OTM5",
]
ExpiryType = Literal["weekly", "monthly"]

_DEPTH = {
    "ATM": 0,
    "ITM1": 1, "ITM2": 2, "ITM3": 3, "ITM4": 4, "ITM5": 5,
    "ITM10": 10, "ITM15": 15, "ITM20": 20,
    "OTM1": 1, "OTM2": 2, "OTM3": 3, "OTM4": 4, "OTM5": 5,
}


def _parse_expiry(raw: object) -> Optional[date]:
    try:
        return date.fromisoformat(str(raw or "")[:10])
    except (TypeError, ValueError):
        return None


def _expiry_date_set(chain: Sequence[dict], today: date) -> dict[str, set[str]]:
    """Classify actual listed future dates as weekly or monthly.

    The latest listed expiry in each represented calendar month is monthly. Earlier
    listed expiries in that month are weekly. A chain with one expiry per month is
    therefore monthly-only; holiday-shifted dates need no special weekday rule.
    """
    future = sorted({
        d for row in chain
        if (d := _parse_expiry(row.get("expiry_date") or row.get("expiry"))) is not None
        and d >= today
    })
    by_month: dict[tuple[int, int], list[date]] = {}
    for expiry in future:
        by_month.setdefault((expiry.year, expiry.month), []).append(expiry)

    classified: dict[str, set[str]] = {}
    for dates in by_month.values():
        monthly = max(dates)
        for expiry in dates:
            classified[expiry.isoformat()] = {"monthly"} if expiry == monthly else {"weekly"}
    return classified


def _series_dates(chain: Sequence[dict], expiry_type: ExpiryType, today: date) -> list[date]:
    labels = _expiry_date_set(chain, today)
    return sorted(
        expiry for raw, kinds in labels.items()
        if expiry_type in kinds and (expiry := _parse_expiry(raw)) is not None
    )


def _filter_chain_by_expiry(
    chain: Sequence[dict], expiry_types: Sequence[ExpiryType], today: date
) -> list[dict]:
    if not expiry_types:
        return list(chain)
    labels = _expiry_date_set(chain, today)
    wanted = set(expiry_types)
    return [
        row for row in chain
        if wanted & labels.get(
            str(row.get("expiry_date") or row.get("expiry") or "")[:10], set()
        )
    ]


def filter_chain_by_expiry_types(
    chain: Sequence[dict], expiry_types: Sequence[ExpiryType], today: date
) -> list[dict]:
    """Public form of the expiry-series filter.

    The derivatives scan narrows its chain BEFORE resolving contracts, so the
    restriction holds even when the caller also supplies explicit per-series ranks
    (which bypass ``expiry_types`` inside ``pick_contracts``).
    """
    return _filter_chain_by_expiry(chain, expiry_types, today)


def _filter_chain_by_series(
    chain: Sequence[dict], *, expiry_type: ExpiryType, expiry_rank: int, today: date
) -> list[dict]:
    dates = _series_dates(chain, expiry_type, today)
    if expiry_rank < 0 or expiry_rank >= len(dates):
        return []
    selected = dates[expiry_rank].isoformat()
    return [
        row for row in chain
        if str(row.get("expiry_date") or row.get("expiry") or "")[:10] == selected
    ]


def chain_rows_for(option_instruments: Sequence[dict], name: str, today: date) -> List[dict]:
    """Extract strike-picker rows for an underlying from an NFO/BFO dump."""
    wanted_name = name.upper()
    alias = {"SENSEX": "BSX", "BANKEX": "BKX"}.get(wanted_name)
    rows: List[dict] = []
    for instrument in option_instruments:
        instrument_name = str(instrument.get("name", "")).upper()
        symbol = str(instrument.get("tradingsymbol", "")).upper()
        prefix_hit = (
            symbol.startswith(wanted_name)
            and len(symbol) > len(wanted_name)
            and symbol[len(wanted_name)].isdigit()
        )
        if instrument_name != wanted_name and not (alias and instrument_name == alias) and not prefix_hit:
            continue
        instrument_type = instrument.get("instrument_type")
        if instrument_type not in ("CE", "PE"):
            continue
        raw_expiry = str(instrument.get("expiry", ""))[:10]
        try:
            expiry = datetime.strptime(raw_expiry, "%Y-%m-%d").date()
            strike = float(instrument.get("strike") or 0.0)
        except (ValueError, TypeError):
            continue
        if strike <= 0 or expiry < today:
            continue
        rows.append({
            "strike": strike,
            "option_type": "call" if instrument_type == "CE" else "put",
            "expiry_date": raw_expiry,
            "dte": (expiry - today).days,
            "instrument_name": str(instrument.get("tradingsymbol", "")),
            "lot_size": int(instrument.get("lot_size") or 0),
            "token": int(instrument.get("instrument_token") or 0),
        })
    return rows


@dataclass(frozen=True)
class OptionPick:
    option_symbol: str
    strike: float
    option_type: str
    expiry: str
    dte: int
    lot_size: int = 0
    token: int = 0


def _select_row(rows: Sequence[dict], *, spot: float, want_call: bool, moneyness: Moneyness) -> Optional[dict]:
    ordered = sorted(rows, key=lambda row: float(row["strike"]))
    if not ordered:
        return None
    if moneyness == "ATM":
        return min(ordered, key=lambda row: abs(float(row["strike"]) - spot))

    is_itm = moneyness.startswith("ITM")
    if want_call:
        valid = [row for row in ordered if float(row["strike"]) < spot] if is_itm else [row for row in ordered if float(row["strike"]) > spot]
        valid = sorted(valid, key=lambda row: float(row["strike"]), reverse=is_itm)
    else:
        valid = [row for row in ordered if float(row["strike"]) > spot] if is_itm else [row for row in ordered if float(row["strike"]) < spot]
        valid = sorted(valid, key=lambda row: float(row["strike"]), reverse=not is_itm)
    if not valid:
        return None
    requested_depth = _DEPTH[moneyness]
    return valid[min(requested_depth, len(valid)) - 1]



def in_expiry_window(row: dict, *, min_dte: int = 0, max_dte: Optional[int] = None,
                     avoid_expiry_day: bool = False) -> bool:
    """Whether a chain row's days-to-expiry falls inside the configured window.

    One predicate for every engine, so "minimum days to expiry" cannot come to
    mean one thing on the SuperTrend page and another on Gamma Move's.

    Defaults are permissive on purpose: every existing caller passes nothing and
    must keep resolving exactly the contracts it resolved before.
    """
    dte = int(row.get("dte", 0))
    if avoid_expiry_day and dte == 0:
        return False
    if dte < int(min_dte):
        return False
    return max_dte is None or dte <= int(max_dte)


def expiry_window_of(cfg, fallback=None) -> dict:
    """The expiry-window kwargs for a config, ready to splat into the pickers.

    Takes any object carrying the three shared field names, so one call site
    works for every engine. ``fallback`` covers Navigator, whose fields are
    Optional and mean "follow the Kite engine's" when unset — resolved here so
    that rule lives in one place rather than at each call site.
    """
    def pick(name, default):
        value = getattr(cfg, name, None)
        if value is None and fallback is not None:
            value = getattr(fallback, name, None)
        return default if value is None else value

    return {
        "min_dte": int(pick("expiry_dte_min", 0)),
        "max_dte": pick("expiry_dte_max", None),
        "avoid_expiry_day": bool(pick("avoid_expiry_day", False)),
    }


def pick_strike(
    chain: Sequence[dict], *, spot: float, direction: str,
    moneyness: Moneyness = "ATM", min_dte: int = 0,
    max_dte: Optional[int] = None, avoid_expiry_day: bool = False,
    expiry_types: Sequence[ExpiryType] = (), expiry_type: Optional[ExpiryType] = None,
    expiry_rank: int = 0, today: Optional[date] = None,
) -> Optional[OptionPick]:
    """Resolve one CE/PE contract for a requested expiry series and moneyness.

    If a requested depth exceeds the available ladder, the deepest valid strike on
    that same ITM/OTM side is returned. The resolver never crosses to the wrong side.
    """
    want_call = direction == "long"
    wanted_type = "call" if want_call else "put"
    rows = [
        row for row in chain
        if str(row.get("option_type", "")).lower() == wanted_type
        and in_expiry_window(row, min_dte=min_dte, max_dte=max_dte,
                             avoid_expiry_day=avoid_expiry_day)
    ]
    current = today or date.today()
    if expiry_type is not None:
        rows = _filter_chain_by_series(
            rows, expiry_type=expiry_type, expiry_rank=expiry_rank, today=current
        )
    elif expiry_types:
        rows = _filter_chain_by_expiry(rows, expiry_types, current)
        if rows:
            nearest_dte = min(int(row.get("dte", 0)) for row in rows)
            rows = [row for row in rows if int(row.get("dte", 0)) == nearest_dte]
    elif rows:
        nearest_dte = min(int(row.get("dte", 0)) for row in rows)
        rows = [row for row in rows if int(row.get("dte", 0)) == nearest_dte]
    selected = _select_row(rows, spot=spot, want_call=want_call, moneyness=moneyness)
    if selected is None:
        return None
    return OptionPick(
        option_symbol=str(selected.get("instrument_name", "")),
        strike=float(selected["strike"]),
        option_type="CE" if want_call else "PE",
        expiry=str(selected.get("expiry_date", "")),
        dte=int(selected.get("dte", 0)),
        lot_size=int(selected.get("lot_size", 0) or 0),
        token=int(selected.get("token", 0) or 0),
    )


def filter_liquid_contracts(
    rows: Sequence[dict],
    *,
    is_stock: bool = False,
    max_spread_pct: float | None = None,
    min_volume: int = 0,
    min_oi: int = 0,
) -> list[dict]:
    """Filter out illiquid options, especially wide-spread monthly stock contracts."""
    if not rows:
        return []
    threshold = max_spread_pct if max_spread_pct is not None else (3.5 if is_stock else 1.0)
    filtered = []
    for r in rows:
        bid = float(r.get("bid") or 0.0)
        ask = float(r.get("ask") or 0.0)
        vol = int(r.get("volume") or 0)
        oi = int(r.get("oi") or 0)
        if min_volume > 0 and vol < min_volume:
            continue
        if min_oi > 0 and oi < min_oi:
            continue
        if bid > 0 and ask > 0:
            mid = (bid + ask) / 2.0
            spread_pct = ((ask - bid) / mid) * 100.0 if mid > 0 else 0.0
            if spread_pct > threshold:
                continue
        filtered.append(r)
    return filtered if filtered else list(rows)


def _default_series(expiry_types: Sequence[ExpiryType]) -> dict[ExpiryType, Sequence[int]]:
    """Legacy callers get all supported user-facing series without extra wiring."""
    return {
        kind: ([0, 1, 2, 3] if kind == "weekly" else [0, 1])
        for kind in expiry_types
    }


def pick_strikes(
    chain: Sequence[dict], *, spot: float, direction: str,
    moneynesses: Sequence[Moneyness], min_dte: int = 0,
    max_dte: Optional[int] = None, avoid_expiry_day: bool = False,
    expiry_types: Sequence[ExpiryType] = (),
    expiry_ranks_by_type: Optional[dict[ExpiryType, Sequence[int]]] = None,
    today: Optional[date] = None,
) -> List[tuple]:
    """Resolve requested strikes across independently selected weekly/monthly ranks."""
    output: List[tuple] = []
    seen: set[str] = set()
    series = expiry_ranks_by_type if expiry_ranks_by_type is not None else _default_series(expiry_types)
    if not series:
        series = {None: [0]}  # type: ignore[dict-item]
    for kind, ranks in series.items():
        for rank in ranks:
            for moneyness in moneynesses:
                pick = pick_strike(
                    chain, spot=spot, direction=direction, moneyness=moneyness,
                    min_dte=min_dte, max_dte=max_dte,
                    avoid_expiry_day=avoid_expiry_day, expiry_types=expiry_types,
                    expiry_type=kind, expiry_rank=int(rank), today=today,
                )
                if pick and pick.option_symbol not in seen:
                    seen.add(pick.option_symbol)
                    output.append((moneyness, pick))
    return output


def pick_contracts(
    chain: Sequence[dict], *, spot: float,
    moneynesses: Sequence[Moneyness], min_dte: int = 0,
    max_dte: Optional[int] = None, avoid_expiry_day: bool = False,
    expiry_types: Sequence[ExpiryType] = (),
    expiry_ranks_by_type: Optional[dict[ExpiryType, Sequence[int]]] = None,
    today: Optional[date] = None,
) -> List[tuple]:
    """Resolve both CE and PE across selected strike and expiry series."""
    output: List[tuple] = []
    seen: set[str] = set()
    series = expiry_ranks_by_type if expiry_ranks_by_type is not None else _default_series(expiry_types)
    if not series:
        series = {None: [0]}  # type: ignore[dict-item]
    for kind, ranks in series.items():
        for rank in ranks:
            for moneyness in moneynesses:
                for direction in ("long", "short"):
                    pick = pick_strike(
                        chain, spot=spot, direction=direction, moneyness=moneyness,
                        min_dte=min_dte, expiry_types=expiry_types,
                        expiry_type=kind, expiry_rank=int(rank), today=today,
                    )
                    if pick and pick.option_symbol not in seen:
                        seen.add(pick.option_symbol)
                        output.append((moneyness, pick))
    return output


def pick_by_delta(
    chain: Sequence[dict], *, spot: float, direction: str,
    target_delta: float = 0.90, iv: float = 0.18,
    min_dte: int = 0, max_dte: Optional[int] = None, avoid_expiry_day: bool = False,
    expiry_types: Sequence[ExpiryType] = (),
    expiry_type: Optional[ExpiryType] = None, expiry_rank: int = 0,
    today: Optional[date] = None,
) -> Optional[OptionPick]:
    from app.services.kite_engine.greeks import black_scholes_greeks

    want_call = direction == "long"
    wanted_type = "call" if want_call else "put"
    rows = [
        row for row in chain
        if str(row.get("option_type", "")).lower() == wanted_type
        and in_expiry_window(row, min_dte=min_dte, max_dte=max_dte,
                             avoid_expiry_day=avoid_expiry_day)
    ]
    current = today or date.today()
    if expiry_type is not None:
        rows = _filter_chain_by_series(
            rows, expiry_type=expiry_type, expiry_rank=expiry_rank, today=current
        )
    elif expiry_types:
        rows = _filter_chain_by_expiry(rows, expiry_types, current)
    if not rows:
        return None
    nearest_dte = min(int(row.get("dte", 0)) for row in rows)
    rows = [row for row in rows if int(row.get("dte", 0)) == nearest_dte]

    absolute_target = abs(target_delta)
    best_row = None
    best_distance = float("inf")
    for row in rows:
        strike = float(row.get("strike", 0))
        dte_days = max(1.0, float(row.get("dte", 1)))
        greeks = black_scholes_greeks(
            spot=spot, strike=strike, dte_days=dte_days, iv=iv,
            option_type="CE" if want_call else "PE",
        )
        distance = abs(abs(greeks.delta) - absolute_target)
        if distance < best_distance:
            best_distance = distance
            best_row = row
    if best_row is None:
        return None
    return OptionPick(
        option_symbol=str(best_row.get("instrument_name", "")),
        strike=float(best_row["strike"]),
        option_type="CE" if want_call else "PE",
        expiry=str(best_row.get("expiry_date", "")),
        dte=int(best_row.get("dte", 0)),
        lot_size=int(best_row.get("lot_size", 0) or 0),
        token=int(best_row.get("token", 0) or 0),
    )
