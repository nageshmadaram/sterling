"""Slice A: session authority, coverage without fallback, writer-side economics,
and a complete broker mutation guard.

Four places where the previous release still let missing evidence look measurable.
"""

from __future__ import annotations

import math
import os
import tempfile
from datetime import datetime, timezone

import pytest


# ------------------------------------------------------- session predicates


def _row(**over):
    base = {
        "session_date": "2026-09-17",
        "scanner_status": "COMPLETE",
        "universe_expected": 200,
        "universe_scanned": 200,
        "symbol_failures": 0,
        "market_gate_status": "EVALUATED",
        "entry_phase_status": "COMPLETE",
        "eod_phase_status": "COMPLETE",
        "package_status": "COMPLETE",
        "evidence_gap_codes_json": "[]",
    }
    base.update(over)
    return base


def test_scan_completeness_only_covers_the_scanner():
    from app.services.snapback_session_ledger import session_scan_complete

    # Scanner done, but the trading day's phases have not run yet.
    row = _row(entry_phase_status="PENDING", eod_phase_status="PENDING",
               package_status="PENDING")

    assert session_scan_complete(row) is True


def test_evidence_completeness_requires_every_trading_phase():
    """Packaging is excluded on purpose: requiring it here would deadlock the gate
    behind its own report. See session_package_complete for the packaging question."""
    from app.services.snapback_session_ledger import (
        session_market_evidence_complete, session_package_complete,
    )

    assert session_market_evidence_complete(_row()) is True

    for field in ("entry_phase_status", "eod_phase_status"):
        assert session_market_evidence_complete(_row(**{field: "PENDING"})) is False

    assert session_market_evidence_complete(_row(package_status="PENDING")) is True
    assert session_package_complete(_row(package_status="PENDING")) is False


def test_an_evidence_gap_disqualifies_the_session():
    from app.services.snapback_session_ledger import session_evidence_complete

    assert session_evidence_complete(_row(evidence_gap_codes_json='["EOD_EVIDENCE_GAP"]')) is False


def test_runner_uses_scan_completeness_not_evidence_completeness():
    import inspect

    from app.services import snapback_runner

    source = inspect.getsource(snapback_runner)

    assert "session_scan_complete" in source
    assert "session_evidence_complete" not in source


def test_promotion_counts_only_evidence_complete_sessions(tmp_path):
    from app.services.snapback_observation_warehouse import SnapbackObservationWarehouse
    from app.services.snapback_session_ledger import (
        SessionStatus, observed_session_count, record_session_scan, update_session_phase,
    )

    wh = SnapbackObservationWarehouse(db_path=str(tmp_path / "w.db"))

    for day in ("2026-09-17", "2026-09-18"):
        record_session_scan(
            wh, session_date=day, experiment_id="E", calendar_version="v",
            universe_expected=200, universe_scanned=200, symbol_failures=0,
            market_gate_status="EVALUATED", signals_authoritative=0,
            status=SessionStatus.COMPLETE,
        )

    # Only one session finished its trading-day phases.
    update_session_phase(
        wh, "2026-09-17", entry_phase_status="COMPLETE",
        eod_phase_status="COMPLETE", package_status="COMPLETE",
    )

    assert observed_session_count(wh) == 1


def test_a_fully_observed_session_with_no_trade_still_counts(tmp_path):
    """The 60-session rule counts observed sessions, not trading days."""
    from app.services.snapback_observation_warehouse import SnapbackObservationWarehouse
    from app.services.snapback_session_ledger import (
        SessionStatus, observed_session_count, record_session_scan, update_session_phase,
    )

    wh = SnapbackObservationWarehouse(db_path=str(tmp_path / "w.db"))
    record_session_scan(
        wh, session_date="2026-09-17", experiment_id="E", calendar_version="v",
        universe_expected=200, universe_scanned=200, symbol_failures=0,
        market_gate_status="EVALUATED", signals_authoritative=0,
        status=SessionStatus.COMPLETE,
    )
    update_session_phase(
        wh, "2026-09-17", entry_phase_status="COMPLETE",
        eod_phase_status="COMPLETE", package_status="COMPLETE",
    )

    assert observed_session_count(wh) == 1


