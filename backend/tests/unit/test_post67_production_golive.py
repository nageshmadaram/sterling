"""The acceptance tests §31 adds on top of the 67 fixes.

Each one names a way the go-live could be wrong while every existing suite
stayed green: a shadow intent that quietly reaches a broker, an options result
counted as a futures result, one account's fills reconciled as another's, a
challenger that starts with somebody else's sample, an order permitted from a
host whose outbound address was never verified.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.core.broker_account_binding import (
    BindingError,
    BindingStatus,
    BrokerAccountBinding,
    SecretInBindingError,
    segment_id,
)
from app.core.capital_permission import EntryPermissionInputs, may_send_entry
from app.core.evidence import EvidenceClass
from app.core.execution_regime import (
    POOLED_FORWARD,
    SEPARATE_REGIMES,
    PoolingNotDeclared,
    assert_pooling_declared,
    regime_statistics,
)
from app.core.execution_vehicle import (
    ExecutionVehicle,
    VehicleError,
    VehiclePolicy,
    assert_same_vehicle,
    is_authoritative_live_vehicle,
)
from app.core.lane_promotion import collect_lane_evidence
from app.engines.sterling_kite_engine import directional_challenger as challenger
from app.engines.sterling_kite_engine.lanes import identity_for as core_identity_for
from app.services.account_binding_service import (
    AccountBindingStore,
    FillAttributionError,
    attribute_fill,
)
from app.services.continuity_doctor import egress_status, order_placement_ready
from app.services.shadow_execution import (
    MarketFacts,
    OrderCapabilityError,
    ShadowExecutionService,
    ShadowIntent,
    ShadowStore,
    assert_read_only,
)
from app.core.shadow_record import BookObservation, RefusalReason, ShadowOutcome

SHA = "0123456789abcdef0123456789abcdef01234567"
TAG = "release/sterling-family-v1"


# --------------------------------------------------------------------------
# Shadow no-send
# --------------------------------------------------------------------------
class _BrokerLikeClient:
    def place_order(self, **_kwargs):  # pragma: no cover - must never be called
        raise AssertionError("the shadow path placed an order")


class _ReadOnlyFeed:
    def quote(self, _symbol):
        return {"bid": 100.0, "ask": 100.5}


def test_shadow_service_refuses_any_client_that_can_place_orders():
    with pytest.raises(OrderCapabilityError):
        ShadowExecutionService(market_reader=_BrokerLikeClient())

    # A genuine reader is accepted, so the guard is about capability, not type.
    service = ShadowExecutionService(market_reader=_ReadOnlyFeed(), store=ShadowStore("/tmp/unused"))
    assert service.market_reader is not None


def test_assert_read_only_names_every_ordering_method():
    class Mixed:
        def place_order(self):  # pragma: no cover
            ...

        def cancel_order(self):  # pragma: no cover
            ...

    with pytest.raises(OrderCapabilityError) as exc:
        assert_read_only(Mixed())
    assert "place_order" in str(exc.value)
    assert "cancel_order" in str(exc.value)


def test_shadow_intent_never_produces_a_fill_it_did_not_observe(tmp_path):
    service = ShadowExecutionService(store=ShadowStore(tmp_path))
    intent = ShadowIntent(
        lane_key="snapback:swing",
        session_date="2026-09-18",
        signal_at="2026-09-18T09:20:03+05:30",
        contract="NIFTY26SEP24000CE",
        quantity=50,
        reference_price=120.0,
        execution_vehicle=ExecutionVehicle.OPTIONS_LONG,
    )

    unknown_margin = service.record(intent, MarketFacts(book=BookObservation("t", ask=121.0, ask_qty=100)))
    assert unknown_margin.outcome is ShadowOutcome.REFUSED
    assert unknown_margin.refusal_reason is RefusalReason.UNKNOWN_MARGIN
    assert unknown_margin.filled_quantity == 0
    assert unknown_margin.hypothetical_fill_price is None

    unlisted = service.evaluate(intent, MarketFacts(contract_listed=False))
    assert unlisted.refusal_reason is RefusalReason.NO_LISTED_CONTRACT

    stale = service.evaluate(
        intent,
        MarketFacts(book=BookObservation("t", ask=121.0, ask_qty=100), quote_age_seconds=60.0),
    )
    assert stale.refusal_reason is RefusalReason.STALE_QUOTE


def test_shadow_partial_fill_is_recorded_as_partial_not_rounded_up(tmp_path):
    service = ShadowExecutionService(store=ShadowStore(tmp_path))
    intent = ShadowIntent(
        lane_key="snapback:swing",
        session_date="2026-09-18",
        signal_at="2026-09-18T09:20:03+05:30",
        contract="NIFTY26SEP24000CE",
        quantity=100,
        reference_price=120.0,
        execution_vehicle=ExecutionVehicle.OPTIONS_LONG,
    )
    record = service.record(
        intent,
        MarketFacts(
            book=BookObservation("t", ask=121.0, ask_qty=40),
            broker_margin=50_000.0,
            margin_available=200_000.0,
            protection_feasible=True,
        ),
    )
    assert record.outcome is ShadowOutcome.PARTIAL
    assert record.filled_quantity == 40
    assert record.slippage == pytest.approx(1.0)

    # And it round-trips through the store, so the report reads what was written.
    rows = ShadowStore(tmp_path).read("2026-09-18")
    assert [r["outcome"] for r in rows][-1] == "PARTIAL"


def test_unknown_depth_is_unobserved_not_a_full_fill(tmp_path):
    service = ShadowExecutionService(store=ShadowStore(tmp_path))
    intent = ShadowIntent(
        lane_key="supertrend:swing",
        session_date="2026-09-18",
        signal_at="2026-09-18T10:00:00+05:30",
        contract="NIFTY26SEPFUT",
        quantity=50,
        reference_price=24_000.0,
        execution_vehicle=ExecutionVehicle.FUTURES,
    )
    record = service.evaluate(
        intent,
        MarketFacts(
            book=BookObservation("t", ask=24_010.0, ask_qty=None),
            broker_margin=100_000.0,
            margin_available=500_000.0,
            protection_feasible=True,
        ),
    )
    assert record.outcome is ShadowOutcome.UNOBSERVED
    assert record.filled_quantity == 0


# --------------------------------------------------------------------------
# Vehicle isolation
# --------------------------------------------------------------------------
def _row(lane: str, vehicle: str, pnl: float, date: str, identity: str = "id1") -> dict:
    return {
        "lane_key": lane,
        "execution_vehicle": vehicle,
        "authoritative": 1,
        "evidence_class": EvidenceClass.BROKER.value,
        "actual_total_pnl": pnl,
        "actual_costs": 10.0,
        "entry_date": date,
        "identity_hash": identity,
    }


def test_long_option_rows_cannot_count_toward_a_futures_sample():
    rows = [
        _row("supertrend:swing", "OPTIONS_LONG", 100.0, "2026-09-01"),
        _row("supertrend:swing", "OPTIONS_LONG", -50.0, "2026-09-02"),
        _row("supertrend:swing", "FUTURES", 20.0, "2026-09-03"),
    ]
    futures = collect_lane_evidence(rows, "supertrend:swing", required_vehicle="FUTURES")
    assert futures.eligible == 1
    assert futures.excluded_other_lane == 2

    options = collect_lane_evidence(rows, "supertrend:swing", required_vehicle="OPTIONS_LONG")
    assert options.eligible == 2


def test_pooling_two_vehicles_is_refused_outright():
    rows = [
        _row("supertrend:swing", "OPTIONS_LONG", 100.0, "2026-09-01"),
        _row("supertrend:swing", "FUTURES", 20.0, "2026-09-03"),
    ]
    with pytest.raises(VehicleError):
        assert_same_vehicle(rows, context="supertrend:swing")


def test_a_row_with_no_declared_vehicle_is_not_assumed():
    rows = [{"lane_key": "snapback:swing"}]
    with pytest.raises(VehicleError):
        assert_same_vehicle(rows)


def test_paper_synthetic_can_never_be_a_live_vehicle():
    assert is_authoritative_live_vehicle(ExecutionVehicle.PAPER_SYNTHETIC) is False
    assert is_authoritative_live_vehicle(ExecutionVehicle.FUTURES) is True
    with pytest.raises(VehicleError):
        VehiclePolicy(
            vehicle=ExecutionVehicle.PAPER_SYNTHETIC,
            instrument_class="X",
            product="NRML",
            roll_policy="none",
            research_only=False,
        )


def test_vehicle_contract_hash_moves_when_the_product_changes():
    base = VehiclePolicy(
        vehicle=ExecutionVehicle.FUTURES,
        instrument_class="INDEX_FUT",
        product="NRML",
        roll_policy="near_month",
    )
    intraday = VehiclePolicy(
        vehicle=ExecutionVehicle.FUTURES,
        instrument_class="INDEX_FUT",
        product="MIS",
        roll_policy="near_month",
    )
    assert base.vehicle_contract_hash != intraday.vehicle_contract_hash


# --------------------------------------------------------------------------
# Account-binding isolation and migration
# --------------------------------------------------------------------------
def _binding(store: AccountBindingStore, client_id: str, holder: str, scope: str = "shadow"):
    return store.declare(
        broker="zerodha",
        legal_account_holder=holder,
        client_id=client_id,
        account_scope=scope,
        deployment_id="prod-1",
        static_egress_profile="vps-mumbai",
    )


def test_fills_from_one_account_cannot_reconcile_as_another(tmp_path):
    store = AccountBindingStore(tmp_path)
    account_a = _binding(store, "AB1234", "Original Holder")
    account_b = _binding(store, "CD5678", "Authorised Heir")

    fill = attribute_fill({"order_id": "1", "qty": 50}, account_a)
    assert fill["account_segment"] == segment_id(account_a)

    with pytest.raises(FillAttributionError):
        attribute_fill(fill, account_b)


def test_a_fill_reported_for_another_client_is_refused(tmp_path):
    store = AccountBindingStore(tmp_path)
    account = _binding(store, "AB1234", "Original Holder")
    with pytest.raises(FillAttributionError):
        attribute_fill({"client_id": "ZZ9999"}, account)


def test_migration_keeps_the_strategy_identity_and_changes_only_the_segment(tmp_path):
    store = AccountBindingStore(tmp_path)
    old = store.activate(_binding(store, "AB1234", "Original Holder").binding_id)
    identity_before = core_identity_for("swing", runtime_sha=SHA, release_tag=TAG)

    # Two ACTIVE bindings is refused: the incumbent is retired explicitly.
    new = _binding(store, "CD5678", "Authorised Heir")
    with pytest.raises(BindingError):
        store.activate(new.binding_id)

    store.deactivate(old.binding_id, reason="account holder deceased; transmission complete")
    activated = store.activate(new.binding_id)

    identity_after = core_identity_for("swing", runtime_sha=SHA, release_tag=TAG)
    assert identity_after.identity_hash == identity_before.identity_hash
    assert segment_id(activated) != segment_id(old)

    retired = store.get(old.binding_id)
    assert retired.status is BindingStatus.DEACTIVATED
    assert retired.usable is False


def test_a_deactivated_binding_can_never_be_reactivated(tmp_path):
    store = AccountBindingStore(tmp_path)
    binding = _binding(store, "AB1234", "Original Holder")
    store.deactivate(binding.binding_id)
    with pytest.raises(BindingError):
        store.activate(binding.binding_id)


def test_binding_metadata_refuses_credentials(tmp_path):
    store = AccountBindingStore(tmp_path)
    with pytest.raises((SecretInBindingError, BindingError)):
        store.declare(
            broker="zerodha",
            legal_account_holder="Holder",
            client_id="AB1234",
            account_scope="live_minimum",
            deployment_id="prod-1",
            password="hunter2",
        )


def test_shadow_scope_binding_is_usable_but_not_live_ready(tmp_path):
    store = AccountBindingStore(tmp_path)
    binding = store.activate(_binding(store, "AB1234", "Holder", scope="shadow").binding_id)
    assert binding.usable is True
    assert binding.live_ready is False

    live = BrokerAccountBinding(
        binding_id="zerodha-ef9012-20260918",
        broker="zerodha",
        legal_account_holder="Holder",
        client_id="EF9012",
        account_scope="live_minimum",
        deployment_id="prod-1",
        static_egress_profile="vps-mumbai",
    ).activated()
    assert live.live_ready is True


# --------------------------------------------------------------------------
# Static egress readiness
# --------------------------------------------------------------------------
def _stamp(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat()


def test_egress_is_not_verified_without_a_registered_address():
    assert egress_status(env={}).verified is False


def test_egress_mismatch_and_staleness_both_refuse():
    mismatch = egress_status(
        env={
            "STERLING_STATIC_EGRESS_IP": "203.0.113.7",
            "STERLING_OBSERVED_EGRESS_IP": "198.51.100.2",
            "STERLING_EGRESS_VERIFIED_AT": _stamp(0),
        }
    )
    assert mismatch.verified is False

    stale = egress_status(
        env={
            "STERLING_STATIC_EGRESS_IP": "203.0.113.7",
            "STERLING_OBSERVED_EGRESS_IP": "203.0.113.7",
            "STERLING_EGRESS_VERIFIED_AT": _stamp(30),
        }
    )
    assert stale.verified is False

    current = egress_status(
        env={
            "STERLING_STATIC_EGRESS_IP": "203.0.113.7",
            "STERLING_OBSERVED_EGRESS_IP": "203.0.113.7",
            "STERLING_EGRESS_VERIFIED_AT": _stamp(1),
        }
    )
    assert current.verified is True


def test_order_placement_blocks_when_egress_is_not_current(monkeypatch):
    monkeypatch.delenv("STERLING_STATIC_EGRESS_IP", raising=False)
    ready, blockers = order_placement_ready(
        binding_live_ready=True,
        broker_session_valid=True,
        api_order_permission_verified=True,
        safety_normal=True,
        lane_capital_state="LIVE_MINIMUM",
    )
    assert ready is False
    assert "static_egress_verified" in blockers


# --------------------------------------------------------------------------
# Long options cannot reach real money; the challenger starts at zero
# --------------------------------------------------------------------------
def test_no_supertrend_lane_may_send_a_real_entry_today():
    from app.core.lane_registry import LANES, LIVE_EXECUTION_ENABLED

    assert LIVE_EXECUTION_ENABLED is False
    for key, lane in LANES.items():
        verdict = may_send_entry(
            EntryPermissionInputs(lane_key=key, lane_state=lane.state.value)
        )
        assert verdict.allowed is False
        assert "GLOBAL_LIVE_DISABLED" in verdict.blockers


def test_permission_requires_all_nine_conditions_together():
    passing = dict(
        global_live_switch=True,
        release_certified=True,
        account_binding_ready=True,
        safety_may_increase_exposure=True,
        lane_state="LIVE_MINIMUM",
        lane_promotion_passed=True,
        lane_identity_matches_frozen=True,
        shadow_gate_passed=True,
        risk_gate_passed=True,
        exposure_gate_passed=True,
        vehicle_live_capable=True,
    )
    assert may_send_entry(EntryPermissionInputs(lane_key="snapback:swing", **passing)).allowed

    for field in passing:
        broken = dict(passing)
        broken[field] = "" if field == "lane_state" else False
        verdict = may_send_entry(EntryPermissionInputs(lane_key="snapback:swing", **broken))
        assert verdict.allowed is False, f"{field} alone did not refuse"


def test_an_unknown_input_refuses_and_is_reported_as_unknown():
    verdict = may_send_entry(
        EntryPermissionInputs(
            lane_key="snapback:swing",
            global_live_switch=True,
            release_certified=True,
            account_binding_ready=True,
            safety_may_increase_exposure=True,
            lane_state="LIVE_MINIMUM",
            lane_promotion_passed=None,
            lane_identity_matches_frozen=True,
            shadow_gate_passed=True,
            risk_gate_passed=True,
            exposure_gate_passed=True,
            vehicle_live_capable=True,
        )
    )
    assert verdict.allowed is False
    assert "lane_promotion_passed" in verdict.unknowns


def test_directional_challenger_has_its_own_identity_and_zero_sample():
    manifest = challenger.challenger_manifest(runtime_sha=SHA, release_tag=TAG)
    assert manifest["sample"] == 0
    assert manifest["live_orders"] == "DISABLED"
    assert manifest["track"] == "C"
    assert manifest["execution_vehicle"] == ExecutionVehicle.FUTURES.value

    core = core_identity_for("swing", runtime_sha=SHA, release_tag=TAG)
    assert manifest["identity"]["identity_hash"] != core.identity_hash
    assert manifest["identity"]["rule_hash"] != core.rule_hash
    # The signal core itself is untouched: this is a vehicle challenger.
    assert manifest["signal_core"] == core.strategy_version


def test_challenger_refuses_to_place_an_order_and_to_enter_on_a_forming_bar():
    engine = challenger.DirectionalChallenger()
    with pytest.raises(challenger.LiveOrdersDisabled):
        engine.place_order(symbol="NIFTY")

    forming = challenger.DirectionalSignal(
        symbol="NIFTY",
        direction="bull",
        signal_at="2026-09-18T10:00:00+05:30",
        underlying_price=24_000.0,
        finalized=False,
    )
    with pytest.raises(Exception):
        engine.shadow_intent(forming, session_date="2026-09-18", contract="NIFTY26SEPFUT")

    finalized = challenger.DirectionalSignal(
        symbol="NIFTY",
        direction="bear",
        signal_at="2026-09-18T10:00:00+05:30",
        underlying_price=24_000.0,
        finalized=True,
    )
    intent = engine.shadow_intent(finalized, session_date="2026-09-18", contract="NIFTY26SEPFUT")
    assert intent.execution_vehicle is ExecutionVehicle.FUTURES
    assert intent.lane_key.endswith(challenger.CHALLENGER_SLUG)
    # A bear signal is expressible because the vehicle permits shorting.
    assert "SELL" in intent.notes


def test_challenger_evidence_does_not_land_in_the_track_a_lane():
    rows = [
        _row("supertrend:swing", "OPTIONS_LONG", 100.0, "2026-09-01"),
        _row(
            f"supertrend:swing#{challenger.CHALLENGER_SLUG}",
            "FUTURES",
            30.0,
            "2026-09-01",
        ),
    ]
    track_a = collect_lane_evidence(rows, "supertrend:swing", required_vehicle="OPTIONS_LONG")
    assert track_a.eligible == 1


# --------------------------------------------------------------------------
# Execution regimes stay separate
# --------------------------------------------------------------------------
def test_regimes_are_reported_separately_and_not_pooled_by_default():
    rows = [
        {**_row("snapback:swing", "OPTIONS_LONG", 10.0, "2026-09-01"), "evidence_class": "paper"},
        {**_row("snapback:swing", "OPTIONS_LONG", 10.0, "2026-09-02"), "evidence_class": "shadow"},
        {**_row("snapback:swing", "OPTIONS_LONG", 10.0, "2026-09-03"), "evidence_class": "broker"},
    ]
    stats = regime_statistics(rows, "snapback:swing")
    assert stats["paper"]["trades"] == 1
    assert stats["shadow"]["trades"] == 1
    assert stats["broker"]["trades"] == 1

    with pytest.raises(PoolingNotDeclared):
        assert_pooling_declared(
            SEPARATE_REGIMES, [EvidenceClass.PAPER, EvidenceClass.BROKER]
        )
    # A predeclared policy may pool; nothing else may.
    assert_pooling_declared(POOLED_FORWARD, [EvidenceClass.PAPER, EvidenceClass.BROKER])


def test_a_pooling_policy_must_say_when_and_why_it_was_declared():
    from app.core.execution_regime import PromotionPolicy

    with pytest.raises(PoolingNotDeclared):
        PromotionPolicy(
            policy_id="ad_hoc",
            pooled_classes={EvidenceClass.PAPER, EvidenceClass.BROKER},
        )


# --------------------------------------------------------------------------
# Release certification and the handoff restart
# --------------------------------------------------------------------------
def test_an_unrecorded_gate_is_not_a_passed_gate(tmp_path):
    from app.services.release_certification import (
        CertificationStore,
        certification_report,
    )

    store = CertificationStore(tmp_path)
    report = certification_report(sha=SHA, tag=TAG, store=store, unresolved_exposure=0)
    assert report.release_ready is False
    assert {r.key for r in report.unknowns}

    store.attest(SHA, "remote_ci", "PASS", attested_by="operator", evidence_ref="ci/123")
    again = certification_report(sha=SHA, tag=TAG, store=store, unresolved_exposure=0)
    assert again.by_key["remote_ci"].passed is True
    assert again.release_ready is False  # the rest are still unrecorded


def test_a_pass_must_name_who_observed_it(tmp_path):
    from app.services.release_certification import CertificationStore

    with pytest.raises(ValueError):
        CertificationStore(tmp_path).attest(SHA, "reconnect", "PASS", attested_by="  ")


def test_unknown_unresolved_exposure_blocks_release(tmp_path):
    from app.services.release_certification import (
        RELEASE_GATES,
        CertificationStore,
        certification_report,
    )

    from app.services.release_certification import _DERIVED_GATES

    store = CertificationStore(tmp_path)
    for gate in RELEASE_GATES:
        # A gate derived from its own per-item register cannot be attested in
        # one line — that blanket claim is what the register replaced.
        if gate.key in _DERIVED_GATES:
            continue
        store.attest(SHA, gate.key, "PASS", attested_by="operator")

    unknown = certification_report(sha=SHA, tag=TAG, store=store, unresolved_exposure=None)
    assert unknown.release_ready is False

    open_exposure = certification_report(sha=SHA, tag=TAG, store=store, unresolved_exposure=1)
    assert open_exposure.release_ready is False


def test_attestations_do_not_carry_across_shas(tmp_path):
    from app.services.release_certification import CertificationStore

    store = CertificationStore(tmp_path)
    store.attest(SHA, "remote_ci", "PASS", attested_by="operator")
    other = "f" * 40
    assert store.read(other) == {}


def test_a_fresh_deployment_that_reported_nothing_reads_recovery_required():
    from app.core.health import SystemHealth, compose_health

    assert compose_health({}).overall is SystemHealth.RECOVERY_REQUIRED


# --------------------------------------------------------------------------
# Non-developer operability
# --------------------------------------------------------------------------
def test_sterlingctl_documents_every_continuity_command():
    from pathlib import Path

    script = Path(__file__).resolve().parents[3] / "scripts" / "sterlingctl"
    text = script.read_text(encoding="utf-8")
    for command in ("certify", "bindings", "bind ", "migrate", "continuity", "shadow", "permission"):
        assert command in text, f"sterlingctl does not offer {command!r}"


def test_continuity_checklist_items_start_unanswered(tmp_path):
    from app.services.continuity_doctor import load_checklist, record_answer

    path = tmp_path / "continuity.json"
    assert all(not item.acknowledged for item in load_checklist(path))

    record_answer("authorised_operator", "The account holder's spouse, client CD5678.", path=path)
    items = {i.key: i for i in load_checklist(path)}
    assert items["authorised_operator"].acknowledged is True
    assert items["nominee"].acknowledged is False


def test_a_blank_continuity_answer_is_not_an_answer(tmp_path):
    from app.services.continuity_doctor import record_answer

    with pytest.raises(ValueError):
        record_answer("nominee", "   ", path=tmp_path / "continuity.json")


def test_continuity_answers_refuse_secrets(tmp_path):
    from app.services.continuity_doctor import record_answer

    with pytest.raises(SecretInBindingError):
        record_answer("secrets", "in the vault", path=tmp_path / "c.json")


def test_release_manifest_exposes_the_vehicle_as_a_first_class_field():
    from app.core.release_manifest import build_release_manifest

    manifest = build_release_manifest(sha=SHA, tag=TAG)
    assert all(lane["execution_vehicle"] for lane in manifest["lanes"])
    assert manifest["challengers"][0]["execution_vehicle"] == ExecutionVehicle.FUTURES.value
