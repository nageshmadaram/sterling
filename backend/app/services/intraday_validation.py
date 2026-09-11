"""What the walk-forward harness found, and what it therefore permits.

The gate has to have teeth or it is a document. This is the teeth: a strategy
that has not cleared the harness cannot be AUTO-executed, whatever the switches
say. Manual arming stays open — an operator taking an unproven setup with their
eyes open is their call, and refusing that would be paternalism rather than
safety — but nothing unproven trades while nobody is watching.

The record is written by ``study/intraday_walkforward.py`` and read by the
engine, the board and the settings page, so all four agree about what has been
measured without any of them holding a second copy.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Optional

from app.core.logging import get_logger
from app.engines.intraday import STRATEGY_KEYS

log = get_logger(__name__)

_KEY = "intraday_validation"

__all__ = ["StrategyValidation", "load", "record", "is_promoted",
           "auto_execution_blocker", "clear"]


@dataclass
class StrategyValidation:
    """One strategy's standing, as the harness left it."""

    strategy: str
    promoted: bool = False
    #: ISO date of the run. A promotion from a year ago is not a promotion.
    measured_at: str = ""
    #: What the run actually measured, so a reader can disagree with the gate.
    oos_trades: int = 0
    oos_net: float = 0.0
    sharpe: float = 0.0
    deflated_sharpe: float = 0.0
    permutation_p: Optional[float] = None
    max_drawdown_pct: float = 0.0
    #: Which checks passed, and the sentences for those that did not.
    checks: dict[str, bool] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    #: The run's own parameters. A promotion at 0.05% slippage says nothing
    #: about a book paying 0.5%, and the number travels with the verdict so
    #: nobody has to go and find the script to know which was measured.
    slippage_pct: Optional[float] = None
    symbols: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy, "promoted": self.promoted,
            "measured_at": self.measured_at, "oos_trades": self.oos_trades,
            "oos_net": round(self.oos_net, 2), "sharpe": round(self.sharpe, 3),
            "deflated_sharpe": round(self.deflated_sharpe, 4),
            "permutation_p": self.permutation_p,
            "max_drawdown_pct": round(self.max_drawdown_pct, 2),
            "checks": dict(self.checks), "reasons": list(self.reasons),
            "slippage_pct": self.slippage_pct, "symbols": list(self.symbols),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "StrategyValidation":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in dict(d).items() if k in known})


def load() -> dict[str, StrategyValidation]:
    """Every strategy's standing. Unmeasured strategies appear, unpromoted.

    Absent is NOT the same as unpromoted-and-measured, and both appear here
    rather than one of them being a missing key the caller has to remember to
    handle.
    """
    out = {k: StrategyValidation(strategy=k) for k in STRATEGY_KEYS}
    try:
        from app.services import db
        raw = db.get_config(_KEY)
        stored = json.loads(raw) if isinstance(raw, str) and raw else (raw or {})
        for key, value in dict(stored).items():
            if key in out and isinstance(value, dict):
                out[key] = StrategyValidation.from_dict({**value, "strategy": key})
    except Exception as exc:                                       # noqa: BLE001
        # A store we cannot read means nothing is proven, which is the safe
        # reading — an unreadable record must never promote anything.
        log.error("intraday: validation record unreadable (%s); nothing promoted", exc)
    return out


def record(verdicts: dict[str, StrategyValidation]) -> None:
    """Persist a harness run's verdicts, replacing what was there."""
    current = {k: v.as_dict() for k, v in load().items()}
    for key, value in verdicts.items():
        current[key] = value.as_dict()
    try:
        from app.services import db
        db.set_config(_KEY, json.dumps(current, separators=(",", ":")))
    except Exception as exc:                                       # noqa: BLE001
        log.error("intraday: FAILED to persist the validation record: %s", exc)


def clear() -> None:
    try:
        from app.services import db
        db.set_config(_KEY, "")
    except Exception as exc:                                       # noqa: BLE001
        log.error("intraday: could not clear the validation record: %s", exc)


def is_promoted(strategy: str) -> bool:
    return bool(load().get(strategy, StrategyValidation(strategy)).promoted)


def auto_execution_blocker(strategy: str) -> Optional[str]:
    """Why this strategy may not trade unattended, or ``None`` if it may.

    The sentence is the point. "Not promoted" tells an operator nothing; the
    harness's own reason tells them what would have to change.
    """
    v = load().get(strategy)
    if v is None:
        return f"{strategy} is not a strategy this engine knows"
    if v.promoted:
        return None
    if not v.measured_at:
        return (f"{strategy} has never been through the walk-forward harness — "
                "run study/intraday_walkforward.py")
    first = v.reasons[0] if v.reasons else "it did not clear the gate"
    return f"{strategy} did not pass the harness on {v.measured_at}: {first}"