def test_gate_uses_the_supplied_session_count_not_entry_dates():
    from study.snapback_forward_gate import evaluate_forward_gate

    outcomes = [
        {
            "opportunity_id": f"OPP-{i}",
            "actual_total_pnl": 10.0, "actual_option_pnl": 10.0,
            "actual_futures_pnl": 0.0, "actual_costs": 1.0,
            "entry_ts": "2026-09-17T09:20:00+05:30",  # all on one day
            "authoritative": 1,
        }
        for i in range(3)
    ]

    verdict = evaluate_forward_gate(
        records={"outcomes": outcomes, "paper_positions": [], "daily_mtm": [],
                 "option_quotes": [], "quote_quality_events": [
                     {"required_for_economics": 1, "accepted": 1} for _ in range(20)
                 ]},
        observed_sessions=42,
    )

    # Three trades on one entry date, but 42 fully observed sessions.
    assert verdict["total_sessions"] == 42


# --------------------------------------------------------- quote coverage


def _outcome(i=0):
    return {
        "opportunity_id": f"OPP-{i}", "actual_total_pnl": 10.0,
        "actual_option_pnl": 10.0, "actual_futures_pnl": 0.0, "actual_costs": 1.0,
        "entry_ts": f"2026-09-{i + 1:02d}T09:20:00+05:30", "authoritative": 1,
    }


def test_no_attempt_evidence_with_trades_is_a_data_quality_failure():
    from study.snapback_forward_gate import evaluate_forward_gate

    verdict = evaluate_forward_gate(
        records={
            "outcomes": [_outcome()],
            "paper_positions": [], "daily_mtm": [],
            # The legacy table is populated and perfect-looking...
            "option_quotes": [{"bid": 10.0, "ask": 11.0, "is_stale": 0}],
            # ...but no required attempt was ever recorded.
            "quote_quality_events": [],
        },
    )

    assert verdict["data_quality_ok"] is False
    assert any("attempt" in e for e in verdict["data_quality_errors"])
    assert verdict["verdict"] == "INCONCLUSIVE"


def test_legacy_option_quotes_cannot_substitute_for_attempts():
    from study.snapback_forward_gate import build_gate_inputs

    inputs = build_gate_inputs(records={
        "outcomes": [_outcome()], "paper_positions": [], "daily_mtm": [],
        "option_quotes": [{"bid": 10.0, "ask": 11.0, "is_stale": 0}] * 10,
        "quote_quality_events": [],
    })

    # No fabricated coverage from the old table.
    assert inputs["quote_coverage_pct"] is None


def test_coverage_comes_from_attempts_when_present():
    from study.snapback_forward_gate import build_gate_inputs

    inputs = build_gate_inputs(records={
        "outcomes": [], "paper_positions": [], "daily_mtm": [], "option_quotes": [],
        "quote_quality_events": [
            {"required_for_economics": 1, "accepted": 1},
            {"required_for_economics": 1, "accepted": 0},
        ],
    })

    assert inputs["quote_coverage_pct"] == pytest.approx(50.0)


# ------------------------------------------------- writer-side economics


@pytest.fixture
def warehouse():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as fh:
        path = fh.name
    from app.services.snapback_observation_warehouse import SnapbackObservationWarehouse

    wh = SnapbackObservationWarehouse(db_path=path)
    yield wh
    if os.path.exists(path):
        os.remove(path)


