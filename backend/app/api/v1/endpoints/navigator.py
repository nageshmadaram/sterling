"""Sterling Value-Flow Navigator endpoints — `/api/v1/kite/navigator/*`.

Kite-only, off-by-default, advisory-first. Follows the exact
`UserContext`/`get_current_user` auth pattern used by
`/api/v1/kite/engine/*` (see `kite_engine.py`) — scoped to the calling
user, no cross-user access.
"""
from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ValidationError

from app.core.auth import UserContext, get_current_user
from app.core.logging import get_logger
from app.engines.navigator.quality import CandleValidationError
from app.engines.navigator.schemas import NavigatorConfigModel, NavigatorConfigRecord
from app.services.kite_engine import state as kite_engine_state
from app.services.navigator import (
    chart_series, config_store, repository, runtime as nav_runtime, service as nav_service,
)
from app.services.navigator.repository import NavigatorStorageError, RevisionConflict

log = get_logger(__name__)
router = APIRouter(prefix="/kite/navigator", tags=["kite-navigator"])

_SERVER_CAPABILITIES = {
    "engine_sources": ["kite_triple_supertrend"],
    "price_timeframe": "60minute",
    "schema_version": 1,
}


def _default_underlyings(uid: str) -> list[str]:
    """Navigator's first-enable default mirrors whatever the user's
    existing Kite engine already scans — no surprise universe expansion."""
    try:
        return list(kite_engine_state.get_config(uid).scan_indices)
    except Exception:
        return []


class ConfigUpdateRequest(BaseModel):
    config: dict
    expected_revision: int


class ConfigResponse(BaseModel):
    record: NavigatorConfigRecord
    capabilities: dict


def _to_response(record: NavigatorConfigRecord) -> ConfigResponse:
    return ConfigResponse(record=record, capabilities=_SERVER_CAPABILITIES)


@router.get("/config")
async def get_config(user: UserContext = Depends(get_current_user)) -> ConfigResponse:
    record = config_store.get(user.user_id, default_underlyings=_default_underlyings(user.user_id))
    return _to_response(record)


@router.put("/config")
async def put_config(body: ConfigUpdateRequest, user: UserContext = Depends(get_current_user)) -> ConfigResponse:
    try:
        new_config = NavigatorConfigModel.model_validate(body.config)
    except ValidationError as exc:
        raise HTTPException(400, f"INVALID_CONFIG: {exc}") from exc

    if new_config.operating_mode == "gate":
        current = config_store.get(user.user_id, default_underlyings=_default_underlyings(user.user_id))
        if current.calibration_readiness != "ready":
            raise HTTPException(423, "GATE_NOT_CALIBRATED: Navigator gate mode requires a promoted calibration report")

    try:
        record = config_store.save(
            user.user_id, new_config, expected_revision=body.expected_revision,
            default_underlyings=_default_underlyings(user.user_id),
        )
    except RevisionConflict as exc:
        raise HTTPException(409, f"REVISION_CONFLICT: {exc}") from exc
    except NavigatorStorageError as exc:
        raise HTTPException(502, f"NAVIGATOR_STORAGE_ERROR: {exc}") from exc
    if not record.config.enabled:
        await nav_runtime.stop_user_samplers(user.user_id)
    return _to_response(record)


@router.post("/config/validate")
async def validate_config(body: dict, user: UserContext = Depends(get_current_user)) -> dict:
    try:
        config_store.validate(body)
    except ValidationError as exc:
        raise HTTPException(400, f"INVALID_CONFIG: {exc}") from exc
    return {"valid": True}


@router.post("/config/reset")
async def reset_config(user: UserContext = Depends(get_current_user)) -> ConfigResponse:
    try:
        record = config_store.reset(user.user_id, default_underlyings=_default_underlyings(user.user_id))
    except NavigatorStorageError as exc:
        raise HTTPException(502, f"NAVIGATOR_STORAGE_ERROR: {exc}") from exc
    await nav_runtime.stop_user_samplers(user.user_id)
    return _to_response(record)


