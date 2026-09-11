"""Indian-market strategy configuration endpoints."""
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, model_validator

from app.core.auth import UserContext, get_current_user

router = APIRouter(prefix="/config", tags=["config"])




# ------------------------------------------------------------------ Gamma Move

@router.get("/gamma-move")
async def get_gamma_move_config(user: UserContext = Depends(get_current_user)) -> dict:
    """Current config plus the engine's own defaults, vocabularies and calibration.

    Defaults and enums are published rather than mirrored in the client, so the
    UI cannot drift from the engine -- the recurring bug class in this codebase
    is a UI that claims backend behaviour the backend does not honour.

    ``calibration`` is published for a second reason: every threshold in this
    engine was measured, and one of them (the regime multiplier) is a default
    chosen specifically because the conventional value inverted the gate. An
    operator changing a number should be able to see what it cost to pick it.
    """
    from app.engines.gamma_move.config import (EXIT_POLICIES, EXPIRY_SELECTIONS,
                                               EXPIRY_SERIES, LEVEL_TIMEFRAMES,
                                               RESEARCH_ONLY_EXIT_POLICIES,
                                               SIZING_MODES, STOP_BASES, STOP_MODES,
                                               TRIGGER_TIMEFRAMES, GammaMoveConfig)
    from app.services.kite_engine.stock_registry import HIGH_LIQUIDITY_STOCK_NAMES
    from app.services.gamma_move import descriptor, get_config
    uid = getattr(user, "user_id", None) or getattr(user, "uid", None) or "default"
    cfg = get_config(uid)
    return {
        "strategy": {**descriptor(), "enabled": cfg.enabled},
        "config": cfg.as_dict(),
        "defaults": GammaMoveConfig().as_dict(),
        "vocabularies": {
            "level_timeframe": sorted(LEVEL_TIMEFRAMES),
            "regime_timeframe": sorted(LEVEL_TIMEFRAMES),
            "trigger_timeframe": sorted(TRIGGER_TIMEFRAMES),
            "exit_policy": sorted(EXIT_POLICIES),
            "stop_basis": sorted(STOP_BASES),
            "sizing_mode": sorted(SIZING_MODES),
            "stop_mode": sorted(STOP_MODES),
            # Contract vocabulary, shared with every other option engine.
            "expiry_selection": sorted(EXPIRY_SELECTIONS),
            "scan_expiries_indices": sorted(EXPIRY_SERIES),
            "scan_expiries_stocks": ["monthly"],
            "data_source": ["kite"],
            # The eligible universe, published rather than typed: the same
            # curated high-liquidity registry every other engine scans.
            "scan_stocks": sorted(HIGH_LIQUIDITY_STOCK_NAMES),
        },
        # The source gives no exit rule at all, so everything but the time stop
        # is unsupported by evidence. It is a warning, not a refusal.
        "research_only": {"exit_policy": sorted(RESEARCH_ONLY_EXIT_POLICIES)},
        # Configured choices worth stating out loud, computed by the engine so
        # the UI cannot invent its own list.
        "warnings": cfg.warnings(),
    }


@router.put("/gamma-move")
async def update_gamma_move_config(body: dict = Body(...),
                                   user: UserContext = Depends(get_current_user)) -> dict:
    """Apply a partial config change.

    Only the keys present are changed. Unknown keys are refused rather than
    ignored: a silently dropped setting is worse than a 422, because the UI has
    no way to tell it did not take.
    """
    from app.services.gamma_move import set_config
    values = {k: v for k, v in dict(body).items() if v is not None}
    if not values:
        raise HTTPException(status_code=422, detail="no settings to change")
    uid = getattr(user, "user_id", None) or getattr(user, "uid", None) or "default"
    try:
        cfg = set_config(values, uid=uid)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"config": cfg.as_dict()}


@router.get("/gamma-move/snapshot")
async def gamma_move_snapshot(user: UserContext = Depends(get_current_user)) -> dict:
    """Config, what the scan found, and every reason nothing is armed."""
    from app.services.simulation import simulation_runner
    if simulation_runner.has_session_view:
        return simulation_runner.get_gamma_move_snapshot()

    uid = getattr(user, "user_id", None) or getattr(user, "uid", None)
    if not uid:
        raise HTTPException(status_code=401, detail="authenticated user is required")
    from app.services.gamma_move import snapshot
    try:
        return await snapshot(uid)
    except Exception as exc:
        raise HTTPException(status_code=502,
                            detail=f"Gamma Move snapshot failed: {exc}") from exc


