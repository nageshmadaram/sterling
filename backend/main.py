import os
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.logging import setup_logging, get_logger
from app.core.observability import (
    configure_json_logging, new_correlation_id, set_correlation_id, reset_correlation_id,
)
from app.api.v1.endpoints.health import router as health_router
from app.api.v1.endpoints.backtest import router as backtest_router
from app.api.v1.endpoints.candles import router as candles_router
from app.api.v1.endpoints.config import router as config_router
import secrets
from app.core.csp import reset_csp_nonce, set_csp_nonce

log = get_logger(__name__)


async def _background_kite_alerts(interval: int = 60) -> None:
    """Push new Kite engine signals to Telegram during Indian market hours."""
    import asyncio
    from app.services.notifications import telegram_kite as _kbot
    from app.services.kite_engine.market_hours import is_market_open
    await asyncio.sleep(12)
    while True:
        try:
            if is_market_open():
                await _kbot.push_kite_alerts()
        except Exception as exc:  # noqa: BLE001
            log.debug("kite alert push error: %s", exc)
        await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    configure_json_logging()  # no-op unless settings.log_json (Phase 2 observability)
    # The SQLite store must be open BEFORE anything reads it. This used to
    # happen as a side effect of importing `exchange_account_store`, which went
    # with the crypto surface — after which `kite_accounts.bootstrap()` found
    # `db._available` False, loaded nothing, and `/kite/status` reported
    # "No active Kite account" even though the row was still in the database.
    from app.services import db as _db
    _db.init()

    from app.services.exchanges.kite import accounts as _kite_accounts
    _kite_accounts.bootstrap()
    # Adopt KITE_API_KEY / KITE_API_SECRET (and optionally KITE_ACCESS_TOKEN) from
    # the environment so a fresh DB or a new machine boots already provisioned —
    # no retyping credentials into the UI before anything works.
    try:
        from app.services.exchanges.kite import auth as _kite_auth
        _kite_auth.seed_from_env()
    except Exception as exc:  # noqa: BLE001
        log.warning("Kite env seeding skipped: %s", exc)
    # Init OHLCV table and kick off first fetch in background (non-blocking)
    from app.services.ohlcv_store import init_ohlcv_table
    init_ohlcv_table()
    
    # Restore the single Kite Telegram transport configuration.
    try:
        from app.services.db import get_config
        import app.services.notifications.telegram as _tg_mod
        _tg_token = get_config("telegram_bot_token")
        _tg_chat  = get_config("telegram_chat_id")
        if _tg_token:
            _tg_mod.TELEGRAM_TOKEN = _tg_token
        if _tg_chat:
            _tg_mod.TELEGRAM_CHAT_ID = _tg_chat
        if get_config("telegram_verified") == "1":
            _tg_mod.TELEGRAM_REACHABLE = True
        log.info("Telegram config restored: token=%s chat=%s",
                 "set" if _tg_mod.TELEGRAM_TOKEN else "—",
                 "set" if _tg_mod.TELEGRAM_CHAT_ID else "—")
    except Exception as _e:
        log.warning("Telegram config restore skipped: %s", _e)

    log.info("Startup complete")

    import asyncio

    # Kite tick stream: restart it if its task dies.
    #
    # The stream's own reconnect loop handles a dropped socket. This covers the
    # case that loop cannot: the task itself finishing. `ticker_manager.ensure()`
    # repairs that, but only when something calls it, and its only caller is a
    # subscribe — which the frontend issues when its token set changes. An
    # operator watching a board of live prices changes nothing, so nothing
    # triggered the repair and every price sat on the 30-second REST heartbeat
    # looking alive.
    from app.services.exchanges.kite import ticker_manager as _kite_ticker_manager
    ticker_watchdog_task = asyncio.create_task(_kite_ticker_manager.supervise(interval=30))
    log.info("Kite ticker watchdog started (every 30s)")

    # Gamma Move: levels -> strikes -> trigger, on the strategy's own cadence.
    # The loop is a no-op while the strategy is disabled, which is its default.
    # The loop reconciles against the broker before its first scan, so a restart
    # cannot open a second position in a contract it already holds.
    from app.services.gamma_move_runner import auto_scan_loop as _gamma_move_scan
    gamma_move_task = asyncio.create_task(_gamma_move_scan(interval=300))

    # Adaptive Edge: underlyings -> contracts -> candidates, on a faster cadence
    # because the source is a scalping strategy. Safe to run unconditionally:
    # the loop is a no-op outside the session window, and the strategy's
    # promotion gate refuses live execution regardless of the account's
    # paper/live setting, so this scans and paper-trades but cannot reach real
    # money until somebody promotes it deliberately.
    from app.services.adaptive_edge_runner import auto_scan_loop as _adaptive_edge_scan
    adaptive_edge_task = asyncio.create_task(_adaptive_edge_scan(interval=60))

    log.info("ATM PI auto-arm loop started (every 30s)")
    log.info("Adaptive Edge auto scan loop started (every 60s)")

    # Kite Sterling Kite Engine — background auto-scan of connected Kite
    # accounts (advisory by default; gated auto-exec when the user enables it).
    # First reconcile each account's auto-open guard against the broker's real
    # positions: the guard is DB-persisted across restarts, but a position may
    # have closed/expired while we were down — reconciling prevents both a stale
    # guard (forever-blocked re-entry) and a dropped guard (double-entry).
    from app.services.kite_engine.service import (
        auto_scan_loop as _kite_auto_scan,
        reconcile_all_auto_open as _kite_reconcile_auto_open,
    )
    try:
        await _kite_reconcile_auto_open()
    except Exception as exc:  # noqa: BLE001
        log.warning("Kite auto-open startup reconcile failed: %s", exc)
    kite_engine_task = asyncio.create_task(_kite_auto_scan())
    log.info("Kite Sterling Kite Engine auto-scan loop started (every 5 min)")

    # Kite session keeper — renews access tokens shortly before the 06:00 IST reset
    # for accounts Zerodha issued a refresh_token to. The strategy engines run
    # headless, so without this a token that lapses overnight leaves the 09:15
    # scans sessionless until someone opens the UI.
    from app.services.exchanges.kite.auth import session_keeper_loop as _kite_session_keeper
    kite_session_task = asyncio.create_task(_kite_session_keeper())
    log.info("Kite session keeper started (every 10 min)")

    # Sterling Value-Flow Navigator — independent strategy scanner. It reuses
    # the Kite account/client/instrument caches, but does not depend on the
    # Triple-Supertrend engine being enabled or scanning.
    from app.services.navigator.runtime import auto_scan_loop as _navigator_auto_scan
    navigator_task = asyncio.create_task(_navigator_auto_scan())
    log.info("Value-Flow Navigator auto-scan loop started (every 5 min)")

    # ── Telegram bot + Kite signal alerts ────────────────────────────────────
    from app.services.notifications import telegram_bot as _tg_bot
    tg_bot_task = asyncio.create_task(_tg_bot.poll_loop())
    tg_kite_alert_task = asyncio.create_task(_background_kite_alerts(interval=60))
    log.info("Telegram bot + Kite signal alerts started")

    # ── Live event bus + agents (Phase 3) — only when enable_event_bus is set ──
    from app.core.config import settings as _settings
    if getattr(_settings, "enable_event_bus", False):
        try:
            from app.bus.event_bus import EventBus
            from app.agents import PNLAgent, ReconciliationAgent, Orchestrator
            from app.services import event_emit
            _bus = EventBus()
            _pnl_agent = PNLAgent(bus=_bus)
            _recon_agent = ReconciliationAgent(bus=_bus)
            _orchestrator = Orchestrator(bus=_bus, heartbeat_interval=60.0)
            event_emit.configure(_bus, {
                "pnl": _pnl_agent, "reconciliation": _recon_agent,
                "orchestrator": _orchestrator,
            })
            app.state.event_bus = _bus
            app.state.pnl_agent = _pnl_agent
            app.state.orchestrator = _orchestrator
            await _orchestrator.start()
            log.info("Live event bus + agents started (PNL/Reconciliation, heartbeat 60s)")
        except Exception as exc:
            log.warning("event bus startup failed (non-fatal): %s", exc)

    yield

    try:
        _orch = getattr(app.state, "orchestrator", None)
        if _orch is not None:
            await _orch.stop()
        from app.services import event_emit as _ee
        _ee.reset()
    except Exception as _exc:
        log.debug("suppressed: %s", _exc)

    for _t in (tg_bot_task, tg_kite_alert_task):
        _t.cancel()
        try:
            await _t
        except (Exception, BaseException):
            pass

    kite_engine_task.cancel()
    try:
        await kite_engine_task
    except (Exception, BaseException):
        pass
    kite_session_task.cancel()
    try:
        await kite_session_task
    except (Exception, BaseException):
        pass
    navigator_task.cancel()
    try:
        await navigator_task
    except (Exception, BaseException):
        pass
    gamma_move_task.cancel()
    try:
        await gamma_move_task
    except (Exception, BaseException):
        pass

    adaptive_edge_task.cancel()
    try:
        await adaptive_edge_task
    except (Exception, BaseException):
        pass
    ticker_watchdog_task.cancel()
    try:
        await ticker_watchdog_task
    except asyncio.CancelledError:
        pass

    log.info("Sterling shutdown complete")


