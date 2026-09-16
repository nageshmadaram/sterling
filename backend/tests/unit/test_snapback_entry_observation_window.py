"""E12: a T+1 fill must be the first PROVABLY OBSERVED executable quote.

"First executable quote" cannot mean "the first quote Sterling happened to see after
restarting at 09:40" — that silently assumes nothing qualifying happened between 09:15
and 09:40, which is exactly what nobody observed.
"""

from __future__ import annotations

import os
import tempfile
from datetime import date, datetime, timedelta, timezone

import pytest

from app.services.snapback_entry_observation import (
    ENTRY_OBSERVATION_MAX_GAP_MS,
    EntryObservation,
    continuity_verdict,
)
from app.services.snapback_observation_warehouse import SnapbackObservationWarehouse

_IST = timezone(timedelta(hours=5, minutes=30))


@pytest.fixture
def warehouse():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as fh:
        path = fh.name
    wh = SnapbackObservationWarehouse(db_path=path)
    yield wh
    if os.path.exists(path):
        os.remove(path)


def _t(h, m, s=0):
    return datetime(2026, 9, 17, h, m, s, tzinfo=_IST)


# ------------------------------------------------------------- continuity


def test_monitoring_from_the_open_permits_a_fill():
    verdict = continuity_verdict(
        session_open=_t(9, 15),
        monitor_started_at=_t(9, 15),
        last_heartbeat_at=_t(9, 23),
        max_gap_ms=0,
        now=_t(9, 23),
    )

    assert verdict.continuous is True
    assert verdict.reasons == []


def test_a_cold_start_after_the_open_cannot_fill():
    verdict = continuity_verdict(
        session_open=_t(9, 15),
        monitor_started_at=_t(9, 40),
        last_heartbeat_at=_t(9, 40),
        max_gap_ms=0,
        now=_t(9, 40),
    )

    assert verdict.continuous is False
    assert "INCONCLUSIVE_MISSED_OPEN_OBSERVATION" in verdict.reasons


def test_a_monitor_gap_invalidates_a_later_entry():
    verdict = continuity_verdict(
        session_open=_t(9, 15),
        monitor_started_at=_t(9, 15),
        last_heartbeat_at=_t(9, 30),
        max_gap_ms=ENTRY_OBSERVATION_MAX_GAP_MS + 60_000,
        now=_t(9, 30),
    )

    assert verdict.continuous is False
    assert "INCONCLUSIVE_ENTRY_OBSERVATION_GAP" in verdict.reasons


def test_a_gap_inside_tolerance_is_acceptable():
    verdict = continuity_verdict(
        session_open=_t(9, 15),
        monitor_started_at=_t(9, 15),
        last_heartbeat_at=_t(9, 30),
        max_gap_ms=ENTRY_OBSERVATION_MAX_GAP_MS - 1,
        now=_t(9, 30),
    )

    assert verdict.continuous is True


def test_a_stale_heartbeat_right_now_is_also_a_gap():
    verdict = continuity_verdict(
        session_open=_t(9, 15),
        monitor_started_at=_t(9, 15),
        last_heartbeat_at=_t(9, 20),
        max_gap_ms=0,
        now=_t(9, 30),          # ten minutes since the last heartbeat
    )

    assert verdict.continuous is False


def test_never_started_is_not_continuous():
    verdict = continuity_verdict(
        session_open=_t(9, 15), monitor_started_at=None,
        last_heartbeat_at=None, max_gap_ms=0, now=_t(9, 30),
    )

    assert verdict.continuous is False
    assert "INCONCLUSIVE_MISSED_OPEN_OBSERVATION" in verdict.reasons


def test_the_policy_tolerance_is_declared_not_implicit():
    from app.engines.snapback.policy import EXECUTION_POLICY

    assert EXECUTION_POLICY.entry_observation_max_gap_ms == ENTRY_OBSERVATION_MAX_GAP_MS
    # Three missed 30-second runner cycles.
    assert ENTRY_OBSERVATION_MAX_GAP_MS == 90_000


# ---------------------------------------------------------- attempt ledger


def _attempt(wh, seq, decision, *, opp="OPP-1", continuous=True, reasons=None):
    wh.record_entry_attempt(
        attempt_id=f"ATT-{opp}-{seq}",
        opportunity_id=opp,
        session_date="2026-09-17",
        attempt_sequence=seq,
        attempted_at=_t(9, 15 + seq).isoformat(),
        monitoring_continuous_from_open=continuous,
        decision=decision,
        reason_codes=reasons or [],
    )


