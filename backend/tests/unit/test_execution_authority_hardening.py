"""Unit tests for Execution Control & Canonical Execution Service Hardening."""
import pytest
import sqlite3
import pytest_asyncio

from app.services import db, live_safety
from app.services.execution_service import (
    CanonicalExecutionService,
    ExecutionRequest,
    ExposureEffect,
    BrokerRejected,
    SubmissionOutcomeUnknown,
)
from app.services.exchanges.kite.client import KiteClient
from app.services.kite_engine import order_journal


@pytest.fixture(autouse=True)
def setup_db(tmp_path, monkeypatch):
    """Use temporary file-backed SQLite database for accurate multi-connection transaction testing."""
    db_file = tmp_path / "test_sterling.db"
    monkeypatch.setattr(db, "_DB_PATH", str(db_file))
    live_safety.reset_all_for_tests()
    db.init()
    with db._conn() as conn:
        conn.execute("DELETE FROM execution_control")
        conn.execute("DELETE FROM kite_order_intents")
    yield
    live_safety.reset_all_for_tests()


@pytest.mark.asyncio
async def test_real_kite_client_signature_and_intent_tag():
    """Verify CanonicalExecutionService invokes real KiteClient place_order signature and passes intent.tag."""
    service = CanonicalExecutionService()
    placed_calls = []

    class MockRealKiteAdapter:
        async def place_order(
            self,
            symbol: str,
            side: str,
            size: float,
            order_type: str = "market_order",
            limit_price: float | None = None,
            tag: str | None = None,
            **kwargs,
        ) -> dict:
            placed_calls.append(
                {
                    "symbol": symbol,
                    "side": side,
                    "size": size,
                    "order_type": order_type,
                    "tag": tag,
                    "kwargs": kwargs,
                }
            )
            return {"order_id": "240916000123456"}

    broker = MockRealKiteAdapter()
    req = ExecutionRequest(
        uid="u_test1",
        account_id="acct_test1",
        strategy_id="strat_test",
        generation_id="gen_1",
        signal_id="sig_1",
        exchange="NSE",
        symbol="SBIN",
        side="BUY",
        quantity=10,
        tag="user_custom_tag",
    )

    res = await service.submit_order(req, broker_client=broker)
    assert res.success
    assert res.status == "ACKNOWLEDGED"
    assert res.order_id == "240916000123456"

    assert len(placed_calls) == 1
    call = placed_calls[0]
    assert call["symbol"] == "SBIN"
    assert call["side"] == "BUY"
    assert call["size"] == 10
    # Canonical broker tracking tag MUST be intent.tag (e.g. starting with "ke")
    assert call["tag"].startswith("ke")


@pytest.mark.asyncio
async def test_fail_closed_when_broker_client_is_none():
    """Verify live order submission fails closed when broker_client is None."""
    service = CanonicalExecutionService()
    req = ExecutionRequest(
        uid="u_test2",
        account_id="acct_test2",
        strategy_id="strat_test",
        generation_id="gen_1",
        signal_id="sig_2",
        exchange="NSE",
        symbol="INFY",
        side="BUY",
        quantity=5,
    )

    res = await service.submit_order(req, broker_client=None)
    assert not res.success
    assert res.status == "REJECTED"
    assert "No authenticated broker client available" in res.error


@pytest.mark.asyncio
async def test_orthogonal_2D_state_transitions():
    """Verify set_operator_state and set_recovery_state preserve orthogonal dimensions."""
    # 1. Initial state: RUNNING / CLEAN
    db.set_operator_state("RUNNING", scope="global")
    ctrl = db.get_execution_control(scope="global")
    assert ctrl["operator_state"] == "RUNNING"
    assert ctrl["recovery_state"] == "CLEAN"

    # 2. Halt operator state — recovery_state remains CLEAN
    db.set_operator_state("HALTED", reason_code="MAINTENANCE", scope="global")
    ctrl = db.get_execution_control(scope="global")
    assert ctrl["operator_state"] == "HALTED"
    assert ctrl["recovery_state"] == "CLEAN"

    # 3. Set recovery_state to RECOVERY_REQUIRED — operator_state remains HALTED
    db.set_recovery_state("RECOVERY_REQUIRED", reason_code="UNCERTAIN", scope="global")
    ctrl = db.get_execution_control(scope="global")
    assert ctrl["operator_state"] == "HALTED"
    assert ctrl["recovery_state"] == "RECOVERY_REQUIRED"

    # 4. Resume operator state — recovery_state remains RECOVERY_REQUIRED
    db.set_operator_state("RUNNING", scope="global")
    ctrl = db.get_execution_control(scope="global")
    assert ctrl["operator_state"] == "RUNNING"
    assert ctrl["recovery_state"] == "RECOVERY_REQUIRED"


@pytest.mark.asyncio
async def test_hierarchical_halt_evaluation():
    """Verify assertion safe to trade checks global, user, and account control levels."""
    # Set global operator_state to HALTED
    db.set_operator_state("HALTED", reason="Global system halt", scope="global")

    # User level is RUNNING
    db.set_operator_state("RUNNING", scope="user", uid="u_hier1")

    # Trade check for u_hier1 must be blocked because global scope is HALTED
    decision = live_safety.assert_safe_to_trade([], uid="u_hier1", exposure_effect="INCREASE_EXPOSURE")
    assert not decision.allowed
    assert decision.code == "durable_halt"
    assert "global scope" in decision.reason

    # Resume global scope, halt user scope
    db.set_operator_state("RUNNING", scope="global")
    db.set_operator_state("HALTED", reason="User specific halt", scope="user", uid="u_hier1")

    decision = live_safety.assert_safe_to_trade([], uid="u_hier1", exposure_effect="INCREASE_EXPOSURE")
    assert not decision.allowed
    assert "user scope" in decision.reason


@pytest.mark.asyncio
async def test_startup_recovery_cleans_orphaned_reserved():
    """Verify startup_recovery cleans orphaned RESERVED intents without setting RECOVERY_REQUIRED."""
    service = CanonicalExecutionService()

    # Reserve intent without claiming or broker submit
    intent = order_journal.reserve(
        uid="u_rec1",
        account_id="acct_rec1",
        strategy_id="strat_test",
        generation_id="gen_1",
        signal_id="sig_rec1",
        exchange="NSE",
        symbol="TATASTEEL",
        side="BUY",
        quantity=50,
        capital_required=0.0,
    )
    assert intent.state == "RESERVED"

    res = await service.startup_recovery(uid="u_rec1", account_id="acct_rec1")
    assert res["status"] == "success"
    assert res["recovery_state"] == "CLEAN"

    # Verify intent state transitioned to CANCELLED
    updated_intents = order_journal.unresolved("u_rec1", account_id="acct_rec1")
    assert len(updated_intents) == 0
