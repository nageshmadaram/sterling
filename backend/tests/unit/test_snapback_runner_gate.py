"""Item 2: the runner starts only when every startup condition holds.

Splitting the checks made a contradiction possible: /health/ready could answer
503 on a bad clock, a full disk, a schema mismatch or the wrong experiment's
database, while the runner started anyway and wrote evidence under exactly those
conditions. Readiness that the runner ignores is not a gate, it is a label.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
MAIN = REPO / "backend" / "main.py"


def _main_source() -> str:
    return MAIN.read_text(encoding="utf-8")


def test_the_runner_is_gated_on_the_composed_preflight():
    source = _main_source()

    assert "run_preflight" in source, "the composed preflight is not consulted at startup"

    gate = re.search(r"if\s+([a-z_]+)\.may_start_runner\s*:", source)
    assert gate, "the runner's start is not behind may_start_runner"


def test_the_composed_result_carries_may_start_runner():
    """main.py reads .may_start_runner; the composed result must answer it, so a
    failed clock or disk check blocks the runner the same way a bad config does.
    """
    from app.services.snapback_preflight import run_preflight

    result = run_preflight(
        build_identity_fn=lambda: (True, []),
        worktree_clean_fn=lambda: (True, []),
        config_identity_fn=lambda: (True, []),
        database_fn=lambda: (True, {}),
        evidence_meta_fn=lambda: (True, {}),
        calendar_fn=lambda: (True, {}),
        family_account_fn=lambda: (True, {}),
        allocation_capital_fn=lambda: (True, {}),
        clock_fn=lambda: (True, {}),
        disk_fn=lambda: (True, {}),
        backup_writable_fn=lambda: (True, {}),
        lifecycle_fn=lambda: (True, {}),
    )

    assert result.may_start_runner is True
    assert result.status == "READY"


def test_an_operational_failure_blocks_the_runner():
    """A bad clock used to stop readiness but not the runner."""
    from app.services.snapback_preflight import run_preflight

    result = run_preflight(
        build_identity_fn=lambda: (True, []),
        worktree_clean_fn=lambda: (True, []),
        config_identity_fn=lambda: (True, []),
        database_fn=lambda: (True, {}),
        evidence_meta_fn=lambda: (True, {}),
        calendar_fn=lambda: (True, {}),
        family_account_fn=lambda: (True, {}),
        allocation_capital_fn=lambda: (True, {}),
        clock_fn=lambda: (False, {"clock_sync_ok": False}),
        disk_fn=lambda: (True, {}),
        backup_writable_fn=lambda: (True, {}),
        lifecycle_fn=lambda: (True, {}),
    )

    assert result.may_start_runner is False
    assert "clock" in result.errors


def test_a_database_failure_is_recovery_not_a_plain_halt():
    """The two states mean different things to an operator: one needs a restore,
    the other needs a fix."""
    from app.services.snapback_preflight import run_preflight

    result = run_preflight(
        build_identity_fn=lambda: (True, []),
        worktree_clean_fn=lambda: (True, []),
        config_identity_fn=lambda: (True, []),
        database_fn=lambda: (False, {"error": "database_missing"}),
        evidence_meta_fn=lambda: (True, {}),
        calendar_fn=lambda: (True, {}),
        family_account_fn=lambda: (True, {}),
        allocation_capital_fn=lambda: (True, {}),
        clock_fn=lambda: (True, {}),
        disk_fn=lambda: (True, {}),
        backup_writable_fn=lambda: (True, {}),
        lifecycle_fn=lambda: (True, {}),
    )

    assert result.status == "RECOVERY_REQUIRED"
    assert result.may_start_runner is False


def test_an_optional_check_does_not_block_the_runner():
    from app.services.snapback_preflight import run_preflight

    result = run_preflight(
        build_identity_fn=lambda: (True, []),
        worktree_clean_fn=lambda: (True, []),
        config_identity_fn=lambda: (True, []),
        database_fn=lambda: (True, {}),
        evidence_meta_fn=lambda: (True, {}),
        calendar_fn=lambda: (True, {}),
        family_account_fn=lambda: (True, {}),
        allocation_capital_fn=lambda: (True, {}),
        clock_fn=lambda: (True, {}),
        disk_fn=lambda: (True, {}),
        backup_writable_fn=lambda: (False, {"error": "not writable"}),
        lifecycle_fn=lambda: (True, {}),
    )

    assert result.may_start_runner is True


def test_readiness_and_the_runner_gate_read_the_same_result():
    """One function, so the two answers cannot disagree."""
    import inspect

    from app.api.v1.endpoints import snapback_ops

    endpoint = inspect.getsource(snapback_ops.health_ready)

    assert "run_preflight" in endpoint
    assert "run_preflight" in _main_source()


def test_the_narrow_startup_preflight_is_no_longer_the_runner_gate():
    source = _main_source()

    # snapback_startup.run_startup_preflight() remains as the config-identity
    # probe inside the composed check; it must not be the thing that decides.
    assert "run_startup_preflight()" not in source
