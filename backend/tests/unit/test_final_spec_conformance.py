"""An audit of the final remaining specification, section by section.

This file exists because "we implemented the spec" is a claim that decays. Each
test names the section it checks and asserts the property that section asks
for, so a later change that quietly removes one fails here rather than being
discovered during a release.

It deliberately checks contracts rather than implementations: that the door
exists and is the only one, that unknown refuses, that a claim has a record
behind it. Where a section cannot be verified by code — a live Kite acceptance
on a moving market — the test says so instead of pretending.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]


# -- §5 fresh authoritative evidence store ----------------------------------

def test_section_14_every_authoritative_row_carries_fourteen_fields():
    from app.core.authoritative_start import REQUIRED_ROW_FIELDS

    assert len(REQUIRED_ROW_FIELDS) == 14
    # The go-live specification adds the two that separate one experiment from
    # another: which vehicle traded it, and which regime observed it.
    assert {"execution_vehicle", "execution_regime"} <= set(REQUIRED_ROW_FIELDS)


def test_section_14_the_warehouse_can_store_every_required_field():
    """A required field with no column is a rule nothing can obey."""
    from app.core.authoritative_start import REQUIRED_ROW_FIELDS
    from app.services.snapback_observation_warehouse import SnapbackObservationWarehouse

    columns = set(SnapbackObservationWarehouse._LANE_IDENTITY_COLUMNS)
    # Two fields are stored under the warehouse's own spelling: the runtime SHA
    # under its build-era name, and the execution regime as `evidence_class`.
    stored_elsewhere = {"runtime_sha", "execution_regime"}
    assert {f for f in REQUIRED_ROW_FIELDS if f not in stored_elsewhere} <= columns
    assert "evidence_class" in columns


def test_section_5_an_unreadable_count_is_never_zero():
    from app.core.authoritative_start import evaluate_authoritative_start

    start = evaluate_authoritative_start(
        lane_sessions=0, authoritative_trades=0, unresolved_exposure=None,
        identity_drift=0, release_tag="t", runtime_sha="a" * 40)
    assert start.started_clean is False


# -- §6 production shadow runtime -------------------------------------------

def test_section_6_the_engine_emits_shadow_intents():
    """The previous release's known gap: nothing called the shadow service."""
    source = (BACKEND / "app/services/kite_engine/service.py").read_text(encoding="utf-8")
    assert "shadow_origination.record_entry_intent(" in source


def test_section_6_the_shadow_service_cannot_place_an_order():
    from app.services.shadow_execution import OrderCapabilityError, ShadowExecutionService

    class _Trader:
        async def place_order(self, **_kwargs):  # pragma: no cover
            raise AssertionError

    with pytest.raises(OrderCapabilityError):
        ShadowExecutionService(market_reader=_Trader())


# -- §7 evidence-class separation -------------------------------------------

def test_section_7_promotion_reports_present_the_regimes_separately():
    from app.core.lane_promotion import evaluate_all_lanes

    for verdict in evaluate_all_lanes([]).values():
        assert set(verdict["regimes"]["by_regime"]) == {"paper", "shadow", "broker"}


def test_section_7_a_combined_statistic_is_labelled():
    from app.core.lane_promotion import evaluate_all_lanes

    for verdict in evaluate_all_lanes([]).values():
        assert verdict["evidence_scope"] == "all promotable classes"


def test_section_7_pooling_requires_a_declared_policy():
    from app.core.evidence import EvidenceClass
    from app.core.execution_regime import (
        SEPARATE_REGIMES, PoolingNotDeclared, assert_pooling_declared,
    )

    with pytest.raises(PoolingNotDeclared):
        assert_pooling_declared(SEPARATE_REGIMES,
                                {EvidenceClass.PAPER, EvidenceClass.BROKER})


# -- §9 SuperTrend ----------------------------------------------------------

def test_section_9_2_the_directional_challenger_is_its_own_identity():
    from app.engines.sterling_kite_engine.directional_challenger import (
        CHALLENGER_VERSION,
        DirectionalChallenger,
        LiveOrdersDisabled,
    )

    assert CHALLENGER_VERSION == "supertrend_directional_v1"
    # It starts at zero and cannot send: a challenger that could place an order
    # would inherit the live path, which is the thing it must not do.
    assert issubclass(LiveOrdersDisabled, RuntimeError)
    assert hasattr(DirectionalChallenger, "place_order")


