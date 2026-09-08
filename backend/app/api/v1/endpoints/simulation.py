"""
Market Replay Simulation endpoints.
"""
import asyncio
import json

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional, List, Literal
from app.services.simulation import simulation_runner, SimConfig, SimState, SimStatus

router = APIRouter(prefix="/simulation", tags=["simulation"])


class SpeedBody(BaseModel):
    speed: float


class AvailableDatesResponse(BaseModel):
    dates: List[str]
    instrument: str
    resolution: str
    # Whether these dates came from the candle store or were synthesised. The
    # endpoint invents 90 business days when the store is empty, and a client
    # that presents those as "sessions with data" is lying on its behalf.
    source: Literal["store", "fallback"]
    earliest: Optional[str] = None
    latest: Optional[str] = None
    # The store walk skips weekends but NOT exchange holidays, so a date in this
    # list is "plausibly a session", not "confirmed to have candles".
    holidays_filtered: bool = False


@router.post("/start", response_model=SimStatus)
async def start_sim(config: SimConfig, force: bool = Query(False)):
    if config.end_date and config.end_date < config.date:
        raise HTTPException(
            400,
            detail={
                "code": "invalid_date_range",
                "message": f"Start date ({config.date}) must precede or equal end date ({config.end_date}).",
            },
        )
    # Starting over a live replay used to happen silently, so the client could
    # not tell "your replay restarted" from "your replay was already running".
    if simulation_runner.status.state != SimState.IDLE and not force:
        raise HTTPException(
            409,
            detail={
                "code": "already_running",
                "message": "A replay is already running. Stop it, or start with force=true to restart.",
            },
        )
    return await simulation_runner.start(config)


@router.post("/stop", response_model=SimStatus)
async def stop_sim():
    return await simulation_runner.stop()


@router.post("/clear", response_model=SimStatus)
async def clear_sim():
    """Discard a finished session's signals and trades."""
    if simulation_runner.status.state != SimState.IDLE:
        raise HTTPException(
            400,
            detail={"code": "not_idle", "message": "Stop the replay before clearing it."},
        )
    return simulation_runner.clear()


@router.post("/pause", response_model=SimStatus)
async def pause_sim():
    if simulation_runner.status.state != SimState.RUNNING:
        raise HTTPException(400, detail={"code": "not_running", "message": "Replay is not running."})
    return await simulation_runner.pause()


@router.post("/resume", response_model=SimStatus)
async def resume_sim():
    if simulation_runner.status.state != SimState.PAUSED:
        raise HTTPException(400, detail={"code": "not_paused", "message": "Replay is not paused."})
    return await simulation_runner.resume()


class SeekBody(BaseModel):
    bars_offset: Optional[int] = None
    bar_index: Optional[int] = None      # absolute bar
    to_pct: Optional[float] = None       # 0..100 through the session
    to_time: Optional[str] = None        # "HH:MM:SS" IST or ISO datetime
    target_epoch: Optional[float] = None # epoch timestamp in seconds
    action: Optional[str] = None         # "jump_start", "jump_end", "step"


@router.post("/speed", response_model=SimStatus)
async def set_speed(body: SpeedBody):
    return simulation_runner.set_speed(body.speed)


@router.post("/seek", response_model=SimStatus)
async def seek_sim(body: SeekBody):
    if simulation_runner.status.state == SimState.IDLE:
        raise HTTPException(400, detail={"code": "not_running", "message": "Replay is not running."})
    # Precedence: named action, then the absolute forms, then the relative one.
    if body.action == "jump_start":
        return simulation_runner.jump_start()
    if body.action == "jump_end":
        return simulation_runner.jump_end()
    if (
        body.bar_index is not None
        or body.to_pct is not None
        or body.to_time is not None
        or body.target_epoch is not None
    ):
        return simulation_runner.seek_to(
            bar_index=body.bar_index,
            to_pct=body.to_pct,
            to_time=body.to_time,
            target_epoch=body.target_epoch,
        )
    if body.bars_offset is not None:
        return simulation_runner.step_bars(body.bars_offset)
    return simulation_runner.status


