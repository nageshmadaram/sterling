"""Kite Sterling Kite Engine endpoints — `/api/v1/kite/engine/*`.

Scoped to the calling Kite user. Advisory by default; auto-execute is opt-in via
config and runs through the same Kite order path + live-safety gate as manual
orders. Imports no other engine's strategy/signal/derivative logic.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException

from app.core.auth import UserContext, get_current_user
from app.core.logging import get_logger
from app.engines.sterling_kite_engine.config import SterlingKiteEngineConfig
from app.engines.sterling_kite_engine.schemas import (
    ActivityResponse, BacktestRequest, BacktestResponse,
    ActivityResponse, BacktestRequest, BacktestResponse, EmergencyActionResponse,
    EngineConfigModel, EngineDetailResponse, EngineOrderRequest, EngineOrderResponse,
    EngineSignalRow, OpenPositionRecord, OpenPositionsResponse, SetupChart, SignalsResponse,
    EngineSignalRow, OpenPositionRecord, OpenPositionsResponse, ReadinessResponse, SetupChart, SignalsResponse,
)
from app.services.exchanges.kite import accounts as kite_accounts
from app.services.exchanges.kite.errors import KiteError
from app.services.kite_engine import positions as kite_positions, service, state
from app.services.kite_engine.detail import build_detail
from app.services.kite_engine.expiry_calendar import build_expiry_calendar
from app.services.kite_engine.market_hours import is_market_open
from app.services.kite_engine.scanner import build_setup_chart, scanner
from app.services.kite_engine.stock_registry import CURATED_STOCK_NAMES
from app.services.kite_engine.universe import load_cfg as load_universe_config

log = get_logger(__name__)
router = APIRouter(prefix="/kite/engine", tags=["kite-engine"])
_IST = timezone(timedelta(hours=5, minutes=30))


def _ts_cfg(c: EngineConfigModel) -> SterlingKiteEngineConfig:
    return SterlingKiteEngineConfig(
        trail_target=c.trail_target,
        exit_mode=c.exit_mode,
        exit_aligned_trail=getattr(c, 'exit_aligned_trail', False),
        price_stop_exit=getattr(c, 'price_stop_exit', True),
        max_contract_staleness_bars=getattr(c, 'max_contract_staleness_bars', 0),
        time_stop_bars=getattr(c, 'time_stop_bars', 0),
        adx_min=getattr(c, 'adx_min', None),
        atr_pct_min=getattr(c, 'atr_pct_min', None),
    )


async def _client(user: UserContext):
    acct = kite_accounts.get_active(user.user_id)
    if not acct:
        raise HTTPException(409, "No active Kite account — add credentials and log in first.")
    return await kite_accounts.acquire_client(acct)   # warm, cached (no per-call close)


def _signal_row_key(row: EngineSignalRow) -> tuple:
    leg = row.legs[0].option_symbol if row.legs else ""
    return (
        row.source or "spot",
        row.underlying,
        row.token,
        row.direction,
        row.option_type,
        row.timestamp_ms,
        leg,
    )


def _merge_signal_rows(base_rows: list[EngineSignalRow], navigator_rows: list[EngineSignalRow]) -> list[EngineSignalRow]:
    merged: dict[tuple, EngineSignalRow] = {}
    for row in [*base_rows, *navigator_rows]:
        key = _signal_row_key(row)
        existing = merged.get(key)
        if existing is None:
            merged[key] = row
            continue
        row_rank = (1 if row.is_fresh else 0, 1 if row.is_active else 0, row.timestamp_ms)
        existing_rank = (1 if existing.is_fresh else 0, 1 if existing.is_active else 0, existing.timestamp_ms)
        if row_rank >= existing_rank:
            merged[key] = row
    return sorted(merged.values(), key=lambda r: (r.is_fresh or r.is_active, r.timestamp_ms), reverse=True)


def _signals_response(uid: str) -> SignalsResponse:
    from app.services.simulation import simulation_runner
    if simulation_runner.has_session_view:
        res = simulation_runner.get_kite_signals_response()
        return SignalsResponse(**res)

    cfg = state.get_config(uid)
    us = scanner.snapshot(uid)
    st = state.status(uid)
    rows = list(us.rows if cfg.engine_enabled else [])
    generated_ms = us.generated_ms if cfg.engine_enabled else 0
    scanning = bool(us.scanning and cfg.engine_enabled)
    scanning_label = us.scanning_label if scanning else ""
    next_scan_ms = st.next_scan_ms if cfg.engine_enabled else 0
    auto_scan = service.is_auto_running()

    try:
        from app.services.navigator import config_store as navigator_config_store
        from app.services.navigator import runtime as navigator_runtime

        nav_record = navigator_config_store.get(uid, default_underlyings=cfg.scan_indices)
        if nav_record.config.enabled:
            nav_snap = navigator_runtime.snapshot(uid)
            nav_status = navigator_runtime.status(uid)
            rows = _merge_signal_rows(rows, list(nav_snap.rows))
            generated_ms = max(generated_ms, nav_snap.generated_ms)
            if nav_status.scanning:
                scanning = True
                scanning_label = nav_status.scanning_label
            if nav_status.next_scan_ms and (next_scan_ms == 0 or nav_status.next_scan_ms < next_scan_ms):
                next_scan_ms = nav_status.next_scan_ms
            auto_scan = auto_scan or navigator_runtime.is_auto_running()
    except Exception as exc:  # noqa: BLE001
        log.debug("Navigator rows unavailable for shared signal response user=%s: %s", uid, exc)

    return SignalsResponse(
        generated_ms=generated_ms,
        scanning=scanning,
        scanning_label=scanning_label,
        rows=rows,
        next_scan_ms=next_scan_ms,
        auto_scan=auto_scan,
        market_open=is_market_open(),
    )


@router.get("/config")
async def get_config(user: UserContext = Depends(get_current_user)) -> EngineConfigModel:
    return state.get_config(user.user_id)


def _gate_autoexec(uid: str, was_on: bool, now_on: bool, force: bool) -> None:
    """Refuse to switch auto-execute ON while the engine cannot account for what it is
    already carrying.

    Turning this on tells an unattended process to open new real-money positions. Doing
    that on top of holdings with no stop, entries whose fill we never confirmed, or a red
    counter that stopped updating is how one unguarded position becomes several. Turning
    it OFF is never gated. The legacy `force` parameter is accepted for API
    compatibility but cannot bypass unresolved exposure or protection.
    """
    if not now_on or was_on:
        return
    reasons = service.autoexec_preflight(uid)
    if reasons:
        raise HTTPException(409, {
            "error": "auto_execute_blocked",
            "message": "Auto-execute was not enabled — resolve these conditions first.",
            "reasons": reasons,
        })


@router.post("/config")
async def set_config(body: EngineConfigModel,
                     force: bool = False,
                     user: UserContext = Depends(get_current_user)) -> EngineConfigModel:
    _gate_autoexec(user.user_id,
                   bool(state.get_config(user.user_id).auto_execute),
                   bool(body.auto_execute), force)
    return state.set_config(user.user_id, body)


@router.patch("/config")
async def patch_config(body: dict,
                       force: bool = False,
                       user: UserContext = Depends(get_current_user)) -> EngineConfigModel:
    """Merge only the supplied fields into the stored config.

    ``POST /config`` replaces the whole model, so every UI write had to be a
    read-modify-write off a cached copy: change one toggle and the client
    re-sends its idea of all 38 fields. If anything had moved in between — a
    second browser tab, another surface in the same tab, a server-side
    normalisation — those fields were silently reverted to the stale snapshot,
    with no error and nothing on screen to show it. These are real-money
    settings; a stop mode that quietly reverts is not an acceptable failure.

    A partial write cannot revert a field it does not mention. Unknown keys are
    rejected rather than ignored, so a typo in a client fails loudly instead of
    silently not taking effect.
    """
    unknown = sorted(set(body) - set(EngineConfigModel.model_fields))
    if unknown:
        raise HTTPException(422, f"Unknown engine config field(s): {', '.join(unknown)}")
    current = state.get_config(user.user_id).model_dump()
    merged = EngineConfigModel(**{**current, **body})
    _gate_autoexec(user.user_id, bool(current.get("auto_execute")),
                   bool(merged.auto_execute), force)
    # Re-validate through the model so field validators still run on the merged
    # result (scan_expiries_stocks is forced to monthly, target_delta is bounded).
    return state.set_config(user.user_id, merged)


@router.post("/config/reset")
async def reset_config(user: UserContext = Depends(get_current_user)) -> EngineConfigModel:
    return state.set_config(user.user_id, EngineConfigModel())


@router.post("/config/production")
async def apply_production_config(user: UserContext = Depends(get_current_user)) -> EngineConfigModel:
    """Apply the sweep-validated, fail-safe production configuration preset."""
    prod_cfg = EngineConfigModel.production()
    return state.set_config(user.user_id, prod_cfg)


@router.get("/readiness")
async def live_readiness(user: UserContext = Depends(get_current_user)) -> ReadinessResponse:
    """Pre-flight diagnostic for live production trading."""
    client = None
    acct = kite_accounts.get_active(user.user_id)
    if acct:
        try:
            client = await kite_accounts.acquire_client(acct)
        except Exception:
            client = None
    report = await service.check_live_readiness(client, user.user_id)
    return ReadinessResponse(**report)


@router.post("/emergency-square-off")
async def emergency_square_off(user: UserContext = Depends(get_current_user)) -> EmergencyActionResponse:
    """Emergency manual square-off for all open/pending positions in this engine."""
    client = await _client(user)
    res = await service.emergency_square_off_all(client, user.user_id)
    return EmergencyActionResponse(**res)


@router.post("/emergency-halt")
async def emergency_halt_all(user: UserContext = Depends(get_current_user)) -> EmergencyActionResponse:
    """Engage global kill switch, turn off auto-execute, and square off all open positions."""
    client = await _client(user)
    res = await service.emergency_halt(client, user.user_id)
    return EmergencyActionResponse(
        status=res["status"],
        message=res["message"],
        positions_count=res["square_off"]["positions_count"],
        squared_off=res["square_off"]["squared_off"],
        failed=res["square_off"]["failed"],
        details=res["square_off"]["details"],
    )


@router.get("/expiry-calendar")
async def expiry_calendar(user: UserContext = Depends(get_current_user)) -> dict:
    """Return exact option expiries listed by Kite for every supported underlying.

    The response is display metadata for the expiry selector. Dates are copied from
    the cached NFO/BFO instrument dumps and classified with the same month-end rule
    as the production strike resolver; no weekday or holiday date is calculated.
    """
    client = await _client(user)
    try:
        nfo_rows, bfo_rows = await asyncio.gather(
            client.search_instruments("", "NFO", limit=1_000_000),
            client.search_instruments("", "BFO", limit=1_000_000),
        )
    except KiteError as exc:
        raise HTTPException(502, f"Kite instrument fetch failed: {exc}") from exc

    universe_config = load_universe_config()
    return build_expiry_calendar(
        nfo_rows=nfo_rows,
        bfo_rows=bfo_rows,
        index_definitions=universe_config.get("indices", []),
        stock_names=CURATED_STOCK_NAMES,
        today=datetime.now(_IST).date(),
    )


@router.post("/backtest")
async def backtest(body: BacktestRequest,
                   user: UserContext = Depends(get_current_user)) -> BacktestResponse:
    """Honest options backtest (workstream H). data_mode synthetic | real | both:
    synthetic replays the signal on real underlying history with BS-modeled premium
    (full history, modeled price); real replays an actual live-contract premium
    series (true price, short lookback); both runs each + reports BS-vs-real drift.
    Read-only: no orders, no live-safety gate."""
    from app.services.kite_engine import backtest_service
    client = await _client(user)
    try:
        result = await backtest_service.run_backtest(client, body)
    except KiteError as exc:
        raise HTTPException(502, f"Kite data fetch failed: {exc}") from exc
    return BacktestResponse(**result)


@router.get("/signals")
async def signals(user: UserContext = Depends(get_current_user)) -> SignalsResponse:
    return _signals_response(user.user_id)


@router.get("/activity")
async def activity(limit: int = 2000,
                   user: UserContext = Depends(get_current_user)) -> ActivityResponse:
    uid = user.user_id
    st = state.status(uid)
    us = scanner.snapshot(uid)
    return ActivityResponse(
        events=state.activity(uid, limit), scanning=st.scanning, auto_scan=service.is_auto_running(),
        last_scan_ms=st.last_scan_ms, next_scan_ms=st.next_scan_ms, signal_count=st.signal_count,
        scanning_label=us.scanning_label if us.scanning else "",
        market_open=is_market_open(),
    )


@router.get("/server-logs")
async def server_logs(limit: int = 300,
                      user: UserContext = Depends(get_current_user)) -> dict:
    """Recent backend server logs (in-memory ring buffer) for the Sterling Kite Terminal.
    Lets the UI interleave real server logs with engine activity. Read-only."""
    from app.core.logging import recent_logs
    return {"logs": recent_logs(limit)}


@router.post("/scan")
async def run_scan(user: UserContext = Depends(get_current_user)) -> SignalsResponse:
    """Manual scan trigger (the background loop also scans automatically)."""
    uid = user.user_id
    client = await _client(user)
    await service.scan_user(client, uid)
    return _signals_response(uid)


@router.post("/scan/cancel")
async def cancel_scan(user: UserContext = Depends(get_current_user)) -> SignalsResponse:
    """Force-stop a running scan. Returns the current snapshot after cancellation."""
    uid = user.user_id
    cancelled = scanner.cancel(uid)
    if cancelled:
        state.log(uid, "info", "Scan cancelled by user.")
        state.set_scanning(uid, False)
        state.set_cooldown(uid)
    return _signals_response(uid)


@router.get("/setup/{token}")
async def setup(token: int, underlying: str = "",
                user: UserContext = Depends(get_current_user)) -> SetupChart:
    client = await _client(user)
    return await build_setup_chart(client, token, underlying, _ts_cfg(state.get_config(user.user_id)))


@router.post("/order")
async def place_order(body: EngineOrderRequest,
                     user: UserContext = Depends(get_current_user)) -> EngineOrderResponse:
    """Place a manual BUY/SELL from the detail panel — same live-safety gate as the
    standard order path, but logged to the engine terminal with full error surfacing."""
    res = await service.place_manual_order(
        user.user_id, body.option_symbol, body.side, body.quantity, body.exchange)
    if res["status"] == "blocked":
        raise HTTPException(423, detail={"reason": res.get("reason"), "code": res.get("code")})
    if res["status"] == "error":
        raise HTTPException(502, detail=res.get("message", "Order failed"))
    return EngineOrderResponse(
        order_id=res.get("order_id", ""), status=res["status"],
        message=res.get("message", ""),
        # A BUY that could not be armed is still status "ok" (the order is live), so
        # these two are the only way the board learns the position has no stop.
        protected=bool(res.get("protected", True)),
        protection=str(res.get("protection", "") or ""))


@router.get("/detail/{token}")
async def detail(
    token: int, timestamp_ms: int = 0, source: str = "",
    user: UserContext = Depends(get_current_user),
) -> EngineDetailResponse:
    """Trigger context + live underlying price + per-leg quote/depth/greeks for a
    ready signal (BUY/SELL are placed via the standard /kite/orders endpoint).

    `source` is the clicked row's own scan source. A Navigator origination shares
    its underlying's token with every SuperTrend row for that instrument, so
    without it the two are indistinguishable here and the wrong plan can answer."""
    client = await _client(user)
    d = await build_detail(client, user.user_id, token, timestamp_ms, source=source or None)
    if d is None:
        raise HTTPException(404, "No ready signal for that instrument in the latest scan.")
    return d


@router.get("/open-positions")
async def open_positions(user: UserContext = Depends(get_current_user)) -> OpenPositionsResponse:
    """Return the engine's currently tracked open positions including vehicle/direction labels."""
    uid = user.user_id
    records = []
    for p in kite_positions.open_positions(uid):
        em = getattr(p, 'exit_mode', 'one_red')
        from app.engines.common.exit_counter import get_exit_threshold
        thresh = get_exit_threshold(em)
        records.append(OpenPositionRecord(
            symbol=p.symbol, exchange=p.exchange, token=p.token,
            qty=p.qty, lot_size=p.lot_size,
            entry_premium=p.entry_premium, fill_price=p.fill_price,
            stop_premium=p.stop_premium, status=p.status,
            direction=p.direction, vehicle=p.vehicle, underlying=p.underlying,
            opened_ms=p.opened_ms, exit_reason=p.exit_reason, order_id=p.order_id,
            exit_pending=bool(p.exit_order_id) and p.status in (kite_positions.OPEN, kite_positions.PENDING),
            pnl_reconciliation_required=p.pnl_reconciliation_required,
            exit_mode=em,
            current_red_count=getattr(p, 'current_red_count', 0),
            exit_threshold=thresh,
        ))
    return OpenPositionsResponse(positions=records)