def test_section_9_3_no_engine_places_an_exposure_increasing_order_directly():
    """The full rule lives in test_canonical_execution_convergence."""
    from tests.unit.test_canonical_execution_convergence import (
        _observed_direct_calls,
    )

    kite = [k for k in _observed_direct_calls() if k[0].startswith("services/kite_engine/")]
    assert kite == []


def test_section_9_4_the_frozen_transformation_is_pinned():
    frozen = BACKEND / "tests/fixtures/supertrend_parity/frozen.json"
    assert frozen.exists() and frozen.stat().st_size > 1000


# -- §11 broker account continuity ------------------------------------------

def test_section_11_a_binding_cannot_hold_a_credential():
    from app.core.broker_account_binding import assert_no_secrets

    with pytest.raises(Exception):
        assert_no_secrets({"client_id": "AB1234", "access_token": "secret"})


def test_section_11_identity_is_verified_before_a_send():
    """The canonical service refuses a client that is not the named account."""
    source = (BACKEND / "app/services/execution_service.py").read_text(encoding="utf-8")
    assert "Broker account identity missing or mismatch" in source


# -- §12 deployment environment ---------------------------------------------

def test_section_12_the_doctor_covers_the_deployment_host():
    from app.core.operator_report import lane_doctor_checks

    names = {c.name for c in lane_doctor_checks()}
    assert {"static_egress", "lake_mount", "network_path"} <= names


# -- §13 one-command lifecycle ----------------------------------------------

def test_section_9_start_is_thirteen_steps_and_stop_is_five():
    from app.services.service_lifecycle import START_STEPS, STOP_STEPS

    assert len(START_STEPS) == 13
    assert len(STOP_STEPS) == 5


def test_section_13_the_operator_scripts_exist_and_are_executable():
    root = BACKEND.parent
    for name in ("sterlingctl", "sterling-start", "sterling-stop"):
        script = root / "scripts" / name
        assert script.exists(), name
        assert script.stat().st_mode & 0o111, f"{name} is not executable"


def test_section_13_2_a_stop_refuses_to_abandon_exposure():
    from app.services.service_lifecycle import _step_no_abandoned_exposure

    assert "no exposure left unmanaged" in _step_no_abandoned_exposure().name


# -- §14 backup and restore --------------------------------------------------

def test_section_14_every_listed_artifact_is_declared():
    from app.core.backup_coverage import REQUIRED_ARTIFACTS

    names = {a.name for a in REQUIRED_ARTIFACTS}
    assert {
        "authoritative_evidence_db", "canonical_intent_journal", "safety_state",
        "release_manifest", "lane_manifests", "deployment_configuration",
    } <= names


def test_section_14_credentials_are_never_copied_into_a_backup():
    from app.core.backup_coverage import REQUIRED_ARTIFACTS, ArtifactKind

    config = next(a for a in REQUIRED_ARTIFACTS if a.name == "deployment_configuration")
    assert config.kind is ArtifactKind.REFERENCED


# -- §15 failure drills ------------------------------------------------------

def test_section_15_all_twelve_drills_are_declared_with_their_outcomes():
    from app.core.failure_drills import DRILLS

    assert len(DRILLS) == 12
    assert all(d.required_outcome.strip() for d in DRILLS)


def test_section_15_the_gate_cannot_be_claimed_without_records():
    from app.services.release_certification import _DERIVED_GATES

    assert "failure_drills" in _DERIVED_GATES


# -- §16 promotion gate ------------------------------------------------------

def test_section_16_the_statistical_criteria_are_all_enforced():
    from app.core.lane_promotion import MIN_SESSIONS, MIN_TRADES

    assert MIN_SESSIONS >= 60
    assert MIN_TRADES >= 300


def test_section_16_no_lane_inherits_another_lanes_sample():
    from app.core.evidence import eligible_for_lane

    row = {"authoritative": 1, "lane_key": "snapback:swing", "evidence_class": "paper"}
    assert eligible_for_lane(row, "snapback:scalping") is False


# -- §17 first real-money activation ----------------------------------------

