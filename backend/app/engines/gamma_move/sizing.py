"""How large a Gamma Move trade is, and how it shrinks after losses.

The de-scaling ladder is the one risk rule the source actually states: after two
or three losing trades in a row, cut the size, and restore it only once the
trades are working again.
"""
from __future__ import annotations

from typing import Optional

from .config import GammaMoveConfig
from .models import TradeRecord


def risk_multiplier(record: TradeRecord, cfg: GammaMoveConfig) -> float:
    """1.0 normally, then ``descale_factor`` then ``descale_factor ** 2``.

    The later source segment (46:22–60:00) is a two-step ladder: after two or
    three losers cut 1% to 0.5%, and if the losing streak continues cut again
    to 0.25%. Reads the record's latched ``descale_step`` rather than the live
    streak. Deriving from the streak is wrong and subtly so: a single winner
    resets ``consecutive_losses``, so a run of three losses followed by one
    small win would restore full size immediately -- the opposite of the rule.
    """
    step = max(0, min(int(getattr(record, "descale_step", 0) or 0), 2))
    return float(cfg.descale_factor) ** step


def lots_for(entry: float, stop: float, lot_size: int, cfg: GammaMoveConfig,
             record: Optional[TradeRecord] = None) -> int:
    """Whole lots, sized so the stop costs about ``risk_per_trade_pct``.

    Three ceilings apply and the tightest wins: the risk budget, the premium
    outlay cap, and the operator's explicit lot count in LOTS mode. Returns 0
    when even one lot breaches a cap -- which is a refusal to trade, not a
    rounding artefact, and the caller must surface it as such.
    """
    lot = max(1, int(lot_size or 1))
    if cfg.sizing_mode == "LOTS":
        lots = max(0, int(cfg.lots))
    else:
        risk_per_unit = float(entry) - float(stop)
        if risk_per_unit <= 0:
            return 0
        mult = risk_multiplier(record, cfg) if record is not None else 1.0
        budget = cfg.capital_inr * (cfg.risk_per_trade_pct / 100.0) * mult
        lots = int(budget // (risk_per_unit * lot))

    if lots <= 0:
        return 0
    # Premium outlay ceiling. A bought option's whole premium is at risk if the
    # stop gaps, so this cap is on the outlay, not on the stop distance.
    if entry > 0 and cfg.max_premium_at_risk_inr > 0:
        max_lots = int(cfg.max_premium_at_risk_inr // (entry * lot))
        lots = min(lots, max_lots)
    return max(0, lots)


def sizing_blocker(entry: float, stop: float, lot_size: int, cfg: GammaMoveConfig,
                   record: Optional[TradeRecord] = None) -> Optional[str]:
    """Why the size came out at zero, naming the constraint that actually bound.

    Three ceilings can each produce zero lots, and "size not set" is the wrong
    answer for two of them. A board row that blames the wrong setting sends the
    operator to change a number that was never the problem.
    """
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
    budget = cfg.capital_inr * (cfg.risk_per_trade_pct / 100.0) * mult
    need = risk_per_unit * lot
    detail = " (halved by the losing streak)" if mult < 1.0 else ""
    return (f"one lot risks Rs {need:,.0f} to the stop, above the "
            f"Rs {budget:,.0f} risk budget{detail}")


def at_risk_inr(entry: float, stop: float, quantity: int) -> float:
    return round(max(0.0, (float(entry) - float(stop))) * int(quantity), 2)


def deployed_inr(entry: float, quantity: int) -> float:
    return round(float(entry) * int(quantity), 2)
