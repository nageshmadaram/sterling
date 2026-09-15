"""Unit tests for Execution Control Authority, 2D Orthogonal States, CAS Contract, and Exposure Effects."""
import pytest
from app.services import db
from app.services import live_safety
from app.services.execution_service import CanonicalExecutionService, ExecutionRequest, ExposureEffect


@pytest.fixture(autouse=True)
def setup_db_for_test(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test_ctrl.db")
    monkeypatch.setenv("STERLING_DB_PATH", db_file)
    monkeypatch.setattr(db, "_DB_PATH", db_file)
    monkeypatch.setattr(db, "_available", True)
    db.init()
    yield


def test_execution_control_default_state():
    """Default execution control for unconfigured account should be RUNNING + CLEAN."""
    ctrl = db.get_execution_control(uid="user1", account_id="acc1")
    assert ctrl["operator_state"] == "RUNNING"
    assert ctrl["recovery_state"] == "CLEAN"
    assert ctrl["revision"] == 0


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

    res_inc = await service.submit_order(req_increase)
    assert not res_inc.success
    assert res_inc.status == "HALTED"

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

    res_close = await service.submit_order(req_close)
    assert res_close.success
    assert res_close.status == "ACKNOWLEDGED"
