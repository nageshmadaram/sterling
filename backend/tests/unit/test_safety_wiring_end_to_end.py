"""Proof that health, exposure, risk limits and the intent record are connected."""
from __future__ import annotations

import pytest

from app.core.exposure import Exposure
from app.core.health import SystemHealth
from app.core.risk_hierarchy import ENV_VAR, RiskConfigurationError, configured_hierarchy
from app.core.trade_intent import IntentState, IntentStore, ProtectionState
from app.services.snapback_capacity import ObservedHedgeMargin, evaluate_capacity
from app.services.snapback_system_health import system_health

FUNDABLE = dict(
    capital=1_000_000.0,
    reserved_margin=0.0,
    option_premium_cash=10_000.0,
    hedge_margin=ObservedHedgeMargin(20_000.0),
    fee_reserve=500.0,
    open_positions=0,
    max_open_positions=5,
    underlying="NIFTY",
)
LIMITS = (
    '{"global": {"": 100000}, "strategy": {"snapback": 60000},'
    ' "mode": {"snapback:swing": 30000}, "position": {"snapback:swing": 10000}}'
)


@pytest.fixture(autouse=True)
def _clean_env(tmp_path, monkeypatch):
    from tests.conftest import normal_safe_mode_file

    normal_safe_mode_file(monkeypatch, str(tmp_path / "safe_mode.json"))
    monkeypatch.delenv(ENV_VAR, raising=False)


def _exposure(lane="snapback:swing", **over) -> Exposure:
    base = dict(
        lane_key=lane, underlying="NIFTY", exchange="NFO",
        tradingsymbol="NIFTY26SEP24000CE", direction="long", quantity=75,
        expiry="2026-09-24", strike=24000.0, option_type="CE",
    )
    base.update(over)
    return Exposure(**base)


# ── exposure ──────────────────────────────────────────────────────────────


def test_an_unconflicting_exposure_is_admitted():
    decision = evaluate_capacity(
        **FUNDABLE, lane_key="snapback:swing",
        requested_exposure=_exposure(), open_exposures=[],
    )
    assert decision.allowed is True


def test_a_second_lane_on_the_same_contract_is_refused():
    decision = evaluate_capacity(
        **FUNDABLE, lane_key="snapback:swing",
        requested_exposure=_exposure(lane="snapback:scalping"),
        open_exposures=[_exposure()],
    )
    assert decision.allowed is False
    assert decision.status == "EXPOSURE_REFUSED"
    assert "EXPOSURE_CONFLICT_SAME_CONTRACT" in decision.reasons


def test_an_undeclared_interaction_is_refused_at_admission():
    spread = _exposure(
        lane="snapback:scalping", tradingsymbol="NIFTY26SEP24500PE",
        strike=24500.0, option_type="PE", direction="short",
    )
    decision = evaluate_capacity(
        **FUNDABLE, lane_key="snapback:swing",
        requested_exposure=spread, open_exposures=[_exposure()],
    )
    assert decision.status == "EXPOSURE_REFUSED"
    assert "EXPOSURE_INTERACTION_UNDECLARED" in decision.reasons


def test_a_different_venue_is_a_different_contract():
    sensex = _exposure(
        lane="snapback:scalping", underlying="SENSEX", exchange="BFO",
        tradingsymbol="SENSEX26SEP80000CE",
    )
    decision = evaluate_capacity(
        **FUNDABLE, lane_key="snapback:swing",
        requested_exposure=sensex, open_exposures=[_exposure()],
    )
    assert decision.allowed is True


# ── risk hierarchy ────────────────────────────────────────────────────────


def test_no_configured_hierarchy_is_not_consulted():
    """Inventing limits nobody set would be worse than declaring none."""
    assert configured_hierarchy() is None
    assert evaluate_capacity(**FUNDABLE, lane_key="snapback:swing").allowed is True


def test_a_configured_hierarchy_is_enforced(monkeypatch):
    monkeypatch.setenv(ENV_VAR, LIMITS)
    decision = evaluate_capacity(
        **FUNDABLE, lane_key="snapback:swing",
        risk_used={},
    )
    # 10_000 premium + 20_000 hedge = 30_000, over the 10_000 position limit.
    assert decision.allowed is False
    assert decision.status == "RISK_LIMIT_EXCEEDED_POSITION"


