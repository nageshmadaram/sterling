"""What the walk-forward harness found for Snapback, and what it permits.

The gate has to have teeth or it is a document. This is the teeth: Snapback has
not cleared the harness, so it cannot AUTO-execute whatever the switches say.
Manual arming stays open — an operator taking a measured-but-unproven setup with
their eyes open is their call, and refusing that would be paternalism rather
than safety — but nothing unproven trades while nobody is watching.

The record is written by ``study/snapback_research.py --part gate`` and read by
the engine descriptor, the board and the settings page, so none of them holds a
second copy of the verdict.

**The record also carries the checks that PASSED.** Snapback's situation is
unusual for this repo: it clears seven of nine, including the entry-timing
permutation that every other strategy here has failed and the day-clustered
interval nothing here has ever cleared, and misses on the deflated Sharpe and a
year-consistency bar. A boolean ``promoted`` alone would
flatten that into the same "no" as a strategy whose entries lose money, and
those are not the same thing to an operator deciding whether to arm one by
hand.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from typing import Any, Optional

from app.core.logging import get_logger

log = get_logger(__name__)

_KEY = "snapback_validation"
COST_MODEL_VERSION = "zerodha_options_v2_per_leg_gap_calendar"

__all__ = ["Validation", "load", "record", "is_promoted",
           "auto_execution_blocker", "clear", "current_manifest",
           "is_compatible", "compatibility_reasons"]


@dataclass
class Validation:
    """The harness's verdict, and enough of the run to disagree with it."""

    promoted: bool = False
    #: ISO date of the run. A promotion from a year ago is not a promotion.
    measured_at: str = ""
    span: str = ""
    universe: list[str] = field(default_factory=list)
    oos_trades: int = 0
    oos_entry_days: int = 0
    oos_mean_day_return_pct: float = 0.0
    oos_ci_pct: list[float] = field(default_factory=list)
    sharpe: float = 0.0
    deflated_sharpe: float = 0.0
    #: Permutation against random entries of identical exposure, restricted to
    #: the out-of-sample windows. The headline evidence.
    permutation_p: Optional[float] = None
    permutation_p_full_sample: Optional[float] = None
    #: The vol multiple at which the book stops paying, against the 1.15-1.30
    #: the market charges. This is the check that decides whether the result is
    #: an artefact of the modelled premium.
    breakeven_vrp: Optional[float] = None
    max_drawdown_pct: float = 0.0
    allocation_pct: float = 0.0
    total_return_pct: float = 0.0
    per_year_pct: dict[str, float] = field(default_factory=dict)
    checks: dict[str, bool] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    #: A verdict at one slippage says nothing about a book paying another, so
    #: the number travels with it rather than living only in the script.
    slippage_pct: Optional[float] = None
    engine_version: str = ""
    config_hash: str = ""
    calendar_version: str = ""
    cost_model_version: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "promoted": self.promoted, "measured_at": self.measured_at,
            "span": self.span, "universe": list(self.universe),
            "oos_trades": self.oos_trades,
            "oos_entry_days": self.oos_entry_days,
            "oos_mean_day_return_pct": round(self.oos_mean_day_return_pct, 3),
            "oos_ci_pct": list(self.oos_ci_pct),
            "sharpe": round(self.sharpe, 3),
            "deflated_sharpe": round(self.deflated_sharpe, 4),
            "permutation_p": self.permutation_p,
            "permutation_p_full_sample": self.permutation_p_full_sample,
            "breakeven_vrp": self.breakeven_vrp,
            "max_drawdown_pct": round(self.max_drawdown_pct, 2),
            "allocation_pct": round(self.allocation_pct, 2),
            "total_return_pct": round(self.total_return_pct, 2),
            "per_year_pct": dict(self.per_year_pct),
            "checks": dict(self.checks), "reasons": list(self.reasons),
            "slippage_pct": self.slippage_pct,
            "engine_version": self.engine_version,
            "config_hash": self.config_hash,
            "calendar_version": self.calendar_version,
            "cost_model_version": self.cost_model_version,
            "manifest": {
                "engine_version": self.engine_version,
                "config_hash": self.config_hash,
                "calendar_version": self.calendar_version,
                "cost_model_version": self.cost_model_version,
            },
            "passed": sum(1 for v in self.checks.values() if v),
            "total_checks": len(self.checks),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Validation":
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in dict(d).items() if k in known})



