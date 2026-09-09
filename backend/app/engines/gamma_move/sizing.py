"""How large a Gamma Move trade is, and how it shrinks after losses."""
from __future__ import annotations

from typing import Optional

from .config import GammaMoveConfig
from .models import TradeRecord


def risk_multiplier(record: TradeRecord, cfg: GammaMoveConfig) -> float:
    step = max(0, min(int(getattr(record, "descale_step", 0) or 0), 2))
    return float(cfg.descale_factor) ** step


def lots_for(entry: float, stop: float, lot_size: int, cfg: GammaMoveConfig,
             record: Optional[TradeRecord] = None) -> int:
    lot = max(1, int(lot_size or 1))
    mult = risk_multiplier(record, cfg) if record is not None else 1.0
    if cfg.sizing_mode == "LOTS":
        lots = int(max(0, int(cfg.lots)) * mult)
    else:
        risk_per_unit = float(entry) - float(stop)
        if risk_per_unit <= 0:
            return 0
        budget = cfg.capital_inr * (cfg.risk_per_trade_pct / 100.0) * mult
        lots = int(budget // (risk_per_unit * lot))
    if lots <= 0:
        return 0
    if entry > 0 and cfg.max_premium_at_risk_inr > 0:
        max_lots = int(cfg.max_premium_at_risk_inr // (entry * lot))
        lots = min(lots, max_lots)
    return max(0, lots)


def sizing_blocker(entry: float, stop: float, lot_size: int, cfg: GammaMoveConfig,
                   record: Optional[TradeRecord] = None) -> Optional[str]:
    lot = max(1, int(lot_size or 1))
    if lots_for(entry, stop, lot_size, cfg, record) > 0:
        return None
    if cfg.sizing_mode == "LOTS" and cfg.lots <= 0:
        return "lots not set"
    outlay = float(entry) * lot
    if cfg.max_premium_at_risk_inr > 0 and outlay > cfg.max_premium_at_risk_inr:
        return (f"one lot costs Rs {outlay:,.0f} in premium, above the "
                f"Rs {cfg.max_premium_at_risk_inr:,.0f} outlay cap")
    risk_per_unit = float(entry) - float(stop)
    if risk_per_unit <= 0:
        return "stop is not below entry"
    mult = risk_multiplier(record, cfg) if record is not None else 1.0
    if cfg.sizing_mode == "LOTS":
        if int(cfg.lots * mult) <= 0:
            return (f"lots cut to zero by the losing-streak size ladder "
                    f"(step {getattr(record, 'descale_step', 0)})")
        return "lots not set"
    budget = cfg.capital_inr * (cfg.risk_per_trade_pct / 100.0) * mult
    need = risk_per_unit * lot
    detail = " (cut by the losing streak)" if mult < 1.0 else ""
    return (f"one lot risks Rs {need:,.0f} to the stop, above the "
            f"Rs {budget:,.0f} risk budget{detail}")


def at_risk_inr(entry: float, stop: float, quantity: int) -> float:
    return round(max(0.0, (float(entry) - float(stop))) * int(quantity), 2)


def deployed_inr(entry: float, quantity: int) -> float:
    return round(float(entry) * int(quantity), 2)
