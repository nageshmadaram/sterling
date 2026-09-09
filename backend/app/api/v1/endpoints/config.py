"""Indian-market strategy configuration endpoints."""
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, model_validator

from app.core.auth import UserContext, get_current_user

router = APIRouter(prefix="/config", tags=["config"])

class NiftyOrbConfigRequest(BaseModel):
    enabled: bool | None = None
    underlying: str | None = None
    scan_indices: list[str] | None = None
    scan_stocks: list[str] | None = None
    scan_all_stocks: bool | None = None
    scan_stock_contracts: bool | None = None
    interval_minutes: int | None = None
    opening_range_minutes: int | None = None
    entry_start: str | None = None
    entry_end: str | None = None
    min_breakout_atr: float | None = None
    volume_multiplier: float | None = None
    vwap_slope_lookback: int | None = None
    trend_lookback: int | None = None
    atr_period: int | None = None
    stop_buffer_atr: float | None = None
    target_r: float | None = None
    option_moneyness: str | None = None
    option_steps_itm: int | None = None
    max_risk_inr: float | None = None
    max_trades_per_day: int | None = None
    avoid_expiry_day: bool | None = None
    expiry_selection: str | None = None
    expiry_dte_min: int | None = None
    expiry_dte_max: int | None = None
    execution_broker: str | None = None
    data_source: str | None = None
    max_spread_pct: float | None = None
    min_option_volume: float | None = None
    min_open_interest: float | None = None
    max_quote_staleness_s: int | None = None
    risk_free_rate: float | None = None
    truedata_use_ticks: bool | None = None
    truedata_use_oi: bool | None = None
    truedata_use_bid_ask: bool | None = None
    truedata_use_quote_freshness: bool | None = None
    strike_moneyness: list[str] | None = None
    scan_expiries_indices: list[str] | None = None
    scan_weekly_series_indices: list[int] | None = None
    scan_monthly_series_indices: list[int] | None = None
    scan_monthly_series_stocks: list[int] | None = None

    @model_validator(mode="before")
    @classmethod
    def _no_strategy_local_execution(cls, data):
        """Paper/Live and Manual/Auto are shared Trading Mode, never ORB config."""
        if isinstance(data, dict):
            forbidden = [k for k in ("auto_execute", "paper_only") if k in data]
            if forbidden:
                raise ValueError(
                    "Manual/Auto and Paper/Live are shared Trading Mode switches, "
                    f"not ORB config fields: {', '.join(forbidden)}"
                )
        return data

@router.get("/nifty-orb-options")
async def get_nifty_orb_options_config() -> dict:
    """Current ORB config, plus the engine's own defaults.

    The defaults are published rather than mirrored in the client so the UI can
    mark which fields are still at default without keeping a second copy that
    could drift from the engine — the failure mode this codebase keeps hitting.
    """
    from app.engines.nifty_orb_options import StrategyConfig
    from app.services.nifty_orb_options import get_config
    cfg = get_config()
    return {
        "config": cfg.__dict__,
        "defaults": StrategyConfig().__dict__,
        "supported_data_sources": ["kite", "truedata"],
        "execution_brokers": ["kite"],
    }

@router.put("/nifty-orb-options")
async def update_nifty_orb_options_config(body: NiftyOrbConfigRequest) -> dict:
    from app.services.nifty_orb_options import set_config
    try:
        cfg = set_config({k: v for k, v in body.model_dump().items() if v is not None})
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"config": cfg.__dict__}

@router.post("/nifty-orb-options/snapshot")
async def nifty_orb_options_snapshot(user: UserContext = Depends(get_current_user)) -> dict:
    from app.services.simulation import simulation_runner
    if simulation_runner.has_session_view:
        return simulation_runner.get_nifty_orb_signals_response()
    from app.services.nifty_orb_options import snapshot
    try:
        return await snapshot(user.user_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"NIFTY ORB snapshot failed: {exc}") from exc

@router.post("/nifty-orb-options/scan")
async def nifty_orb_options_scan(user: UserContext = Depends(get_current_user)) -> dict:
    from app.services.simulation import simulation_runner
    if simulation_runner.has_session_view:
        return simulation_runner.get_nifty_orb_signals_response()
    from app.services.nifty_orb_scanner import scan_user
    try:
        return await scan_user(user.user_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"NIFTY ORB scan failed: {exc}") from exc

