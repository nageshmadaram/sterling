"""Data the Gamma Move engine passes around. No broker, no socket, no clock.

Every price level is Optional on purpose. A missing number must reach the board
as ``None`` and render as an em dash -- on a stop column a fabricated ``0`` is a
trade-destroying lie, and this codebase has shipped that bug before.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional, Sequence

OptionType = Literal["CE", "PE"]
LevelKind = Literal["support", "resistance"]
Regime = Literal["up", "down", "unknown"]
SignalState = Literal["watching", "armed", "running", "weakening", "ended", "error"]


def q2(value: float) -> float:
    """Two decimals. Rupee prices are quoted to paise and compared for equality."""
    return round(float(value) + 0.0, 2)


def align_to_tick(price: float, tick: float, *, side: str = "buy") -> float:
    """Snap to a tradable tick.

    Buys floor so a limit never pays more than the intended price. Sells
    (exits) ceil so a limit never sells cheaper than the intended price.
    An unaligned limit is rejected by the exchange; rounding the wrong way
    is a fill at a price the stop was not sized for.
    """
    t = float(tick or 0.05) or 0.05
    raw = float(price) / t
    if str(side).lower() in ("sell", "exit"):
        snapped = int(raw) * t if raw == int(raw) else (int(raw) + 1) * t
    else:
        snapped = int(raw) * t
    return q2(snapped)


@dataclass(frozen=True)
class Candle:
    ts_ms: int
    open: float
    high: float
    low: float
    close: float
    volume: int = 0


@dataclass(frozen=True)
class OICandle(Candle):
    """A candle that also carries open interest.

    ``volume`` is this bar's own traded quantity, which is what the historical
    API returns. A tick's ``volume_traded`` is a day-cumulative figure and must
    be differenced before it can be put in this field -- mixing the two makes
    every volume ratio enormous and constant, which silently removes one third
    of the entry rule.
    """
    oi: int = 0


@dataclass(frozen=True)
class InstrumentRef:
    instrument_id: str
    tradingsymbol: str
    option_type: OptionType
    strike: float
    expiry: str
    lot_size: int = 1
    tick_size: float = 0.05
    exchange: str = "NFO"


@dataclass(frozen=True)
class SpotLevel:
    """A clustered swing level on the underlying's own chart."""
    price: float
    kind: LevelKind
    touches: int
    last_touch_ms: int = 0

    def distance_pct(self, spot: float) -> float:
        """Unsigned distance from spot, as a percent of the level."""
        if self.price <= 0:
            return float("inf")
        return abs(float(spot) - self.price) / self.price * 100.0


@dataclass(frozen=True)
class StrikeCandidate:
    underlying: str
    level: SpotLevel
    instrument: InstrumentRef
    oi: int
    days_to_expiry: int
    spot: float
    premium: float = 0.0
    #: Highest open interest on the same expiry + option type. None means the
    #: caller did not look at the rest of the chain, so the wall check is skipped
    #: rather than invented. The scanner always fills this.
    chain_oi_max: int | None = None

    @property
    def option_type(self) -> OptionType:
        return self.instrument.option_type

    @property
    def distance_pct(self) -> float:
        return self.level.distance_pct(self.spot)


@dataclass(frozen=True)
class TriggerMetrics:
    """The three conditions, each with the number behind it.

    Kept together with the booleans so the board can show *which* leg of the
    triple is short rather than only that the setup did not fire.
    """
    oi_drop_pct: float
    volume_ratio: float
    price_gain_pct: float
    unwinding: bool
    abnormal: bool
    rising: bool
    bars_confirmed: int = 0
    bars_required: int = 1

    @property
    def triggered(self) -> bool:
        return (self.unwinding and self.abnormal and self.rising
                and self.bars_confirmed >= self.bars_required)

    def shortfall(self) -> Optional[str]:
        """Which condition is missing, in the operator's words. None if all hold."""
        missing = [name for name, ok in (("open interest is not unwinding", self.unwinding),
                                         ("volume is not abnormal", self.abnormal),
                                         ("premium is not rising", self.rising)) if not ok]
        if missing:
            return "; ".join(missing)
        if self.bars_confirmed < self.bars_required:
            return f"confirmed on {self.bars_confirmed} of {self.bars_required} bars"
        return None

    def as_dict(self) -> dict:
        return {"oi_drop_pct": q2(self.oi_drop_pct), "volume_ratio": q2(self.volume_ratio),
                "price_gain_pct": q2(self.price_gain_pct), "unwinding": self.unwinding,
                "abnormal": self.abnormal, "rising": self.rising,
                "bars_confirmed": self.bars_confirmed, "bars_required": self.bars_required,
                "triggered": self.triggered}


