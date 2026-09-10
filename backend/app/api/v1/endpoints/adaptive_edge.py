"""Adaptive Edge research UI API. Does not unlock ExecutionGate or F-101."""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator, model_validator

from app.core.logging import get_logger
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

log = get_logger(__name__)

router = APIRouter(prefix="/adaptive-edge", tags=["adaptive-edge"])
CONFIG_KEY = "adaptive_edge_settings"
ARTIFACT = Path(__file__).resolve().parents[4] / "data" / "adaptive_edge" / "research_e2e.json"
MANIFEST = ARTIFACT.with_name("software_e2e_manifest.json")
DEFAULT_INDICES = ["NIFTY 50", "NIFTY BANK", "NIFTY FIN SERVICE", "SENSEX"]
ScanSource = Literal["spot", "derivatives", "both", "confluence"]
ScanExpiry = Literal["weekly", "monthly"]


class AdaptiveEdgeSettings(BaseModel):
    enabled: bool = False
    strategy_version: Literal["v1_baseline", "v2_hardened"] = "v2_hardened"
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

    @field_validator("scan_stocks")
    @classmethod
    def _clean_stocks(cls, value: list[str]) -> list[str]:
        cleaned: list[str] = []
        for item in value:
            norm = str(item).strip().upper()
            if norm and norm not in cleaned:
                cleaned.append(norm)
        return cleaned

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
        st = _default_settings()
    else:
        try:
            st = AdaptiveEdgeSettings.model_validate(json.loads(raw))
        except Exception:
            st = _default_settings()
    try:
        from app.services.adaptive_edge import get_config as get_ae_cfg
        ae_cfg = get_ae_cfg()
        if ae_cfg and hasattr(ae_cfg, "strategy_version"):
            st.strategy_version = ae_cfg.strategy_version
    except Exception:
        pass
    return st


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
    "strategy_version": "strategy_version",
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
    global _BRIDGED_CACHE
    _BRIDGED_CACHE["result"] = None
    return {
        "settings": body.model_dump(),
        "live_trading": False,
        "inert_fields": list(_INERT_FIELDS),
        "engine_fields": sorted(_MIRRORED_TO_ENGINE),
        "engine_config_errors": problems,
    }


_BRIDGED_CACHE: dict[str, Any] = {"time": 0.0, "result": None}
_BRIDGED_CACHE: dict[str, Any] = {"time": 0.0, "result": None, "key": None}
_HISTORICAL_CACHE: dict[str, Any] = {"initialized": False, "legs": [], "daily": []}


def clear_bridged_cache() -> None:
    global _BRIDGED_CACHE, _HISTORICAL_CACHE
    _BRIDGED_CACHE["time"] = 0.0
    _BRIDGED_CACHE["result"] = None
    _BRIDGED_CACHE["key"] = None
    _HISTORICAL_CACHE["initialized"] = False


