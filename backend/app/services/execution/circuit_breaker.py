from enum import Enum
from dataclasses import dataclass, field
from app.services.notifications.formatters import fmt_circuit_breaker


class CircuitState(str, Enum):
    OK = "ok"
    HALTED = "halted"
    NO_NEW_ENTRIES = "no_new_entries"
    SIZE_REDUCED = "size_reduced"
    MAX_POSITIONS = "max_positions"


@dataclass
class CircuitCheck:
    state: CircuitState
    reason: str
    size_multiplier: float = 1.0


class CircuitBreaker:
    def __init__(self, telegram=None):
        self.telegram = telegram
        self._halted = False
        self._size_mult = 1.0

    async def check(
        self,
        daily_pnl_pct: float,
        free_margin_pct: float,
        open_count: int,
        mode_max_concurrent: int,
    ) -> CircuitCheck:
        if self._halted:
            return CircuitCheck(CircuitState.HALTED, "Previously halted")

        if daily_pnl_pct < -0.05:
            self._halted = True
            if self.telegram:
                await self.telegram.send(fmt_circuit_breaker("Daily loss -5% reached"))
            return CircuitCheck(CircuitState.HALTED, "Daily loss limit")

        if free_margin_pct < 0.20:
            if self.telegram:
                await self.telegram.send(
                    fmt_circuit_breaker(f"Margin {free_margin_pct:.0%}")
                )
            return CircuitCheck(CircuitState.NO_NEW_ENTRIES, "Margin < 20%")

        if open_count >= mode_max_concurrent:
            return CircuitCheck(
                CircuitState.MAX_POSITIONS,
                f"{open_count}/{mode_max_concurrent} positions open",
            )

        from app.services import db as _db
        recent_raw = _db.get_recent_closed_trades(5)
        consec_losses = sum(1 for t in recent_raw if (t.get("realized_pnl_usd") or 0) < 0)
        consec_losses = 0
        for t in recent_raw:
            pnl = (
                t.get("realized_pnl_inr")
                if t.get("realized_pnl_inr") is not None
                else t.get("realized_pnl")
                if t.get("realized_pnl") is not None
                else t.get("realized_pnl_usd")
                if t.get("realized_pnl_usd") is not None
                else t.get("pnl", 0.0)
            ) or 0.0
            if float(pnl) < 0:
                consec_losses += 1
            else:
                break

        if consec_losses >= 5:
            self._size_mult = 0.5
            if self.telegram:
                await self.telegram.send("5 consecutive losses — size halved")
                try:
                    await self.telegram.send("5 consecutive losses — size halved")
                except Exception:
                    pass
            return CircuitCheck(
                CircuitState.SIZE_REDUCED,
                "5 consecutive losses",
                size_multiplier=0.5,
            )
        elif self._size_mult < 1.0 and consec_losses < 3:
            # Recovery: winning trade breaks losing streak, restore sizing
            self._size_mult = 1.0

        return CircuitCheck(
            CircuitState.OK,
            "All checks passed",
            size_multiplier=self._size_mult,
        )

    def reset(self) -> None:
        self._halted = False
        self._size_mult = 1.0

    @property
    def halted(self) -> bool:
        return self._halted

    @property
    def size_multiplier(self) -> float:
        return self._size_mult