@router.post("/nifty-orb-options/backtest")
async def nifty_orb_options_backtest(body: dict) -> dict:
    from app.services.nifty_orb_options import backtest_from_bars
    rows = body.get("bars") if isinstance(body, dict) else None
    if not isinstance(rows, list):
        raise HTTPException(status_code=422, detail="bars must be a list of OHLCV rows")
    return backtest_from_bars(rows)

@router.post("/nifty-orb-options/execute")
async def nifty_orb_options_execute(user: UserContext = Depends(get_current_user)) -> dict:
    """Return the same ticket Auto would use. Does not place.

    Manual Buy is the board order window. A previous implementation called
    ``place_manual_order`` here without protection or same-ticket gates.
    """
    from app.services.nifty_orb_options import execute_manual
    try:
        return await execute_manual(user.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"NIFTY ORB execution failed: {exc}") from exc

@router.on_event("startup")
async def _start_nifty_orb_runner() -> None:
    from app.services.nifty_orb_options_runner import start
    start()

# --------------------------------------------------------------------------
# ATM Premium Imbalance
#
# Registered exactly the way NIFTY ORB is: engine package + service config
# store + these endpoints. The repository has no central strategy registry to
# add to -- app/engines/edge/catalog.py catalogues backtest-validated
# (symbol, tf, profile) combos for the edge feed, which is a different concept
# -- so inventing a second registry here would create the parallel
# infrastructure the strategy contract forbids. The strategy publishes its own
# identity on GET instead.
# --------------------------------------------------------------------------

# No hand-written mirror of the config here on purpose. The previous one listed
# every field by name and fell behind the dataclass, so newly added settings were
# silently dropped by pydantic on the way in -- the UI looked like it saved and
# nothing changed. `set_config` already rejects unknown fields and validates
# through the engine's own rules, so this endpoint takes the body as-is and lets
# the single definition do the work. `test_every_config_field_is_settable` is
# what keeps it honest.


@router.get("/atm-premium-imbalance")
async def get_atm_premium_imbalance_config() -> dict:
    """Current config plus the engine's own defaults and vocabularies.

    Defaults and enums are published rather than mirrored in the client, so the
    UI cannot drift from the engine -- the recurring bug class in this codebase
    is a UI that claims backend behaviour the backend does not honour.
    """
    from app.engines.atm_premium_imbalance.config import (
        ENTRY_PRICE_POLICIES, EXIT_POLICIES, EXPIRY_POLICIES, FIRST_TICK_SOURCES,
        PROTECTION_MODES, QUOTE_MODES, RESEARCH_ONLY_ENTRY_POLICIES, SIZING_MODES,
        RESEARCH_ONLY_EXIT_POLICIES, STRIKE_POLICIES, ATMPremiumImbalanceConfig,
    )
    from app.services.atm_premium_imbalance import descriptor, get_config
    cfg = get_config()
    return {
        "strategy": {**descriptor(), "enabled": cfg.enabled},
        "config": cfg.as_dict(),
        "defaults": ATMPremiumImbalanceConfig().as_dict(),
        "vocabularies": {
            "expiry_policy": sorted(EXPIRY_POLICIES),
            "strike_policy": sorted(STRIKE_POLICIES),
            "quote_mode": sorted(QUOTE_MODES),
            "entry_price_policy": sorted(ENTRY_PRICE_POLICIES),
            "exit_policy": sorted(EXIT_POLICIES),
            "protection_mode": sorted(PROTECTION_MODES),
            "sizing_mode": sorted(SIZING_MODES),
            "first_tick_source": sorted(FIRST_TICK_SOURCES),
            "data_source": ["kite", "truedata"],
            "execution_mode": ["paper", "live"],
        },
        # The UI must be able to grey these out rather than offer a switch that
        # validate() will refuse.
        "research_only": {
            "entry_price_policy": sorted(RESEARCH_ONLY_ENTRY_POLICIES),
            "exit_policy": sorted(RESEARCH_ONLY_EXIT_POLICIES),
        },
        # Live refuses NONE: a crash while long would leave the position with
        # nothing watching it. Published so the UI can say so up front.
        # Computed from the engine's own live gate, never a hand-written mirror of
        # it. The previous static dict had already drifted: it named protection,
        # quote mode and the session-origin gate while silently omitting the size
        # and the stop, so the panel could show a config as live-ready that
        # validate() would refuse.
        "live_blockers": cfg.live_blockers(),
    }


