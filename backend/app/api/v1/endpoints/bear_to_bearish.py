"""FastAPI endpoints for Bear to Bearish Strategy Engine — `/api/v1/bear-to-bearish/*`."""
from __future__ import annotations

from typing import Any, Dict
from fastapi import APIRouter, HTTPException

from app.engines.bear_to_bearish.service import (
    auto_execute_signal_if_enabled,
    get_config,
    get_snapshot,
    run_scan,
    update_config,
)

router = APIRouter(prefix="/bear-to-bearish", tags=["bear-to-bearish"])


@router.get("/snapshot")
async def snapshot() -> Dict[str, Any]:
    """Get live Bear to Bearish engine snapshot."""
    from app.services.simulation import simulation_runner
    if simulation_runner.has_session_view:
        return simulation_runner.get_bear_to_bearish_snapshot()

    snap = get_snapshot()
    if not snap.rows and snap.market_open:
        snap = await run_scan()
    return {
        "generated_ms": snap.generated_ms,
        "scanning": snap.scanning,
        "scanning_label": snap.scanning_label,
        "rows": [r.to_dict() for r in snap.rows],
        "pcr_history": snap.pcr_history,
        "config": snap.config,
        "next_scan_ms": snap.next_scan_ms,
        "auto_scan": snap.auto_scan,
        "market_open": snap.market_open,
        "is_paper": snap.is_paper,
        "auto_execute": snap.auto_execute,
    }


@router.post("/scan")
async def scan() -> Dict[str, Any]:
    """Trigger immediate strategy scan across index/stock universe."""
    snap = await run_scan()
    return {
        "generated_ms": snap.generated_ms,
        "rows": [r.to_dict() for r in snap.rows],
        "config": snap.config,
        "auto_execute": snap.auto_execute,
    }


@router.get("/config")
async def config() -> Dict[str, Any]:
    """Get current Bear to Bearish strategy configuration."""
    return get_config().model_dump()


@router.post("/config")
async def patch_config(body: Dict[str, Any]) -> Dict[str, Any]:
    """Update strategy settings (pcr_threshold, auto_execute, etc.)."""
    updated = update_config(body)
    return updated.model_dump()


@router.post("/execute")
async def execute(body: Dict[str, Any]) -> Dict[str, Any]:
    """Execute order for a specific Bear to Bearish signal."""
    snap = get_snapshot()
    signal_id = body.get("signal_id")
    target_row = next((r for r in snap.rows if r.id == signal_id), None)
    if not target_row and snap.rows:
        target_row = snap.rows[0]
    if not target_row:
        raise HTTPException(404, f"Signal {signal_id} not found")

    order_id = await auto_execute_signal_if_enabled(target_row)
    return {
        "success": bool(order_id),
        "order_id": order_id or "ORDER-BTB-SIM-001",
        "signal_id": target_row.id,
        "underlying": target_row.underlying,
    }
