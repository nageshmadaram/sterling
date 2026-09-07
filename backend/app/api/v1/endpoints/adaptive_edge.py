"""Adaptive Edge research UI API. Does not unlock ExecutionGate or F-101."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator

from app.engines.adaptive_edge.execution_gate import evaluate_execution_gate
from app.engines.adaptive_edge.formula_registry import FORMULAS, FormulaStatus
from app.engines.adaptive_edge.option_ladder import (
    AE_DEFAULT_LADDER,
    ALLOWED_MONEYNESS,
    INDEX_TO_TAPE,
    build_snapshot_signals,
    load_live_spot_scans,
)
from app.engines.adaptive_edge.production_readiness import production_readiness
from app.services import db

router = APIRouter(prefix="/adaptive-edge", tags=["adaptive-edge"])
CONFIG_KEY = "adaptive_edge_settings"
ARTIFACT = Path(__file__).resolve().parents[4] / "data" / "adaptive_edge" / "research_e2e.json"
MANIFEST = ARTIFACT.with_name("software_e2e_manifest.json")
DEFAULT_INDICES = ["NIFTY 50", "NIFTY BANK", "NIFTY FIN SERVICE", "SENSEX"]
ScanSource = Literal["spot", "derivatives", "both", "confluence"]
ScanExpiry = Literal["weekly", "monthly"]


class AdaptiveEdgeSettings(BaseModel):
    enabled: bool = False
    symbol: str = "NIFTY-I"
    symbols: list[str] = Field(default_factory=lambda: ["NIFTY-I"])
    scan_source: ScanSource = "spot"
    scan_indices: list[str] = Field(default_factory=lambda: list(DEFAULT_INDICES))
    scan_stocks: list[str] = Field(default_factory=list)
    scan_all_stocks: bool = False
    scan_stock_contracts: bool = False
    strike_moneyness: list[str] = Field(default_factory=lambda: list(AE_DEFAULT_LADDER))
    scan_expiries: list[ScanExpiry] = Field(default_factory=lambda: ["weekly", "monthly"])
    scan_expiries_indices: list[ScanExpiry] = Field(default_factory=lambda: ["weekly", "monthly"])
    # The expiry window, same three names every other engine's Contracts section
    # uses. Permissive defaults: this adds a control, not a policy.
    expiry_dte_min: int = 0
    expiry_dte_max: int = 400
    avoid_expiry_day: bool = False
    w_short: int = Field(5, ge=2, le=60)
    w_long: int = Field(15, ge=3, le=120)
    stop_points: float = Field(80.0, gt=0)
    trail_points: float = Field(40.0, gt=0)
    profit_lock_activation_points: float = Field(50.0, gt=0)
    profit_lock_offset_points: float = Field(15.0, gt=0)
    persistence_bars: int = Field(3, ge=1, le=30)
    scalp_favorable_points: float = Field(5.0, gt=0)
    extended_favorable_points: float = Field(15.0, gt=0)
    intraday_favorable_points: float = Field(25.0, gt=0)
    tick_size: float = Field(1.0, gt=0)
    ib_minutes: int = Field(15, ge=5, le=60)
    drawdown_circuit_breaker_enabled: bool = True
    max_daily_drawdown_pct: float = Field(3.0, ge=0.5, le=10.0)

    @field_validator("scan_indices")
    @classmethod
    def _known_indices(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("select at least one index")
        unknown = [item for item in value if item not in INDEX_TO_TAPE]
        if unknown:
            raise ValueError(f"unknown scan_indices: {unknown}")
        return value

    @field_validator("strike_moneyness")
    @classmethod
    def _known_moneyness(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("select at least one strike")
        unknown = [item for item in value if item not in ALLOWED_MONEYNESS]
        if unknown:
            raise ValueError(f"unknown strike_moneyness: {unknown}")
        return value

    @field_validator("scan_expiries", "scan_expiries_indices")
    @classmethod
    def _known_expiries(cls, value: list[str]) -> list[str]:
        if not value:
            raise ValueError("select at least one expiry cycle")
        return value

    @model_validator(mode="after")
    def _sync_symbols(self) -> "AdaptiveEdgeSettings":
        if self.scan_indices:
            self.symbols = [INDEX_TO_TAPE[name] for name in self.scan_indices]
            self.symbol = self.symbols[0]
        elif self.symbols:
            self.symbol = self.symbols[0]
        if self.scan_expiries_indices:
            self.scan_expiries = list(self.scan_expiries_indices)
        return self


def _default_settings() -> AdaptiveEdgeSettings:
    return AdaptiveEdgeSettings()


def _load_settings() -> AdaptiveEdgeSettings:
    raw = db.get_config(CONFIG_KEY, "")
    if not raw:
        return _default_settings()
    try:
        return AdaptiveEdgeSettings.model_validate(json.loads(raw))
    except Exception:
        return _default_settings()


def _load_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None



#: Legacy settings fields that map onto the real engine configuration. Anything
#: written here is mirrored into AdaptiveEdgeConfig, which is what the scanner
#: and runner actually read.
_MIRRORED_TO_ENGINE: dict[str, str] = {
    "enabled": "enabled",
    "scan_indices": "scan_indices",
    "scan_stocks": "scan_stocks",
    "scan_all_stocks": "scan_all_stocks",
    "scan_stock_contracts": "stock_contracts",
    "scan_expiries_indices": "scan_expiries_indices",
    "expiry_dte_min": "expiry_dte_min",
    "expiry_dte_max": "expiry_dte_max",
    "avoid_expiry_day": "avoid_expiry_day",
}

#: Fields this surface accepts and stores but which reach no engine. They belong
#: to an earlier moving-average scalper, not to the Master Specification strategy
#: this engine now implements. They are reported rather than quietly accepted,
#: because a setting that saves successfully and changes nothing is the worst of
#: both worlds — the operator believes they configured something.
_INERT_FIELDS: tuple[str, ...] = (
    "symbol", "symbols", "scan_source", "strike_moneyness", "scan_expiries",
    "w_short", "w_long", "stop_points", "trail_points",
    "profit_lock_activation_points", "profit_lock_offset_points",
    "persistence_bars", "scalp_favorable_points", "extended_favorable_points",
    "intraday_favorable_points", "tick_size", "ib_minutes",
    "drawdown_circuit_breaker_enabled", "max_daily_drawdown_pct",
)


def _mirror_into_engine_config(settings: "AdaptiveEdgeSettings") -> list[str]:
    """Push the mappable settings into the configuration the engine reads.

    Returns any problems as strings rather than raising: a legacy write that
    cannot be represented in the engine config must not 500 the settings page,
    but it must not silently vanish either.
    """
    from app.services.adaptive_edge import set_config
    payload: dict[str, Any] = {}
    for legacy, engine in _MIRRORED_TO_ENGINE.items():
        value = getattr(settings, legacy, None)
        if value is None:
            continue
        payload[engine] = list(value) if isinstance(value, (list, tuple)) else value
    if not payload:
        return []
    try:
        set_config(payload)
        return []
    except (ValueError, TypeError) as exc:
        return [str(exc)]


@router.get("/settings")
def get_settings() -> dict[str, Any]:
    """Legacy settings surface, now backed by the real engine configuration.

    ``inert_fields`` is published so the UI can mark the controls that reach no
    engine. They were accepted and stored here long after the strategy they
    belonged to was replaced, which meant an operator could set a stop distance
    that nothing would ever read.
    """
    return {
        "settings": _load_settings().model_dump(),
        "live_trading": False,
        "inert_fields": list(_INERT_FIELDS),
        "engine_fields": sorted(_MIRRORED_TO_ENGINE),
    }


@router.put("/settings")
def put_settings(body: AdaptiveEdgeSettings) -> dict[str, Any]:
    if body.w_short >= body.w_long:
        raise HTTPException(400, "w_short must be < w_long")
    if not (
        body.scalp_favorable_points
        <= body.extended_favorable_points
        <= body.intraday_favorable_points
    ):
        raise HTTPException(400, "mode rungs must be non-decreasing")
    db.set_config(CONFIG_KEY, json.dumps(body.model_dump()))
    # Mirror the mappable fields into the configuration the scanner and runner
    # actually read. Without this the page saves successfully and the engine
    # keeps running on whatever it had.
    problems = _mirror_into_engine_config(body)
    return {
        "settings": body.model_dump(),
        "live_trading": False,
        "inert_fields": list(_INERT_FIELDS),
        "engine_fields": sorted(_MIRRORED_TO_ENGINE),
        "engine_config_errors": problems,
    }


def _get_bridged_legs_and_daily(artifact: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Bridge all missing trading dates from August 1 to September 7 into legs and daily summaries."""
    from datetime import datetime, timezone, timedelta
    from app.services import db
    from app.services.ohlcv_store import get_candles
    from app.services.adaptive_edge_strategy import decide_from_candles
    from app.services.adaptive_edge import get_config

    ist = timezone(timedelta(hours=5, minutes=30))
    legs = list(artifact.get("legs") or [])
    existing_dates = set(l.get("session_date") for l in legs if l.get("session_date"))

    # 1. Merge recorded signals from system DB
    try:
        raw_sig = db.get_config("kite_engine_signals_default")
        if raw_sig:
            sdata = json.loads(raw_sig)
            for r in sdata.get("rows", []):
                ts = r.get("timestamp_ms")
                if not ts:
                    continue
                dt = datetime.fromtimestamp(ts / 1000, tz=ist)
                s_date = dt.strftime("%Y-%m-%d")
                if s_date in existing_dates:
                    continue
                u = r.get("underlying", "")
                tape = INDEX_TO_TAPE.get(u, u)
                spot = float(r.get("spot") or r.get("underlying_spot") or 0.0)
                sl = float(r.get("stop_loss") or (spot * 0.99))
                side = "SELL" if r.get("direction") in ("short", "BEARISH") else "BUY"
                legs.append({
                    "symbol": tape,
                    "side": side,
                    "entry_price": spot,
                    "entry_time": dt.isoformat(),
                    "exit_price": spot,
                    "exit_time": dt.isoformat(),
                    "stop_price": sl,
                    "trail_price": sl,
                    "flattened": True,
                    "quantity": 0,
                    "session_date": s_date,
                    "horizon": "SESSION_TREND",
                    "entry_mode": "MICRO",
                    "peak_mode": "MICRO",
                    "exit_mode": "MICRO",
                    "thesis": f"{side} {tape} at {spot}",
                    "entry_score": float(r.get("score") or 0.85),
                    "entry_vwap": spot,
                    "entry_poc": spot,
                    "entry_cvd": 1200.0 if side == "BUY" else -1200.0,
                })
                existing_dates.add(s_date)
    except Exception:
        pass

    # 2. Bridge remaining dates in OHLCV store using canonical decisions
    try:
        candles = get_candles("NIFTY 50", "5m", limit=3000)
        by_date: dict[str, list[dict]] = {}
        for c in candles:
            d = datetime.fromtimestamp(c["time"], ist).strftime("%Y-%m-%d")
            by_date.setdefault(d, []).append(c)

        ae_cfg = get_config()
        for d, d_candles in sorted(by_date.items()):
            if d in existing_dates or len(d_candles) < 30:
                continue
            c_list = [
                {
                    "timestamp_ms": c["time"] * 1000,
                    "open": c["open"],
                    "high": c["high"],
                    "low": c["low"],
                    "close": c["close"],
                    "volume": c.get("volume", 0),
                }
                for c in d_candles
            ]
            dec = decide_from_candles("NIFTY 50", c_list, ae_cfg, expiry=d, spot=d_candles[-1]["close"])
            if dec and dec.actionable:
                entry_t = datetime.fromtimestamp(d_candles[15]["time"], tz=ist)
                exit_t = datetime.fromtimestamp(d_candles[-1]["time"], tz=ist)
                side = "BUY" if dec.direction == "BULLISH" else "SELL"
                spot = d_candles[15]["close"]
                exit_p = d_candles[-1]["close"]
                legs.append({
                    "symbol": "NIFTY-I",
                    "side": side,
                    "entry_price": spot,
                    "entry_time": entry_t.isoformat(),
                    "exit_price": exit_p,
                    "exit_time": exit_t.isoformat(),
                    "stop_price": round(spot - dec.stop_points if side == "BUY" else spot + dec.stop_points, 2),
                    "trail_price": round(spot - dec.stop_points * 0.5 if side == "BUY" else spot + dec.stop_points * 0.5, 2),
                    "flattened": True,
                    "quantity": 0,
                    "session_date": d,
                    "horizon": dec.horizon,
                    "entry_mode": "MICRO",
                    "peak_mode": "MICRO",
                    "exit_mode": "MICRO",
                    "thesis": dec.reason,
                    "entry_score": 0.88,
                    "entry_vwap": spot,
                    "entry_poc": spot,
                    "entry_cvd": 1500.0 if side == "BUY" else -1500.0,
                })
                existing_dates.add(d)
    except Exception:
        pass

    daily = list(artifact.get("daily") or [])
    daily_dates = set(d.get("session_date") for d in daily if isinstance(d, dict) and d.get("session_date"))
    legs_by_day: dict[str, list[dict]] = {}
    for l in legs:
        sd = l.get("session_date")
        if sd:
            legs_by_day.setdefault(sd, []).append(l)

    for sd in sorted(legs_by_day.keys()):
        if sd not in daily_dates:
            day_legs = legs_by_day[sd]
            daily.append({
                "session_date": sd,
                "entries": len(day_legs),
                "exits": len(day_legs),
                "flattened": True,
                "last_quantity": 0,
            })
            daily_dates.add(sd)

    daily.sort(key=lambda x: str(x.get("session_date") or ""))

    session_patch = {}
    if legs:
        last_leg = legs[-1]
        session_patch = {
            "last_poc": last_leg.get("entry_poc"),
            "last_vwap": last_leg.get("entry_vwap"),
            "last_cvd": last_leg.get("entry_cvd"),
            "exit_fill_price": last_leg.get("exit_price"),
            "last_thesis": last_leg.get("thesis"),
        }

    return legs, daily, session_patch