@router.post("/gamma-move/scan")
async def gamma_move_scan(user: UserContext = Depends(get_current_user)) -> dict:
    """Run one on-demand levels -> strikes -> trigger pass."""
    from app.services.simulation import simulation_runner
    if simulation_runner.has_session_view:
        raise HTTPException(status_code=409,
                            detail="replay is driving this board — live scan is off")
    uid = getattr(user, "user_id", None) or getattr(user, "uid", None)
    if not uid:
        raise HTTPException(status_code=401, detail="authenticated user is required")
    from app.services.gamma_move_runner import scan_once
    try:
        return await scan_once(uid)
    except Exception as exc:
        raise HTTPException(status_code=502,
                            detail=f"Gamma Move scan failed: {exc}") from exc


@router.post("/gamma-move/arm")
async def gamma_move_arm(body: dict = Body(...),
                         user: UserContext = Depends(get_current_user)) -> dict:
    """Enter one armed signal by id."""
    from app.services.simulation import simulation_runner
    if simulation_runner.has_session_view:
        raise HTTPException(status_code=409,
                            detail="replay is driving this board — live entry is off")
    uid = getattr(user, "user_id", None) or getattr(user, "uid", None)
    if not uid:
        raise HTTPException(status_code=401, detail="authenticated user is required")
    signal_id = str(dict(body).get("signal_id") or "").strip()
    if not signal_id:
        raise HTTPException(status_code=422, detail="signal_id is required")
    from app.services.gamma_move_runner import arm
    return await arm(uid, signal_id)


@router.post("/gamma-move/adopt")
async def gamma_move_adopt(body: dict = Body(...),
                           user: UserContext = Depends(get_current_user)) -> dict:
    """Take responsibility for a position this engine did not open."""
    uid = getattr(user, "user_id", None) or getattr(user, "uid", None)
    if not uid:
        raise HTTPException(status_code=401, detail="authenticated user is required")
    data = dict(body)
    symbol = str(data.get("symbol") or "").strip()
    try:
        quantity = int(data.get("quantity") or 0)
        entry_price = float(data.get("entry_price") or 0)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="quantity and entry_price "
                                                    "must be numbers") from exc
    if not symbol or quantity <= 0 or entry_price <= 0:
        raise HTTPException(status_code=422,
                            detail="symbol, a positive quantity and entry_price are required")
    from app.services.simulation import simulation_runner
    if simulation_runner.has_session_view:
        raise HTTPException(status_code=409,
                            detail="replay is driving this board — live adopt is off")
    from app.services.gamma_move_runner import adopt
    return await adopt(uid, symbol, quantity, entry_price)


@router.post("/gamma-move/simulate")
async def gamma_move_simulate(body: dict = Body(...),
                              user: UserContext = Depends(get_current_user)) -> dict:
    """Replay named contracts through the same engine the live path runs."""
    uid = getattr(user, "user_id", None) or getattr(user, "uid", None)
    if not uid:
        raise HTTPException(status_code=401, detail="authenticated user is required")
    data = dict(body)
    symbols = [str(s).strip() for s in (data.get("symbols") or []) if str(s).strip()]
    if not symbols:
        raise HTTPException(status_code=422, detail="symbols is required")
    try:
        days = int(data.get("days") or 60)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="days must be a number") from exc
    if not 5 <= days <= 180:
        raise HTTPException(status_code=422, detail="days must be between 5 and 180")
    from app.services.gamma_move_sim import start
    return await start(uid, symbols, days)


@router.post("/gamma-move/simulate/stop")
async def gamma_move_simulate_stop(user: UserContext = Depends(get_current_user)) -> dict:
    uid = getattr(user, "user_id", None) or getattr(user, "uid", None)
    if not uid:
        raise HTTPException(status_code=401, detail="authenticated user is required")
    from app.services.gamma_move_sim import stop
    return await stop(uid)





# ---------------------------------------------------------------- Adaptive Edge

