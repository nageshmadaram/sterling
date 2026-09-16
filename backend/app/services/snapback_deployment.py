"""Block 7: the deployment shape a family machine must have.

Family Mode exists so exactly one strategy can move exposure on the family
account. Every other auto-runner is a second way for a position to appear, and
none of them announce themselves: the money is committed before anybody reads a
log line. The same silence applies to a socket bound to every interface on a home
network, and to a shutdown that tears the process down between an insert and its
commit.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, FrozenSet, Optional

log = logging.getLogger(__name__)

# Every background loop that can reach the broker or open a position. Listed by
# module name so main.py and this contract cannot drift apart silently.
_SUPPRESSED_IN_FAMILY_MODE: FrozenSet[str] = frozenset({
    "gamma_move_runner",
    "adaptive_edge_runner",
    "intraday_runner",
    "kite_engine",
    "navigator",
})


def suppressed_runners_in_family_mode() -> FrozenSet[str]:
    """Runners that must not start when Family Mode is on."""
    return _SUPPRESSED_IN_FAMILY_MODE


def bind_host() -> str:
    """Where the API listens. Closed by default.

    0.0.0.0 on a home network exposes an authenticated broker session to every
    other device on that network. Opening it is a deliberate act, so it takes an
    explicit environment variable.
    """
    return os.environ.get("STERLING_BIND_HOST", "127.0.0.1")


def record_shutdown(*, reason: str = "SIGTERM", warehouse: Any = None) -> Optional[Dict[str, Any]]:
    """Record that this process stopped on purpose.

    A process that crashed and one that was stopped cleanly leave identical
    evidence behind unless one of them says so. Without this, a gap in the
    session record cannot be told apart from a runtime that died mid-session.
    """
    stopped_at = datetime.now(timezone.utc).isoformat()
    payload = {"event": "RUNTIME_SHUTDOWN", "reason": reason, "stopped_at": stopped_at}

    try:
        if warehouse is None:
            from app.services.snapback_prospective_collector import (
                SnapbackObservationWarehouse,
            )

            warehouse = SnapbackObservationWarehouse()
        from app.services.snapback_health import record_cycle

        record_cycle("runtime_shutdown")
    except Exception as exc:  # noqa: BLE001 - shutdown must never raise
        log.warning("Snapback shutdown record failed: %s", exc)
        return None

    log.warning("Snapback runtime stopped (%s) at %s", reason, stopped_at)
    return payload
