"""Operational (non-strategy) endpoints for the frozen Snapback prospective runtime."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from app.services.snapback_health import get_prospective_health

log = logging.getLogger(__name__)

router = APIRouter(tags=["snapback-ops"])


@router.get("/snapback/prospective/health")
async def prospective_health() -> dict:
    """Return server-truth health of the prospective paper runtime.

    A failing probe is reported as HALTED with HTTP 503; it is never converted
    into a green response.
    """
    try:
        return get_prospective_health()
    except Exception as exc:
        log.exception("Snapback prospective health probe failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail={
                "status": "HALTED",
                "healthy": False,
                "unresolved_errors": ["health_probe_failed"],
                "error": str(exc),
                "generated_at": datetime.now(timezone.utc).isoformat(),
            },
        )