@router.get("/status")
async def get_status(user: UserContext = Depends(get_current_user)) -> dict:
    status = nav_service.get_status(user.user_id, default_underlyings=_default_underlyings(user.user_id))
    rt = nav_runtime.status(user.user_id)
    return {
        "health": status.health,
        "enabled": status.enabled,
        "operating_mode": status.operating_mode,
        "calibration_readiness": status.calibration_readiness,
        "config_revision": status.config_revision,
        "activation_watermark_ms": status.activation_watermark_ms,
        "components": [
            {"name": c.name, "quality": c.quality, "last_updated_ms": c.last_updated_ms, "reason_codes": c.reason_codes}
            for c in status.components
        ],
        "last_decision_at_ms": status.last_decision_at_ms,
        "sampler_running": status.sampler_running,
        "scanning": rt.scanning,
        "scanning_label": rt.scanning_label,
        "last_scan_ms": rt.last_scan_ms,
        "next_scan_ms": rt.next_scan_ms,
        "signal_count": rt.signal_count,
        "scan_source": rt.scan_source,
        "failures": rt.failures,
        "auto_scan": nav_runtime.is_auto_running(),
    }


@router.get("/activity")
async def get_activity(limit: int = 2000, user: UserContext = Depends(get_current_user)) -> dict:
    rt = nav_runtime.status(user.user_id)
    return {
        "events": nav_runtime.activity(user.user_id, limit),
        "scanning": rt.scanning,
        "scanning_label": rt.scanning_label,
        "last_scan_ms": rt.last_scan_ms,
        "next_scan_ms": rt.next_scan_ms,
        "signal_count": rt.signal_count,
        "auto_scan": nav_runtime.is_auto_running(),
        "failures": rt.failures,
    }


@router.post("/scan")
async def run_scan(user: UserContext = Depends(get_current_user)) -> dict:
    from app.services.exchanges.kite import accounts as kite_accounts
    from app.services.exchanges.kite.errors import KiteTokenError

    record = config_store.get(user.user_id, default_underlyings=_default_underlyings(user.user_id))
    if not record.config.enabled:
        # `scan_user` would return 0 and this would answer with a
        # success-shaped, permanently empty board. Say why instead.
        raise HTTPException(409, "NAVIGATOR_DISABLED: Navigator is off — enable it under Connect → Value-Flow Navigator.")
    acct = kite_accounts.get_active(user.user_id)
    if not acct:
        raise HTTPException(409, "No active Kite account — add credentials and log in first.")
    client = await kite_accounts.acquire_client(acct)
    try:
        await nav_runtime.scan_user(client, user.user_id, acct=acct)
    except KiteTokenError as exc:
        # Mirrors the auto-scan loop: drop the dead session so the next
        # acquire_client rebuilds, and tell the user to reconnect.
        kite_accounts.clear_session(acct.user_id, acct.id)
        await kite_accounts.release_client(acct.id)
        raise HTTPException(409, f"KITE_SESSION_EXPIRED: {exc} — reconnect from the Connect tab.") from exc
    snap = nav_runtime.snapshot(user.user_id)
    rt = nav_runtime.status(user.user_id)
    return {
        "generated_ms": snap.generated_ms,
        "scanning": rt.scanning,
        "scanning_label": rt.scanning_label,
        "rows": [r.model_dump(mode="json") for r in snap.rows],
        "next_scan_ms": rt.next_scan_ms,
        "auto_scan": nav_runtime.is_auto_running(),
    }


@router.post("/scan/cancel")
async def cancel_scan(user: UserContext = Depends(get_current_user)) -> dict:
    cancelled = nav_runtime.cancel(user.user_id)
    snap = nav_runtime.snapshot(user.user_id)
    rt = nav_runtime.status(user.user_id)
    return {
        "cancelled": cancelled,
        "generated_ms": snap.generated_ms,
        "scanning": rt.scanning,
        "scanning_label": rt.scanning_label,
        "rows": [r.model_dump(mode="json") for r in snap.rows],
        "next_scan_ms": rt.next_scan_ms,
        "auto_scan": nav_runtime.is_auto_running(),
    }


@router.get("/snapshot/{underlying}")
async def get_snapshot(underlying: str, user: UserContext = Depends(get_current_user)) -> dict:
    record = config_store.get(user.user_id, default_underlyings=_default_underlyings(user.user_id))
    if not record.config.enabled:
        raise HTTPException(503, "NAVIGATOR_WARMING_UP: Navigator is disabled for this user")
    nav_runtime.snapshot(user.user_id)
    matches = nav_service.get_cached_decisions_for_underlying(user.user_id, underlying)
    if not matches:
        raise HTTPException(503, f"NAVIGATOR_WARMING_UP: no evidence cached yet for {underlying}")
    latest = max(matches, key=lambda d: d.generated_at_ms)
    return latest.model_dump(mode="json")