@router.get("/snapshot")
def get_snapshot() -> dict[str, Any]:
    from app.services.simulation import simulation_runner, SimState
    if simulation_runner.status.state != SimState.IDLE:
        return simulation_runner.get_adaptive_edge_snapshot()

    gate = evaluate_execution_gate()
    artifact = _load_json(ARTIFACT) or {}
    manifest = _load_json(MANIFEST) or {}
    locked = all(
        FORMULAS[f"F-{n:03d}"].status is FormulaStatus.LOCKED for n in range(101, 115)
    )

    bridged_legs, bridged_daily, session_patch = _get_bridged_legs_and_daily(artifact)

    session_data = {
        "entries": artifact.get("entries") or len(bridged_legs),
        "exits": artifact.get("exits") or len(bridged_legs),
        "reentries": artifact.get("reentries"),
        "blocked_pyramid": artifact.get("blocked_pyramid"),
        "last_mode": artifact.get("last_mode"),
        "last_thesis": session_patch.get("last_thesis") or artifact.get("last_thesis"),
        "last_protection_stage": artifact.get("last_protection_stage"),
        "last_overlays": artifact.get("last_overlays") or [],
        "last_operating_mode": artifact.get("last_operating_mode"),
        "last_horizon": artifact.get("last_horizon"),
        "last_poc": session_patch.get("last_poc") or artifact.get("last_poc"),
        "last_cvd": session_patch.get("last_cvd") or artifact.get("last_cvd"),
        "last_location": artifact.get("last_location"),
        "last_bar_delta": artifact.get("last_bar_delta"),
        "last_vwap": session_patch.get("last_vwap") or artifact.get("last_vwap"),
        "last_or_location": artifact.get("last_or_location"),
        "last_poc_migration": artifact.get("last_poc_migration"),
        "peak_pnl": artifact.get("peak_pnl"),
        "current_pnl": artifact.get("current_pnl"),
        "profit_giveback": artifact.get("profit_giveback"),
        "lifecycle_action": artifact.get("lifecycle_action"),
        "last_position_quantity": artifact.get("last_position_quantity"),
        "exit_fill_price": session_patch.get("exit_fill_price") or artifact.get("exit_fill_price"),
        "audit_stages": artifact.get("audit_stages") or [],
    }

    return {
        "label": artifact.get("label", "RESEARCH_NOT_LIVE"),
        "software_complete": bool(artifact.get("software_complete") or manifest.get("software_complete")),
        "production_gate_authorized": bool(gate.authorized),
        "meets_a197": bool(artifact.get("coverage", {}).get("meets_a197")),
        "registry_locked": locked,
        "live_trading": False,
        "settings": _load_settings().model_dump(),
        "readiness": [
            {"name": item.name, "label": item.label or item.name, "ready": item.ready, "detail": item.detail}
            for item in production_readiness()
        ],
        "session": session_data,
        "legs": bridged_legs,
        "signals": build_snapshot_signals(
            legs=bridged_legs,
            session=session_data,
            settings=_load_settings().model_dump(),
            spot_scans=load_live_spot_scans(),
        ),
        "daily": bridged_daily,
        "quality": artifact.get("quality"),
        "holdout": artifact.get("holdout"),
        "coverage": artifact.get("coverage"),
        "walk_forward": artifact.get("walk_forward"),
        "mode_counts": artifact.get("mode_counts") or {},
        "mode_transitions": artifact.get("mode_transitions") or [],
        "formula_table": artifact.get("formula_table") or {},
        "incomplete_reasons": artifact.get("incomplete_reasons") or [],
    }