def test_a_lane_absent_from_the_limits_is_inconclusive_not_unlimited(monkeypatch):
    monkeypatch.setenv(ENV_VAR, LIMITS)
    decision = evaluate_capacity(
        **{**FUNDABLE, "option_premium_cash": 1.0, "hedge_margin": ObservedHedgeMargin(1.0)},
        lane_key="snapback:scalping",
    )
    assert decision.status == "INCONCLUSIVE_RISK_CONFIGURATION"


def test_a_malformed_limits_value_raises_rather_than_yielding_none(monkeypatch):
    """An operator who tried to set limits and mistyped must not get none."""
    monkeypatch.setenv(ENV_VAR, "{not json")
    with pytest.raises(RiskConfigurationError):
        configured_hierarchy()


def test_an_unknown_risk_level_raises(monkeypatch):
    monkeypatch.setenv(ENV_VAR, '{"portfolio": {"": 1}}')
    with pytest.raises(RiskConfigurationError):
        configured_hierarchy()


# ── system health ─────────────────────────────────────────────────────────


def test_system_health_reads_every_required_component():
    report = system_health()
    assert {c.name for c in report.components} >= {
        "broker", "market_data", "evidence", "reconciliation", "safe_mode"
    }
    assert report.missing == ()
    assert isinstance(report.overall, SystemHealth)


def test_a_probe_that_raises_is_recorded_as_failed(monkeypatch):
    """"The broker check threw" must never read the same as "the broker is fine"."""
    import app.services.snapback_system_health as mod

    def boom():
        raise ConnectionError("no session")

    monkeypatch.setitem(mod._COMPONENTS, "broker", boom)
    report = mod.system_health()
    broker = next(c for c in report.components if c.name == "broker")
    assert broker.status is SystemHealth.BROKER_ERROR
    assert "ConnectionError" in broker.detail
    assert report.may_open_new_exposure is False


def test_safe_mode_shows_in_the_composed_status(tmp_path, monkeypatch):
    path = tmp_path / "safe.json"
    path.write_text('{"state": "SAFE_MODE", "triggers": ["OPERATOR"], "reason": "t"}')
    from tests.conftest import normal_safe_mode_file

    normal_safe_mode_file(monkeypatch, str(path))
    report = system_health()
    safe = next(c for c in report.components if c.name == "safe_mode")
    assert safe.status is SystemHealth.SAFE_MODE
    assert report.may_open_new_exposure is False
    # Managing what is already open is never blocked.
    assert report.may_manage_existing_exposure is True


# ── the plan carries its venue, and the intent record is written ──────────


def _plan_store(tmp_path, *, option_exchange="NFO", futures_exchange="NFO"):
    from app.services.snapback_plan_store import PlanStore

    store = PlanStore(db_path=str(tmp_path / "plans.db"))
    store.publish(
        plan_id="PLAN-V", opportunity_id="OPP-V", account_id="ACC",
        option_symbol="SENSEX26SEP80000CE", option_quantity=10,
        futures_symbol="SENSEX26SEPFUT", target_futures_quantity=10,
        max_option_price=120.0, hedge_price_limit=80_500.0, risk_amount=1.0,
        cash_required=1.0, margin_required=1.0, policy_snapshot_hash="p",
        runtime_build_sha="b", option_exchange=option_exchange,
        futures_exchange=futures_exchange,
    )
    return store


def test_the_plan_store_returns_the_venue_it_was_given(tmp_path):
    """It used to accept the field and hand back nothing, which the executor's
    `or "NFO"` fallback quietly covered for."""
    plan = _plan_store(tmp_path, option_exchange="BFO", futures_exchange="BFO").get("PLAN-V")
    assert plan["option_exchange"] == "BFO"
    assert plan["futures_exchange"] == "BFO"


def test_a_real_column_is_not_shadowed_by_an_extra(tmp_path):
    store = _plan_store(tmp_path)
    assert store.get("PLAN-V")["mode"] == "PAPER"


def test_the_executor_refuses_a_plan_with_no_option_venue(tmp_path):
    import asyncio

    from app.services.snapback_live_executor import SnapbackLiveExecutor

    store = _plan_store(tmp_path, option_exchange="", futures_exchange="")
    store.arm("PLAN-V", expected_revision=1, idempotency_key="k", armed_by="u")

    executor = SnapbackLiveExecutor(
        plan_store=store,
        execution_service=None,
        reconciliation_fn=lambda _a: {"clean": True, "fresh": True},
        risk_approval_fn=lambda **_k: type("A", (), {"approved": True})(),
        protection_fn=lambda **_k: {"state": "ACTIVE"},
        revalidate_fn=lambda _p: {"ok": True},
    )
    result = asyncio.run(executor.execute("PLAN-V"))
    assert result.status == "REFUSED"
    assert "plan_missing_option_exchange" in result.reasons


