"""Block 3: creating an alert and delivering it are different facts.

`create_task(send)` returning is not delivery. A network failure after that point is
invisible, so the dispatcher counts a notification nobody received — the one failure
mode that makes every other alert untrustworthy.
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta, timezone

_IST = timezone(timedelta(hours=5, minutes=30))

import pytest

from app.services.snapback_alert_outbox import (
    MAX_ATTEMPTS,
    AlertOutbox,
    OutboxStatus,
    dedupe_key_for,
)
from app.services.snapback_observation_warehouse import SnapbackObservationWarehouse


@pytest.fixture
def warehouse():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as fh:
        path = fh.name
    wh = SnapbackObservationWarehouse(db_path=path)
    yield wh
    if os.path.exists(path):
        os.remove(path)


@pytest.fixture
def outbox(warehouse):
    return AlertOutbox(warehouse=warehouse)


def _alert(code="broker_disconnected", severity="WARNING"):
    from app.services.snapback_alerts import OperationalAlert

    return OperationalAlert(
        code=code, severity=severity, title="Broker login required",
        message="Sterling is not connected to the broker.",
    )


NOW = datetime(2026, 10, 15, 9, 0, tzinfo=timezone.utc)


# ------------------------------------------------------------------ creation


def test_an_alert_is_queued_not_sent(outbox, warehouse):
    outbox.enqueue(_alert(), trading_session="2026-10-15", now=NOW)

    rows = warehouse.get_records_by_table("operational_alert_outbox")

    assert len(rows) == 1
    assert rows[0]["status"] == OutboxStatus.PENDING
    assert rows[0]["delivered_at"] is None
    assert rows[0]["attempt_count"] == 0


def test_the_same_fault_in_one_session_is_queued_once(outbox, warehouse):
    outbox.enqueue(_alert(), trading_session="2026-10-15", now=NOW)
    outbox.enqueue(_alert(), trading_session="2026-10-15", now=NOW + timedelta(minutes=5))

    assert len(warehouse.get_records_by_table("operational_alert_outbox")) == 1


def test_the_next_session_can_alert_again(outbox, warehouse):
    outbox.enqueue(_alert(), trading_session="2026-10-15", now=NOW)
    outbox.enqueue(_alert(), trading_session="2026-10-16", now=NOW + timedelta(days=1))

    assert len(warehouse.get_records_by_table("operational_alert_outbox")) == 2


def test_the_dedupe_key_includes_the_entity():
    assert dedupe_key_for(code="exit_pending_stale", trading_session="2026-10-15",
                          entity="OPP-1") == "exit_pending_stale:2026-10-15:OPP-1"
    assert dedupe_key_for(code="broker_login_required",
                          trading_session="2026-10-15") == "broker_login_required:2026-10-15"


# ------------------------------------------------------------------ delivery


@pytest.mark.asyncio
async def test_a_successful_send_is_recorded_as_delivered(outbox, warehouse):
    outbox.enqueue(_alert(), trading_session="2026-10-15", now=NOW)

    async def sink(payload):
        return "msg-1"

    result = await outbox.process(send=sink, now=NOW)

    row = warehouse.get_records_by_table("operational_alert_outbox")[0]

    assert result.delivered == 1
    assert row["status"] == OutboxStatus.DELIVERED
    assert row["delivered_at"]
    assert row["delivery_message_id"] == "msg-1"


@pytest.mark.asyncio
async def test_a_failed_send_is_never_recorded_as_delivered(outbox, warehouse):
    outbox.enqueue(_alert(), trading_session="2026-10-15", now=NOW)

    async def broken(payload):
        raise RuntimeError("telegram unreachable")

    result = await outbox.process(send=broken, now=NOW)

    row = warehouse.get_records_by_table("operational_alert_outbox")[0]

    assert result.failed == 1
    assert row["status"] == OutboxStatus.RETRY
    assert row["delivered_at"] is None
    assert "telegram unreachable" in row["last_error"]
    assert row["attempt_count"] == 1


@pytest.mark.asyncio
async def test_a_retry_waits_for_its_backoff(outbox, warehouse):
    outbox.enqueue(_alert(), trading_session="2026-10-15", now=NOW)

    async def broken(payload):
        raise RuntimeError("down")

    await outbox.process(send=broken, now=NOW)

    # Immediately after the failure the item is not yet due.
    calls = []

    async def counting(payload):
        calls.append(payload)
        return "msg"

    await outbox.process(send=counting, now=NOW + timedelta(seconds=1))
    assert calls == []

    await outbox.process(send=counting, now=NOW + timedelta(minutes=10))
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_a_restart_resumes_pending_items(warehouse):
    first = AlertOutbox(warehouse=warehouse)
    first.enqueue(_alert(), trading_session="2026-10-15", now=NOW)

    # New process, same database.
    second = AlertOutbox(warehouse=warehouse)

    async def sink(payload):
        return "msg-2"

    result = await second.process(send=sink, now=NOW)

    assert result.delivered == 1


@pytest.mark.asyncio
async def test_an_exhausted_alert_becomes_dead(outbox, warehouse):
    outbox.enqueue(_alert(), trading_session="2026-10-15", now=NOW)

    async def broken(payload):
        raise RuntimeError("down")

    moment = NOW
    for _ in range(MAX_ATTEMPTS + 1):
        await outbox.process(send=broken, now=moment)
        moment = moment + timedelta(hours=1)

    row = warehouse.get_records_by_table("operational_alert_outbox")[0]

    assert row["status"] == OutboxStatus.DEAD
    assert row["delivered_at"] is None


@pytest.mark.asyncio
async def test_a_delivered_alert_is_not_sent_again(outbox, warehouse):
    outbox.enqueue(_alert(), trading_session="2026-10-15", now=NOW)

    sent = []

    async def sink(payload):
        sent.append(payload)
        return "msg"

    await outbox.process(send=sink, now=NOW)
    await outbox.process(send=sink, now=NOW + timedelta(hours=1))

    assert len(sent) == 1


# -------------------------------------------------------------------- health


def test_health_reports_the_outbox_state(outbox, warehouse):
    outbox.enqueue(_alert(), trading_session="2026-10-15", now=NOW)

    state = outbox.health()

    assert state["alert_outbox_pending"] == 1
    assert state["alert_outbox_dead"] == 0
    assert state["last_alert_delivery_success"] is None


@pytest.mark.asyncio
async def test_a_dead_alert_degrades_health(outbox, warehouse):
    from app.services.snapback_health import build_prospective_health

    outbox.enqueue(_alert(), trading_session="2026-10-15", now=NOW)

    async def broken(payload):
        raise RuntimeError("down")

    moment = NOW
    for _ in range(MAX_ATTEMPTS + 1):
        await outbox.process(send=broken, now=moment)
        moment = moment + timedelta(hours=1)

    class W:
        def opportunity_status_counts(self):
            return {}

        def paper_position_status_counts(self):
            return {}

    body = build_prospective_health(
        warehouse=W(), runtime_sha="sha", strategy_manifest="m", manifest_ok=True,
        mode="PAPER", broker_connected=True, market_data_fresh=True, calendar_ok=True,
        database_ok=True, runner_alive=True, last_runner_tick=NOW,
        alert_outbox={"alert_outbox_pending": 0, "alert_outbox_dead": 1},
        market_open=False, now=NOW,
    )

    assert body["alert_outbox_dead"] == 1
    assert body["healthy"] is False
    assert "alert_delivery_failed" in body["unresolved_errors"]


# ------------------------------------------------------------ morning alerts


def test_the_morning_warning_is_session_scoped(outbox, warehouse):
    from app.services.snapback_alert_outbox import morning_login_alert

    morning_login_alert(outbox, broker_connected=False,
                        now=datetime(2026, 10, 15, 8, 45, tzinfo=_IST),
                        trading_session="2026-10-15")
    morning_login_alert(outbox, broker_connected=False,
                        now=datetime(2026, 10, 15, 8, 50, tzinfo=_IST),
                        trading_session="2026-10-15")

    rows = warehouse.get_records_by_table("operational_alert_outbox")
    assert len(rows) == 1
    assert rows[0]["severity"] == "WARNING"


def test_a_still_disconnected_account_escalates(outbox, warehouse):
    from app.services.snapback_alert_outbox import morning_login_alert

    morning_login_alert(outbox, broker_connected=False,
                        now=datetime(2026, 10, 15, 8, 45, tzinfo=_IST),
                        trading_session="2026-10-15")
    morning_login_alert(outbox, broker_connected=False,
                        now=datetime(2026, 10, 15, 9, 10, tzinfo=_IST),
                        trading_session="2026-10-15")

    rows = warehouse.get_records_by_table("operational_alert_outbox")
    severities = {r["severity"] for r in rows}

    assert "CRITICAL" in severities


def test_a_connected_account_raises_nothing(outbox, warehouse):
    from app.services.snapback_alert_outbox import morning_login_alert

    morning_login_alert(outbox, broker_connected=True,
                        now=datetime(2026, 10, 15, 8, 45, tzinfo=_IST),
                        trading_session="2026-10-15")

    assert warehouse.get_records_by_table("operational_alert_outbox") == []