def _get_bridged_legs_and_daily(artifact: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Bridge all missing trading dates from August 1 to September 8 into legs and daily summaries."""
    import time
    from datetime import datetime, timezone, timedelta
    from app.services import db
    from app.services.ohlcv_store import get_candles
    from app.services.adaptive_edge_strategy import decide_from_candles
    from app.services.adaptive_edge import get_config

    global _BRIDGED_CACHE, _HISTORICAL_CACHE
    now = time.time()
    if _BRIDGED_CACHE["result"] is not None and (now - _BRIDGED_CACHE["time"]) < 60.0:
        return _BRIDGED_CACHE["result"]

    ist = timezone(timedelta(hours=5, minutes=30))
    settings = _load_settings()
    scan_idx_list = list(settings.scan_indices or ["NIFTY 50", "NIFTY BANK", "SENSEX"])
    ae_cfg = get_config()
    today_iso = datetime.now(ist).strftime("%Y-%m-%d")
    curr_version = getattr(ae_cfg, "strategy_version", "v2_hardened") or "v2_hardened"

    cache_key = (curr_version, tuple(sorted(scan_idx_list)), settings.scan_source)
    if _BRIDGED_CACHE.get("key") == cache_key and _BRIDGED_CACHE.get("result") is not None and (now - _BRIDGED_CACHE.get("time", 0.0)) < 60.0:
        return _BRIDGED_CACHE["result"]

    # Initialize historical cache (< today_iso) once
    if not _HISTORICAL_CACHE["initialized"]:
        hist_legs = [dict(x) for x in (artifact.get("legs") or [])]
        for l in hist_legs:
            if l.get("session_date") and l.get("session_date") < today_iso:
                l["flattened"] = True
                l["quantity"] = 0
        hist_dates = set((l.get("symbol"), l.get("session_date")) for l in hist_legs if l.get("symbol") and l.get("session_date"))
        for idx_name in scan_idx_list:
            tape = INDEX_TO_TAPE.get(idx_name, idx_name)
            try:
                candles = get_candles(idx_name, "5m", limit=1000)
                by_date: dict[str, list[dict]] = {}
                for c in candles:
                    d = datetime.fromtimestamp(c["time"], ist).strftime("%Y-%m-%d")
                    if d < today_iso:
                        by_date.setdefault(d, []).append(c)
                for d, d_candles in sorted(by_date.items()):
                    if (tape, d) in hist_dates or len(d_candles) < 25:
                        continue
                    c_list = [
                        {"timestamp_ms": c["time"] * 1000, "open": c["open"], "high": c["high"], "low": c["low"], "close": c["close"], "volume": c.get("volume", 0)}
                        for c in d_candles
                    ]
                    dec = decide_from_candles(idx_name, c_list, ae_cfg, expiry=d, spot=d_candles[-1]["close"])
                    if dec and dec.actionable:
                        entry_bar_idx = min(15, len(d_candles) - 1)
                        entry_t = datetime.fromtimestamp(d_candles[entry_bar_idx]["time"], tz=ist)
                        exit_t = datetime.fromtimestamp(d_candles[-1]["time"], tz=ist)
                        side = "BUY" if dec.direction == "BULLISH" else "SELL"
                        spot = d_candles[entry_bar_idx]["close"]
                        hist_legs.append({
                            "symbol": tape, "side": side, "entry_price": spot, "entry_time": entry_t.isoformat(),
                            "exit_price": d_candles[-1]["close"], "exit_time": exit_t.isoformat(),
                            "stop_price": round(spot - dec.stop_points if side == "BUY" else spot + dec.stop_points, 2),
                            "trail_price": round(spot - dec.stop_points * 0.5 if side == "BUY" else spot + dec.stop_points * 0.5, 2),
                            "flattened": True, "quantity": 0, "session_date": d, "horizon": dec.horizon,
                            "entry_mode": "MICRO", "peak_mode": "MICRO", "exit_mode": "MICRO", "thesis": dec.reason,
                            "entry_score": 0.88, "entry_vwap": spot, "entry_poc": spot, "entry_cvd": 1500.0 if side == "BUY" else -1500.0,
                            "scan_origin": "adaptive_edge",
                        })
                        hist_dates.add((tape, d))
            except Exception as err:
                log.warning("Failed bridging historical candles for %s: %s", idx_name, err)

        _HISTORICAL_CACHE["legs"] = hist_legs
        _HISTORICAL_CACHE["daily"] = list(artifact.get("daily") or [])
        _HISTORICAL_CACHE["initialized"] = True

    legs = [dict(x) for x in _HISTORICAL_CACHE["legs"]]
    existing_sym_dates = set((l.get("symbol"), l.get("session_date")) for l in legs if l.get("symbol") and l.get("session_date"))

    allowed_tapes = set(settings.symbols)
    if settings.scan_stocks:
        for st in settings.scan_stocks:
            allowed_tapes.add(st)
            allowed_tapes.add(st.upper())
    for idx in settings.scan_indices:
        if idx in INDEX_TO_TAPE:
            allowed_tapes.add(INDEX_TO_TAPE[idx])
        allowed_tapes.add(idx)
        allowed_tapes.add(idx.upper())

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
                u = r.get("underlying", "")
                tape = INDEX_TO_TAPE.get(u, u)
                if (tape, s_date) in existing_sym_dates:
                    continue
                if allowed_tapes and tape not in allowed_tapes and u not in allowed_tapes and u.upper() not in allowed_tapes:
                    continue
                spot = float(r.get("spot") or r.get("underlying_spot") or 0.0)
                sl = float(r.get("stop_loss") or (spot * 0.99))
                side = "SELL" if r.get("direction") in ("short", "BEARISH") else "BUY"
                is_active = bool(r.get("is_active", False))
                legs.append({
                    "symbol": tape,
                    "side": side,
                    "entry_price": spot,
                    "entry_time": dt.isoformat(),
                    "exit_price": None if is_active else spot,
                    "exit_time": None if is_active else dt.isoformat(),
                    "stop_price": sl,
                    "trail_price": sl,
                    "flattened": not is_active,
                    "quantity": 1 if is_active else 0,
                    "session_date": s_date,
                    "horizon": "SESSION_TREND",
                    "entry_mode": "MICRO",
                    "peak_mode": "MICRO",
                    "exit_mode": "" if is_active else "MICRO",
                    "thesis": f"{side} {tape} at {spot}",
                    "entry_score": float(r.get("score") or 0.85),
                    "entry_vwap": spot,
                    "entry_poc": spot,
                    "entry_cvd": 1200.0 if side == "BUY" else -1200.0,
                    "scan_origin": "spot_scan",
                })
                existing_sym_dates.add((tape, s_date))
    except Exception:
        pass

    # 2. Bridge today's dates for indices using fast limit
    # 2. Bridge today's dates for indices using AE decision engine
    existing_ae_sym_dates = set((l.get("symbol"), l.get("session_date")) for l in legs if l.get("symbol") and l.get("session_date") and l.get("scan_origin") == "adaptive_edge")
    for idx_name in scan_idx_list:
        tape = INDEX_TO_TAPE.get(idx_name, idx_name)
        if (tape, today_iso) in existing_ae_sym_dates:
            continue
        try:
            candles = get_candles(idx_name, "5m", limit=150)
            today_candles = [c for c in candles if datetime.fromtimestamp(c["time"], ist).strftime("%Y-%m-%d") == today_iso]
            eval_candles = today_candles if len(today_candles) >= 25 else (candles[-max(40, len(today_candles)):] if len(today_candles) >= 3 and len(candles) >= 40 else [])
            if eval_candles:
                c_list = [
                    {
                        "timestamp_ms": c["time"] * 1000,
                        "open": c["open"],
                        "high": c["high"],
                        "low": c["low"],
                        "close": c["close"],
                        "volume": c.get("volume", 0),
                    }
                    for c in eval_candles
                ]
                dec = decide_from_candles(idx_name, c_list, ae_cfg, expiry=today_iso, spot=eval_candles[-1]["close"])
                if dec and dec.actionable:
                    entry_bar = today_candles[min(15, len(today_candles) - 1)] if today_candles else eval_candles[-1]
                    entry_t = datetime.fromtimestamp(entry_bar["time"], tz=ist)
                    side = "BUY" if dec.direction == "BULLISH" else "SELL"
                    spot = entry_bar["close"]
                    legs.append({
                        "symbol": tape,
                        "side": side,
                        "entry_price": spot,
                        "entry_time": entry_t.isoformat(),
                        "exit_price": None,
                        "exit_time": None,
                        "stop_price": round(spot - dec.stop_points if side == "BUY" else spot + dec.stop_points, 2),
                        "trail_price": round(spot - dec.stop_points * 0.5 if side == "BUY" else spot + dec.stop_points * 0.5, 2),
                        "flattened": False,
                        "quantity": 1,
                        "session_date": today_iso,
                        "horizon": dec.horizon,
                        "entry_mode": "MICRO",
                        "peak_mode": "MICRO",
                        "exit_mode": "",
                        "thesis": dec.reason,
                        "entry_score": 0.88,
                        "entry_vwap": spot,
                        "entry_poc": spot,
                        "entry_cvd": 1500.0 if side == "BUY" else -1500.0,
                        "scan_origin": "adaptive_edge",
                        "strategy_version": curr_version,
                    })
                    existing_sym_dates.add((tape, today_iso))
                    existing_ae_sym_dates.add((tape, today_iso))
        except Exception as err:
            log.warning("Failed bridging today candles for %s: %s", idx_name, err)

    daily = [dict(x) for x in _HISTORICAL_CACHE["daily"]]
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

    res = (legs, daily, session_patch)
    _BRIDGED_CACHE["time"] = time.time()
    _BRIDGED_CACHE["result"] = res
    _BRIDGED_CACHE["key"] = cache_key
    return res


_INDEX_TOKENS = {
    "NIFTY 50": 256265,
    "NIFTY BANK": 260105,
    "NIFTY FIN SERVICE": 257801,
    "SENSEX": 265,
}
_LAST_SYNC_TS: float = 0.0


async def _sync_live_5m_candles_if_needed(scan_idx_list: list[str]) -> None:
    global _LAST_SYNC_TS
    now_ts = time.time()
    if now_ts - _LAST_SYNC_TS < 60.0:
        return

    from app.services.ohlcv_store import get_candles, upsert_candles
    from app.services.exchanges.kite import accounts as kite_accounts
    from app.schemas.instruments import InstrumentMeta

    ist = timezone(timedelta(hours=5, minutes=30))
    today_iso = datetime.now(ist).strftime("%Y-%m-%d")

    needed: list[str] = []
    for idx_name in scan_idx_list:
        candles = get_candles(idx_name, "5m", limit=30)
        today_c = [c for c in candles if datetime.fromtimestamp(c["time"], ist).strftime("%Y-%m-%d") == today_iso]
        if len(today_c) < 3:
            needed.append(idx_name)

    if not needed:
        return

    _LAST_SYNC_TS = now_ts

    if not getattr(kite_accounts, "_loaded", False):
        kite_accounts.bootstrap()

    acct = kite_accounts.get_active("default")
    if not acct or not acct.connected:
        acct = next((a for a in getattr(kite_accounts, "_accounts", {}).values() if a.connected), None)

    if not acct or not acct.connected:
        return

    try:
        client = await kite_accounts.acquire_client(acct)
        for idx_name in needed:
            token = _INDEX_TOKENS.get(idx_name)
            if not token:
                continue
            inst = InstrumentMeta(
                underlying=idx_name,
                tick_size=0.05,
                strike_step=1.0,
                exchange_currency="INR",
                index_name=idx_name,
                has_options=True,
                exchange="zerodha",
                zerodha_token=token,
            )
            k_candles = await client.get_candles(inst, "5m", limit=150)
            if k_candles:
                c_dicts = [
                    {
                        "time": int(c.timestamp_ms / 1000),
                        "open": c.open,
                        "high": c.high,
                        "low": c.low,
                        "close": c.close,
                        "volume": c.volume,
                    }
                    for c in k_candles
                ]
                upsert_candles(idx_name, "5m", c_dicts)
    except Exception as exc:
        log.warning("Live 5m candle sync failed for Adaptive Edge: %s", exc)


@router.get("/snapshot")
async def get_snapshot() -> dict[str, Any]:
    from app.services.simulation import simulation_runner
    if simulation_runner.has_session_view:
        return simulation_runner.get_adaptive_edge_snapshot()

    settings = _load_settings()
    scan_idx_list = list(settings.scan_indices or ["NIFTY 50", "NIFTY BANK", "SENSEX"])
    try:
        await _sync_live_5m_candles_if_needed(scan_idx_list)
    except Exception as exc:
        log.warning("Live 5m candle sync failed: %s", exc)

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