@router.get("/series/{underlying}")
async def get_series(
    underlying: str, since_bar_close_ms: int = 0, limit: int = 500,
    user: UserContext = Depends(get_current_user),
) -> dict:
    limit = max(1, min(limit, 2000))
    rows = nav_service.get_feature_series(user.user_id, underlying, since_bar_close_ms=since_bar_close_ms, limit=limit)
    return {"underlying": underlying, "points": rows}


@router.get("/chart/{underlying}")
async def get_chart_overlay(
    underlying: str, bars: int = 320, user: UserContext = Depends(get_current_user),
) -> dict:
    """Per-bar Navigator evidence for the chart overlay.

    Deliberately NOT gated on `enabled`: the point of the overlay is to let
    someone see what Navigator would have made of these bars before trusting it
    with anything, and `chart_series` labels a disabled/unconfigured read
    honestly in `notes` rather than pretending it is a recorded decision.
    """
    from app.services.exchanges.kite import accounts as kite_accounts
    from app.services.exchanges.kite.errors import KiteTokenError
    from app.services.kite_engine.universe import load_cfg
    from app.services.navigator import chart_series

    bars = max(80, min(bars, 1000))
    record = config_store.get(user.user_id, default_underlyings=_default_underlyings(user.user_id))
    acct = kite_accounts.get_active(user.user_id)
    if not acct:
        raise HTTPException(409, "No active Kite account — add credentials and log in first.")
    client = await kite_accounts.acquire_client(acct)

    token = chart_series.resolve_underlying_token(load_cfg().get("indices", []), underlying)
    if token is None:
        for exchange in ("NSE", "BSE"):
            try:
                token = await client.resolve_token(underlying, exchange)
                break
            except Exception:  # noqa: BLE001 — try the next exchange, then give up
                token = None
    if not token:
        raise HTTPException(404, f"Could not resolve a spot token for {underlying}.")

    try:
        return await chart_series.build_chart_series(
            client, user.user_id, underlying, token=int(token), record=record, bars=bars,
        )
    except KiteTokenError as exc:
        kite_accounts.clear_session(acct.user_id, acct.id)
        await kite_accounts.release_client(acct.id)
        raise HTTPException(409, f"KITE_SESSION_EXPIRED: {exc} — reconnect from the Connect tab.") from exc
    except CandleValidationError as exc:
        raise HTTPException(422, f"NAVIGATOR_CANDLES_INVALID: {exc}") from exc


async def _resolve_chart_token(client, underlying: str) -> Optional[int]:
    """Spot token for the chart overlay: the static index config first (no
    network), then the NSE/BSE equity dumps for a stock."""
    from app.services.kite_engine.universe import load_cfg

    try:
        token = chart_series.resolve_underlying_token(load_cfg().get("indices", []), underlying)
    except Exception as exc:  # noqa: BLE001 — a bad config file must not 500 the chart
        log.debug("navigator chart: index config lookup failed for %s: %s", underlying, exc)
        token = None
    if token:
        return token
    for exchange in ("NSE", "BSE"):
        try:
            resolved = await client.resolve_token(underlying, exchange)
        except Exception:  # noqa: BLE001 — "not found on this exchange" is expected
            continue
        if resolved:
            return int(resolved)
    return None


@router.get("/chart/{underlying}")
async def get_chart_overlay(
    underlying: str, bars: int = 320, user: UserContext = Depends(get_current_user),
) -> dict:
    """Per-bar Navigator evidence for the chart, on Navigator's own 1H basis.

    Deliberately NOT gated on `enabled`: a user who is deciding whether to turn
    Navigator on should be able to see what it would have drawn, and the
    response says plainly (in `enabled`/`configured`/`notes`) which parts are a
    real recorded evaluation and which are only its maths applied to candles.
    """
    from app.services.exchanges.kite import accounts as kite_accounts
    from app.services.exchanges.kite.errors import KiteTokenError

    bars = max(60, min(bars, 1000))
    record = config_store.get(user.user_id, default_underlyings=_default_underlyings(user.user_id))
    acct = kite_accounts.get_active(user.user_id)
    if not acct:
        raise HTTPException(409, "No active Kite account — add credentials and log in first.")
    client = await kite_accounts.acquire_client(acct)
    token = await _resolve_chart_token(client, underlying)
    if not token:
        raise HTTPException(404, f"No spot instrument found for {underlying} — nothing to compute structure on.")
    try:
        return await chart_series.build_chart_series(
            client, user.user_id, underlying, token=token, record=record, bars=bars,
        )
    except KiteTokenError as exc:
        kite_accounts.clear_session(acct.user_id, acct.id)
        await kite_accounts.release_client(acct.id)
        raise HTTPException(409, f"KITE_SESSION_EXPIRED: {exc} — reconnect from the Connect tab.") from exc