def create_app() -> FastAPI:
    app = FastAPI(
        title="Sterling",
        description="Indian markets options platform — paper trading, engine-driven",
        version="0.4.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def _security_headers(request, call_next):
        # Correlation id: honor an inbound one or mint a fresh id, bind it for
        # the request's logging context, and echo it back. (Phase 2 observability)
        cid = request.headers.get("X-Correlation-ID") or new_correlation_id()
        _cid_token = set_correlation_id(cid)
        # Minted BEFORE the handler runs so the handler can stamp it on the tags
        # it emits. A nonce the page never sees is a nonce that blocks the page.
        nonce = secrets.token_urlsafe(16)
        request.state.csp_nonce = nonce
        _nonce_token = set_csp_nonce(nonce)
        try:
            response = await call_next(request)
        finally:
            reset_correlation_id(_cid_token)
            reset_csp_nonce(_nonce_token)
        response.headers["X-Correlation-ID"] = cid
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        # CSP.
        #
        # `default-src 'none'` is right for the API and wrong for the one route
        # that serves a page: the Kite callback. That page is a self-contained
        # HTML document with an inline stylesheet and an inline script, and this
        # header silently blocked both — so it rendered as unstyled user-agent
        # defaults, AND its handoff script never ran. That script is what tells
        # the open Sterling tab the session arrived; without it the app looked
        # like the login had failed, which is what sent people to copy the
        # request_token out of the URL and paste it — a token already spent by
        # the callback, so it could only ever be rejected. One header, the whole
        # symptom.
        #
        # An HTML response therefore gets a nonce rather than `unsafe-inline`:
        # the page's own style and script run, and injected ones still cannot.
        if response.headers.get("content-type", "").startswith("text/html"):
            response.headers["Content-Security-Policy"] = (
                f"default-src 'none'; style-src 'nonce-{nonce}'; "
                f"script-src 'nonce-{nonce}'; img-src data:; "
                "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
            )
        else:
            response.headers["Content-Security-Policy"] = (
                "default-src 'none'; frame-ancestors 'none'"
            )
        return response

    app.include_router(health_router)
    from app.api.v1.endpoints import stream
    app.include_router(stream.router, prefix="/api/v1/stream", tags=["stream"])
    # Generic OHLC candles for any underlying. Removed with the crypto sweep,
    # but 13 MOUNTED Kite components read it through `useCandles` —
    # KiteTicker, InstrumentPane, KiteDashboard, AstroPane and every chart —
    # so every chart in the Kite tab was 404ing.
    app.include_router(candles_router, prefix="/api/v1")
    app.include_router(config_router, prefix="/api/v1")
    app.include_router(backtest_router, prefix="/api/v1")

    # Zerodha Kite (Indian markets) — multi-tenant manual console
    from app.api.v1.endpoints.kite import router as kite_router
    app.include_router(kite_router, prefix="/api/v1")

    # TrueData market data provider endpoints
    from app.api.v1.endpoints.truedata import router as truedata_router
    app.include_router(truedata_router, prefix="/api/v1")

    # Kite-exclusive Sterling Kite Engine options engine (scanner + advisory/auto-exec)
    from app.api.v1.endpoints.kite_engine import router as kite_engine_router
    app.include_router(kite_engine_router, prefix="/api/v1")

    # Kite-specific Telegram alert targets (per-user)
    from app.api.v1.endpoints.kite_telegram import router as kite_telegram_router
    app.include_router(kite_telegram_router, prefix="/api/v1")

    # Sterling Value-Flow Navigator (Kite-only, off by default)
    from app.api.v1.endpoints.navigator import router as navigator_router
    app.include_router(navigator_router, prefix="/api/v1")

    # Advisory 09:15 same-minute relative-volume leader scanner.
    from app.api.v1.endpoints.opening_volume_leaders import router as opening_volume_leaders_router
    app.include_router(opening_volume_leaders_router, prefix="/api/v1")

    from app.api.v1.endpoints.adaptive_edge import router as adaptive_edge_router
    app.include_router(adaptive_edge_router, prefix="/api/v1")

    # Offline market-data lake (kitelake). Storage is relocatable — typically a removable
    # drive — so these endpoints report an absent volume as data, never as an error.
    from app.api.v1.endpoints.datalake import router as datalake_router
    app.include_router(datalake_router, prefix="/api/v1")

    from app.api.v1.endpoints.pcr import router as pcr_router
    app.include_router(pcr_router, prefix="/api/v1")

    from app.api.v1.endpoints.simulation import router as simulation_router
    app.include_router(simulation_router, prefix="/api/v1")

    return app


app = create_app()

if __name__ == "__main__":
    import os
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=True)