@router.put("/atm-premium-imbalance")
async def update_atm_premium_imbalance_config(body: dict = Body(...)) -> dict:
    """Apply a partial config change.

    Only the keys present are changed. Unknown keys are refused rather than
    ignored: a silently dropped setting is worse than a 422, because the UI has
    no way to tell it did not take.
    """
    from app.services.atm_premium_imbalance import set_config
    values = {k: v for k, v in dict(body).items() if v is not None}
    if not values:
        raise HTTPException(status_code=422, detail="no settings to change")
    try:
        cfg = set_config(values)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"config": cfg.as_dict()}


@router.get("/atm-premium-imbalance/snapshot")
async def atm_premium_imbalance_snapshot(user: UserContext = Depends(get_current_user)) -> dict:
    """Config, the resolved ATM pair, and every reason the strategy is not armed.

    The tenant comes from the authenticated session, never from the request:
    taking it from the body would let any caller resolve instruments against
    another user's broker credentials and rate limit.
    """
    from app.services.simulation import simulation_runner
    if simulation_runner.has_session_view:
        return simulation_runner.get_atm_imbalance_snapshot()

    from app.services.atm_premium_imbalance import snapshot
    uid = str(user.user_id or "").strip()
    if not uid:
        raise HTTPException(status_code=401, detail="authenticated user is required")
    try:
        return await snapshot(uid)
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"ATM Premium Imbalance snapshot failed: {exc}"
        ) from exc


@router.post("/atm-premium-imbalance/arm")
async def atm_premium_imbalance_arm(user: UserContext = Depends(get_current_user)) -> dict:
    """Resolve the ATM pair, subscribe both legs, and arm the session.

    Idempotent for the day: arming twice returns ``already_armed`` rather than
    creating a second session that could place a second entry. The strategy then
    runs off the Kite tick stream, not off this call.
    """
    from app.services.atm_premium_imbalance_runner import arm
    uid = str(user.user_id or "").strip()
    if not uid:
        raise HTTPException(status_code=401, detail="authenticated user is required")
    try:
        return await arm(uid)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"ATM Premium Imbalance arm failed: {exc}") from exc


@router.post("/atm-premium-imbalance/adopt")
async def atm_premium_imbalance_adopt(
    symbol: str, user: UserContext = Depends(get_current_user),
) -> dict:
    """Take charge of an open position the app has no session for.

    Requires the symbol rather than adopting whatever is found: a long option on
    this underlying may be a hand-placed trade, and the operator is the one who
    knows which position is the strategy's.
    """
    from app.services.atm_premium_imbalance_runner import adopt
    uid = str(user.user_id or "").strip()
    if not uid:
        raise HTTPException(status_code=401, detail="authenticated user is required")
    try:
        return await adopt(uid, symbol)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"ATM Premium Imbalance adopt failed: {exc}"
        ) from exc


@router.post("/atm-premium-imbalance/simulate")
async def atm_premium_imbalance_simulate(
    speed: float = 1.0, lots: Optional[int] = None, continuous: bool = True,
    overrides: Optional[dict] = Body(default=None, embed=True),
    user: UserContext = Depends(get_current_user),
) -> dict:
    """Replay the last traded session through the live code path, on a fake clock.

    Real time by default: one simulated second per real second, advancing the
    clock second by second so it reads like a live session. The clock starts at
    09:14 IST so the pre-open refusal is visible, then the strategy runs against
    real minute bars. Nothing reaches a broker: the session is marked as a
    simulation, which is also what stops today's live ticks from driving it.

    ``continuous`` (the default) keeps the session working after a trade closes
    rather than stopping at the first one. It relaxes the per-session trade limit
    and the entry window, and reports which in ``relaxed``.

    Results are illustrative, not a backtest — see the module docstring for the
    two structural reasons (minute bars have no intrabar order, and fills are
    modelled at the limit price).
    """
    from app.services.atm_premium_imbalance_sim import start
    uid = str(user.user_id or "").strip()
    if not uid:
        raise HTTPException(status_code=401, detail="authenticated user is required")
    try:
        return await start(uid, speed=speed, lots=lots, continuous=continuous,
                           overrides=overrides)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"ATM Premium Imbalance simulation failed: {exc}"
        ) from exc


@router.post("/atm-premium-imbalance/simulate/stop")
async def atm_premium_imbalance_simulate_stop(
    user: UserContext = Depends(get_current_user),
) -> dict:
    from app.services.atm_premium_imbalance_sim import stop
    uid = str(user.user_id or "").strip()
    if not uid:
        raise HTTPException(status_code=401, detail="authenticated user is required")
    return await stop(uid)


# ------------------------------------------------------------------ Gamma Move