@router.get("/signals")
async def list_signals(
    underlying: Optional[str] = None, before_generated_at_ms: Optional[int] = None,
    before_decision_id: Optional[str] = None, limit: int = 50,
    user: UserContext = Depends(get_current_user),
) -> dict:
    from app.services.simulation import simulation_runner
    if simulation_runner.has_session_view:
        return simulation_runner.get_navigator_signals_response()

    limit = max(1, min(limit, 200))
    try:
        rows = repository.fetch_signal_events_page(
            user.user_id, underlying=underlying, before_generated_at_ms=before_generated_at_ms,
            before_decision_id=before_decision_id, limit=limit,
        )
    except NavigatorStorageError as exc:
        raise HTTPException(502, f"NAVIGATOR_STORAGE_ERROR: {exc}") from exc
    # Tie-safe cursor: a whole scan's decisions can share one
    # generated_at_ms, so the cursor must carry decision_id too or a page
    # boundary inside that tied group would skip the rest of it forever.
    next_cursor = (
        {"generated_at_ms": rows[-1]["generated_at_ms"], "decision_id": rows[-1]["decision_id"]}
        if len(rows) == limit else None
    )
    return {"decisions": [r["payload_json"] for r in rows], "next_cursor": next_cursor}


@router.get("/signals/{decision_id}")
async def get_signal(decision_id: str, user: UserContext = Depends(get_current_user)) -> dict:
    try:
        row = repository.fetch_signal_event(decision_id)
    except NavigatorStorageError as exc:
        raise HTTPException(502, f"NAVIGATOR_STORAGE_ERROR: {exc}") from exc
    if row is None or row["user_id"] != user.user_id:
        raise HTTPException(404, "signal decision not found")
    import json as _json
    return _json.loads(row["payload_json"])


def _criteria_of(state: Optional[dict]) -> Optional[dict]:
    """Pull the stored criteria verdict back out of a calibration-state row."""
    if not state or not state.get("metrics_json"):
        return None
    try:
        import json as _json
        return _json.loads(state["metrics_json"]).get("criteria")
    except (ValueError, TypeError):
        return None


@router.get("/calibration")
async def get_calibration(user: UserContext = Depends(get_current_user)) -> dict:
    try:
        latest = repository.fetch_latest_calibration_state(user.user_id)
    except NavigatorStorageError as exc:
        raise HTTPException(502, f"NAVIGATOR_STORAGE_ERROR: {exc}") from exc
    record = config_store.get(user.user_id, default_underlyings=_default_underlyings(user.user_id))
    return {
        "calibration_readiness": record.calibration_readiness,
        "calibration_report_id": record.calibration_report_id,
        "revision": record.revision,
        "latest_report": latest,
        "criteria": _criteria_of(latest),
    }