@router.get("/adaptive-edge")
async def get_adaptive_edge_config() -> dict:
    """Current config plus the engine's defaults, vocabularies and provenance.

    Defaults and enums are published rather than mirrored in the client, so the
    UI cannot drift from the engine — the recurring bug class here is a UI that
    claims backend behaviour the backend does not honour.

    ``calibration`` is published for the opposite reason to Gamma Move's. There
    every threshold was measured; here none were, and ``calibrated_fields`` is
    empty. The UI needs both facts to mark each number uncalibrated rather than
    rendering a bare figure an operator may reasonably read as meaningful.
    """
    from app.engines.adaptive_edge.config import (
        DATA_SOURCES, DECISION_TIMEFRAMES, EXIT_POLICIES, SIZING_MODES,
        STOP_MODES, AdaptiveEdgeConfig,
        STOP_MODES, STRATEGY_VERSIONS, AdaptiveEdgeConfig,
    )
    from app.engines.option_contracts import EXPIRY_SELECTIONS, EXPIRY_SERIES
    from app.services.kite_engine.stock_registry import HIGH_LIQUIDITY_STOCK_NAMES
    from app.services.adaptive_edge import descriptor, get_config
    cfg = get_config()
    return {
        "strategy": {**descriptor(), "enabled": cfg.enabled},
        "config": cfg.as_dict(),
        "defaults": AdaptiveEdgeConfig().as_dict(),
        "vocabularies": {
            "strategy_version": sorted(STRATEGY_VERSIONS),
            "decision_timeframe": sorted(DECISION_TIMEFRAMES),
            "data_source": sorted(DATA_SOURCES),
            "exit_policy": sorted(EXIT_POLICIES),
            "sizing_mode": sorted(SIZING_MODES),
            "stop_mode": sorted(STOP_MODES),
            "expiry_selection": sorted(EXPIRY_SELECTIONS),
            "expiry_series": sorted(EXPIRY_SERIES),
            "stocks": sorted(HIGH_LIQUIDITY_STOCK_NAMES),
        },
        "warnings": cfg.warnings(),
    }


@router.put("/adaptive-edge")
async def update_adaptive_edge_config(body: dict = Body(...)) -> dict:
    """Apply a partial config change.

    Only the keys present are changed. Unknown keys are refused rather than
    ignored: a silently dropped setting is worse than a 422, because the UI has
    no way to tell it did not take.
    """
    from app.services.adaptive_edge import set_config
    values = {k: v for k, v in dict(body).items() if v is not None}
    if not values:
        raise HTTPException(status_code=422, detail="no settings to change")
    try:
        cfg = set_config(values)
        try:
            from app.api.v1.endpoints.adaptive_edge import clear_bridged_cache
            clear_bridged_cache()
        except Exception:
            pass
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"config": cfg.as_dict()}


@router.get("/adaptive-edge/snapshot")
async def adaptive_edge_snapshot(user: UserContext = Depends(get_current_user)) -> dict:
    """Config, what the scan found, and every reason nothing is armed."""
    from app.services.simulation import simulation_runner
    if simulation_runner.has_session_view:
        return simulation_runner.get_adaptive_edge_snapshot()

    uid = getattr(user, "user_id", None) or getattr(user, "uid", None)
    if not uid:
        raise HTTPException(status_code=401, detail="authenticated user is required")
    from app.services.adaptive_edge import snapshot
    try:
        return await snapshot(uid)
    except Exception as exc:
        raise HTTPException(status_code=502,
                            detail=f"Adaptive Edge snapshot failed: {exc}") from exc


@router.post("/adaptive-edge/scan")
async def adaptive_edge_scan(user: UserContext = Depends(get_current_user)) -> dict:
    """Run one on-demand underlyings -> contracts -> candidates pass."""
    uid = getattr(user, "user_id", None) or getattr(user, "uid", None)
    if not uid:
        raise HTTPException(status_code=401, detail="authenticated user is required")
    from app.services.adaptive_edge_runner import scan_once
    try:
        return await scan_once(uid)
    except Exception as exc:
        raise HTTPException(status_code=502,
                            detail=f"Adaptive Edge scan failed: {exc}") from exc