def _config_hash(cfg: Any = None) -> str:
    if cfg is None:
        from app.engines.snapback import SnapbackConfig
        cfg = SnapbackConfig()
    if hasattr(cfg, "as_dict"):
        payload = cfg.as_dict()
    else:
        payload = dict(getattr(cfg, "__dict__", {}) or {})
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def current_manifest(cfg: Any = None) -> dict[str, str]:
    from app.engines.snapback import CONTRACT_VERSION
    from app.services.navigator.calendar import CALENDAR_VERSION
    return {
        "engine_version": str(CONTRACT_VERSION),
        "config_hash": _config_hash(cfg),
        "calendar_version": str(CALENDAR_VERSION),
        "cost_model_version": COST_MODEL_VERSION,
    }


def compatibility_reasons(record: Optional[dict[str, Any]] = None, cfg: Any = None) -> list[str]:
    v = dict(load() if record is None else record)
    manifest = current_manifest(cfg)
    reasons: list[str] = []
    for key, expected in manifest.items():
        got = v.get(key) or (v.get("manifest") or {}).get(key)
        if got != expected:
            reasons.append(f"{key} is {got or 'missing'}, expected {expected}")
    return reasons


def is_compatible(record: Optional[dict[str, Any]] = None, cfg: Any = None) -> bool:
    return not compatibility_reasons(record, cfg)

def load() -> dict[str, Any]:
    """The stored verdict as a plain dict, or ``{}`` when there is none.

    An unreadable store returns ``{}``, which reads as "nothing proven". That is
    the safe direction: an unreadable record must never promote anything.
    """
    try:
        from app.services import db
        raw = db.get_config(_KEY)
        stored = json.loads(raw) if isinstance(raw, str) and raw else (raw or {})
        return dict(stored) if isinstance(stored, dict) else {}
    except Exception as exc:                                       # noqa: BLE001
        log.error("snapback: validation record unreadable (%s); nothing promoted",
                  exc)
        return {}


def record(verdict: Validation) -> bool:
    """Store the verdict. Returns whether it actually landed.

    The caller needs the answer. A write that failed and a write that succeeded
    look identical to anyone reading the script's output otherwise, and the
    difference is whether the board is showing this run's verdict or the last
    one's.
    """
    try:
        from app.services import db
        manifest = current_manifest()
        for k, v in manifest.items():
            if not getattr(verdict, k):
                setattr(verdict, k, v)
        db.set_config(_KEY, json.dumps(verdict.as_dict(), separators=(",", ":")))
        return True
    except Exception as exc:                                       # noqa: BLE001
        log.error("snapback: FAILED to persist the validation record: %s", exc)
        return False


def clear() -> None:
    try:
        from app.services import db
        db.set_config(_KEY, "")
    except Exception as exc:                                       # noqa: BLE001
        log.error("snapback: could not clear the validation record: %s", exc)


def is_promoted() -> bool:
    v = load()
    return bool(v.get("promoted")) and is_compatible(v)


def auto_execution_blocker(cfg: Any = None) -> Optional[str]:
    """Why Snapback may not trade unattended, or ``None`` if it may.

    The sentence is the point. "Not promoted" tells an operator nothing; the
    harness's own first reason tells them what would have to change.
    """
    if cfg is not None and cfg.trading_mode != "swing":
        return ("Snapback scalping/intraday has no promoted option-tape validation "
                "or connected broker protection lifecycle; live rows are plans only")
    v = load()
    if v.get("promoted"):
        stale = compatibility_reasons(v, cfg)
        if not stale:
            return None
        return "Snapback validation evidence is stale for this implementation: " + "; ".join(stale)
    if not v.get("measured_at"):
        return ("Snapback has never been through the walk-forward harness — run "
                "study/snapback_research.py --part gate")
    reasons = list(v.get("reasons") or [])
    passed, total = v.get("passed"), v.get("total_checks")
    tally = f" (passed {passed} of {total} checks)" if passed is not None else ""
    first = reasons[0] if reasons else "it did not clear the gate"
    return f"Snapback did not pass the harness on {v['measured_at']}{tally}: {first}"