def test_the_intent_is_recorded_before_the_broker_is_called(tmp_path):
    """A crash mid-submit must be recoverable as SUBMITTED_UNKNOWN."""
    from app.services.snapback_live_executor import SnapbackLiveExecutor

    intents = IntentStore(tmp_path / "intents.db")
    executor = SnapbackLiveExecutor(
        plan_store=_plan_store(tmp_path), execution_service=None,
        reconciliation_fn=lambda _a: {"clean": True, "fresh": True},
        risk_approval_fn=lambda **_k: None,
        protection_fn=lambda **_k: {},
        revalidate_fn=lambda _p: {"ok": True},
        intent_store=intents,
    )
    plan = {
        "plan_id": "PLAN-V", "opportunity_id": "OPP-V", "mode": "swing",
        "option_instrument_token": 4242, "max_loss_budget": 9000.0,
        "horizon_plan_id": "H-1", "runtime_sha": "abc", "config_hash": "cfg",
    }
    intent = executor._reserve_intent(
        plan, symbol="SENSEX26SEP80000CE", exchange="BFO", quantity=10, action="entry"
    )
    assert intent is not None
    stored = intents.get(intent.intent_id)
    assert stored.state is IntentState.SUBMITTED_UNKNOWN
    assert stored.needs_broker_query is True
    assert stored.intent.exchange == "BFO"
    assert stored.intent.lane_key == "snapback:swing"


def test_a_fill_then_protection_moves_the_durable_record(tmp_path):
    from app.services.snapback_live_executor import SnapbackLiveExecutor
    from app.services.snapback_protection import ProtectionState as BrokerProtection

    intents = IntentStore(tmp_path / "intents.db")
    executor = SnapbackLiveExecutor(
        plan_store=_plan_store(tmp_path), execution_service=None,
        reconciliation_fn=lambda _a: {}, risk_approval_fn=lambda **_k: None,
        protection_fn=lambda **_k: {}, revalidate_fn=lambda _p: {"ok": True},
        intent_store=intents,
    )
    plan = {
        "plan_id": "PLAN-V", "opportunity_id": "OPP-V", "mode": "swing",
        "option_instrument_token": 4242, "max_loss_budget": 9000.0,
        "horizon_plan_id": "H-1", "runtime_sha": "abc", "config_hash": "cfg",
    }
    intent = executor._reserve_intent(
        plan, symbol="SENSEX26SEP80000CE", exchange="BFO", quantity=10, action="entry"
    )
    executor._record_broker_outcome(
        intent,
        type("R", (), {"status": "FILLED", "success": True, "filled_quantity": 10,
                       "order_id": "B-1"})(),
    )
    assert intents.get(intent.intent_id).state is IntentState.FILLED
    assert intents.get(intent.intent_id).position_is_unprotected is True

    executor._record_protection(intent, BrokerProtection.ACTIVE)
    stored = intents.get(intent.intent_id)
    assert stored.protection is ProtectionState.CONFIRMED
    assert stored.position_is_unprotected is False


def test_an_unknown_submission_stays_unknown(tmp_path):
    """Never overwritten with a guess; the broker must be asked first."""
    from app.services.snapback_live_executor import SnapbackLiveExecutor

    intents = IntentStore(tmp_path / "intents.db")
    executor = SnapbackLiveExecutor(
        plan_store=_plan_store(tmp_path), execution_service=None,
        reconciliation_fn=lambda _a: {}, risk_approval_fn=lambda **_k: None,
        protection_fn=lambda **_k: {}, revalidate_fn=lambda _p: {"ok": True},
        intent_store=intents,
    )
    plan = {
        "plan_id": "PLAN-V", "opportunity_id": "OPP-V", "mode": "swing",
        "option_instrument_token": 4242, "max_loss_budget": 9000.0,
        "horizon_plan_id": "H-1", "runtime_sha": "abc", "config_hash": "cfg",
    }
    intent = executor._reserve_intent(
        plan, symbol="X", exchange="BFO", quantity=10, action="entry"
    )
    executor._record_broker_outcome(
        intent, type("R", (), {"status": "UNKNOWN", "success": False,
                               "filled_quantity": 0, "order_id": ""})(),
    )
    assert intents.get(intent.intent_id).state is IntentState.SUBMITTED_UNKNOWN