@router.post("/calibration/report")
async def generate_calibration_report(user: UserContext = Depends(get_current_user)) -> dict:
    """Score every decision Navigator has made so far against what the market
    actually did next, store the resulting report, and return it with its
    §19.5 criteria verdict.

    Read-and-measure only — generating a report NEVER promotes anything, even
    when every criterion passes. Promotion is the separate POST below.
    """
    uid = user.user_id
    defaults = _default_underlyings(uid)
    record = config_store.get(uid, default_underlyings=defaults)

    from app.services.exchanges.kite import accounts as kite_accounts
    from app.services.kite_engine import state as kite_state
    from app.services.kite_engine.universe import build_universe, select_scan_universe

    acct = kite_accounts.get_active(uid)
    if not acct:
        raise HTTPException(409, "No active Kite account — add credentials and log in first.")
    try:
        # Warm, cached client (no per-call close) — same helper the engine
        # endpoints use.
        client = await kite_accounts.acquire_client(acct)
        engine_cfg = kite_state.get_config(uid)
        nfo, bfo, nse, bse = await asyncio.gather(
            client.search_instruments("", "NFO", limit=1_000_000),
            client.search_instruments("", "BFO", limit=1_000_000),
            client.search_instruments("", "NSE", limit=1_000_000),
            client.search_instruments("", "BSE", limit=1_000_000),
        )
        full_universe = build_universe(nfo_instruments=nfo, bfo_instruments=bfo, equities=nse + bse)
        cfg = record.config
        # Resolve the same universe the live pass would, so a decision's
        # forward prices are read from the instrument it was actually made on.
        if cfg.scan_scope_mode == "custom":
            selected = select_scan_universe(
                full_universe, indices=cfg.scan_indices,
                stocks=cfg.scan_stocks, all_stocks=cfg.scan_all_stocks)
        else:
            selected = select_scan_universe(
                full_universe, indices=engine_cfg.scan_indices,
                stocks=engine_cfg.scan_stocks, all_stocks=engine_cfg.scan_all_stocks)
        report, criteria, state = await nav_service.generate_calibration_report(
            client, uid, underlying_tokens={u.name: u.token for u in selected},
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        log.warning("calibration report generation failed for %s: %s", uid, exc)
        raise HTTPException(502, f"CALIBRATION_REPORT_FAILED: {exc}") from exc

    try:
        repository.insert_calibration_state(state)
    except NavigatorStorageError as exc:
        raise HTTPException(502, f"NAVIGATOR_STORAGE_ERROR: {exc}") from exc

    return {
        "report_id": state["report_id"],
        "report": report,
        "criteria": criteria,
        "calibration_readiness": record.calibration_readiness,
    }


class PromoteRequest(BaseModel):
    report_id: str
    expected_revision: int


@router.post("/calibration/promote")
async def promote_calibration(
    body: PromoteRequest, user: UserContext = Depends(get_current_user),
) -> ConfigResponse:
    """Mark calibration ready, against a specific reviewed report.

    Refuses (409) unless that exact report exists AND every §19.5 criterion
    in it passes — the server re-checks rather than trusting the client's
    view of eligibility. Promotion unlocks `gate` as a CHOICE; it does not
    switch the operating mode and never touches auto-execute (§19.5:
    "Promotion never turns on auto_execute").
    """
    uid = user.user_id
    try:
        latest = repository.fetch_latest_calibration_state(uid)
    except NavigatorStorageError as exc:
        raise HTTPException(502, f"NAVIGATOR_STORAGE_ERROR: {exc}") from exc

    if not latest or latest.get("report_id") != body.report_id:
        raise HTTPException(
            409, "REPORT_NOT_CURRENT: generate a fresh calibration report and review it "
                 "before promoting — the id given is not the latest stored report",
        )
    criteria = _criteria_of(latest)
    if not criteria or not criteria.get("eligible"):
        failed = [c["label"] for c in (criteria or {}).get("criteria", []) if not c.get("passed")]
        raise HTTPException(
            409, "CRITERIA_NOT_MET: this report does not satisfy the promotion criteria "
                 f"({'; '.join(failed) or 'no criteria recorded'})",
        )

    try:
        record = config_store.promote_calibration(
            uid, report_id=body.report_id, expected_revision=body.expected_revision,
            default_underlyings=_default_underlyings(uid),
        )
    except RevisionConflict as exc:
        raise HTTPException(409, f"REVISION_CONFLICT: {exc}") from exc
    except NavigatorStorageError as exc:
        raise HTTPException(502, f"NAVIGATOR_STORAGE_ERROR: {exc}") from exc
    return _to_response(record)


class DemoteRequest(BaseModel):
    expected_revision: int


@router.post("/calibration/demote")
async def demote_calibration(
    body: DemoteRequest, user: UserContext = Depends(get_current_user),
) -> ConfigResponse:
    """Revoke a promotion — back to not-ready, and off `gate` mode if it was
    selected (a gate that can never be satisfied would silently block every
    order rather than obviously reverting to advisory)."""
    try:
        record = config_store.demote_calibration(
            user.user_id, expected_revision=body.expected_revision,
            default_underlyings=_default_underlyings(user.user_id),
        )
    except RevisionConflict as exc:
        raise HTTPException(409, f"REVISION_CONFLICT: {exc}") from exc
    except NavigatorStorageError as exc:
        raise HTTPException(502, f"NAVIGATOR_STORAGE_ERROR: {exc}") from exc
    return _to_response(record)