@router.get("/status", response_model=SimStatus)
async def get_status(
    since_events: Optional[int] = Query(None, ge=0),
    since_trades: Optional[int] = Query(None, ge=0),
):
    # Omitting both offsets returns the full payload, exactly as before.
    return simulation_runner.status_since(since_events, since_trades)


@router.get("/stream")
async def stream_sim(request: Request):
    """Server-sent replay events.

    Replaces polling `/status` at 150ms for the whole ledger. The payload per
    tick is now O(1) scalars, and signals and trades arrive once each rather
    than being re-sent on every poll.

    `/status` remains fully supported: a reverse proxy that buffers SSE will
    make this endpoint useless, and the client falls back to it.
    """
    async def gen():
        try:
            async for evt in simulation_runner.subscribe():
                if await request.is_disconnected():
                    break
                yield f"event: {evt.kind}\ndata: {json.dumps(evt.data, default=str)}\n\n"
        except asyncio.CancelledError:
            return

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # nginx buffers by default, which turns a stream into one long wait.
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/available-dates", response_model=AvailableDatesResponse)
async def available_dates(instrument: str = "NIFTY", resolution: str = "5m"):
    # Both the store read and the day walk run in a worker thread. They used to
    # run inline on the event loop, and the store read was a GROUP BY across all
    # ~20M candle rows (~7s). For those 7s the loop served nothing: POST /start
    # from the replay dock's play button sat in the queue, so pressing play
    # looked like nothing happened at all.
    return await run_in_threadpool(_available_dates_sync, instrument, resolution)


def _available_dates_sync(instrument: str, resolution: str) -> AvailableDatesResponse:
    from app.services.ohlcv_store import get_symbol_coverage
    from datetime import datetime, timezone, timedelta

    dates = set()
    earliest_iso: Optional[str] = None
    latest_iso: Optional[str] = None

    # One indexed lookup for the series actually asked about, instead of
    # summarising every symbol in the store and then discarding all but one.
    try:
        from zoneinfo import ZoneInfo
        ist_tz = ZoneInfo("Asia/Kolkata")
    except ImportError:
        ist_tz = timezone(timedelta(hours=5, minutes=30))

    entry = get_symbol_coverage(instrument.upper(), resolution)
    if not entry and resolution != "5m":
        # Check if 5m coverage exists in local store as source of truth for available market dates
        entry = get_symbol_coverage(instrument.upper(), "5m")

    if entry and entry["earliest"] and entry["latest"]:
        current = datetime.fromtimestamp(entry["earliest"], tz=ist_tz)
        end = datetime.fromtimestamp(entry["latest"], tz=ist_tz)
        earliest_iso = current.strftime("%Y-%m-%d")
        latest_iso = end.strftime("%Y-%m-%d")
        while current <= end:
            if current.weekday() < 5:
                dates.add(current.strftime("%Y-%m-%d"))
            current += timedelta(days=1)

    source: Literal["store", "fallback"] = "store"

    # Fallback to the last 90 business days when the store holds nothing. These
    # dates are INVENTED — `source` is how the client knows not to present them
    # as evidence that candles exist.
    if not dates:
        source = "fallback"
        today = datetime.now(tz=ist_tz)
        curr = today - timedelta(days=90)
        while curr <= today:
            if curr.weekday() < 5:
                dates.add(curr.strftime("%Y-%m-%d"))
            curr += timedelta(days=1)

    ordered = sorted(dates)[-90:]
    return AvailableDatesResponse(
        dates=ordered,
        instrument=instrument.upper(),
        resolution=resolution,
        source=source,
        earliest=earliest_iso or (ordered[0] if ordered else None),
        latest=latest_iso or (ordered[-1] if ordered else None),
        holidays_filtered=False,
    )
