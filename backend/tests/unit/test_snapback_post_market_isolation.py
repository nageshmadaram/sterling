"""Block 5: the post-market chain must not block the event loop, must read a
point-in-time snapshot, and must publish a package only once it is complete.

Three separate failure modes:

  - run_due() does seconds-to-minutes of synchronous SQLite and file work. Called
    directly from the scheduler coroutine it stalls every other task in the
    process, including the liveness endpoint and alert delivery.
  - Reporting from the live database reads a file that is still being written.
    The numbers are real, but they are not any single moment.
  - A package written in place is readable while half-written. A consumer cannot
    tell a partial directory from a finished one.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

_IST = timezone(timedelta(hours=5, minutes=30))


# ------------------------------------------------------------- loop isolation


def test_the_post_market_chain_runs_off_the_event_loop():
    """A blocking run_due inside the loop stalls liveness and alert delivery."""
    from app.services.snapback_ops_scheduler import SnapbackOpsScheduler

    thread_names: list[str] = []

    def slow_trading_day(_d):
        import threading
        thread_names.append(threading.current_thread().name)
        return False

    scheduler = SnapbackOpsScheduler(
        is_trading_day_fn=slow_trading_day,
        state_path=Path("/tmp/does-not-matter-isolation.json"),
    )

    async def exercise():
        import threading
        loop_thread = threading.current_thread().name
        await scheduler.run_due_async(now_ist=datetime(2026, 9, 17, 17, 0, tzinfo=_IST))
        return loop_thread

    loop_thread = asyncio.run(exercise())

    assert thread_names, "run_due_async never invoked the chain"
    assert thread_names[0] != loop_thread


def test_the_run_loop_does_not_call_the_blocking_form():
    import inspect

    from app.services import snapback_ops_scheduler

    source = inspect.getsource(snapback_ops_scheduler.run_forever)

    assert "run_due_async" in source
    assert "scheduler.run_due(" not in source
    assert "poll_health_async" in source


def test_the_loop_keeps_serving_while_the_chain_runs():
    from app.services.snapback_ops_scheduler import SnapbackOpsScheduler

    import time

    def slow_trading_day(_d):
        time.sleep(0.4)
        return False

    scheduler = SnapbackOpsScheduler(
        is_trading_day_fn=slow_trading_day,
        state_path=Path("/tmp/does-not-matter-isolation2.json"),
    )

    ticks = 0

    async def heartbeat():
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0.02)

    async def exercise():
        beat = asyncio.create_task(heartbeat())
        await scheduler.run_due_async(now_ist=datetime(2026, 9, 17, 17, 0, tzinfo=_IST))
        beat.cancel()

    asyncio.run(exercise())

    # A blocked loop would tick once or twice, not repeatedly.
    assert ticks > 5, f"event loop was starved: only {ticks} ticks"


# ------------------------------------------------------- snapshot-based report


def test_the_report_reads_the_verified_backup_not_the_live_database(tmp_path):
    from app.services.snapback_ops_scheduler import SnapbackOpsScheduler

    live_db = tmp_path / "live.db"
    live_db.write_text("live")
    snapshot_db = tmp_path / "backups" / "snap.db"
    snapshot_db.parent.mkdir(parents=True)
    snapshot_db.write_text("snapshot")

    seen: dict = {}

    class _Artifact:
        db_path = snapshot_db
        checksum_path = snapshot_db.with_suffix(".sha256")

    def report_fn(**kwargs):
        seen.update(kwargs)
        return None

    scheduler = SnapbackOpsScheduler(
        source_db=live_db,
        report_root=tmp_path / "reports",
        state_path=tmp_path / "state.json",
        is_trading_day_fn=lambda _d: True,
        session_evidence_complete_fn=lambda _d: True,
        backup_fn=lambda **_k: _Artifact(),
        verify_backup_fn=lambda *_a, **_k: True,
        report_fn=report_fn,
        health_fn=lambda: {"status": "HEALTHY", "healthy": True},
    )

    scheduler.run_due(now_ist=datetime(2026, 9, 17, 17, 0, tzinfo=_IST))

    assert seen.get("snapshot_db_path") == snapshot_db


def test_an_unverified_backup_does_not_produce_a_report(tmp_path):
    """A report from an unverified snapshot cannot be distinguished from a good one."""
    from app.services.snapback_ops_scheduler import SnapbackOpsScheduler

    live_db = tmp_path / "live.db"
    live_db.write_text("live")
    calls = []

    class _Artifact:
        db_path = tmp_path / "snap.db"
        checksum_path = tmp_path / "snap.sha256"

    scheduler = SnapbackOpsScheduler(
        source_db=live_db,
        report_root=tmp_path / "reports",
        state_path=tmp_path / "state.json",
        is_trading_day_fn=lambda _d: True,
        session_evidence_complete_fn=lambda _d: True,
        backup_fn=lambda **_k: _Artifact(),
        verify_backup_fn=lambda *_a, **_k: False,
        report_fn=lambda **k: calls.append(k),
        health_fn=lambda: {"status": "HEALTHY", "healthy": True},
    )

    result = scheduler.run_due(now_ist=datetime(2026, 9, 17, 17, 0, tzinfo=_IST))

    assert calls == []
    assert result.report_ok is False
    assert result.status == "PARTIAL_FAILURE"
    assert "backup_verification_failed" in result.errors
    assert "report_skipped_no_verified_snapshot" in result.errors


# --------------------------------------------------------- atomic publication


def test_a_package_is_published_atomically(tmp_path):
    from study.snapback_forward_report import publish_package

    def build(directory: Path) -> None:
        (directory / "evidence_summary.json").write_text('{"a": 1}')
        (directory / "trades.csv").write_text("opportunity_id\n")

    final = publish_package(
        root=tmp_path / "artifacts", name="2026-09-17", build=build,
    )

    assert final == tmp_path / "artifacts" / "2026-09-17"
    assert (final / "evidence_summary.json").exists()
    assert not (tmp_path / "artifacts" / ".staging").exists()


def test_a_published_package_carries_a_checksum_of_every_file(tmp_path):
    from study.snapback_forward_report import publish_package

    def build(directory: Path) -> None:
        (directory / "a.json").write_text("alpha")
        (directory / "b.csv").write_text("beta")

    final = publish_package(root=tmp_path / "artifacts", name="d", build=build)

    manifest = json.loads((final / "package_checksums.json").read_text())

    assert set(manifest["files"]) == {"a.json", "b.csv"}
    assert manifest["files"]["a.json"] == hashlib.sha256(b"alpha").hexdigest()
    assert manifest["files"]["b.csv"] == hashlib.sha256(b"beta").hexdigest()


def test_a_failed_build_publishes_nothing(tmp_path):
    from study.snapback_forward_report import publish_package

    def build(directory: Path) -> None:
        (directory / "partial.json").write_text("half")
        raise RuntimeError("report generation blew up")

    with pytest.raises(RuntimeError):
        publish_package(root=tmp_path / "artifacts", name="d", build=build)

    assert not (tmp_path / "artifacts" / "d").exists()
    # And no staging debris is left to be mistaken for a package.
    staging = tmp_path / "artifacts" / ".staging"
    assert not staging.exists() or list(staging.iterdir()) == []


def test_republishing_replaces_the_previous_package(tmp_path):
    from study.snapback_forward_report import publish_package

    publish_package(
        root=tmp_path / "a", name="d",
        build=lambda p: (p / "x.json").write_text("first"),
    )
    final = publish_package(
        root=tmp_path / "a", name="d",
        build=lambda p: (p / "y.json").write_text("second"),
    )

    assert (final / "y.json").read_text() == "second"
    # The old file is gone, not merged into the new package.
    assert not (final / "x.json").exists()


def test_the_checksum_manifest_verifies(tmp_path):
    from study.snapback_forward_report import publish_package, verify_package

    final = publish_package(
        root=tmp_path / "a", name="d",
        build=lambda p: (p / "x.json").write_text("content"),
    )

    assert verify_package(final) is True

    (final / "x.json").write_text("tampered")
    assert verify_package(final) is False


def test_the_daily_report_publishes_through_the_atomic_path():
    import inspect

    from study import snapback_forward_report

    source = inspect.getsource(snapback_forward_report.write_forward_report)

    assert "publish_package" in source
