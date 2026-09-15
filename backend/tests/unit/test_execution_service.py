import pytest
from app.services.execution_service import CanonicalExecutionService, ExecutionRequest
from app.services import db


def test_execution_service_halted_rejection(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test_exec.db")
    monkeypatch.setenv("STERLING_DB_PATH", db_file)
    monkeypatch.setattr(db, "_DB_PATH", db_file)
    monkeypatch.setattr(db, "_available", True)

    with db._conn() as conn:
        db._create_tables(conn)

    service = CanonicalExecutionService()

    # Initially RUNNING
    allowed, msg = service.is_trading_allowed()
    assert allowed is True

    # Set to HALTED
    db.set_execution_control(state="HALTED", reason="Emergency stop initiated")
    allowed, msg = service.is_trading_allowed()
    assert allowed is False
    assert "HALTED" in msg


@pytest.mark.asyncio
async def test_execution_service_risk_rejection(tmp_path, monkeypatch):
    db_file = str(tmp_path / "test_exec.db")
    monkeypatch.setenv("STERLING_DB_PATH", db_file)
    monkeypatch.setattr(db, "_DB_PATH", db_file)
    monkeypatch.setattr(db, "_available", True)

    with db._conn() as conn:
        db._create_tables(conn)

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
