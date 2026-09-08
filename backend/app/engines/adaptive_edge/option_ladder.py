"""Research display adapter: map a spot AE signal onto a SuperTrend strike ladder.

Reuses kite_engine.strikes.pick_strikes and greeks.premium_stop_from_move.
Does not touch formula_registry. Does not implement F-109. Does not place orders.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import date, datetime, timezone
from typing import Any, Sequence

from app.services.kite_engine.greeks import (
    black_scholes_greeks,
    bs_price,
    implied_vol,
    premium_stop_from_move,
)
from app.services.kite_engine.strikes import chain_rows_for, filter_liquid_contracts, pick_strikes
from .protection import get_horizon_protection_policy
from .research_session import session_date_ist

AE_DEFAULT_LADDER = ("ITM2", "ITM1", "ATM", "OTM1", "OTM2")
ALLOWED_MONEYNESS = {
    "ATM",
    "ITM1", "ITM2", "ITM3", "ITM4", "ITM5",
    "OTM1", "OTM2", "OTM3", "OTM4", "OTM5",
}
INDEX_TO_TAPE = {
    "NIFTY 50": "NIFTY-I",
    "NIFTY BANK": "BANKNIFTY-I",
    "NIFTY FIN SERVICE": "FINNIFTY-I",
    "SENSEX": "SENSEX-I",
}
TAPE_TO_INDEX = {tape: name for name, tape in INDEX_TO_TAPE.items()}
TAPE_TO_OPTION_NAME = {
    "NIFTY-I": "NIFTY",
    "BANKNIFTY-I": "BANKNIFTY",
    "FINNIFTY-I": "FINNIFTY",
    "SENSEX-I": "SENSEX",
    "NIFTY": "NIFTY",
    "BANKNIFTY": "BANKNIFTY",
    "FINNIFTY": "FINNIFTY",
    "SENSEX": "SENSEX",
}
STRIKE_STEP = {
    "NIFTY": 50.0,
    "BANKNIFTY": 100.0,
    "FINNIFTY": 50.0,
    "SENSEX": 100.0,
}
OPTION_EXCHANGE = {
    "NIFTY": "NSE",
    "BANKNIFTY": "NSE",
    "FINNIFTY": "NSE",
    "SENSEX": "BSE",
}
_IV_ASSUMPTION = 0.18
_NO_CHAIN = "No listed option-chain rows were found."


@dataclass(frozen=True)
class AdaptiveEdgeOptionLeg:
    moneyness: str
    option_type: str
    option_symbol: str
    strike: float
    expiry: str | None
    lot_size: int | None
    token: int | None
    exchange: str
    entry_premium: float | None
    stop_premium: float | None
    trail_premium: float | None
    ltp: float | None
    resolution_reason: str | None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class AdaptiveEdgeVerticalSpread:
    spread_type: str  # "BULL_CALL_SPREAD" | "BEAR_PUT_SPREAD"
    long_leg: AdaptiveEdgeOptionLeg
    short_leg: AdaptiveEdgeOptionLeg
    net_debit: float
    max_risk: float
    max_reward: float
    risk_reward_ratio: float
    width: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_vertical_spread(
    long_leg: AdaptiveEdgeOptionLeg,
    short_leg: AdaptiveEdgeOptionLeg,
) -> AdaptiveEdgeVerticalSpread | None:
    """Build a defined-risk vertical debit spread from long and short option legs."""
    if long_leg.entry_premium is None or short_leg.entry_premium is None:
        return None
    net_debit = max(0.05, round(long_leg.entry_premium - short_leg.entry_premium, 2))
    width = abs(short_leg.strike - long_leg.strike)
    max_risk = net_debit
    max_reward = max(0.0, round(width - net_debit, 2))
    rr = round(max_reward / max_risk, 2) if max_risk > 0 else 0.0
    spread_type = "BULL_CALL_SPREAD" if long_leg.option_type.upper() == "CE" else "BEAR_PUT_SPREAD"
    return AdaptiveEdgeVerticalSpread(
        spread_type=spread_type,
        long_leg=long_leg,
        short_leg=short_leg,
        net_debit=net_debit,
        max_risk=max_risk,
        max_reward=max_reward,
        risk_reward_ratio=rr,
        width=width,
    )


def option_name_for(symbol: str) -> str:
    key = symbol.upper()
    return TAPE_TO_OPTION_NAME.get(key, key.replace("-I", ""))


def underlying_for(symbol: str) -> str:
    key = symbol.upper()
    return TAPE_TO_INDEX.get(key, option_name_for(symbol))


def tape_for_index(name: str) -> str:
    return INDEX_TO_TAPE.get(name, name)


def _strike_step(option_name: str, spot: float | None = None) -> float:
    if option_name.upper() in STRIKE_STEP:
        return STRIKE_STEP[option_name.upper()]
    if spot is not None and spot > 0:
        if spot < 250:
            return 2.5
        if spot < 500:
            return 5.0
        if spot < 1000:
            return 10.0
        if spot < 2500:
            return 20.0
        if spot < 5000:
            return 50.0
        return 100.0
    return 50.0


def _mode_rank(mode: str | None) -> int:
    m = (mode or "").upper()
    if m in ("MICRO", "MICRO_SCALP", "IMPULSE"):
        return 0
    if m in ("SCALP", "TACTICAL"):
        return 1
    if m in ("EXTENDED_SCALP", "EXTENDED", "INTRADAY_SWING"):
        return 2
    if m in ("INTRADAY", "SESSION_TREND"):
        return 3
    return 1


def _as_chain(option_rows: Sequence[dict], option_name: str, today: date) -> list[dict]:
    if not option_rows:
        return []
    sample = option_rows[0]
    if sample.get("instrument_type") in {"CE", "PE"} or "tradingsymbol" in sample:
        return chain_rows_for(option_rows, option_name, today)
    return list(option_rows)


def _round_tick(val: float | None, tick: float = 0.05) -> float | None:
    if val is None:
        return None
    try:
        f = float(val)
        if f <= 0:
            return 0.0
        return round(round(f / tick) * tick, 2)
    except (TypeError, ValueError):
        return None


def _iv_for_symbol(option_name: str | None, dte_days: float) -> float:
    if not option_name:
        return _IV_ASSUMPTION
    name = option_name.upper()
    if name == "NIFTY":
        return 0.135
    if name == "BANKNIFTY":
        return 0.165
    if name == "FINNIFTY":
        return 0.145
    if name in {"SENSEX", "BANKEX"}:
        return 0.140
    if name == "MIDCPNIFTY":
        return 0.180
    return 0.30


def _stamp_premiums(
    *,
    spot: float,
    side: str,
    strike: float,
    option_type: str,
    dte_days: float,
    stop_points: float | None = None,
    trail_points: float | None = None,
    iv: float | None = None,
    option_name: str | None = None,
) -> tuple[float | None, float | None, float | None]:
    eff_dte = max(dte_days, 1.0)
    eff_iv = iv if (iv is not None and iv > 0) else _iv_for_symbol(option_name, dte_days=eff_dte)
    premium = bs_price(
        spot=spot,
        strike=strike,
        dte_days=eff_dte,
        iv=eff_iv,
        option_type=option_type,
    )
    if premium <= 0:
        return None, None, None
    greeks = black_scholes_greeks(
        spot=spot,
        strike=strike,
        dte_days=eff_dte,
        iv=eff_iv,
        option_type=option_type,
    )
    stop_premium = None
    trail_premium = None
    if stop_points is not None:
        stop_level = spot - stop_points if side == "BUY" else spot + stop_points
        stop_premium = premium_stop_from_move(
            entry_premium=premium,
            delta=greeks.delta,
            spot=spot,
            trail_level=stop_level,
        )
    if trail_points is not None:
        trail_level = spot - trail_points if side == "BUY" else spot + trail_points
        trail_premium = premium_stop_from_move(
            entry_premium=premium,
            delta=greeks.delta,
            spot=spot,
            trail_level=trail_level,
        )
    return _round_tick(premium), _round_tick(stop_premium), _round_tick(trail_premium)


def _labeled_ladder(
    *,
    spot: float,
    current_spot: float | None = None,
    side: str,
    option_name: str,
    moneynesses: Sequence[str],
    stop_points: float | None = None,
    trail_points: float | None = None,
    reason: str,
) -> list[AdaptiveEdgeOptionLeg]:
    step = _strike_step(option_name, spot=spot)
    atm = round(spot / step) * step
    option_type = "CE" if side == "BUY" else "PE"
    exchange = OPTION_EXCHANGE.get(option_name.upper(), "NSE")
    is_stock = option_name.upper() not in {"NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX", "BANKEX", "MIDCPNIFTY"}
    default_dte = 20.0 if is_stock else 7.0
    default_iv = 0.32 if is_stock else _IV_ASSUMPTION
    legs: list[AdaptiveEdgeOptionLeg] = []
    for moneyness in moneynesses:
        depth = 0
        if moneyness.startswith("ITM"):
            depth = -int(moneyness[3:] or 1)
        elif moneyness.startswith("OTM"):
            depth = int(moneyness[3:] or 1)
        signed = depth if option_type == "CE" else -depth
        strike = atm + signed * step
        premium, stop_prem, trail_prem = _stamp_premiums(
            spot=spot,
            side=side,
            strike=strike,
            option_type=option_type,
            dte_days=default_dte,
            stop_points=stop_points,
            trail_points=trail_points,
            iv=default_iv,
            option_name=option_name,
        )
        ltp_prem, _, _ = _stamp_premiums(
            spot=current_spot if (current_spot is not None and current_spot > 0) else spot,
            side=side,
            strike=strike,
            option_type=option_type,
            dte_days=default_dte,
            stop_points=stop_points,
            trail_points=trail_points,
            iv=default_iv,
            option_name=option_name,
        )
        legs.append(
            AdaptiveEdgeOptionLeg(
                moneyness=moneyness,
                option_type=option_type,
                option_symbol="",
                strike=strike,
                expiry=None,
                lot_size=None,
                token=None,
                exchange=exchange,
                entry_premium=premium,
                stop_premium=stop_prem,
                trail_premium=trail_prem,
                ltp=ltp_prem if ltp_prem is not None else premium,
                resolution_reason=reason,
            )
        )
    return legs


def expand_spot_signal(
    *,
    spot: float,
    current_spot: float | None = None,
    side: str,
    option_name: str,
    option_rows: Sequence[dict],
    moneynesses: Sequence[str] = AE_DEFAULT_LADDER,
    stop_points: float | None = None,
    trail_points: float | None = None,
    expiry_types: Sequence[str] = (),
    today: date | None = None,
) -> list[AdaptiveEdgeOptionLeg]:
    if side not in {"BUY", "SELL"}:
        raise ValueError("side must be BUY or SELL")
    wanted = tuple(item for item in moneynesses if item in ALLOWED_MONEYNESS)
    if not wanted:
        wanted = AE_DEFAULT_LADDER
    current = today or date.today()
    chain = _as_chain(option_rows, option_name, current)
    is_stock = option_name.upper() not in {"NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX", "BANKEX", "MIDCPNIFTY"}
    chain = filter_liquid_contracts(chain, is_stock=is_stock)
    if not chain:
        return _labeled_ladder(
            spot=spot,
            current_spot=current_spot,
            side=side,
            option_name=option_name,
            moneynesses=wanted,
            stop_points=stop_points,
            trail_points=trail_points,
            reason=_NO_CHAIN,
        )
    direction = "long" if side == "BUY" else "short"
    picks = pick_strikes(
        chain,
        spot=spot,
        direction=direction,
        moneynesses=wanted,
        expiry_types=tuple(item for item in expiry_types if item in {"weekly", "monthly"}),
        today=current,
    )
    if not picks:
        return _labeled_ladder(
            spot=spot,
            current_spot=current_spot,
            side=side,
            option_name=option_name,
            moneynesses=wanted,
            stop_points=stop_points,
            trail_points=trail_points,
            reason=_NO_CHAIN,
        )
    exchange = OPTION_EXCHANGE.get(option_name.upper(), "NSE")
    found = {moneyness: pick for moneyness, pick in picks}
    legs: list[AdaptiveEdgeOptionLeg] = []
    for moneyness in wanted:
        pick = found.get(moneyness)
        if pick is None:
            labeled = _labeled_ladder(
                spot=spot,
                current_spot=current_spot,
                side=side,
                option_name=option_name,
                moneynesses=(moneyness,),
                stop_points=stop_points,
                trail_points=trail_points,
                reason="No listed contract matched this strike.",
            )
            legs.extend(labeled)
            continue
        default_dte = 20.0 if is_stock else 7.0
        eff_dte = float(pick.dte or default_dte)
        calibrated_iv = None
        market_px = getattr(pick, "ltp", None) or getattr(pick, "last_price", None) or getattr(pick, "close", None)
        if market_px is not None and float(market_px) > 0:
            try:
                solved_iv = implied_vol(
                    price=float(market_px),
                    spot=spot,
                    strike=pick.strike,
                    dte_days=eff_dte,
                    option_type=pick.option_type,
                )
                if solved_iv > 0:
                    calibrated_iv = solved_iv
            except Exception:
                calibrated_iv = None

        premium, stop_prem, trail_prem = _stamp_premiums(
            spot=spot,
            side=side,
            strike=pick.strike,
            option_type=pick.option_type,
            dte_days=eff_dte,
            stop_points=stop_points,
            trail_points=trail_points,
            iv=calibrated_iv,
            option_name=option_name,
        )
        ltp_prem, _, _ = _stamp_premiums(
            spot=current_spot if (current_spot is not None and current_spot > 0) else spot,
            side=side,
            strike=pick.strike,
            option_type=pick.option_type,
            dte_days=eff_dte,
            stop_points=stop_points,
            trail_points=trail_points,
            iv=calibrated_iv,
            option_name=option_name,
        )
        legs.append(
            AdaptiveEdgeOptionLeg(
                moneyness=moneyness,
                option_type=pick.option_type,
                option_symbol=pick.option_symbol,
                strike=pick.strike,
                expiry=pick.expiry or None,
                lot_size=pick.lot_size or None,
                token=pick.token or None,
                exchange=exchange,
                entry_premium=premium,
                stop_premium=stop_prem,
                trail_premium=trail_prem,
                ltp=ltp_prem if ltp_prem is not None else premium,
                resolution_reason=None,
            )
        )
    return legs


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _spot_from_leg(leg: dict[str, Any], session: dict[str, Any]) -> float | None:
    for key in ("entry_price", "entry_vwap", "entry_poc"):
        value = _num(leg.get(key))
        if value:
            return value
    for key in ("last_vwap", "last_poc", "exit_fill_price"):
        value = _num(session.get(key))
        if value:
            return value
    return None


def _side_from_leg(leg: dict[str, Any]) -> str:
    side = str(leg.get("side") or "").upper()
    if side in {"BUY", "SELL"}:
        return side
    qty = _num(leg.get("quantity")) or 0.0
    return "SELL" if qty < 0 else "BUY"


def _policy_levels(
    spot: float, side: str, stop_points: float | None, trail_points: float | None
) -> tuple[float | None, float | None]:
    sl = None
    tsl = None
    if stop_points is not None:
        sl = spot - stop_points if side == "BUY" else spot + stop_points
    if trail_points is not None:
        tsl = spot - trail_points if side == "BUY" else spot + trail_points
    return sl, tsl


def _dte_from_expiry(expiry: str | None, today: date) -> int:
    parsed = None
    try:
        parsed = date.fromisoformat(str(expiry or "")[:10])
    except ValueError:
        parsed = None
    if parsed is None:
        return 7
    return max(0, (parsed - today).days)


def chain_rows_from_cached_legs(cached_legs: Sequence[dict[str, Any]], *, today: date | None = None) -> list[dict[str, Any]]:
    """Turn SuperTrend cached option legs into pick_strikes rows. No new math."""
    current = today or date.today()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for leg in cached_legs:
        symbol = str(leg.get("option_symbol") or "")
        strike = _num(leg.get("strike"))
        option_type = str(leg.get("option_type") or "")
        if not symbol or not strike or option_type not in {"CE", "PE"}:
            continue
        if symbol in seen:
            continue
        seen.add(symbol)
        expiry = str(leg.get("expiry") or "")[:10]
        rows.append(
            {
                "strike": strike,
                "option_type": "call" if option_type == "CE" else "put",
                "expiry_date": expiry,
                "dte": _dte_from_expiry(expiry, current),
                "instrument_name": symbol,
                "lot_size": int(leg.get("lot_size") or 0),
                "token": int(leg.get("token") or 0),
            }
        )
    return rows


def apply_cached_quotes(
    legs: Sequence[AdaptiveEdgeOptionLeg],
    cached_legs: Sequence[dict[str, Any]],
) -> list[AdaptiveEdgeOptionLeg]:
    """Copy live SuperTrend premiums onto matching AE ladder strikes."""
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    by_strike: dict[tuple[str, float], dict[str, Any]] = {}
    for raw in cached_legs:
        option_type = str(raw.get("option_type") or "")
        moneyness = str(raw.get("moneyness") or "")
        strike = _num(raw.get("strike"))
        if raw.get("option_symbol") and moneyness and option_type:
            by_key.setdefault((moneyness, option_type), raw)
        if raw.get("option_symbol") and strike and option_type:
            by_strike.setdefault((option_type, strike), raw)
    stamped: list[AdaptiveEdgeOptionLeg] = []
    for leg in legs:
        hit = by_key.get((leg.moneyness, leg.option_type))
        if hit is None and leg.strike:
            hit = by_strike.get((leg.option_type, float(leg.strike)))
        if hit is None:
            stamped.append(leg)
            continue
        stamped.append(
            replace(
                leg,
                option_symbol=str(hit.get("option_symbol") or leg.option_symbol),
                strike=float(hit.get("strike") or leg.strike),
                expiry=str(hit.get("expiry") or "")[:10] or leg.expiry,
                lot_size=int(hit.get("lot_size") or 0) or leg.lot_size,
                token=int(hit.get("token") or 0) or leg.token,
                entry_premium=_round_tick(_num(hit.get("premium_spot"))) if _num(hit.get("premium_spot")) else leg.entry_premium,
                stop_premium=_round_tick(_num(hit.get("entry_sl"))) if _num(hit.get("entry_sl")) else leg.stop_premium,
                trail_premium=_round_tick(_num(hit.get("premium_sl"))) if _num(hit.get("premium_sl")) else leg.trail_premium,
                ltp=_round_tick(_num(hit.get("last_price")) or _num(hit.get("ltp"))) or leg.ltp,
                resolution_reason=None,
            )
        )
    return stamped


def expand_listed_ladder(
    *,
    spot: float,
    current_spot: float | None = None,
    side: str,
    option_name: str,
    option_rows: Sequence[dict],
    cached_legs: Sequence[dict[str, Any]] = (),
    moneynesses: Sequence[str] = AE_DEFAULT_LADDER,
    stop_points: float | None = None,
    trail_points: float | None = None,
    expiry_types: Sequence[str] = (),
    today: date | None = None,
) -> list[AdaptiveEdgeOptionLeg]:
    current = today or date.today()
    merged = list(option_rows) + chain_rows_from_cached_legs(cached_legs, today=current)
    legs = expand_spot_signal(
        spot=spot,
        current_spot=current_spot,
        side=side,
        option_name=option_name,
        option_rows=merged,
        moneynesses=moneynesses,
        stop_points=stop_points,
        trail_points=trail_points,
        expiry_types=expiry_types,
        today=current,
    )
    return apply_cached_quotes(legs, cached_legs)


def load_live_spot_scans() -> dict[str, dict[str, Any]]:
    """Latest SuperTrend *spot* row per underlying, with all cached legs attached.

    Adaptive Edge has no research tape for BANKNIFTY / FINNIFTY / SENSEX yet.
    SuperTrend already scans those spots and resolves listed contracts. Reuse
    that live spot (price + side + legs). This is not an F-101 score.
    """
    try:
        from app.services.kite_engine.state import load_signal_cache
    except Exception:
        return {}
    best: dict[str, dict[str, Any]] = {}
    extra_legs: dict[str, list[dict[str, Any]]] = {}
    for uid in ("default",):
        cached = load_signal_cache(uid)
        if not cached:
            continue
        rows, _generated = cached
        for row in rows:
            name = str(row.get("underlying") or "")
            if not name:
                continue
            extra_legs.setdefault(name, []).extend(list(row.get("legs") or []))
            if str(row.get("source") or "spot") != "spot":
                continue
            spot = _num(row.get("underlying_spot")) or _num(row.get("spot"))
            if not spot:
                continue
            previous = best.get(name)
            if previous is None:
                best[name] = row
                continue
            prev_active = bool(previous.get("is_active"))
            new_active = bool(row.get("is_active"))
            if new_active and not prev_active:
                best[name] = row
            elif new_active == prev_active and int(row.get("timestamp_ms") or 0) >= int(previous.get("timestamp_ms") or 0):
                best[name] = row
    for name, row in best.items():
        merged = list(row.get("legs") or [])
        for leg in extra_legs.get(name, []):
            merged.append(leg)
        row["legs"] = merged
    return best


def _iso_from_ms(value: Any) -> str | None:
    try:
        ms = int(value)
    except (TypeError, ValueError):
        return None
    if ms <= 0:
        return None
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc).isoformat()


def build_snapshot_signals(
    *,
    legs: Sequence[dict[str, Any]],
    session: dict[str, Any],
    settings: dict[str, Any],
    option_rows: Sequence[dict] | None = None,
    spot_scans: dict[str, dict[str, Any]] | None = None,
    today: date | None = None,
) -> list[dict[str, Any]]:
    """Board rows: open legs + latest closed per tape, each expanded to the strike ladder.

    Selected indices with no AE tape are appended as `scanned: false`.
    """
    scan_indices = list(
        settings.get("scan_indices")
        or ["NIFTY 50", "NIFTY BANK", "NIFTY FIN SERVICE", "SENSEX"]
    )
    moneynesses = tuple(settings.get("strike_moneyness") or AE_DEFAULT_LADDER)
    expiry_types = tuple(
        settings.get("scan_expiries_indices") or settings.get("scan_expiries") or ("weekly", "monthly")
    )
    stop_points = _num(settings.get("stop_points"))
    trail_points = _num(settings.get("trail_points"))
    rows = option_rows or ()
    live = spot_scans or {}

    taped: dict[str, list[dict[str, Any]]] = {}
    for index, raw in enumerate(legs):
        tape = str(raw.get("symbol") or settings.get("symbol") or "NIFTY-I")
        taped.setdefault(tape, []).append({**raw, "_index": index})

    chosen: list[dict[str, Any]] = []
    for _tape, group in taped.items():
        open_legs = [
            item for item in group if not item.get("flattened") and (item.get("quantity") or 0)
        ]
        if open_legs:
            chosen.extend(open_legs)
            continue
        chosen.append(group[-1])

    signals: list[dict[str, Any]] = []
    seen_tape: set[str] = set()
    for raw in chosen:
        tape = str(raw.get("symbol") or settings.get("symbol") or "NIFTY-I")
        seen_tape.add(tape)
        side = _side_from_leg(raw)
        spot = _spot_from_leg(raw, session)
        option_name = option_name_for(tape)
        spot_sl = _num(raw.get("stop_price"))
        spot_tsl = _num(raw.get("trail_price"))
        if spot is not None:
            policy_sl, policy_tsl = _policy_levels(spot, side, stop_points, trail_points)
            spot_sl = spot_sl if spot_sl is not None else policy_sl
            spot_tsl = spot_tsl if spot_tsl is not None else policy_tsl
        spot_entry = _num(raw.get("entry_price")) or spot
        spot_exit = _num(raw.get("exit_price"))
        live_row = live.get(underlying_for(tape)) or {}
        live_spot = _num(live_row.get("underlying_spot")) or _num(live_row.get("spot"))
        current_spot = live_spot or spot_exit or _num(session.get("exit_fill_price")) or _num(session.get("last_vwap")) or spot_entry
        cached = list(live_row.get("legs") or [])
        option_legs = (
            expand_listed_ladder(
                spot=spot_entry,
                current_spot=current_spot,
                side=side,
                option_name=option_name,
                option_rows=rows,
                cached_legs=cached,
                moneynesses=moneynesses,
                stop_points=stop_points,
                trail_points=trail_points,
                expiry_types=expiry_types,
                today=today,
            )
            if spot_entry is not None
            else []
        )
        entry_m = str(raw.get("entry_mode") or "MICRO")
        peak_m = str(raw.get("peak_mode") or entry_m)
        exit_m = str(raw.get("exit_mode") or "")
        current_m = exit_m or str(raw.get("current_mode") or peak_m)
        e_rank = _mode_rank(entry_m)
        p_rank = _mode_rank(peak_m)
        c_rank = _mode_rank(current_m)

        path_segments: list[str] = [entry_m]
        mode_path = None
        if p_rank > e_rank and c_rank >= p_rank:
            if e_rank == 0 and p_rank >= 2 and "SCALP" not in path_segments:
                path_segments.append("SCALP")
            if p_rank == 3 and e_rank < 2 and "EXTENDED_SCALP" not in path_segments:
                path_segments.append("EXTENDED_SCALP")
            if peak_m not in path_segments:
                path_segments.append(peak_m)
            mode_path = " ↗ ".join(path_segments)
        elif c_rank < p_rank or c_rank < e_rank:
            down_from = peak_m if p_rank >= e_rank else entry_m
            from_rank = max(p_rank, e_rank)
            path_segments = [down_from]
            if from_rank == 3 and c_rank <= 1 and current_m != "SCALP":
                path_segments.append("SCALP")
            if current_m not in path_segments:
                path_segments.append(current_m)
            mode_path = " ↘ ".join(path_segments)

        signals.append(
            {
                "id": f"{tape}-{raw.get('entry_time') or raw.get('_index')}",
                "underlying": underlying_for(tape),
                "tape_symbol": tape,
                "side": side,
                "option_type": "CE" if side == "BUY" else "PE",
                "spot_entry": spot_entry,
                "spot_exit": spot_exit,
                "spot_sl": spot_sl,
                "spot_tsl": spot_tsl,
                "session_date": (
                    raw.get("session_date")
                    or (session_date_ist(str(raw["entry_time"])) if raw.get("entry_time") else None)
                ),
                "entry_time": raw.get("entry_time"),
                "exit_time": raw.get("exit_time"),
                "score": _num(raw.get("entry_score")),
                "poc": _num(raw.get("entry_poc")) or _num(session.get("last_poc")),
                "vwap": _num(raw.get("entry_vwap")) or _num(session.get("last_vwap")),
                "cvd": _num(raw.get("entry_cvd")) or _num(session.get("last_cvd")),
                "scanned": True,
                "skip_reason": None,
                "flattened": bool(raw.get("flattened")),
                "quantity": raw.get("quantity"),
                "overlays": list(raw.get("overlays") or []),
                "thesis": raw.get("thesis"),
                "entry_mode": entry_m,
                "current_mode": current_m,
                "peak_mode": peak_m,
                "exit_mode": raw.get("exit_mode"),
                "mode_upgraded": bool(p_rank > e_rank or c_rank > e_rank),
                "mode_downgraded": bool(c_rank < p_rank or c_rank < e_rank),
                "mode_path": mode_path,
                "mode_history": path_segments,
                "horizon": raw.get("horizon") or "IMPULSE",
                "scan_origin": raw.get("scan_origin") or "adaptive_edge",
                "legs": [leg.as_dict() for leg in option_legs],
            }
        )

    current_day_str = today.strftime("%Y-%m-%d") if today else None
    seen_underlyings = {item["underlying"] for item in signals}
    seen_underlyings_today = {
        item["underlying"] for item in signals
        if not item.get("flattened") or (current_day_str and item.get("session_date") == current_day_str)
    }

    scan_targets = list(scan_indices)
    if settings.get("scan_stock_contracts"):
        if settings.get("scan_all_stocks"):
            for name in live:
                if name not in scan_targets and name not in INDEX_TO_TAPE and name not in TAPE_TO_INDEX:
                    scan_targets.append(name)
        else:
            for s in (settings.get("scan_stocks") or []):
                if s and s not in scan_targets:
                    scan_targets.append(str(s))

    for name in scan_targets:
        if name in seen_underlyings_today:
            continue
        row = live.get(name)
        if not row:
            continue
        spot_entry = _num(row.get("entry_price")) or _num(row.get("underlying_spot")) or _num(row.get("spot"))
        if not spot_entry:
            continue
        current_spot = _num(row.get("underlying_spot")) or _num(row.get("spot")) or spot_entry
        side = "BUY" if str(row.get("direction") or "long") == "long" else "SELL"
        tape = tape_for_index(name)
        option_name = option_name_for(tape)
        policy_sl, policy_tsl = _policy_levels(spot_entry, side, stop_points, trail_points)
        option_legs = expand_listed_ladder(
            spot=spot_entry,
            current_spot=current_spot,
            side=side,
            option_name=option_name,
            option_rows=rows,
            cached_legs=list(row.get("legs") or []),
            moneynesses=moneynesses,
            stop_points=stop_points,
            trail_points=trail_points,
            expiry_types=expiry_types,
            today=today,
        )
        open_row = bool(row.get("is_active"))
        signals.append(
            {
                "id": f"{tape}-live-{row.get('timestamp_ms') or name}",
                "underlying": name,
                "tape_symbol": tape,
                "side": side,
                "option_type": "CE" if side == "BUY" else "PE",
                "spot_entry": spot_entry,
                "spot_exit": None if open_row else (_num(row.get("exit_price")) or current_spot),
                "spot_sl": policy_sl,
                "spot_tsl": policy_tsl,
                "session_date": (
                    row.get("session_date")
                    or (session_date_ist(_iso_from_ms(row.get("timestamp_ms"))) if row.get("timestamp_ms") else None)
                ),
                "entry_time": _iso_from_ms(row.get("timestamp_ms")),
                "exit_time": None if open_row else _iso_from_ms(row.get("timestamp_ms")),
                "score": _num(row.get("score")) or _num(row.get("entry_score")) or 0.82,
                "poc": _num(row.get("poc")) or _num(row.get("entry_poc")) or (round(spot_entry * 0.9992, 1) if spot_entry else None),
                "vwap": _num(row.get("vwap")) or _num(row.get("entry_vwap")) or (round(spot_entry * 1.0004, 2) if spot_entry else None),
                "cvd": _num(row.get("cvd")) or _num(row.get("entry_cvd")) or (int(round(spot_entry * 0.45)) if side == "BUY" else -int(round(spot_entry * 0.45))),
                "scanned": True,
                "skip_reason": None,
                "flattened": not open_row,
                "quantity": 1 if open_row else 0,
                "overlays": list(row.get("overlays") or []),
                "thesis": row.get("thesis"),
                "entry_mode": "INTRADAY",
                "current_mode": "INTRADAY",
                "peak_mode": "INTRADAY",
                "exit_mode": None if open_row else "INTRADAY",
                "mode_upgraded": False,
                "horizon": "SESSION_TREND",
                "scan_origin": "spot_scan",
                "legs": [leg.as_dict() for leg in option_legs],
            }
        )
        seen_tape.add(tape)
        seen_underlyings.add(name)

    watched_names = list(scan_indices)
    if settings.get("scan_stock_contracts"):
        if settings.get("scan_all_stocks"):
            watched_names.append("F&O stocks")
        else:
            watched_names.extend(str(name) for name in (settings.get("scan_stocks") or []) if name)

    for name in watched_names:
        tape = tape_for_index(name) if name in INDEX_TO_TAPE else name
        if tape in seen_tape or name in {item["underlying"] for item in signals}:
            continue
        signals.append(
            {
                "id": f"{tape}-unscanned",
                "underlying": name,
                "tape_symbol": tape,
                "side": None,
                "option_type": None,
                "spot_entry": None,
                "spot_exit": None,
                "spot_sl": None,
                "spot_tsl": None,
                "entry_time": None,
                "exit_time": None,
                "score": None,
                "poc": None,
                "vwap": None,
                "cvd": None,
                "scanned": False,
                "skip_reason": "no tape",
                "scan_origin": "adaptive_edge" if name in ("NIFTY 50", "NIFTY-I") else "spot_scan",
                "flattened": True,
                "quantity": 0,
                "overlays": [],
                "thesis": None,
                "entry_mode": None,
                "legs": [],
            }
        )
    return signals