def test_every_attempt_is_persisted_including_the_refusals(warehouse):
    _attempt(warehouse, 1, "NO_FILL", reasons=["spread_too_wide"])
    _attempt(warehouse, 2, "NO_FILL", reasons=["stale_quote"])
    _attempt(warehouse, 3, "FILLED")

    rows = warehouse.get_records_by_table("entry_attempts", opportunity_id="OPP-1")

    assert len(rows) == 3
    assert [r["decision"] for r in sorted(rows, key=lambda r: r["attempt_sequence"])] == [
        "NO_FILL", "NO_FILL", "FILLED",
    ]


def test_attempts_are_append_only_across_a_restart(warehouse):
    _attempt(warehouse, 1, "NO_FILL", reasons=["stale_quote"])

    path = warehouse.db_path
    del warehouse
    reopened = SnapbackObservationWarehouse(db_path=path)
    reopened.record_entry_attempt(
        attempt_id="ATT-OPP-1-2", opportunity_id="OPP-1", session_date="2026-09-17",
        attempt_sequence=2, attempted_at=_t(9, 20).isoformat(),
        monitoring_continuous_from_open=True, decision="FILLED", reason_codes=[],
    )

    rows = reopened.get_records_by_table("entry_attempts", opportunity_id="OPP-1")
    assert len(rows) == 2


def test_one_opportunity_can_fill_at_most_once(warehouse):
    _attempt(warehouse, 1, "FILLED")

    assert warehouse.opportunity_already_filled("OPP-1") is True

    # A second executable attempt must not create a duplicate fill.
    _attempt(warehouse, 2, "DUPLICATE_SUPPRESSED", reasons=["already_filled"])

    filled = [
        r for r in warehouse.get_records_by_table("entry_attempts", opportunity_id="OPP-1")
        if r["decision"] == "FILLED"
    ]
    assert len(filled) == 1


def test_attempts_carry_build_provenance(warehouse):
    _attempt(warehouse, 1, "NO_FILL")

    row = warehouse.get_records_by_table("entry_attempts", opportunity_id="OPP-1")[0]

    assert row["runtime_build_sha"]
    assert row["runtime_build_sha"] != "UNKNOWN"


def test_a_non_continuous_attempt_is_recorded_as_such(warehouse):
    _attempt(warehouse, 1, "INCONCLUSIVE_MISSED_OPEN_OBSERVATION", continuous=False,
             reasons=["INCONCLUSIVE_MISSED_OPEN_OBSERVATION"])

    row = warehouse.get_records_by_table("entry_attempts", opportunity_id="OPP-1")[0]

    assert row["monitoring_continuous_from_open"] == 0
    assert "MISSED_OPEN" in row["reason_codes_json"]


# ------------------------------------------------------- observation state


def test_monitor_state_is_durable(warehouse):
    from app.services.snapback_entry_observation import (
        heartbeat_entry_monitor, entry_monitor_state, start_entry_monitor,
    )

    start_entry_monitor(warehouse, session_date="2026-09-17", at=_t(9, 15))
    heartbeat_entry_monitor(warehouse, session_date="2026-09-17", at=_t(9, 16))

    path = warehouse.db_path
    del warehouse
    reopened = SnapbackObservationWarehouse(db_path=path)

    state = entry_monitor_state(reopened, "2026-09-17")

    assert state["entry_monitor_started_at"].startswith("2026-09-17T09:15")
    assert state["entry_last_heartbeat_at"].startswith("2026-09-17T09:16")


def test_a_heartbeat_gap_is_recorded(warehouse):
    from app.services.snapback_entry_observation import (
        heartbeat_entry_monitor, entry_monitor_state, start_entry_monitor,
    )

    start_entry_monitor(warehouse, session_date="2026-09-17", at=_t(9, 15))
    heartbeat_entry_monitor(warehouse, session_date="2026-09-17", at=_t(9, 16))
    # Backend away for four minutes.
    heartbeat_entry_monitor(warehouse, session_date="2026-09-17", at=_t(9, 20))

    state = entry_monitor_state(warehouse, "2026-09-17")

    assert state["entry_gap_count"] >= 1
    assert state["entry_max_gap_ms"] >= 4 * 60 * 1000


def test_a_restart_resumes_the_recorded_state(warehouse):
    from app.services.snapback_entry_observation import (
        entry_monitor_state, heartbeat_entry_monitor, start_entry_monitor,
    )

    start_entry_monitor(warehouse, session_date="2026-09-17", at=_t(9, 15))
    heartbeat_entry_monitor(warehouse, session_date="2026-09-17", at=_t(9, 16))

    # A second start on the same session must not erase the original start time.
    start_entry_monitor(warehouse, session_date="2026-09-17", at=_t(9, 40))

    state = entry_monitor_state(warehouse, "2026-09-17")
    assert state["entry_monitor_started_at"].startswith("2026-09-17T09:15")