@router.get("/gamma-move")
async def get_gamma_move_config() -> dict:
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
    cfg = get_config()
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
async def update_gamma_move_config(body: dict = Body(...)) -> dict:
    """Apply a partial config change.

    Only the keys present are changed. Unknown keys are refused rather than
    ignored: a silently dropped setting is worse than a 422, because the UI has
    no way to tell it did not take.
    """
    from app.services.gamma_move import set_config
    values = {k: v for k, v in dict(body).items() if v is not None}
    if not values:
        raise HTTPException(status_code=422, detail="no settings to change")
    try:
        cfg = set_config(values)
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


# ------------------------------------------------------------------ OI Wall Flow

@router.get("/oi-wall-flow")
async def get_oi_wall_flow_config() -> dict:
    """Current config plus the engine's own defaults, vocabularies and judgement.

    Thresholds are judgement from one motivating chain, not a calibrated sample.
    They are published as ``judgement_fields`` so the UI can say so, instead of
    pretending they were measured.
    """
    from app.engines.oi_wall_flow.config import STOP_MODES, OIWallFlowConfig
    from app.engines.option_contracts import EXPIRY_SELECTIONS, EXPIRY_SERIES
    from app.services.kite_engine.stock_registry import HIGH_LIQUIDITY_STOCK_NAMES
    from app.services.oi_wall_flow import descriptor, get_config
    cfg = get_config()
    return {
        "strategy": {**descriptor(), "enabled": cfg.enabled},
        "config": cfg.as_dict(),
        "defaults": OIWallFlowConfig().as_dict(),
        "vocabularies": {
            "stop_mode": sorted(STOP_MODES),
            "expiry_selection": sorted(EXPIRY_SELECTIONS),
            "scan_expiries_indices": sorted(EXPIRY_SERIES),
            "scan_expiries_stocks": ["monthly"],
            "data_source": ["kite"],
            "scan_stocks": sorted(HIGH_LIQUIDITY_STOCK_NAMES),
        },
        "research_only": {},
        "warnings": cfg.warnings(),
    }


@router.put("/oi-wall-flow")
async def update_oi_wall_flow_config(body: dict = Body(...)) -> dict:
    """Apply a partial config change. Unknown keys are refused rather than ignored."""
    from app.services.oi_wall_flow import set_config
    values = {k: v for k, v in dict(body).items() if v is not None}
    if not values:
        raise HTTPException(status_code=422, detail="no settings to change")
    try:
        cfg = set_config(values)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"config": cfg.as_dict()}


@router.get("/oi-wall-flow/snapshot")
async def oi_wall_flow_snapshot(user: UserContext = Depends(get_current_user)) -> dict:
    """Config, what the scan found, and every reason nothing is armed."""
    uid = getattr(user, "user_id", None) or getattr(user, "uid", None)
    if not uid:
        raise HTTPException(status_code=401, detail="authenticated user is required")
    from app.services.oi_wall_flow import snapshot
    try:
        return await snapshot(uid)
    except Exception as exc:
        raise HTTPException(status_code=502,
                            detail=f"OI Wall Flow snapshot failed: {exc}") from exc


@router.post("/oi-wall-flow/scan")
async def oi_wall_flow_scan(user: UserContext = Depends(get_current_user)) -> dict:
    """Run one on-demand universe → chain → classify pass."""
    uid = getattr(user, "user_id", None) or getattr(user, "uid", None)
    if not uid:
        raise HTTPException(status_code=401, detail="authenticated user is required")
    from app.services.oi_wall_flow_runner import scan_once
    try:
        return await scan_once(uid)
    except Exception as exc:
        raise HTTPException(status_code=502,
                            detail=f"OI Wall Flow scan failed: {exc}") from exc


@router.post("/oi-wall-flow/arm")
async def oi_wall_flow_arm(body: dict = Body(...),
                           user: UserContext = Depends(get_current_user)) -> dict:
    """Enter one armed signal by id."""
    uid = getattr(user, "user_id", None) or getattr(user, "uid", None)
    if not uid:
        raise HTTPException(status_code=401, detail="authenticated user is required")
    signal_id = str(dict(body).get("signal_id") or "").strip()
    if not signal_id:
        raise HTTPException(status_code=422, detail="signal_id is required")
    from app.services.oi_wall_flow_runner import arm
    return await arm(uid, signal_id)


@router.post("/oi-wall-flow/adopt")
async def oi_wall_flow_adopt(body: dict = Body(...),
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
    from app.services.oi_wall_flow_runner import adopt
    return await adopt(uid, symbol, quantity, entry_price)


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
