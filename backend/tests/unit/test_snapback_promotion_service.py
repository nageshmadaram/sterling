"""One service evaluates the gate and writes one immutable record."""

from __future__ import annotations

import os
import tempfile

import pytest

from app.services.snapback_promotion import PromotionService
from app.services.snapback_observation_warehouse import (
    EvidenceIntegrityError,
    SnapbackObservationWarehouse,
)


@pytest.fixture
def warehouse():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as fh:
        path = fh.name
    wh = SnapbackObservationWarehouse(db_path=path)
    yield wh
    if os.path.exists(path):
        os.remove(path)


def test_an_empty_experiment_is_inconclusive(warehouse):
    result = PromotionService().evaluate(
        warehouse=warehouse, source_snapshot_sha256="snap-1",
    )

    assert result.verdict == "INCONCLUSIVE"
    assert result.promoted is False


def test_a_data_quality_failure_is_inconclusive_not_failed(warehouse):
    """A broken ledger is not an economic verdict about the strategy."""
    warehouse.record_opportunity(
        opportunity_id="OPP-1", symbol="NIFTY", signal_type="SNAPBACK_FADE_UP",
        spot_price=24500.0, trend="BEARISH",
    )
    warehouse.commit_paper_close_transaction(
        opportunity_id="OPP-1",
        outcome_data={
            "outcome_id": "OUTCOME-1", "opportunity_id": "OPP-1", "symbol": "NIFTY",
            "exit_reason": "PREMIUM_STOP",
            "entry_ts": "2026-10-01T09:20:00+05:30",
            "exit_ts": "2026-10-03T11:00:00+05:30",
            "actual_option_pnl": 100.0, "actual_futures_pnl": 0.0,
        },
        cost_events=[],
    )

    result = PromotionService().evaluate(
        warehouse=warehouse, source_snapshot_sha256="snap-1",
    )

    # No cost ledger for a completed trade: inconclusive, never FAILED.
    assert result.verdict == "INCONCLUSIVE"
    assert result.data_quality_ok is False


def test_the_record_is_written_once(warehouse):
    service = PromotionService()
    service.evaluate(warehouse=warehouse, source_snapshot_sha256="snap-1")
    service.evaluate(warehouse=warehouse, source_snapshot_sha256="snap-1")

    rows = warehouse.get_records_by_table("promotion_records")

    assert len(rows) == 1


def test_the_same_snapshot_gives_the_same_verdict(warehouse):
    service = PromotionService()
    first = service.evaluate(warehouse=warehouse, source_snapshot_sha256="snap-1")
    second = service.evaluate(warehouse=warehouse, source_snapshot_sha256="snap-1")

    assert first.gate_input_hash == second.gate_input_hash
    assert first.verdict == second.verdict


def test_latest_returns_the_persisted_record(warehouse):
    service = PromotionService()
    evaluated = service.evaluate(warehouse=warehouse, source_snapshot_sha256="snap-1")

    latest = service.latest(warehouse=warehouse)

    assert latest is not None
    assert latest.verdict == evaluated.verdict
    assert latest.gate_input_hash == evaluated.gate_input_hash


def test_a_contradicting_record_is_refused(warehouse):
    service = PromotionService()
    result = service.evaluate(warehouse=warehouse, source_snapshot_sha256="snap-1")

    with pytest.raises(EvidenceIntegrityError):
        warehouse.record_promotion_record(
            promotion_id=result.promotion_id,
            experiment_id=result.experiment_id,
            gate_version=result.gate_version,
            gate_input_hash=result.gate_input_hash,
            source_snapshot_sha256="snap-1",
            runtime_build_sha=result.runtime_build_sha,
            config_hash=result.config_hash, rule_hash=result.rule_hash,
            execution_policy_hash=result.execution_policy_hash,
            cost_schedule_hash=result.cost_schedule_hash,
            observed_sessions=result.observed_sessions,
            completed_trades=result.completed_trades,
            verdict="PASSED",   # contradicts the stored verdict
            promoted=1, data_quality_ok=1, reasons_json="[]", checks_json="{}",
        )


def test_only_three_verdicts_exist(warehouse):
    result = PromotionService().evaluate(
        warehouse=warehouse, source_snapshot_sha256="snap-1",
    )

    assert result.verdict in {"PASSED", "FAILED", "INCONCLUSIVE"}
