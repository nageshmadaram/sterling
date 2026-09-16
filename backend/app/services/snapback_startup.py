"""Startup preflight for the frozen Snapback prospective runtime.

The runner may only start when every invariant below holds. A failure is reported
as RECOVERY_REQUIRED or HALTED and blocks the runner; it is never downgraded into a
silent "defaults OFF" runtime, because that failure mode looks healthy from the
outside while the strategy generates nothing.

    prospective DB writable
    AND frozen manifest valid
    AND Snapback config load successful (store reachable)
    AND frozen config hash matches
    -> runner may start
"""

from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional

log = logging.getLogger(__name__)

# The config hash frozen into the Snapback reality manifest.
FROZEN_CONFIG_HASH = "6ecbeb53e9768a91"


@dataclass
class PreflightResult:
    status: str
    may_start_runner: bool
    errors: List[str] = field(default_factory=list)
    details: dict = field(default_factory=dict)


def _default_db_writable() -> bool:
    from app.services.snapback_observation_warehouse import DEFAULT_OBSERVATIONS_DB_PATH

    path = os.environ.get("STERLING_OBSERVATIONS_DB_PATH", DEFAULT_OBSERVATIONS_DB_PATH)
    try:
        conn = sqlite3.connect(path, timeout=10.0)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute(
                "CREATE TABLE IF NOT EXISTS _preflight_write_check (checked_at TEXT)"
            )
            conn.execute("DELETE FROM _preflight_write_check")
            conn.commit()
        finally:
            conn.close()
        return True
    except Exception as exc:
        log.error("Snapback preflight: evidence database not writable (%s): %s", path, exc)
        return False


def _default_manifest() -> tuple:
    from app.services.snapback_health import _probe_manifest

    return _probe_manifest()


def _default_config_store_available() -> bool:
    from app.services import db

    return bool(getattr(db, "is_available", lambda: False)())


def _default_config() -> Any:
    from app.services.snapback import get_config

    return get_config("default")


def _default_config_hash(cfg: Any) -> str:
    from app.engines.snapback.manifest import compute_config_hash

    return compute_config_hash(cfg)


def run_startup_preflight(
    *,
    db_writable_fn: Optional[Callable[[], bool]] = None,
    manifest_fn: Optional[Callable[[], tuple]] = None,
    config_store_available_fn: Optional[Callable[[], bool]] = None,
    config_fn: Optional[Callable[[], Any]] = None,
    config_hash_fn: Optional[Callable[[Any], str]] = None,
    frozen_config_hash: str = FROZEN_CONFIG_HASH,
) -> PreflightResult:
    """Verify every startup invariant. Returns the status and whether the runner may start."""
    db_writable_fn = db_writable_fn or _default_db_writable
    manifest_fn = manifest_fn or _default_manifest
    config_store_available_fn = config_store_available_fn or _default_config_store_available
    config_fn = config_fn or _default_config
    config_hash_fn = config_hash_fn or _default_config_hash

    errors: List[str] = []
    details: dict = {}
    recovery = False

    # 1. Evidence database writable.
    try:
        if not db_writable_fn():
            errors.append("evidence_db_not_writable")
            recovery = True
    except Exception as exc:
        errors.append(f"evidence_db_probe_failed:{exc}")
        recovery = True

    # 2. The executing build must be the declared one.
    try:
        from app.services.snapback_identity import verify_build_identity

        build_ok, build_reasons = verify_build_identity()
        details["build_identity"] = list(build_reasons)
        if not build_ok:
            errors.append("build_identity_mismatch")
    except Exception as exc:
        errors.append(f"build_identity_probe_failed:{exc}")

    # 3. Frozen manifest valid.
    try:
        manifest_ok, manifest_reasons = manifest_fn()
        details["manifest_reasons"] = list(manifest_reasons or [])
        if not manifest_ok:
            errors.append("manifest_invalid")
    except Exception as exc:
        errors.append(f"manifest_probe_failed:{exc}")

    # 4. Config store reachable — an unreachable store silently disables the engine.
    store_ok = False
    try:
        store_ok = bool(config_store_available_fn())
        if not store_ok:
            errors.append("config_store_unavailable")
    except Exception as exc:
        errors.append(f"config_store_probe_failed:{exc}")

    # 5. Config loads, is enabled, and matches the frozen hash.
    cfg = None
    try:
        cfg = config_fn()
    except Exception as exc:
        errors.append(f"config_load_failed:{exc}")

    if cfg is not None:
        if not bool(getattr(cfg, "enabled", False)):
            errors.append("snapback_disabled")
        try:
            actual_hash = config_hash_fn(cfg)
            details["config_hash"] = actual_hash
            details["frozen_config_hash"] = frozen_config_hash
            if actual_hash != frozen_config_hash:
                errors.append("config_hash_mismatch")
        except Exception as exc:
            errors.append(f"config_hash_failed:{exc}")

    if not errors:
        return PreflightResult(status="READY", may_start_runner=True, details=details)

    status = "RECOVERY_REQUIRED" if recovery else "HALTED"
    log.error("Snapback preflight FAILED (%s): %s", status, errors)
    return PreflightResult(
        status=status, may_start_runner=False, errors=errors, details=details
    )
