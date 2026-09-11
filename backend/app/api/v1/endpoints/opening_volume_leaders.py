"""Scan, decision, replay comparison, and guarded execution API."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.auth import UserContext, get_current_user
from app.engines.option_contracts import Bar
from app.engines.opening_volume_decision import OpeningDecisionConfig, WEIGHTS
from app.engines.opening_volume_leaders import (
    STRATEGY_CONTRACT,
    OpeningVolumeConfig,
    evaluate_leader,
)
from app.engines.opening_volume_parity import compare_opening_sessions
from app.services.opening_volume_leaders import LiveLeaderScanConfig

router = APIRouter(prefix="/opening-volume-leaders", tags=["opening-volume-leaders"])


class OpeningVolumeBarRequest(BaseModel):
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0


class OpeningVolumeEvaluateRequest(BaseModel):
    symbol: str
    as_of: datetime
    bars: list[OpeningVolumeBarRequest] = Field(min_length=1, max_length=25_000)
    average_turnover_inr: float | None = None
    config: dict = Field(default_factory=dict)


class OpeningVolumeLiveScanRequest(BaseModel):
    symbols: list[str] = Field(default_factory=list)
    scan_all_stocks: bool = True
    include_watch: bool = False
    include_weak: bool = False
    max_candidates: int = 250
    concurrency: int = 3
    history_calendar_days: int = 45
    as_of: datetime | None = None
    config: dict = Field(default_factory=dict)
    sector_by_symbol: dict[str, str] = Field(default_factory=dict)


class OpeningVolumeCompareRequest(BaseModel):
    orion_rows: list[dict] = Field(max_length=10_000)
    sterling_rows: list[dict] = Field(max_length=10_000)
    rvol_tolerance: float = Field(default=0.05, ge=0, le=10)


class OpeningExecutionConfigRequest(BaseModel):
    enabled: bool | None = None
    min_score: float | None = None
    min_conviction: int | None = None
    max_trades_per_day: int | None = None
    risk_pct: float | None = None
    max_lots: int | None = None
    max_quote_staleness_s: float | None = None
    max_spread_pct: float | None = None
    max_underlying_drift_pct: float | None = None
    min_dte: int | None = None


@router.get("/contract")
async def contract() -> dict:
    return {
        "strategy": STRATEGY_CONTRACT,
        "defaults": OpeningVolumeConfig().__dict__,
        "decision_defaults": OpeningDecisionConfig().__dict__,
        "decision_weights": WEIGHTS,
        "live_scan_defaults": LiveLeaderScanConfig().__dict__,
        "live_universe": (
            "current Kite NFO CE/PE underlyings intersected with current NSE cash equities"
        ),
        "tier_score": "Sterling transparent bounded score; not ORION proprietary weights",
        "parity": {
            "evidence_backed": [
                "opening RVOL tiers and direction",
                "first ORB event and freshness",
                "breadth mood and participation",
                "liquidity gate and time windows",
                "chase, stop-distance, repeat-day and risk warnings",
                "live nearest-strike option presentation",
                "causal replay without live-quote leakage",
            ],
            "transparent_local": [
                "candle-quality thresholds",
                "Sterling COMBO formula",
                "bounded 0-100 score with evidence coverage",
                "seven-factor conviction thresholds",
                "Momentum Box X/Y replacement predicates",
                "ORB-boundary follow-through reference",
                "one-minute RSI evidence",
            ],
            "insufficient_evidence": [
                "exact ORION proprietary score weights",
                "exact ORION private COMBO predicate",
                "exact ORION Momentum Lab server-side predicates",
                "exact ORION unpublished conviction thresholds",
            ],
        },
    }


@router.post("/compare")
async def compare(body: OpeningVolumeCompareRequest) -> dict:
    """Compare observable ORION and Sterling fields across multiple sessions."""

    try:
        return compare_opening_sessions(
            body.orion_rows,
            body.sterling_rows,
            rvol_tolerance=body.rvol_tolerance,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/execution-config")
async def execution_config(
    user: Annotated[UserContext, Depends(get_current_user)],
) -> dict:
    from app.services.opening_volume_execution import get_config

    return {"config": get_config(str(user.user_id)).__dict__}


@router.put("/execution-config")
async def update_execution_config(
    body: OpeningExecutionConfigRequest,
    user: Annotated[UserContext, Depends(get_current_user)],
) -> dict:
    from app.services.opening_volume_execution import set_config

    try:
        values = {
            key: value
            for key, value in body.model_dump().items()
            if value is not None
        }
        return {"config": set_config(str(user.user_id), values).__dict__}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/evaluate")
async def evaluate(body: OpeningVolumeEvaluateRequest) -> dict:
    """Deterministically evaluate supplied one-minute OHLCV without broker I/O."""

    try:
        config = OpeningVolumeConfig(**body.config).validate()
        bars = [
            Bar(
                timestamp=row.timestamp,
                open=row.open,
                high=row.high,
                low=row.low,
                close=row.close,
                volume=row.volume,
            )
            for row in body.bars
        ]
        return {
            "strategy": STRATEGY_CONTRACT,
            "signal": evaluate_leader(
                body.symbol,
                bars,
                as_of=body.as_of,
                config=config,
                average_turnover_inr=body.average_turnover_inr,
            ).to_dict(),
        }
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/scan")
async def scan(
    body: OpeningVolumeLiveScanRequest,
    user: Annotated[UserContext, Depends(get_current_user)],
) -> dict:
    """Scan the authenticated user's Kite universe; never submit an order."""

    try:
        scan_config = LiveLeaderScanConfig(
            symbols=tuple(body.symbols),
            scan_all_stocks=body.scan_all_stocks,
            include_watch=body.include_watch,
            include_weak=body.include_weak,
            max_candidates=body.max_candidates,
            concurrency=body.concurrency,
            history_calendar_days=body.history_calendar_days,
            sector_by_symbol=body.sector_by_symbol,
        ).validate()
        signal_config = OpeningVolumeConfig(**body.config).validate()
        from app.services.opening_volume_leaders import scan_kite_leaders

        return await scan_kite_leaders(
            str(user.user_id),
            as_of=body.as_of,
            scan_config=scan_config,
            signal_config=signal_config,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/execute")
async def execute(
    body: OpeningVolumeLiveScanRequest,
    user: Annotated[UserContext, Depends(get_current_user)],
) -> dict:
    """Run a fresh live scan and execute only fail-closed Sterling candidates."""

    if body.as_of is not None:
        raise HTTPException(status_code=422, detail="replay scans cannot execute")
    uid = str(user.user_id)
    try:
        scan_config = LiveLeaderScanConfig(
            symbols=tuple(body.symbols),
            scan_all_stocks=body.scan_all_stocks,
            include_watch=False,
            include_weak=False,
            max_candidates=body.max_candidates,
            concurrency=body.concurrency,
            history_calendar_days=body.history_calendar_days,
            sector_by_symbol=body.sector_by_symbol,
        ).validate()
        signal_config = OpeningVolumeConfig(**body.config).validate()
        from app.services.opening_volume_execution import execute_opening_scan
        from app.services.opening_volume_leaders import scan_kite_leaders

        fresh_scan = await scan_kite_leaders(
            uid,
            scan_config=scan_config,
            signal_config=signal_config,
        )
        return {
            "scan": fresh_scan,
            "execution": await execute_opening_scan(uid, scan=fresh_scan),
        }
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.on_event("startup")
async def _start_opening_volume_runner() -> None:
    from app.services.opening_volume_runner import start

    start()


@router.on_event("shutdown")
async def _stop_opening_volume_runner() -> None:
    from app.services.opening_volume_runner import stop

    stop()
