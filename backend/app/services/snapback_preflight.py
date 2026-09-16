"""Block 4: one place that decides whether this process may run at all.

snapback_startup.run_startup_preflight answers a narrower question — may the runner
start. This module answers the operational one: is this machine, this clock, this
disk and this database fit to produce evidence today. The checks are separated
because each of them fails silently in a way that still looks healthy:

    a clock 40s fast makes every quote freshness test wrong
    a full disk makes an append-only write a partial write
    a schema drift makes an old column mean a new thing
    a corrupt page makes a SELECT return fewer rows, not an error

Every check fails closed. A probe that raises counts as failed, never as passed.
"""

from __future__ import annotations

import logging
import os
import shutil
import sqlite3
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

log = logging.getLogger(__name__)

# Below this the evidence database cannot be confidently appended to, backed up and
# checkpointed for the rest of a session.
DEFAULT_MIN_FREE_DISK_BYTES = 2 * 1024**3


@dataclass
class PreflightCheck:
    code: str
    passed: bool
    required: bool = True
    details: Any = None


# A failure in one of these means the evidence file itself needs attention — a
# restore, or a fresh database — rather than a configuration fix. The two states
# tell an operator to do different things.
_RECOVERY_CHECKS = frozenset({
    "database", "evidence_meta", "disk", "evidence_authority",
})


@dataclass
class PreflightResult:
    passed: bool
    checks: List[PreflightCheck] = field(default_factory=list)

    @property
    def failures(self) -> List[PreflightCheck]:
        return [c for c in self.checks if not c.passed]

    @property
    def required_failures(self) -> List[PreflightCheck]:
        return [c for c in self.checks if c.required and not c.passed]

    @property
    def errors(self) -> List[str]:
        return [c.code for c in self.required_failures]

    @property
    def may_start_runner(self) -> bool:
        """The runner and /health/ready read the same answer.

        Split, they could contradict each other: readiness 503 on a bad clock or
        the wrong experiment's database while the runner started anyway and wrote
        evidence under exactly those conditions.
        """
        return self.passed

    @property
    def status(self) -> str:
        if self.passed:
            return "READY"
        if any(c.code in _RECOVERY_CHECKS for c in self.required_failures):
            return "RECOVERY_REQUIRED"
        return "HALTED"

    @property
    def details(self) -> Dict[str, Any]:
        return {c.code: c.details for c in self.checks}

    def as_dict(self) -> Dict[str, Any]:
        return {
            "passed": self.passed,
            "status": self.status,
            "may_start_runner": self.may_start_runner,
            "failed_checks": [c.code for c in self.failures],
            "checks": [
                {
                    "code": c.code,
                    "passed": c.passed,
                    "required": c.required,
                    "details": c.details,
                }
                for c in self.checks
            ],
        }


# --------------------------------------------------------------------- clock