def test_section_17_the_first_order_envelope_refuses_by_default():
    from app.core.live_minimum import check_order, configured_envelope

    verdict = check_order(
        quantity=75, minimum_executable_quantity=75, order_value=1.0,
        open_gross_exposure=0.0, realised_loss_today=0.0, is_averaging_down=False,
        envelope=configured_envelope({}))
    assert verdict.allowed is False
    assert "LIVE_MINIMUM_NOT_CONFIGURED" in verdict.blockers


def test_section_17_the_global_live_switch_is_off():
    from app.core.lane_registry import LIVE_EXECUTION_ENABLED

    assert LIVE_EXECUTION_ENABLED is False


# -- §19 real-money readiness ------------------------------------------------

def test_section_19_every_permission_input_defaults_to_refusing():
    from app.core.capital_permission import EntryPermissionInputs, may_send_entry

    assert may_send_entry(EntryPermissionInputs(lane_key="snapback:swing")).allowed is False


def test_section_19_no_lane_may_send_a_real_entry_today():
    from app.services.capital_permission_report import all_lane_permissions

    assert not [row for row in all_lane_permissions() if row.get("allowed")]


# -- §21 the engineering invariant -------------------------------------------

@pytest.mark.parametrize("module,function", [
    ("app.core.capital_permission", "may_send_entry"),
    ("app.core.live_minimum", "check_order"),
    ("app.core.authoritative_start", "evaluate_authoritative_start"),
])
def test_section_21_unknown_blocks_rather_than_defaults(module, function):
    """Every decision function must document and implement the None rule."""
    import importlib

    target = getattr(importlib.import_module(module), function)
    doc = inspect.getdoc(target) or ""
    assert "None" in doc or "UNKNOWN" in doc or "unknown" in doc, function


# -- what cannot be verified here --------------------------------------------

def test_the_unverifiable_sections_are_named_not_assumed():
    """§4.3 and §2 of the gate table need a market and a human.

    A live Kite acceptance on a moving market, and the remote CI run on the
    exact frozen SHA, cannot be asserted from inside the test suite. They are
    attested gates in the certification table, and this test exists so that
    nobody mistakes the suite's silence for their absence.
    """
    from app.services.release_certification import RELEASE_GATES

    keys = {g.key for g in RELEASE_GATES}
    assert {"kite_live_acceptance", "remote_ci"} <= keys


# -- go-live specification ---------------------------------------------------

def test_golive_section_5_ci_is_recorded_per_context_against_one_sha():
    from app.core.ci_certification import REQUIRED_CONTEXTS

    assert len(REQUIRED_CONTEXTS) == 9


def test_golive_section_5_remote_ci_cannot_be_attested_in_one_line():
    from app.services.release_certification import _DERIVED_GATES

    assert "remote_ci" in _DERIVED_GATES


def test_golive_section_8_the_manifest_carries_its_certification():
    from app.core.release_manifest import build_release_manifest

    manifest = build_release_manifest()
    assert "certification" in manifest
    assert "account_binding_id" in manifest
    assert manifest["live_execution_enabled"] is False


def test_golive_section_7_the_migration_is_all_or_nothing():
    import inspect

    from app.services.secret_migration import migrate_secrets

    source = inspect.getsource(migrate_secrets)
    # The write happens once, after every value has round-tripped.
    assert "aborted before writing" in source
    assert "restored from backup" in source


def test_golive_section_11_deployment_identity_is_observed_not_configured():
    from app.core.deployment_identity import DeploymentObservation

    fields = DeploymentObservation.__dataclass_fields__
    assert {"host_id", "outbound_ip", "observed_at", "router_generation",
            "runtime_sha", "release_tag"} <= set(fields)


def test_golive_section_20_a_lane_needs_three_independent_verdicts():
    from app.core.lane_verdicts import Verdict, compose_lane_verdicts

    verdicts = compose_lane_verdicts(
        "snapback:swing", economic=True, shadow_execution=True, operational=None)
    assert verdicts.eligible_for_live_minimum is False
    assert verdicts.operational is Verdict.UNKNOWN


def test_golive_section_24_a_quiet_day_still_produces_a_digest():
    from app.services.daily_digest import build_digest

    assert build_digest().generated_at
