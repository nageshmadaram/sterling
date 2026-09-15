import pytest
from app.services.execution_service import CanonicalExecutionService, ExecutionRequest, ExposureEffect
from app.services import db


@pytest.fixture(autouse=True)
def setup_db_for_test(tmp_path, monkeypatch):
    from app.services import live_safety
    db_file = str(tmp_path / "test_exec.db")
    monkeypatch.setenv("STERLING_DB_PATH", db_file)
    monkeypatch.setattr(db, "_DB_PATH", db_file)
    monkeypatch.setattr(db, "_available", True)
    live_safety.reset_all_for_tests()
    db.init()
    with db._conn() as conn:
        conn.execute("DELETE FROM execution_control")
    yield
    live_safety.reset_all_for_tests()


def test_execution_service_halted_rejection():
    service = CanonicalExecutionService()

    # Initially RUNNING
    allowed, msg = service.is_trading_allowed()
    assert allowed is True

    # Set to HALTED
    db.set_execution_control(operator_state="HALTED", reason="Emergency stop initiated")
    allowed, msg = service.is_trading_allowed()
    assert allowed is False
    assert "HALTED" in msg


@pytest.mark.asyncio
async def test_execution_service_risk_rejection():
    service = CanonicalExecutionService()
    req = ExecutionRequest(
        uid="u1",
        account_id="acc1",
        strategy_id="snapback",
        generation_id="gen1",
        signal_id="sig1",
        exchange="NFO",
        symbol="NIFTY26SEP24000PE",
        side="BUY",
        quantity=50,
        tag="st_1001",
    )

    res = await service.submit(req, risk_approved=False)
    assert res.success is False
    assert res.status == "REJECTED"
    assert "Risk check rejected" in res.error


@pytest.mark.asyncio
async def test_execution_service_broker_send_ack():
    class MockBrokerClient:
        _account_id = "acc1"
        async def place_order(self, **kwargs):
            return {"order_id": "ORD_12345"}

    service = CanonicalExecutionService()
    req = ExecutionRequest(
        uid="u1",
        account_id="acc1",
        strategy_id="snapback",
        generation_id="gen1",
        signal_id="sig101",
        exchange="NSE",
        symbol="RELIANCE",
        side="BUY",
        quantity=10,
        tag="st_1002",
    )

    res = await service.submit_order(req, broker_client=MockBrokerClient(), risk_approved=True)
    assert res.success is True
    assert res.status == "ACKNOWLEDGED"
    assert res.order_id == "ORD_12345"


@pytest.mark.asyncio
async def test_execution_service_transport_uncertainty():
    class TimeoutBrokerClient:
        _account_id = "acc1"
        async def place_order(self, **kwargs):
            raise TimeoutError("Broker Gateway Timeout 504")

    service = CanonicalExecutionService()
    req = ExecutionRequest(
        uid="u1",
        account_id="acc1",
        strategy_id="snapback",
        generation_id="gen1",
        signal_id="sig102",
        exchange="NSE",
        symbol="INFY",
        side="BUY",
        quantity=5,
        tag="st_1003",
    )

    res = await service.submit_order(req, broker_client=TimeoutBrokerClient(), risk_approved=True)
    assert res.success is False
    assert res.status == "UNKNOWN"

    ctrl = db.get_execution_control(uid="u1", account_id="acc1")
    assert ctrl["recovery_state"] == "RECOVERY_REQUIRED"


@pytest.mark.asyncio
async def test_execution_service_cancel_and_protection():
    class MockBrokerClient:
        _account_id = "acc1"
        async def cancel_order(self, variety, order_id):
            return True

        async def place_gtt(self, **kwargs):
            return {"trigger_id": "GTT_999"}

    service = CanonicalExecutionService()
    db.set_execution_control(operator_state="HALTED", uid="u1", account_id="acc1")

    # Cancel permitted even when HALTED
    cancel_res = await service.cancel_order(
        uid="u1", account_id="acc1", order_id="ORD_12345", broker_client=MockBrokerClient()
    )
    assert cancel_res.success is True
    assert cancel_res.status == "ACKNOWLEDGED"

    # Protection permitted even when HALTED
    prot_res = await service.place_protection(
        uid="u1",
        account_id="acc1",
        position_id="ORD_12345",
        protection_params={"trigger_type": "single", "tradingsymbol": "SBIN"},
        broker_client=MockBrokerClient(),
    )
    assert prot_res.success is True
    assert prot_res.order_id == "GTT_999"


@pytest.mark.asyncio
async def test_startup_recovery_clean():
    service = CanonicalExecutionService()
    res = await service.startup_recovery(uid="u1", account_id="acc1")
    assert res["status"] == "success"
    assert res["recovery_state"] == "CLEAN"
