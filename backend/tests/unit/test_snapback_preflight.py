"""Block 4: every startup condition is checked, and unknown fails closed.

Quote freshness is meaningless if the clock is wrong; evidence is worthless if the
disk fills mid-write; a schema drift silently changes what a column means. None of
these announce themselves at the moment they matter.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path

import pytest

from app.services.snapback_preflight import (
    PreflightCheck,
    PreflightResult,
    check_clock,
    check_database,
    check_disk,
    check_evidence_meta,
    run_preflight,
)


def _passing_checks(**over):
    base = dict(
        build_identity_fn=lambda: (True, []),
        worktree_clean_fn=lambda: (True, []),
        config_identity_fn=lambda: (True, []),
        database_fn=lambda: (True, {"schema_version": 1}),
        evidence_meta_fn=lambda: (True, {}),
        dataset_start_fn=lambda: (True, {}),
        evidence_authority_fn=lambda: (True, {}),
        calendar_fn=lambda: (True, {}),
        family_account_fn=lambda: (True, {}),
        allocation_capital_fn=lambda: (True, {"allocation_capital": 100_000.0}),
        clock_fn=lambda: (True, {"clock_offset_ms": 12}),
        disk_fn=lambda: (True, {"free_bytes": 50 * 1024**3}),
        backup_writable_fn=lambda: (True, {}),
        lifecycle_fn=lambda: (True, {}),
    )
    base.update(over)
    return base


def test_all_checks_passing_permits_startup():
    result = run_preflight(**_passing_checks())

    assert isinstance(result, PreflightResult)
    assert result.passed is True
    assert all(check.passed for check in result.checks)


@pytest.mark.parametrize("failing", [
    "build_identity_fn", "worktree_clean_fn", "config_identity_fn", "database_fn",
    "evidence_meta_fn", "calendar_fn", "family_account_fn", "allocation_capital_fn",
    "clock_fn", "disk_fn",
])
def test_any_required_failure_blocks_startup(failing):
    kwargs = _passing_checks(**{failing: lambda: (False, {"why": "broken"})})

    result = run_preflight(**kwargs)

    assert result.passed is False
    assert any(not c.passed and c.required for c in result.checks)


def test_a_check_that_raises_is_treated_as_failed():
    def explode():
        raise RuntimeError("probe blew up")

    result = run_preflight(**_passing_checks(clock_fn=explode))

    assert result.passed is False
    failed = next(c for c in result.checks if c.code == "clock")
    assert failed.passed is False
    assert "probe blew up" in str(failed.details)


def test_the_result_names_every_check():
    result = run_preflight(**_passing_checks())

    codes = {c.code for c in result.checks}

    for expected in ("build_identity", "worktree_clean", "config_identity",
                     "database", "evidence_meta", "calendar", "family_account",
                     "allocation_capital", "clock", "disk"):
        assert expected in codes


# ------------------------------------------------------------------- clock


def test_an_unsynchronised_clock_fails(monkeypatch):
    monkeypatch.setattr(
        "app.services.snapback_preflight._ntp_synchronised", lambda: (False, "no NTP"),
    )

    passed, details = check_clock()

    assert passed is False
    assert details["clock_sync_ok"] is False


def test_a_synchronised_clock_passes(monkeypatch):
    monkeypatch.setattr(
        "app.services.snapback_preflight._ntp_synchronised", lambda: (True, "synced"),
    )

    passed, details = check_clock()

    assert passed is True
    assert details["clock_sync_ok"] is True


# -------------------------------------------------------------------- disk


def test_a_full_disk_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("STERLING_MIN_FREE_DISK_BYTES", str(10 * 1024**4))

    passed, details = check_disk(paths=[tmp_path])

    assert passed is False
    assert details["free_bytes"] < int(os.environ["STERLING_MIN_FREE_DISK_BYTES"])


def test_enough_disk_passes(tmp_path, monkeypatch):
    monkeypatch.setenv("STERLING_MIN_FREE_DISK_BYTES", "1024")

    passed, details = check_disk(paths=[tmp_path])

    assert passed is True


def test_an_unreadable_path_fails():
    passed, details = check_disk(paths=[Path("/definitely/not/here/at/all")])

    assert passed is False


# ---------------------------------------------------------------- database


def test_a_healthy_database_passes(tmp_path):
    from app.services.snapback_observation_warehouse import SnapbackObservationWarehouse

    path = tmp_path / "evidence.db"
    SnapbackObservationWarehouse(db_path=str(path))

    passed, details = check_database(db_path=str(path))

    assert passed is True
    assert details["quick_check"] == "ok"
    assert details["writable"] is True


def test_a_corrupt_database_fails(tmp_path):
    path = tmp_path / "corrupt.db"
    path.write_bytes(b"NOT A SQLITE FILE" * 200)

    passed, details = check_database(db_path=str(path))

    assert passed is False


def test_a_missing_database_fails(tmp_path):
    passed, details = check_database(db_path=str(tmp_path / "absent.db"))

    assert passed is False


# ------------------------------------------------------------ evidence meta


def test_matching_evidence_meta_passes(tmp_path):
    from app.services.snapback_observation_warehouse import SnapbackObservationWarehouse

    wh = SnapbackObservationWarehouse(db_path=str(tmp_path / "e.db"))
    wh.write_evidence_meta(
        experiment_id="exp-1", schema_version=1, runtime_build_sha="build-1",
        strategy_config_hash="cfg-1", strategy_rule_hash="rule-1",
        execution_policy_hash="pol-1", execution_cost_schedule_hash="cost-1",
        calendar_version="cal-1", allocation_capital_inr=100_000.0,
    )

    passed, details = check_evidence_meta(
        warehouse=wh,
        expected={"experiment_id": "exp-1", "runtime_build_sha": "build-1"},
    )

    assert passed is True


def test_a_different_experiment_fails(tmp_path):
    from app.services.snapback_observation_warehouse import SnapbackObservationWarehouse

    wh = SnapbackObservationWarehouse(db_path=str(tmp_path / "e.db"))
    wh.write_evidence_meta(
        experiment_id="exp-1", schema_version=1, runtime_build_sha="build-1",
        strategy_config_hash="cfg-1", strategy_rule_hash="rule-1",
        execution_policy_hash="pol-1", execution_cost_schedule_hash="cost-1",
        calendar_version="cal-1", allocation_capital_inr=100_000.0,
    )

    passed, details = check_evidence_meta(
        warehouse=wh,
        expected={"experiment_id": "exp-2", "runtime_build_sha": "build-1"},
    )

    assert passed is False
    assert any("experiment_id" in str(v) for v in details.values())


def test_missing_evidence_meta_fails(tmp_path):
    from app.services.snapback_observation_warehouse import SnapbackObservationWarehouse

    wh = SnapbackObservationWarehouse(db_path=str(tmp_path / "e.db"))

    passed, details = check_evidence_meta(warehouse=wh, expected={"experiment_id": "exp-1"})

    assert passed is False


# ---------------------------------------------------------------- endpoints


def test_live_and_ready_are_separate_endpoints():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.v1.endpoints.snapback_ops import router

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)

    live = client.get("/health/live")
    assert live.status_code == 200
    assert live.json()["alive"] is True

    ready = client.get("/health/ready")
    assert ready.status_code in (200, 503)


def test_a_bound_family_account_passes_the_default_probe(monkeypatch):
    """Regression: the probe read a key the health payload does not have, so a
    correctly bound account reported as unbound and blocked readiness."""
    from app.services import snapback_preflight

    monkeypatch.setattr(
        "app.services.snapback_family_account.family_account_health",
        lambda *_a, **_k: {
            "family_account_configured": True,
            "family_account_identity_ok": True,
            "family_account_id": "KITE-…1B68",
        },
    )

    passed, details = snapback_preflight._default_family_account()

    assert passed is True
    assert details["family_account_id"] == "KITE-…1B68"


def test_an_unresolvable_family_account_fails_the_default_probe(monkeypatch):
    from app.services import snapback_preflight

    monkeypatch.setattr(
        "app.services.snapback_family_account.family_account_health",
        lambda *_a, **_k: {
            "family_account_configured": True,
            "family_account_identity_ok": False,
            "family_account_id": None,
            "family_account_error": "not found",
        },
    )

    passed, _ = snapback_preflight._default_family_account()

    assert passed is False
