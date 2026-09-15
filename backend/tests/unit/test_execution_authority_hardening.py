"""Unit tests for Execution Control & Canonical Execution Service Hardening (Slice 2)."""
import pytest
import sqlite3
import pytest_asyncio
from unittest.mock import MagicMock

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
from app.services.db import ControlPlaneUnavailableError


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
async def test_real_kite_client_protocol_arguments():
    """Verify CanonicalExecutionService invokes KiteClient with exchange, product=NRML, allow_amo=False, trigger_price, and tag."""
    service = CanonicalExecutionService()
    placed_kwargs = {}

    class MockRealKiteClient(KiteClient):
        def __init__(self):
            self._account_id = "acct_test1"

        async def _place(self, **kwargs):
            placed_kwargs.update(kwargs)
            return {"order_id": "240916000999999"}

    client = MockRealKiteClient()
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
        order_type="SL",
        price=500.0,
        trigger_price=495.0,
        tag="user_custom_tag",
    )

    res = await service.submit_order(req, broker_client=client, risk_approved=True)
    assert res.success
    assert res.status == "ACKNOWLEDGED"
    assert res.order_id == "240916000999999"

    # Assert real KiteClient received exact required protocol parameters
    assert placed_kwargs.get("exchange") == "NSE"
    assert placed_kwargs.get("tradingsymbol") == "SBIN"
    assert placed_kwargs.get("transaction_type") == "BUY"
    assert placed_kwargs.get("quantity") == 10
    assert placed_kwargs.get("product") == "NRML"
    assert placed_kwargs.get("allow_amo") is False
    assert placed_kwargs.get("price") == 500.0
    assert placed_kwargs.get("trigger_price") == 495.0
    assert placed_kwargs.get("tag").startswith("ke")


@pytest.mark.asyncio
async def test_account_identity_mismatch_rejection():
    """Verify submit_order rejects when broker_client account identity does not match request account_id."""
    service = CanonicalExecutionService()

    class MismatchedClient:
        _account_id = "acct_A"

        async def place_order(self, **kwargs):
            return {"order_id": "123"}

    client = MismatchedClient()
    req = ExecutionRequest(
        uid="u1",
        account_id="acct_B",  # Mismatch with acct_A!
        strategy_id="strat1",
        generation_id="gen1",
        signal_id="sig1",
        exchange="NSE",
        symbol="SBIN",
        side="BUY",
        quantity=10,
    )

    res = await service.submit_order(req, broker_client=client, risk_approved=True)
    assert not res.success
    assert res.status == "REJECTED"
    assert "account identity" in res.error.lower()


@pytest.mark.asyncio
async def test_unproven_risk_approval_rejection():
    """Verify submit_order rejects when risk_approved is False."""
    service = CanonicalExecutionService()
    req = ExecutionRequest(
        uid="u1",
        account_id="acct1",
        strategy_id="strat1",
        generation_id="gen1",
        signal_id="sig1",
        exchange="NSE",
        symbol="SBIN",
        side="BUY",
        quantity=10,
    )

    # Calling with default risk_approved=False
    res = await service.submit_order(req, broker_client=MagicMock(), risk_approved=False)
    assert not res.success
    assert res.status == "REJECTED"
    assert "missing risk approval" in res.error.lower()


@pytest.mark.asyncio
async def test_exposure_effect_inventory_verification():
    """Verify declared CLOSE_POSITION on zero inventory is reclassified as INCREASE_EXPOSURE and blocked when HALTED."""
    service = CanonicalExecutionService()

    # Halt global execution control
    db.set_operator_state("HALTED", reason="Maintenance halt", scope="global")

    # Request declaring CLOSE_POSITION on zero inventory
    req_spoof_close = ExecutionRequest(
        uid="u_spoof",
        account_id="acct_spoof",
        strategy_id="strat1",
        generation_id="gen1",
        signal_id="sig_close",
        exchange="NSE",
        symbol="RELIANCE",
        side="SELL",
        quantity=10,
        exposure_effect=ExposureEffect.CLOSE_POSITION,
    )

    res = await service.submit_order(req_spoof_close, broker_client=MagicMock(), risk_approved=True)
    assert not res.success
    assert res.status == "HALTED"


@pytest.mark.asyncio
async def test_corrupt_db_state_fails_closed():
    """Verify corrupt operator_state string in DB causes get_execution_control to raise ControlPlaneUnavailableError."""
    with db._conn() as conn:
        conn.execute(
            "INSERT INTO execution_control (scope, uid, account_id, operator_state, recovery_state, revision, created_ms, updated_ms)"
            " VALUES ('global', 'default', 'default', 'INVALID_CORRUPT_STATE', 'CLEAN', 1, 1000, 1000)"
            " ON CONFLICT(scope, uid, account_id) DO UPDATE SET operator_state='INVALID_CORRUPT_STATE'"
        )

    with pytest.raises(ControlPlaneUnavailableError):
        db.get_execution_control(scope="global", uid="default", account_id="default")

    # Safety decision must fail closed
    decision = live_safety.assert_safe_to_trade([], uid="default", exposure_effect="INCREASE_EXPOSURE")
    assert not decision.allowed
    assert decision.code == "safety_unknown"