def _ntp_synchronised() -> Tuple[bool, str]:
    """Ask the host whether its clock is disciplined by a time source.

    Freshness, session boundaries and provider-timestamp ordering are all decided
    against this clock. An undisciplined clock does not report itself as wrong.
    """
    try:
        out = subprocess.run(
            ["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
            capture_output=True, text=True, timeout=5,
        )
    except Exception as exc:  # noqa: BLE001 - probe failure is a failed check
        return False, f"timedatectl_unavailable:{exc}"

    value = (out.stdout or "").strip().lower()
    if value == "yes":
        return True, "synchronised"
    return False, f"not_synchronised:{value or 'unknown'}"


def check_clock() -> Tuple[bool, Dict[str, Any]]:
    if os.environ.get("STERLING_SKIP_CLOCK_CHECK", "").lower() in ("1", "true", "yes"):
        return True, {"clock_sync_ok": True, "clock_sync_detail": "skipped_by_env"}

    ok, detail = _ntp_synchronised()
    return ok, {"clock_sync_ok": ok, "clock_sync_detail": detail}


# ---------------------------------------------------------------------- disk


def _default_disk_paths() -> List[Path]:
    from app.services.snapback_observation_warehouse import DEFAULT_OBSERVATIONS_DB_PATH

    db_path = Path(
        os.environ.get("STERLING_OBSERVATIONS_DB_PATH", DEFAULT_OBSERVATIONS_DB_PATH)
    )
    paths = [db_path.parent]
    backup_dir = os.environ.get("STERLING_BACKUP_DIR")
    if backup_dir:
        paths.append(Path(backup_dir))
    return paths


def check_disk(paths: Optional[Sequence[Path]] = None) -> Tuple[bool, Dict[str, Any]]:
    minimum = int(
        os.environ.get("STERLING_MIN_FREE_DISK_BYTES", DEFAULT_MIN_FREE_DISK_BYTES)
    )
    targets = list(paths) if paths is not None else _default_disk_paths()

    free_by_path: Dict[str, Any] = {}
    worst: Optional[int] = None
    ok = True

    for target in targets:
        try:
            free = int(shutil.disk_usage(str(target)).free)
        except Exception as exc:  # noqa: BLE001
            free_by_path[str(target)] = f"unreadable:{exc}"
            ok = False
            continue
        free_by_path[str(target)] = free
        worst = free if worst is None else min(worst, free)
        if free < minimum:
            ok = False

    if worst is None:
        ok = False

    return ok, {
        "free_bytes": worst if worst is not None else 0,
        "min_free_bytes": minimum,
        "paths": free_by_path,
    }


# ------------------------------------------------------------------ database


def check_database(db_path: Optional[str] = None) -> Tuple[bool, Dict[str, Any]]:
    """Integrity, schema version and writability of the evidence file."""
    from app.services.snapback_observation_warehouse import (
        DEFAULT_OBSERVATIONS_DB_PATH, EVIDENCE_SCHEMA_VERSION,
    )

    path = db_path or os.environ.get(
        "STERLING_OBSERVATIONS_DB_PATH", DEFAULT_OBSERVATIONS_DB_PATH
    )
    details: Dict[str, Any] = {"db_path": path, "writable": False}

    if not os.path.exists(path):
        details["error"] = "database_missing"
        return False, details

    try:
        conn = sqlite3.connect(path, timeout=10.0)
    except Exception as exc:  # noqa: BLE001
        details["error"] = f"open_failed:{exc}"
        return False, details

    try:
        conn.execute("PRAGMA busy_timeout=10000")
        try:
            quick = conn.execute("PRAGMA quick_check").fetchone()
            details["quick_check"] = str(quick[0]) if quick else "unknown"
        except Exception as exc:  # noqa: BLE001
            details["quick_check"] = f"failed:{exc}"

        if details.get("quick_check") != "ok":
            return False, details

        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        details["schema_version"] = version
        details["expected_schema_version"] = EVIDENCE_SCHEMA_VERSION
        if version != EVIDENCE_SCHEMA_VERSION:
            details["error"] = "schema_version_mismatch"
            return False, details

        with conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS _preflight_write_check (checked_at TEXT)"
            )
            conn.execute("DELETE FROM _preflight_write_check")
        details["writable"] = True
    except Exception as exc:  # noqa: BLE001
        details["error"] = f"probe_failed:{exc}"
        return False, details
    finally:
        conn.close()

    return True, details


# ------------------------------------------------------------- evidence meta


def check_evidence_meta(
    *, warehouse: Any, expected: Dict[str, Any],
) -> Tuple[bool, Dict[str, Any]]:
    """The file must say which experiment it holds, and say the expected one."""
    try:
        meta = warehouse.read_evidence_meta()
    except Exception as exc:  # noqa: BLE001
        return False, {"error": f"evidence_meta_unreadable:{exc}"}

    if not meta:
        return False, {"error": "evidence_meta_missing"}

    mismatches = {
        key: {"expected": value, "actual": meta.get(key)}
        for key, value in expected.items()
        if meta.get(key) != value
    }
    if mismatches:
        return False, {"error": "evidence_meta_mismatch", "mismatches": mismatches}

    return True, {"evidence_meta": meta}


# ----------------------------------------------------------- default probes


def _default_build_identity() -> Tuple[bool, List[str]]:
    from app.services.snapback_identity import verify_build_identity

    return verify_build_identity()