@router.post("/adaptive-edge/arm")
async def adaptive_edge_arm(body: dict = Body(...),
                            user: UserContext = Depends(get_current_user)) -> dict:
    """Enter one signal by id.

    The runner refuses this outright while the account is live and the strategy
    is unpromoted, so the gate is not something this route has to remember.
    """
    uid = getattr(user, "user_id", None) or getattr(user, "uid", None)
    if not uid:
        raise HTTPException(status_code=401, detail="authenticated user is required")
    signal_id = str(dict(body).get("signal_id") or "").strip()
    if not signal_id:
        raise HTTPException(status_code=422, detail="signal_id is required")
    from app.services.adaptive_edge_runner import arm
    return await arm(uid, signal_id)


@router.post("/adaptive-edge/adopt")
async def adaptive_edge_adopt(body: dict = Body(...),
                              user: UserContext = Depends(get_current_user)) -> dict:
    """Take responsibility for a position this engine did not open.

    Protection is placed as part of adopting. A hand-placed position the engine
    is managing but has not protected is the worst of both worlds.
    """
    uid = getattr(user, "user_id", None) or getattr(user, "uid", None)
    if not uid:
        raise HTTPException(status_code=401, detail="authenticated user is required")
    data = dict(body)
    symbol = str(data.get("symbol") or "").strip()
    try:
        quantity = int(data.get("quantity") or 0)
        entry_price = float(data.get("entry_price") or 0)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=422,
                            detail="quantity and entry_price must be numbers") from exc
    if not symbol or quantity <= 0 or entry_price <= 0:
        raise HTTPException(status_code=422,
                            detail="symbol, a positive quantity and entry_price are required")
    from app.services.adaptive_edge_runner import adopt
    return await adopt(uid, symbol, quantity, entry_price)


@router.post("/adaptive-edge/square-off")
async def adaptive_edge_square_off(user: UserContext = Depends(get_current_user)) -> dict:
    """Flatten everything this engine holds, now.

    Deliberately available whatever the manual/auto setting says: auto gates
    opening, and an operator must always be able to close.
    """
    uid = getattr(user, "user_id", None) or getattr(user, "uid", None)
    if not uid:
        raise HTTPException(status_code=401, detail="authenticated user is required")
    from app.services.adaptive_edge_runner import square_off_all
    return await square_off_all(uid)


@router.post("/adaptive-edge/reconcile")
async def adaptive_edge_reconcile(user: UserContext = Depends(get_current_user)) -> dict:
    """Re-sync against the broker: close what it no longer holds, re-protect the rest."""
    uid = getattr(user, "user_id", None) or getattr(user, "uid", None)
    if not uid:
        raise HTTPException(status_code=401, detail="authenticated user is required")
    from app.services.adaptive_edge_runner import reconcile
    return await reconcile(uid)


@router.get("/adaptive-edge/positions")
async def adaptive_edge_positions(user: UserContext = Depends(get_current_user)) -> dict:
    """What this engine is holding, and whether each position has a broker stop."""
    uid = getattr(user, "user_id", None) or getattr(user, "uid", None)
    if not uid:
        raise HTTPException(status_code=401, detail="authenticated user is required")
    from app.services.adaptive_edge_positions import load
    from app.services.adaptive_edge_runner import realised_pnl_today
    rows = []
    for pos in load(uid).values():
        rows.append({
            "symbol": pos.symbol, "underlying": pos.underlying, "type": pos.direction,
            "quantity": pos.quantity, "entry": pos.entry_price, "stop": pos.stop_price,
            "target": pos.target_price, "peak": pos.peak_price, "state": pos.state,
            "open": pos.is_open, "exit_price": pos.exit_price,
            "exit_reason": pos.exit_reason,
            # The difference between protected and protected-only-while-we-live.
            "broker_stop": bool(pos.gtt_id), "stop_mode": pos.stop_mode,
        })
    return {"positions": rows, "realised_pnl_today": realised_pnl_today(uid)}


@router.get("/adaptive-edge/evidence")
async def adaptive_edge_evidence(user: UserContext = Depends(get_current_user)) -> dict:
    """What the engine has measured live, and whether it has earned the right to trade.

    The implied-to-realised ratio is the fact every offline study of this
    strategy was missing — no store here holds option price history. The engine
    records it every scan, traded or not, and this reports the accumulated
    result plus what is still outstanding before the gate can open.
    """
    uid = getattr(user, "user_id", None) or getattr(user, "uid", None)
    if not uid:
        raise HTTPException(status_code=401, detail="authenticated user is required")
    from app.services.adaptive_edge_evidence import summary
    return summary(uid)