def build_validation_report_for_run(
    replay_result: dict[str, Any],
    cfg: Any,
    dataset_manifest_hash: str = "dataset_v1",
) -> Any:
    """Generate a formal ValidationReport artifact from a causal replay run."""
    from app.engines.snapback.intraday_models import ValidationReport
    import numpy as np

    trades = replay_result.get("trades", [])
    opportunities = replay_result.get("opportunities", 0)
    completed = len(trades)

    if completed == 0:
        return ValidationReport(
            run_id=f"run_{int(hashlib.sha256(str(replay_result).encode()).hexdigest()[:12], 16)}",
            config_hash=_config_hash(cfg),
            rule_hash=_config_hash(cfg),
            dataset_manifest_hash=dataset_manifest_hash,
            total_sessions=1,
            total_opportunities=opportunities,
            completed_trades=0,
            net_expectancy_per_trade=0.0,
            lower_95_ci=0.0,
            win_rate=0.0,
            profit_factor=0.0,
            max_drawdown_pct=0.0,
            is_promotable=False,
            limitations=["no_completed_trades_in_replay"],
        )

    net_pnls = [t["net_pnl"] for t in trades]
    total_net = sum(net_pnls)
    net_expectancy = total_net / float(completed)

    wins = [p for p in net_pnls if p > 0]
    losses = [abs(p) for p in net_pnls if p < 0]

    win_rate = len(wins) / float(completed)
    profit_factor = (sum(wins) / sum(losses)) if sum(losses) > 0 else (999.0 if sum(wins) > 0 else 0.0)

    # Compute lower 95% CI via bootstrap
    if len(net_pnls) > 5:
        bootstrap_means = [np.mean(np.random.choice(net_pnls, size=len(net_pnls), replace=True)) for _ in range(500)]
        lower_95_ci = float(np.percentile(bootstrap_means, 2.5))
    else:
        lower_95_ci = net_expectancy - 1.96 * (np.std(net_pnls) / math.sqrt(completed) if completed > 1 else 0.0)

    # Calculate max drawdown
    cum_equity = np.cumsum(net_pnls)
    peak = np.maximum.accumulate(cum_equity)
    drawdowns = (peak - cum_equity)
    max_dd = float(np.max(drawdowns)) if len(drawdowns) > 0 else 0.0

    # DEPRECATED & QUARANTINED: Authoritative promotion gate lives strictly in study/snapback_authoritative_gate.py
    # and 04_VALIDATION_AND_DELIVERY.md. This legacy helper MUST NOT grant is_promotable = True.
    log.warning(
        "build_validation_report_for_run is DEPRECATED & QUARANTINED. "
        "Authoritative promotion authority resides exclusively in study/snapback_authoritative_gate.py."
    )
    promotable = False

    limitations: list[str] = ["quarantined_legacy_report_builder"]
    if completed < 50:
        limitations.append("sample_size_below_50_trades")
    if lower_95_ci <= 0.0:
        limitations.append("lower_95_ci_expectancy_not_positive")
    limitations.append("economic_gates_not_cleared")

    return ValidationReport(
        run_id=f"run_{hashlib.sha256(str(net_pnls).encode()).hexdigest()[:12]}",
        config_hash=_config_hash(cfg),
        rule_hash=_config_hash(cfg),
        dataset_manifest_hash=dataset_manifest_hash,
        total_sessions=1,
        total_opportunities=opportunities,
        completed_trades=completed,
        net_expectancy_per_trade=round(net_expectancy, 2),
        lower_95_ci=round(lower_95_ci, 2),
        win_rate=round(win_rate, 4),
        profit_factor=round(profit_factor, 2),
        max_drawdown_pct=round(max_dd, 2),
        is_promotable=promotable,
        limitations=limitations,
    )