def _default_worktree_clean() -> Tuple[bool, List[str]]:
    """A dirty worktree means the running code is not the tagged code.

    Evidence stamped with a build SHA whose worktree had uncommitted edits records
    a provenance that cannot be reproduced.
    """
    if os.environ.get("STERLING_ALLOW_DIRTY_WORKTREE", "").lower() in ("1", "true", "yes"):
        return True, ["dirty_worktree_allowed_by_env"]
    try:
        out = subprocess.run(
            ["git", "status", "--porcelain"],
            capture_output=True, text=True, timeout=15,
            cwd=str(Path(__file__).resolve().parents[3]),
        )
    except Exception as exc:  # noqa: BLE001
        return False, [f"git_status_failed:{exc}"]

    if out.returncode != 0:
        return False, [f"git_status_rc={out.returncode}"]

    dirty = [line for line in (out.stdout or "").splitlines() if line.strip()]
    return (not dirty), dirty[:20]


def _default_config_identity() -> Tuple[bool, List[str]]:
    from app.services.snapback_startup import run_startup_preflight

    result = run_startup_preflight()
    return result.may_start_runner, list(result.errors)


def _default_database() -> Tuple[bool, Dict[str, Any]]:
    return check_database()


def _default_evidence_meta() -> Tuple[bool, Dict[str, Any]]:
    from app.services.snapback_prospective_collector import SnapbackObservationWarehouse

    expected_experiment = os.environ.get("STERLING_EXPERIMENT_ID")
    if not expected_experiment:
        return False, {"error": "STERLING_EXPERIMENT_ID_not_set"}

    return check_evidence_meta(
        warehouse=SnapbackObservationWarehouse(),
        expected={"experiment_id": expected_experiment},
    )


def check_dataset_start() -> Tuple[bool, Dict[str, Any]]:
    """The experiment must declare when its authoritative sample begins.

    Without it, classify_signal_authority()'s `before_dataset_start` rule can
    never fire, and the only thing keeping an older signal out of the sample is
    the single-session recency check. That is one rule guarding a boundary that
    decides whether the whole experiment is prospective.
    """
    raw = (os.environ.get("STERLING_DATASET_START") or "").strip()
    if not raw:
        return False, {"error": "STERLING_DATASET_START not set"}

    from datetime import datetime as _dt

    try:
        moment = _dt.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return False, {"error": f"STERLING_DATASET_START unparseable: {raw!r}"}

    if moment.tzinfo is None:
        return False, {
            "error": f"STERLING_DATASET_START has no timezone: {raw!r}",
        }

    return True, {"dataset_start": moment.isoformat()}


def _default_dataset_start() -> Tuple[bool, Dict[str, Any]]:
    return check_dataset_start()


def _default_evidence_authority() -> Tuple[bool, Dict[str, Any]]:
    """No stored row may claim an authority its source denies."""
    from app.services.snapback_prospective_collector import SnapbackObservationWarehouse

    try:
        contradictions = SnapbackObservationWarehouse().contradictory_authority_rows()
    except Exception as exc:  # noqa: BLE001
        return False, {"error": f"authority_scan_failed:{exc}"}

    if contradictions:
        return False, {
            "error": "rows claim authority their source denies",
            "count": len(contradictions),
            "examples": contradictions[:5],
        }
    return True, {"contradictions": 0}


def _default_calendar() -> Tuple[bool, Dict[str, Any]]:
    from app.services.snapback_health import _probe_calendar

    answered, trading_day = _probe_calendar()
    return answered, {"calendar_answered": answered, "trading_day": trading_day}


def _default_family_account() -> Tuple[bool, Dict[str, Any]]:
    from app.services.snapback_family_account import family_account_health

    health = family_account_health()
    # Identity, not connectedness: a bound account whose broker session has not
    # been opened yet is correct at 08:00, and must not read as unbound.
    return bool(health.get("family_account_identity_ok")), health


def _default_allocation_capital() -> Tuple[bool, Dict[str, Any]]:
    raw = os.environ.get("STERLING_ALLOCATION_CAPITAL_INR")
    if not raw:
        return False, {"error": "STERLING_ALLOCATION_CAPITAL_INR_not_set"}
    try:
        value = float(raw)
    except ValueError:
        return False, {"error": f"unparseable:{raw!r}"}
    if value <= 0:
        return False, {"error": f"non_positive:{value}"}
    return True, {"allocation_capital": value}


