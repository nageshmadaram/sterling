"""Which contract a level implies: expiry window, strike window, highest OI."""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional, Sequence

from .config import GammaMoveConfig
from .models import InstrumentRef, SpotLevel, StrikeCandidate


def days_to_expiry(expiry: str, today: date) -> Optional[int]:
    try:
        return (datetime.strptime(expiry[:10], "%Y-%m-%d").date() - today).days
    except (ValueError, TypeError):
        return None


def expiry_in_window(expiry: str, today: date, cfg: GammaMoveConfig) -> bool:
    dte = days_to_expiry(expiry, today)
    if dte is None:
        return False
    if cfg.avoid_expiry_day and dte == 0:
        return False
    return cfg.expiry_dte_min <= dte <= cfg.expiry_dte_max


def select_expiry(expiries: Sequence[str], today: date,
                  cfg: GammaMoveConfig) -> Optional[str]:
    ok = sorted({e[:10] for e in expiries if expiry_in_window(e, today, cfg)})
    return ok[0] if ok else None


def strikes_near_level(contracts: Sequence[InstrumentRef], level: SpotLevel,
                       cfg: GammaMoveConfig) -> list[InstrumentRef]:
    want = "CE" if level.kind == "resistance" else "PE"
    if level.price <= 0:
        return []
    return [c for c in contracts
            if c.option_type == want
            and abs(c.strike - level.price) / level.price * 100.0 <= cfg.strike_window_pct]


def is_chain_wall(oi: int, chain_oi_max: int | None, *, required: bool) -> bool:
    if not required or chain_oi_max is None:
        return True
    return int(oi) >= int(chain_oi_max)


def spot_through_or_at_strike(spot: float, strike: float, option_type: str,
                              proximity_pct: float) -> bool:
    if spot <= 0 or strike <= 0:
        return False
    band = max(0.0, float(proximity_pct)) / 100.0
    if option_type == "CE":
        return float(spot) >= float(strike) * (1.0 - band)
    return float(spot) <= float(strike) * (1.0 + band)


def pick_strike(contracts: Sequence[InstrumentRef], level: SpotLevel, *,
                underlying: str, oi_by_id: dict, premium_by_id: dict, spot: float,
                today: date, cfg: GammaMoveConfig) -> Optional[StrikeCandidate]:
    best: Optional[StrikeCandidate] = None
    for c in strikes_near_level(contracts, level, cfg):
        oi = int(oi_by_id.get(c.instrument_id) or 0)
        premium = float(premium_by_id.get(c.instrument_id) or 0.0)
        if oi < cfg.min_option_oi or premium < cfg.min_option_premium:
            continue
        dte = days_to_expiry(c.expiry, today)
        if dte is None or not expiry_in_window(c.expiry, today, cfg):
            continue
        if cfg.require_spot_through_strike and not spot_through_or_at_strike(
                spot, c.strike, c.option_type, cfg.level_proximity_pct):
            continue
        chain_max = max(
            (int(oi_by_id.get(x.instrument_id) or 0) for x in contracts
             if x.option_type == c.option_type and x.expiry[:10] == c.expiry[:10]),
            default=0)
        if not is_chain_wall(oi, chain_max or None,
                             required=cfg.require_chain_max_oi):
            continue
        cand = StrikeCandidate(underlying=underlying, level=level, instrument=c,
                               oi=oi, days_to_expiry=dte, spot=spot, premium=premium,
                               chain_oi_max=chain_max or None)
        if best is None or (oi, -abs(c.strike - level.price)) > (best.oi, -abs(best.instrument.strike - level.price)):
            best = cand
    return best
