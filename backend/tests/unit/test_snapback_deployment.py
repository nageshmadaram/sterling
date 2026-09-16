"""Block 7: what a family deployment must be, before it is ever started.

Family Mode exists so that exactly one strategy can move exposure on the family
account. A second auto-runner does not announce itself — it opens a position and
the money is gone before anybody reads a log line. The same is true of a socket
bound to every interface, and of a shutdown that kills the runner mid-write.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
MAIN = REPO / "backend" / "main.py"


def _main_source() -> str:
    return MAIN.read_text(encoding="utf-8")


# ------------------------------------------------- family mode suppression


@pytest.mark.parametrize("runner", [
    "gamma_move_runner",
    "adaptive_edge_runner",
    "intraday_runner",
    "kite_engine",
    "navigator",
])
def test_every_exposure_capable_runner_is_suppressed_in_family_mode(runner):
    """One strategy means one strategy. Any other loop that can reach the broker
    is a second way for exposure to appear on the family account."""
    from app.services.snapback_deployment import suppressed_runners_in_family_mode

    assert runner in suppressed_runners_in_family_mode()


def test_the_snapback_runtime_is_not_suppressed():
    from app.services.snapback_deployment import suppressed_runners_in_family_mode

    suppressed = suppressed_runners_in_family_mode()

    assert "snapback_runner" not in suppressed
    assert "snapback_ops_scheduler" not in suppressed


# Each suppressed runner's task variable in main.py. A task created outside the
# family-mode guard runs on a family machine no matter what the flag says.
_RUNNER_TASK_VARS = {
    "gamma_move_runner": "gamma_move_task",
    "adaptive_edge_runner": "adaptive_edge_task",
    "intraday_runner": "intraday_task",
    "kite_engine": "kite_engine_task",
    "navigator": "navigator_task",
}


def test_the_task_table_covers_every_suppressed_runner():
    from app.services.snapback_deployment import suppressed_runners_in_family_mode

    assert set(_RUNNER_TASK_VARS) == set(suppressed_runners_in_family_mode())


@pytest.mark.parametrize("runner,task_var", sorted(_RUNNER_TASK_VARS.items()))
def test_main_starts_no_suppressed_runner_unconditionally(runner, task_var):
    source = _main_source()

    creations = [
        m for m in re.finditer(rf"^\s*{task_var}\s*=\s*asyncio\.create_task\(", source, re.M)
    ]
    assert creations, f"{task_var} is never created in main.py"

    for match in creations:
        window = source[max(0, match.start() - 600):match.start()]
        assert "if not _family_mode" in window, (
            f"{runner} starts outside the family-mode guard"
        )


def test_family_mode_blocks_the_engine_order_route():
    from app.services.snapback_family_mode import guard_broker_write

    import os
    os.environ["STERLING_FAMILY_MODE"] = "true"
    try:
        with pytest.raises(PermissionError):
            guard_broker_write("place_order")
    finally:
        os.environ.pop("STERLING_FAMILY_MODE", None)


# ---------------------------------------------------------------- bind host


def test_the_dev_server_does_not_bind_every_interface():
    """0.0.0.0 on a home network exposes an authenticated broker session to every
    device on that network, including whatever else is on the Wi-Fi."""
    source = _main_source()

    assert '"0.0.0.0"' not in source
    # The default lives in one place, so there is a single thing to audit.
    assert "bind_host()" in source


def test_the_bind_host_is_overridable_but_defaults_closed():
    from app.services.snapback_deployment import bind_host

    import os
    os.environ.pop("STERLING_BIND_HOST", None)
    assert bind_host() == "127.0.0.1"

    os.environ["STERLING_BIND_HOST"] = "0.0.0.0"
    try:
        assert bind_host() == "0.0.0.0"
    finally:
        os.environ.pop("STERLING_BIND_HOST", None)


# ------------------------------------------------------- systemd templates


@pytest.mark.parametrize("unit", ["sterling-backend.service", "sterling-backend.watchdog.service"])
def test_a_systemd_unit_is_committed(unit):
    assert (REPO / "deploy" / "systemd" / unit).exists()


def test_the_service_unit_restarts_and_does_not_carry_secrets():
    text = (REPO / "deploy" / "systemd" / "sterling-backend.service").read_text()

    assert "Restart=always" in text
    # Secrets come from a file the unit references, never from the unit itself,
    # because the unit is committed and the environment file is not.
    assert "EnvironmentFile=" in text
    for forbidden in ("KITE_API_SECRET=", "TELEGRAM_BOT_TOKEN=", "ACCESS_TOKEN="):
        assert forbidden not in text


def test_the_watchdog_unit_probes_liveness_not_readiness():
    """Readiness is allowed to be false — a non-trading day, a pending login.
    Restarting on it would loop the service all weekend."""
    text = (REPO / "deploy" / "systemd" / "sterling-backend.watchdog.service").read_text()

    assert "/health/live" in text
    assert "/health/ready" not in text


# ------------------------------------------------------ graceful shutdown


def test_shutdown_waits_for_the_snapback_runner():
    source = _main_source()

    # The runner writes evidence. Cancelling without awaiting can tear the
    # process down between an insert and its commit.
    assert "snapback_runner_task.cancel()" in source
    assert "await snapback_runner_task" in source


def test_shutdown_drains_the_alert_outbox_worker():
    source = _main_source()

    assert "snapback_ops_task.cancel()" in source
    assert "await snapback_ops_task" in source


def test_a_shutdown_marks_the_runtime_as_stopped():
    """A process that vanished and a process that stopped cleanly look identical
    from the evidence afterwards unless one of them says so."""
    from app.services.snapback_deployment import record_shutdown

    assert callable(record_shutdown)
    assert "record_shutdown" in _main_source()
