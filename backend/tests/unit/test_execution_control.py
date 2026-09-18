"""Unit tests for Execution Control Authority, 2D Orthogonal States, CAS Contract, and Exposure Effects."""
import pytest
from app.services import db
from app.services import live_safety
from app.services.execution_service import CanonicalExecutionService, ExecutionRequest, ExposureEffect, RiskApproval


@pytest.fixture(autouse=True)
def setup_db_for_test(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test_ctrl.db")
    monkeypatch.setenv("STERLING_DB_PATH", db_file)
    monkeypatch.setattr(db, "_DB_PATH", db_file)
    monkeypatch.setattr(db, "_available", True)
    live_safety.reset_all_for_tests()
    db.init()
    with db._conn() as conn:
        conn.execute("DELETE FROM execution_control")
    yield
    live_safety.reset_all_for_tests()


def _reconciled_global_scope():
    """The state a completed startup recovery leaves behind for the default scope.

    ``assert_safe_to_trade`` walks from the global scope inward, so a test about a
    narrower scope has to stand the outer one up first or it is really testing the
    uninitialised-control-plane refusal.
    """
    db.set_execution_control(
        operator_state="RUNNING",
        recovery_state="CLEAN",
        reason="startup recovery complete",
        scope="global",
        uid="default",
        account_id="default",
    )


def _erase_control_plane():
    with db._conn() as conn:
        conn.execute("DELETE FROM execution_control")


def test_a_missing_control_row_is_recovery_required_not_clean():
    """An absent row means startup recovery never ran, and must read that way.

    Reporting CLEAN let a brand-new database assert "we have reconciled with the
    broker" without ever having spoken to it.
    """
    _erase_control_plane()
    ctrl = db.get_execution_control(uid="user1", account_id="acc1")
    assert ctrl["operator_state"] == "RUNNING"
    assert ctrl["recovery_state"] == "RECOVERY_REQUIRED"
    assert ctrl["initialized"] == 0
    assert ctrl["revision"] == 0


def test_a_missing_control_row_blocks_new_exposure():
    _erase_control_plane()
    decision = live_safety.assert_safe_to_trade(
        [], exposure_effect="INCREASE_EXPOSURE"
    )
    assert decision.allowed is False
    assert decision.code == "recovery_required"


def test_a_missing_control_row_still_permits_closing():
    _erase_control_plane()
    for effect in ("CLOSE_POSITION", "REDUCE_EXPOSURE", "CANCEL_ORDER"):
        assert live_safety.assert_safe_to_trade(
            [], exposure_effect=effect
        ).allowed is True


def test_a_missing_row_on_a_narrower_scope_inherits_rather_than_blocking():
    """An absent per-account row is no override, not an unreconciled account."""
    _reconciled_global_scope()
    assert live_safety.assert_safe_to_trade(
        [], uid="user1", account_id="acc1", exposure_effect="INCREASE_EXPOSURE"
    ).allowed is True


def test_creating_a_row_does_not_invent_a_reconciliation():
    """The first write must not silently claim CLEAN."""
    _erase_control_plane()
    res = db.set_operator_state(operator_state="RUNNING", uid="fresh", account_id="fresh")
    assert res["recovery_state"] == "RECOVERY_REQUIRED"
    assert db.get_execution_control(uid="fresh", account_id="fresh")["initialized"] == 1


def test_execution_control_set_and_cas():
    """Setting execution control increments revision and enforces expected_revision CAS."""
    res1 = db.set_execution_control(
        operator_state="HALTED",
        recovery_state="CLEAN",
        reason="Manual halt test",
        uid="user1",
        account_id="acc1",
    )
    assert res1["operator_state"] == "HALTED"
    assert res1["revision"] == 1

    # Successful CAS transition with expected_revision=1
    res2 = db.set_execution_control(
        operator_state="HALTED",
        recovery_state="RECOVERY_REQUIRED",
        reason="Recovery required test",
        uid="user1",
        account_id="acc1",
        expected_revision=1,
    )
    assert res2["recovery_state"] == "RECOVERY_REQUIRED"
    assert res2["revision"] == 2

    # Failed CAS transition with wrong expected_revision
    with pytest.raises(db.ExecutionControlCASConflictError):
        db.set_execution_control(
            operator_state="RUNNING",
            recovery_state="CLEAN",
            uid="user1",
            account_id="acc1",
            expected_revision=1,  # Current is 2
        )


def test_execution_control_fail_closed():
    """Database read failure or uninitialized state must raise ControlPlaneUnavailableError."""
    db._available = False
    with pytest.raises(db.ControlPlaneUnavailableError):
        db.get_execution_control(uid="user1")

    with pytest.raises(db.ControlPlaneUnavailableError):
        db.set_execution_control(operator_state="HALTED", uid="user1")


def test_live_safety_exposure_effects():
    """HALTED blocks INCREASE_EXPOSURE, but permits CLOSE_POSITION / CANCEL_ORDER."""
    _reconciled_global_scope()
    db.set_execution_control(
        operator_state="HALTED",
        recovery_state="CLEAN",
        reason="Maintenance halt",
        uid="user1",
    )

    # INCREASE_EXPOSURE blocked
    decision = live_safety.assert_safe_to_trade(
        [], uid="user1", exposure_effect="INCREASE_EXPOSURE"
    )
    assert not decision.allowed
    assert decision.code == "durable_halt"

    # CLOSE_POSITION permitted even when HALTED
    close_decision = live_safety.assert_safe_to_trade(
        [], uid="user1", exposure_effect="CLOSE_POSITION"
    )
    assert close_decision.allowed

    # CANCEL_ORDER permitted even when HALTED
    cancel_decision = live_safety.assert_safe_to_trade(
        [], uid="user1", exposure_effect="CANCEL_ORDER"
    )
    assert cancel_decision.allowed


def test_durable_kill_switch_wiring():
    """live_safety.set_kill_switch must durably persist state to execution_control table."""
    live_safety.set_kill_switch(True, reason="Emergency button pressed", uid="user1")

    ctrl = db.get_execution_control(uid="user1")
    assert ctrl["operator_state"] == "HALTED"
    assert ctrl["reason"] == "Emergency button pressed"


@pytest.mark.asyncio
async def test_canonical_execution_service_exposure_policy():
    """CanonicalExecutionService respects 2D control state and exposure effects."""
    service = CanonicalExecutionService()
    db.set_execution_control(
        operator_state="HALTED",
        recovery_state="CLEAN",
        reason="Operator halt",
        uid="user1",
        account_id="acc1",
    )

    req_increase = ExecutionRequest(
        uid="user1",
        account_id="acc1",
        strategy_id="strat1",
        generation_id="gen1",
        signal_id="sig1",
        exchange="NSE",
        symbol="SBIN",
        side="BUY",
        quantity=10,
        tag="test",
        exposure_effect=ExposureEffect.INCREASE_EXPOSURE,
    )

    approval = RiskApproval(
        approval_id="app1",
        uid="user1",
        account_id="acc1",
        strategy_id="strat1",
        signal_id="sig1",
        symbol="SBIN",
        side="BUY",
        quantity=10,
        generation_id="gen1",
        available_capital=100000.0,
    )

    res_inc = await service.submit_order(req_increase, risk_approval=approval)
    assert not res_inc.success
    assert res_inc.status == "HALTED"

    # Set open inventory so CLOSE_POSITION is valid
    db.set_inventory(account_id="acc1", uid="user1", symbol="SBIN", net_quantity=10)

    req_close = ExecutionRequest(
        uid="user1",
        account_id="acc1",
        strategy_id="strat1",
        generation_id="gen1",
        signal_id="sig2",
        exchange="NSE",
        symbol="SBIN",
        side="SELL",
        quantity=10,
        tag="close_test",
        exposure_effect=ExposureEffect.CLOSE_POSITION,
    )

    class MockBroker:
        _account_id = "acc1"
        async def place_order(self, **kwargs):
            return {"order_id": "ORD_CLOSE_123"}

    res_close = await service.submit_order(req_close, broker_client=MockBroker(), risk_approved=True)
    assert res_close.success
    assert res_close.status == "ACKNOWLEDGED"