def _default_clock() -> Tuple[bool, Dict[str, Any]]:
    return check_clock()


def _default_disk() -> Tuple[bool, Dict[str, Any]]:
    return check_disk()


def _default_backup_writable() -> Tuple[bool, Dict[str, Any]]:
    backup_dir = os.environ.get("STERLING_BACKUP_DIR")
    if not backup_dir:
        return True, {"backup_dir": None, "note": "no backup dir configured"}
    try:
        Path(backup_dir).mkdir(parents=True, exist_ok=True)
        probe = Path(backup_dir) / ".preflight_write_probe"
        probe.write_text("ok")
        probe.unlink()
    except Exception as exc:  # noqa: BLE001
        return False, {"error": f"backup_dir_not_writable:{exc}"}
    return True, {"backup_dir": backup_dir}


def _default_lifecycle() -> Tuple[bool, Dict[str, Any]]:
    from app.services.snapback_prospective_collector import SnapbackObservationWarehouse
    from app.services.snapback_stale import stale_lifecycle_state

    try:
        report = stale_lifecycle_state(SnapbackObservationWarehouse())
    except Exception as exc:  # noqa: BLE001
        return False, {"error": f"lifecycle_probe_failed:{exc}"}
    return True, {"stale": report}


# The order is the order they are reported in. `required` says whether a failure
# blocks startup; nothing is optional today, but the field keeps that decision
# explicit rather than implied by position.
_CHECK_ORDER: Tuple[Tuple[str, str, bool], ...] = (
    ("build_identity", "build_identity_fn", True),
    ("worktree_clean", "worktree_clean_fn", True),
    ("config_identity", "config_identity_fn", True),
    ("database", "database_fn", True),
    ("evidence_meta", "evidence_meta_fn", True),
    ("dataset_start", "dataset_start_fn", True),
    ("evidence_authority", "evidence_authority_fn", True),
    ("calendar", "calendar_fn", True),
    ("family_account", "family_account_fn", True),
    ("allocation_capital", "allocation_capital_fn", True),
    ("clock", "clock_fn", True),
    ("disk", "disk_fn", True),
    ("backup_writable", "backup_writable_fn", False),
    ("lifecycle", "lifecycle_fn", False),
)

_DEFAULTS: Dict[str, Callable[[], Tuple[bool, Any]]] = {
    "build_identity_fn": _default_build_identity,
    "worktree_clean_fn": _default_worktree_clean,
    "config_identity_fn": _default_config_identity,
    "database_fn": _default_database,
    "evidence_meta_fn": _default_evidence_meta,
    "dataset_start_fn": _default_dataset_start,
    "evidence_authority_fn": _default_evidence_authority,
    "calendar_fn": _default_calendar,
    "family_account_fn": _default_family_account,
    "allocation_capital_fn": _default_allocation_capital,
    "clock_fn": _default_clock,
    "disk_fn": _default_disk,
    "backup_writable_fn": _default_backup_writable,
    "lifecycle_fn": _default_lifecycle,
}


def run_preflight(**overrides: Callable[[], Tuple[bool, Any]]) -> PreflightResult:
    """Run every startup condition. A raising probe counts as a failure."""
    unknown = set(overrides) - set(_DEFAULTS)
    if unknown:
        raise TypeError(f"unknown preflight override(s): {sorted(unknown)}")

    checks: List[PreflightCheck] = []
    for code, kwarg, required in _CHECK_ORDER:
        probe = overrides.get(kwarg) or _DEFAULTS[kwarg]
        try:
            passed, details = probe()
        except Exception as exc:  # noqa: BLE001 - unknown is not healthy
            checks.append(
                PreflightCheck(
                    code=code, passed=False, required=required,
                    details={"error": f"{type(exc).__name__}: {exc}"},
                )
            )
            continue
        checks.append(
            PreflightCheck(
                code=code, passed=bool(passed), required=required, details=details,
            )
        )

    passed = all(c.passed for c in checks if c.required)
    if not passed:
        log.error(
            "Snapback preflight FAILED: %s",
            [c.code for c in checks if c.required and not c.passed],
        )
    return PreflightResult(passed=passed, checks=checks)