def _full_outcome(**over):
    base = {
        "outcome_id": "OUT-1", "opportunity_id": "OPP-1", "symbol": "NIFTY",
        "exit_reason": "PREMIUM_STOP",
        "entry_ts": "2026-10-01T09:20:00+05:30",
        "exit_ts": "2026-10-03T11:00:00+05:30",
        "actual_option_pnl": -1000.0, "actual_futures_pnl": 200.0,
        "actual_costs": 120.0, "actual_total_pnl": -920.0,
    }
    base.update(over)
    return base


@pytest.mark.parametrize("field", [
    "actual_option_pnl", "actual_futures_pnl", "actual_costs", "actual_total_pnl",
    "entry_ts", "exit_ts", "exit_reason",
])
def test_a_missing_required_field_is_refused_at_the_writer(warehouse, field):
    from app.services.snapback_observation_warehouse import EvidenceIntegrityError

    payload = _full_outcome()
    payload.pop(field)

    with pytest.raises(EvidenceIntegrityError):
        warehouse.commit_paper_close_transaction(
            opportunity_id="OPP-1", outcome_data=payload, cost_events=[],
        )

    assert warehouse.get_records_by_table("outcomes", opportunity_id="OPP-1") == []


def test_a_none_economic_value_is_refused(warehouse):
    from app.services.snapback_observation_warehouse import EvidenceIntegrityError

    with pytest.raises(EvidenceIntegrityError):
        warehouse.commit_paper_close_transaction(
            opportunity_id="OPP-1",
            outcome_data=_full_outcome(actual_costs=None), cost_events=[],
        )


def test_a_non_finite_value_is_refused(warehouse):
    from app.services.snapback_observation_warehouse import EvidenceIntegrityError

    with pytest.raises(EvidenceIntegrityError):
        warehouse.commit_paper_close_transaction(
            opportunity_id="OPP-1",
            outcome_data=_full_outcome(actual_total_pnl=float("nan")), cost_events=[],
        )


def test_a_complete_outcome_still_writes(warehouse):
    result = warehouse.commit_paper_close_transaction(
        opportunity_id="OPP-1", outcome_data=_full_outcome(), cost_events=[],
    )

    assert result["status"] == "RECORDED"
    assert len(warehouse.get_records_by_table("outcomes", opportunity_id="OPP-1")) == 1


# ------------------------------------------------------ broker guard scope


@pytest.fixture(autouse=True)
def _family_mode(monkeypatch):
    monkeypatch.setenv("STERLING_FAMILY_MODE", "true")
    yield
    monkeypatch.delenv("STERLING_FAMILY_MODE", raising=False)


@pytest.mark.parametrize("operation", [
    "place_order", "modify_order", "place_gtt", "modify_gtt",
    "place_mf_order", "place_mf_sip", "modify_mf_sip", "convert_position",
])
def test_every_exposure_changing_mutation_is_guarded(operation):
    from app.services.snapback_family_mode import guard_broker_write

    with pytest.raises(PermissionError):
        guard_broker_write(operation)


@pytest.mark.parametrize("operation", ["cancel_order", "cancel_gtt"])
def test_protective_cancels_are_allowed_under_a_declared_capability(operation):
    from app.services.snapback_family_mode import (
        canonical_broker_capability, guard_broker_write,
    )

    # Cancelling is how a halted system protects itself; it must stay possible,
    # but still inside a capability that says what it is.
    with canonical_broker_capability("EXIT-1", intent="CANCEL"):
        guard_broker_write(operation)


def test_a_reduce_exposure_capability_does_not_authorise_an_entry():
    from app.services.snapback_family_mode import (
        canonical_broker_capability, guard_broker_write,
    )

    with canonical_broker_capability("EXIT-1", intent="REDUCE_EXPOSURE"):
        with pytest.raises(PermissionError):
            guard_broker_write("place_order")


def test_transport_methods_consult_the_guard():
    import inspect

    from app.services.exchanges.kite.client import KiteClient

    for name in ("place_order", "modify_order"):
        fn = getattr(KiteClient, name, None)
        if fn is None:
            continue
        assert "guard_broker_write" in inspect.getsource(fn), name
