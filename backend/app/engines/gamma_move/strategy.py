"""The Gamma Move state machine."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Optional, Sequence

from . import exit as exits
from . import sizing
from .config import GammaMoveConfig
from .levels import live_levels, option_type_for
from .models import (Candle, GammaSignal, OICandle, PositionState, SpotLevel,
                     StrikeCandidate, TradeRecord, q2)
from .regime import regime_allows, regime_of, regime_reason
from .selection import expiry_in_window, is_chain_wall, spot_through_or_at_strike
from .trigger import closed_bars, evaluate as evaluate_trigger


class Phase(str, Enum):
    IDLE = "idle"
    SCANNING = "scanning"
    WATCHING = "watching"
    ARMED = "armed"
    IN_POSITION = "in_position"
    HALTED = "halted"


class Intent(str, Enum):
    NONE = "none"
    ENTER = "enter"
    EXIT = "exit"
    HALT = "halt"


@dataclass
class Decision:
    intent: Intent
    signal: Optional[GammaSignal] = None
    reason: str = ""
    exit_position: Optional[PositionState] = None
    exit_reason: str = ""


@dataclass
class SessionState:
    day: str = ""
    phase: Phase = Phase.IDLE
    trades_today: int = 0
    positions: dict = field(default_factory=dict)
    record: TradeRecord = field(default_factory=TradeRecord)
    halt_reason: str = ""

    def roll(self, today: str) -> None:
        if self.day == today:
            return
        self.day = today
        self.trades_today = 0
        self.halt_reason = ""
        for pos in self.positions.values():
            pos.sessions_held += 1


class GammaMoveStrategy:
    def __init__(self, cfg: GammaMoveConfig, state: Optional[SessionState] = None):
        self.cfg = cfg
        self.state = state or SessionState()

    def levels_for(self, spot_candles: Sequence[Candle], spot: float,
                   levels: Sequence[SpotLevel]) -> list[SpotLevel]:
        return live_levels(levels, spot, self.cfg.level_proximity_pct)

    def screen(self, *, underlying: str, spot: float, levels: Sequence[SpotLevel],
               spot_candles: Sequence[Candle], today: date) -> tuple[list, Optional[str]]:
        near = self.levels_for(spot_candles, spot, levels)
        if not near:
            return [], (f"spot {q2(spot)} is not within "
                        f"{self.cfg.level_proximity_pct}% of a confirmed level")
        if self.cfg.regime_enabled:
            regime = regime_of(spot_candles, self.cfg)
            near = [lv for lv in near
                    if regime_allows(regime, option_type_for(lv), self.cfg)]  # type: ignore[arg-type]
            if not near:
                kinds = {option_type_for(lv) for lv in
                         self.levels_for(spot_candles, spot, levels)}
                return [], regime_reason(regime, next(iter(kinds)) if kinds else "CE")  # type: ignore[arg-type]
        return list(near), None

    def evaluate(self, candidate: StrikeCandidate, bars: Sequence[OICandle],
                 *, now_ms: int, today: date, regime: str = "unknown",
                 signal_id: Optional[str] = None) -> GammaSignal:
        sid = signal_id or (f"{candidate.instrument.tradingsymbol}"
                            f"@{candidate.level.kind}:{int(candidate.level.price)}")
        base = dict(id=sid, candidate=candidate, at_ms=now_ms, regime=regime,
                    ltp=q2(bars[-1].close) if bars else None)

        if not expiry_in_window(candidate.instrument.expiry, today, self.cfg):
            return GammaSignal(**base, metrics=None, state="watching",
                               reason=(
                                   f"{candidate.days_to_expiry} days to expiry is outside "
                                   f"the {self.cfg.expiry_dte_min}-{self.cfg.expiry_dte_max} "
                                   f"day window"
                                   + (" (expiry day excluded)"
                                      if self.cfg.avoid_expiry_day
                                      and candidate.days_to_expiry == 0 else "")))
        if not regime_allows(regime, candidate.option_type, self.cfg):  # type: ignore[arg-type]
            return GammaSignal(**base, metrics=None, state="watching",
                               reason=regime_reason(regime, candidate.option_type))  # type: ignore[arg-type]

        inst = candidate.instrument
        if getattr(self.cfg, "require_spot_through_strike", True) and not spot_through_or_at_strike(
                candidate.spot, inst.strike, inst.option_type,
                self.cfg.level_proximity_pct):
            side = "above" if inst.option_type == "CE" else "below"
            return GammaSignal(**base, metrics=None, state="watching",
                               reason=(f"spot {candidate.spot} has not broken {side} "
                                       f"the {inst.strike:g} wall"))
        if not is_chain_wall(candidate.oi, getattr(candidate, "chain_oi_max", None),
                             required=getattr(self.cfg, "require_chain_max_oi", True)):
            return GammaSignal(**base, metrics=None, state="watching",
                               reason=(f"strike OI {candidate.oi:,} is not the chain "
                                       f"wall ({candidate.chain_oi_max:,})"))

        closed = closed_bars(bars, self.cfg, now_ms)
        metrics = evaluate_trigger(closed, self.cfg, now_ms=now_ms)
        if metrics is None:
            return GammaSignal(**base, metrics=None, state="watching",
                               reason="not enough of today's bars to judge the trigger")
        if not metrics.triggered:
            return GammaSignal(**base, metrics=metrics, state="watching",
                               reason=metrics.shortfall() or "trigger incomplete")

        entry = q2(closed[-1].close) if closed else q2(bars[-1].close)
        stop = exits.initial_stop(entry, closed or bars, self.cfg)
        if stop is None:
            return GammaSignal(**base, metrics=metrics, state="watching",
                               reason="no valid stop: the recent swing low is not below entry")

        lots = sizing.lots_for(entry, stop, candidate.instrument.lot_size,
                               self.cfg, self.state.record)
        blocker = sizing.sizing_blocker(entry, stop, candidate.instrument.lot_size,
                                        self.cfg, self.state.record)
        if blocker:
            return GammaSignal(**base, metrics=metrics, state="watching", reason=blocker)

        qty = self.cfg.effective_quantity(candidate.instrument.lot_size, lots)
        return GammaSignal(
            **base, metrics=metrics, state="armed", reason=None, entry=entry, stop=stop,
            target=exits.target_price(entry, self.cfg), lots=lots, quantity=qty,
            at_risk_inr=sizing.at_risk_inr(entry, stop, qty),
            deployed_inr=sizing.deployed_inr(entry, qty))

    def admit(self, signal: GammaSignal, today: str) -> Optional[str]:
        self.state.roll(today)
        if self.state.halt_reason:
            return f"halted: {self.state.halt_reason}"
        if not self.cfg.enabled:
            return "strategy disabled"
        if signal.state != "armed":
            return "signal is not armed"
        if signal.candidate.instrument.tradingsymbol in self.state.positions:
            return "already holding this contract"
        if len(self.state.positions) >= self.cfg.max_concurrent_positions:
            return (f"already holding {len(self.state.positions)} positions, the cap is "
                    f"{self.cfg.max_concurrent_positions}")
        if self.state.trades_today >= self.cfg.max_new_trades_per_day:
            return f"daily trade limit of {self.cfg.max_new_trades_per_day} reached"
        if self.state.record.day == today and \
                self.state.record.day_realised_inr <= -self.cfg.daily_loss_limit_inr:
            return (f"daily loss limit of Rs {self.cfg.daily_loss_limit_inr:,.0f} reached")
        return None

    def on_entry(self, signal: GammaSignal, fill: float, now_ms: int,
                 today: str) -> PositionState:
        stop = signal.stop if signal.stop is not None else fill * 0.7
        pos = PositionState(
            signal_id=signal.id, instrument=signal.candidate.instrument, entry=q2(fill),
            stop=q2(stop), quantity=int(signal.quantity or 0), lots=int(signal.lots or 0),
            entered_ms=now_ms, entry_day=today, target=signal.target)
        self.state.positions[signal.candidate.instrument.tradingsymbol] = pos
        self.state.trades_today += 1
        self.state.phase = Phase.IN_POSITION
        return pos

    def on_price(self, pos: PositionState, ltp: float, now_ms: int, today: str,
                 *, session_over: bool = False) -> Decision:
        exits.update_trail(pos, ltp, self.cfg)
        reason = exits.should_exit(pos, ltp, now_ms, today, self.cfg,
                                   session_over=session_over)
        if reason and not pos.exiting:
            pos.exiting = True
            return Decision(intent=Intent.EXIT, exit_position=pos, exit_reason=reason)
        return Decision(intent=Intent.NONE)

    def on_exit(self, pos: PositionState, price: float, today: str) -> float:
        pnl = exits.realised_inr(pos, price)
        self.state.record.record(pnl, today,
                                 descale_after=self.cfg.descale_after_losses,
                                 rescale_after=self.cfg.rescale_after_wins)
        self.state.positions.pop(pos.instrument.tradingsymbol, None)
        if not self.state.positions:
            self.state.phase = Phase.WATCHING
        if self.state.record.day_realised_inr <= -self.cfg.daily_loss_limit_inr:
            self.state.halt_reason = "daily loss limit reached"
            self.state.phase = Phase.HALTED
        return pnl
