"""Family Operations state: the evidence verdict and the STOP ALL NEW TRADES switch.

The switch blocks new exposure only. It never deletes state, never abandons open
positions, and never stops exit or reconciliation processing.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)

_lock = threading.Lock()

_STATE_FILE_ENV = "STERLING_NEW_TRADES_HALT_PATH"
_DEFAULT_STATE_FILE = "data/snapback/new_trades_halt.json"


def _state_path() -> Path:
    return Path(os.environ.get(_STATE_FILE_ENV, _DEFAULT_STATE_FILE))


def new_trades_halted() -> bool:
    """Whether the family stop switch is engaged. Unreadable state fails closed."""
    path = _state_path()
    try:
        if not path.exists():
            return False
        return bool(json.loads(path.read_text(encoding="utf-8")).get("halted", False))
    except Exception as exc:
        log.warning("Family ops: halt state unreadable (%s); treating as halted", exc)
        return True


def set_new_trades_halted(halted: bool, reason: str = "") -> Dict[str, Any]:
    """Engage or release the stop switch. Durable across restarts."""
    payload = {
        "halted": bool(halted),
        "reason": reason,
        "changed_at": datetime.now(timezone.utc).isoformat(),
    }
    path = _state_path()
    with _lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
    log.warning("Family ops: new trades halted=%s reason=%s", halted, reason)
    return payload


def get_family_evidence_verdict() -> Dict[str, Any]:
    """Current authoritative verdict over the frozen prospective evidence."""
    try:
        # The family screen reads the recorded verdict; it does not re-evaluate the
        # gate on every page load, so the answer is stable between packages.
        from app.services.snapback_observation_warehouse import SnapbackObservationWarehouse
        from app.services.snapback_promotion import PromotionService

        record = PromotionService().latest(warehouse=SnapbackObservationWarehouse())
        if record is None:
            return {
                "verdict": "INCONCLUSIVE",
                "completed_trades": 0,
                "total_sessions": 0,
                "missing_requirements": ["no promotion evaluation has been recorded yet"],
                "net_pnl": None,
            }

        return {
            "verdict": record.verdict,
            "completed_trades": record.completed_trades,
            "total_sessions": record.observed_sessions,
            "missing_requirements": list(record.missing_requirements or record.reasons),
            "net_pnl": None,
            "evaluated_at": record.evaluated_at,
        }
    except Exception as exc:
        log.warning("Family ops: evidence verdict unavailable: %s", exc)
        return {"verdict": "INCONCLUSIVE", "error": str(exc), "net_pnl": None}