@router.delete("/open-positions/{symbol}")
async def close_position(symbol: str, user: UserContext = Depends(get_current_user)) -> OpenPositionsResponse:
    """Manually close (mark-closed) a tracked position without placing a broker order.
    Cancels any live broker GTT stop before closing, so it can't fire after removal.
    Use when an order was filled outside the engine or to clean up stale entries."""
    uid = user.user_id
    p = kite_positions.get(uid, symbol)
    if p is not None and p.status in (kite_positions.OPEN, kite_positions.PENDING):
        client = await _client(user)
        # Removing a row cannot remove a broker position or cancel its protection.
        # Reconcile from broker evidence; a missing/malformed reply is not flatness.
        await service._reconcile_closed_positions(client, uid)
        if p.status in (kite_positions.OPEN, kite_positions.PENDING):
            raise HTTPException(status_code=409, detail="Broker position is not confirmed flat; use an exit order.")
    records = []
    for p in kite_positions.open_positions(uid):
        em = getattr(p, 'exit_mode', 'one_red')
        from app.engines.common.exit_counter import get_exit_threshold
        thresh = get_exit_threshold(em)
        records.append(OpenPositionRecord(
            symbol=p.symbol, exchange=p.exchange, token=p.token,
            qty=p.qty, lot_size=p.lot_size,
            entry_premium=p.entry_premium, fill_price=p.fill_price,
            stop_premium=p.stop_premium, status=p.status,
            direction=p.direction, vehicle=p.vehicle, underlying=p.underlying,
            opened_ms=p.opened_ms, exit_reason=p.exit_reason, order_id=p.order_id,
            exit_pending=bool(p.exit_order_id) and p.status in (kite_positions.OPEN, kite_positions.PENDING),
            pnl_reconciliation_required=p.pnl_reconciliation_required,
            exit_mode=em,
            current_red_count=getattr(p, 'current_red_count', 0),
            exit_threshold=thresh,
        ))
    return OpenPositionsResponse(positions=records)


@router.get("/stock-registry")
async def stock_registry() -> list[dict]:
    """Return the curated stock registry with liquidity / volatility metadata,
    plus a separate optional-stocks group for the '+' picker."""
    from app.services.kite_engine.stock_registry import OPTIONAL_STOCKS, STOCKS_BY_LIQUIDITY
    groups = [
        {"liquidity": liq, "stocks": [e.to_dict() for e in entries]}
        for liq in ["Very High", "High", "Good"]
        if liq in STOCKS_BY_LIQUIDITY
        for entries in [STOCKS_BY_LIQUIDITY[liq]]
    ]
    groups.append({"liquidity": "optional", "stocks": [e.to_dict() for e in OPTIONAL_STOCKS]})
    return groups