@pytest.mark.asyncio
async def test_simultaneous_2D_state_writes():
    """Verify set_operator_state and set_recovery_state preserve orthogonal dimensions atomically."""
    db.set_operator_state("HALTED", reason="Manual operator halt", scope="global")
    ctrl1 = db.get_execution_control(scope="global")
    assert ctrl1["operator_state"] == "HALTED"
    assert ctrl1["recovery_state"] == "CLEAN"

    db.set_recovery_state("RECOVERY_REQUIRED", reason="Transport drop", scope="global")
    ctrl2 = db.get_execution_control(scope="global")
    assert ctrl2["operator_state"] == "HALTED"
    assert ctrl2["recovery_state"] == "RECOVERY_REQUIRED"

    db.set_operator_state("RUNNING", scope="global")
    ctrl3 = db.get_execution_control(scope="global")
    assert ctrl3["operator_state"] == "RUNNING"
    assert ctrl3["recovery_state"] == "RECOVERY_REQUIRED"


@pytest.mark.asyncio
async def test_unresolved_journal_intent_blocks_trading():
    """Verify unresolved SUBMITTING/UNKNOWN intent in order_journal blocks assert_safe_to_trade."""
    # Reserve and claim an intent to transition to SUBMITTING
    intent = order_journal.reserve(
        uid="u_unres",
        account_id="acct_unres",
        strategy_id="strat1",
        generation_id="gen1",
        signal_id="sig_unres",
        exchange="NSE",
        symbol="INFY",
        side="BUY",
        quantity=5,
        capital_required=0.0,
    )
    order_journal.claim_submission(intent.intent_key)

    decision = live_safety.assert_safe_to_trade([], uid="u_unres", account_id="acct_unres", exposure_effect="INCREASE_EXPOSURE")
    assert not decision.allowed
    assert decision.code == "recovery_required"
    assert "Unresolved journal intent" in decision.reason


@pytest.mark.asyncio
async def test_restart_after_halt_with_legacy_schema():
    """Verify that restarting db.init() on a legacy schema with 'state' column preserves durable HALTED state."""
    # Simulate legacy table with old 'state' column
    with db._conn() as conn:
        conn.execute("DROP TABLE IF EXISTS execution_control")
        conn.execute("""
            CREATE TABLE execution_control (
                scope TEXT NOT NULL DEFAULT 'global',
                uid TEXT NOT NULL DEFAULT 'default',
                account_id TEXT NOT NULL DEFAULT 'default',
                state TEXT NOT NULL DEFAULT 'RUNNING',
                operator_state TEXT NOT NULL DEFAULT 'RUNNING',
                recovery_state TEXT NOT NULL DEFAULT 'CLEAN',
                reason_code TEXT NOT NULL DEFAULT '',
                reason TEXT NOT NULL DEFAULT '',
                revision INTEGER NOT NULL DEFAULT 1,
                actor TEXT NOT NULL DEFAULT 'system',
                last_reconciled_ms INTEGER NOT NULL DEFAULT 0,
                created_ms INTEGER NOT NULL DEFAULT 0,
                updated_ms INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (scope, uid, account_id)
            )
        """)
        conn.execute("INSERT INTO execution_control (scope, state, operator_state) VALUES ('global', 'RUNNING', 'RUNNING')")

    # Initial migration runs
    db.init()
    ctrl1 = db.get_execution_control(scope="global")
    assert ctrl1["operator_state"] == "RUNNING"

    # Operator halts execution
    db.set_operator_state("HALTED", reason="Emergency halt", scope="global")
    ctrl2 = db.get_execution_control(scope="global")
    assert ctrl2["operator_state"] == "HALTED"

    # Restart application / call db.init() again
    db.init()
    ctrl3 = db.get_execution_control(scope="global")
    # Must remain HALTED! Stale legacy column state must not revert operator_state to RUNNING
    assert ctrl3["operator_state"] == "HALTED"


@pytest.mark.asyncio
async def test_typed_broker_outcome_unknown_on_generic_transport_exception():
    """Verify generic/unclassified exception during place_order transitions intent to UNKNOWN and sets RECOVERY_REQUIRED."""
    service = CanonicalExecutionService()

    class TransportFailureBroker:
        _account_id = "acct_tf1"

        async def place_order(self, **kwargs):
            raise RuntimeError("Unexpected socket drop without standard error string")

    client = TransportFailureBroker()
    req = ExecutionRequest(
        uid="u_tf1",
        account_id="acct_tf1",
        strategy_id="strat1",
        generation_id="gen1",
        signal_id="sig_tf1",
        exchange="NSE",
        symbol="TATASTEEL",
        side="BUY",
        quantity=20,
    )

    res = await service.submit_order(req, broker_client=client, risk_approved=True)
    assert not res.success
    assert res.status == "UNKNOWN"

    ctrl = db.get_execution_control(scope="global", uid="u_tf1", account_id="acct_tf1")
    assert ctrl["recovery_state"] == "RECOVERY_REQUIRED"