@dataclass(frozen=True)
class GammaSignal:
    id: str
    candidate: StrikeCandidate
    metrics: Optional[TriggerMetrics]
    state: SignalState
    at_ms: int
    regime: Regime = "unknown"
    reason: Optional[str] = None
    entry: Optional[float] = None
    stop: Optional[float] = None
    target: Optional[float] = None
    trail: Optional[float] = None
    ltp: Optional[float] = None
    exit_price: Optional[float] = None
    lots: Optional[int] = None
    quantity: Optional[int] = None
    at_risk_inr: Optional[float] = None
    deployed_inr: Optional[float] = None
    entry_day: Optional[str] = None
    exit_reason: Optional[str] = None

    def __post_init__(self) -> None:
        if self.state in ("watching", "error") and not self.reason:
            raise ValueError(f"a '{self.state}' signal must carry a reason")

    def as_dict(self) -> dict:
        c, i = self.candidate, self.candidate.instrument
        return {
            "id": self.id, "state": self.state, "at_ms": self.at_ms,
            "underlying": c.underlying, "regime": self.regime, "reason": self.reason,
            "exit_reason": self.exit_reason, "entry_day": self.entry_day,
            "instrument": {
                "instrument_id": i.instrument_id, "tradingsymbol": i.tradingsymbol,
                "option_type": i.option_type, "strike": i.strike, "expiry": i.expiry,
                "lot_size": i.lot_size, "tick_size": i.tick_size, "exchange": i.exchange,
            },
            "level": {"price": q2(c.level.price), "kind": c.level.kind,
                      "touches": c.level.touches,
                      "distance_pct": q2(c.distance_pct)},
            "oi": c.oi, "days_to_expiry": c.days_to_expiry, "spot": q2(c.spot),
            "metrics": self.metrics.as_dict() if self.metrics else None,
            "levels": {"ltp": self.ltp, "entry": self.entry, "stop": self.stop,
                       "trail": self.trail, "target": self.target, "exit": self.exit_price},
            "sizing": {"lots": self.lots, "quantity": self.quantity,
                       "at_risk_inr": self.at_risk_inr, "deployed_inr": self.deployed_inr},
        }


@dataclass
class PositionState:
    """A live position. Mutable -- the trail ratchets and the high-water moves."""
    signal_id: str
    instrument: InstrumentRef
    entry: float
    stop: float
    quantity: int
    lots: int
    entered_ms: int
    entry_day: str
    target: Optional[float] = None
    trail: Optional[float] = None
    high_water: float = 0.0
    sessions_held: int = 0
    exiting: bool = False
    status: str = "pending"
    order_id: str = ""
    fill_price: float = 0.0
    gtt_id: int = 0
    stop_mode: str = "both"
    idempotency_key: str = ""
    exit_reason: str = ""

    @property
    def effective_entry(self) -> float:
        return self.fill_price if self.fill_price > 0 else self.entry

    @property
    def is_open(self) -> bool:
        return self.status in ("pending", "open")

    def __post_init__(self) -> None:
        if self.high_water <= 0:
            self.high_water = self.effective_entry


@dataclass(frozen=True)
class ExitEvent:
    signal_id: str
    reason: str
    price: float
    at_ms: int


@dataclass
class TradeRecord:
    """Realised outcomes, and the de-scaling streak they drive."""
    trades: int = 0
    wins: int = 0
    losses: int = 0
    consecutive_losses: int = 0
    consecutive_wins: int = 0
    descaled: bool = False
    descale_step: int = 0
    realised_inr: float = 0.0
    day_realised_inr: float = 0.0
    day: str = ""
    history: list = field(default_factory=list)

    def record(self, pnl_inr: float, day: str, *, descale_after: int = 3,
               rescale_after: int = 2) -> None:
        if day != self.day:
            self.day, self.day_realised_inr = day, 0.0
        self.trades += 1
        self.realised_inr += pnl_inr
        self.day_realised_inr += pnl_inr
        if pnl_inr >= 0:
            self.wins += 1
            self.consecutive_wins += 1
            self.consecutive_losses = 0
        else:
            self.losses += 1
            self.consecutive_losses += 1
            self.consecutive_wins = 0
        if pnl_inr < 0 and self.consecutive_losses > 0 and \
                self.consecutive_losses % max(1, int(descale_after)) == 0:
            self.descale_step = min(int(self.descale_step) + 1, 2)
        elif pnl_inr >= 0 and self.descaled and self.consecutive_wins > 0 and \
                self.consecutive_wins % max(1, int(rescale_after)) == 0:
            self.descale_step = max(int(self.descale_step) - 1, 0)
        self.descaled = self.descale_step > 0
        self.history.append({"pnl_inr": q2(pnl_inr), "day": day})

    def as_dict(self) -> dict:
        return {"trades": self.trades, "wins": self.wins, "losses": self.losses,
                "win_rate": q2(100.0 * self.wins / self.trades) if self.trades else None,
                "consecutive_losses": self.consecutive_losses,
                "consecutive_wins": self.consecutive_wins,
                "descaled": self.descaled,
                "descale_step": int(self.descale_step),
                "realised_inr": q2(self.realised_inr),
                "day_realised_inr": q2(self.day_realised_inr), "day": self.day,
                "history": list(self.history),
                "verdict": ("no realised trades yet" if not self.trades
                            else f"{self.wins}/{self.trades} winners")}

    @classmethod
    def from_dict(cls, d: dict | None) -> "TradeRecord":
        rec = cls()
        if not d:
            return rec
        for name in ("trades", "wins", "losses", "consecutive_losses",
                     "consecutive_wins", "descale_step"):
            if name in d:
                try:
                    setattr(rec, name, int(d[name] or 0))
                except (TypeError, ValueError):
                    pass
        rec.descale_step = max(0, min(int(rec.descale_step or 0), 2))
        rec.descaled = rec.descale_step > 0
        for name in ("realised_inr", "day_realised_inr"):
            if name in d:
                try:
                    setattr(rec, name, float(d[name] or 0.0))
                except (TypeError, ValueError):
                    pass
        rec.day = str(d.get("day") or "")
        hist = d.get("history")
        if isinstance(hist, list):
            rec.history = list(hist)
        return rec
