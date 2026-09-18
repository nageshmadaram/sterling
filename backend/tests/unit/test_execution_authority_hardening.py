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
    RiskApproval,
)
from app.services.exchanges.kite.client import KiteClient
from app.services.kite_engine import order_journal, positions
from app.services.db import ControlPlaneUnavailableError


@pytest.fixture(autouse=True)
def _allow_snapback_family_gate(monkeypatch):
    """These tests predate the Snapback family gate and exercise other invariants.

    The gate itself is covered by tests/unit/test_snapback_live_execution_gate.py; here
    it is allowed through so the invariant under test is the one that decides.
    """
    from types import SimpleNamespace

    monkeypatch.setattr(
        "app.services.execution_service.snapback_live_gate_decision",
        lambda **kw: SimpleNamespace(allowed=True, protective=False, reasons=[]),
        raising=False,
    )




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

    approval = RiskApproval(
        approval_id="a1",
        uid="u_test1",
        account_id="acct_test1",
        strategy_id="strat_test",
        signal_id="sig_1",
        symbol="SBIN",
        side="BUY",
        quantity=10,
        generation_id="gen_1",
        available_capital=100000.0,
    )

    res = await service.submit_order(req, broker_client=client, risk_approval=approval)
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

    approval = RiskApproval(
        approval_id="a2",
        uid="u1",
        account_id="acct_B",
        strategy_id="strat1",
        signal_id="sig1",
        symbol="SBIN",
        side="BUY",
        quantity=10,
        generation_id="gen1",
        available_capital=100000.0,
    )

    res = await service.submit_order(req, broker_client=client, risk_approval=approval)
    assert not res.success
    assert res.status == "REJECTED"
    assert "account identity" in res.error.lower()


@pytest.mark.asyncio
async def test_unproven_risk_approval_rejection():
    """Verify submit_order rejects when risk_approved is False or risk_approval is missing for exposure increases."""
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

    # Calling without RiskApproval object
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

    approval = RiskApproval(
        approval_id="a3",
        uid="u_spoof",
        account_id="acct_spoof",
        strategy_id="strat1",
        signal_id="sig_close",
        symbol="RELIANCE",
        side="SELL",
        quantity=10,
        generation_id="gen1",
        available_capital=100000.0,
    )

    res = await service.submit_order(req_spoof_close, broker_client=MagicMock(), risk_approval=approval)
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

    approval = RiskApproval(
        approval_id="a4",
        uid="u_tf1",
        account_id="acct_tf1",
        strategy_id="strat1",
        signal_id="sig_tf1",
        symbol="TATASTEEL",
        side="BUY",
        quantity=20,
        generation_id="gen1",
        available_capital=100000.0,
    )

    res = await service.submit_order(req, broker_client=client, risk_approval=approval)
    assert not res.success
    assert res.status == "UNKNOWN"

    ctrl = db.get_execution_control(scope="global", uid="u_tf1", account_id="acct_tf1")
    assert ctrl["recovery_state"] == "RECOVERY_REQUIRED"


@pytest.mark.asyncio
async def test_risk_approval_proof_validation():
    """Verify RiskApproval proof object is required and validated against request fields."""
    service = CanonicalExecutionService()

    class MockBroker:
        _account_id = "acct_risk1"
        async def place_order(self, **kwargs):
            return {"order_id": "ORD_RISK_1"}

    client = MockBroker()
    req = ExecutionRequest(
        uid="u_risk1",
        account_id="acct_risk1",
        strategy_id="strat1",
        generation_id="gen1",
        signal_id="sig_risk1",
        exchange="NSE",
        symbol="SBIN",
        side="BUY",
        quantity=10,
    )

    # Valid proof matching all fields
    valid_proof = RiskApproval(
        approval_id="app_123",
        uid="u_risk1",
        account_id="acct_risk1",
        strategy_id="strat1",
        signal_id="sig_risk1",
        symbol="SBIN",
        side="BUY",
        quantity=10,
        generation_id="gen1",
        available_capital=100000.0,
        approved=True,
    )
    res_valid = await service.submit_order(req, broker_client=client, risk_approval=valid_proof)
    assert res_valid.success
    assert res_valid.status == "ACKNOWLEDGED"

    # Mismatched quantity proof
    bad_qty_proof = RiskApproval(
        approval_id="app_124",
        uid="u_risk1",
        account_id="acct_risk1",
        strategy_id="strat1",
        signal_id="sig_risk1",
        symbol="SBIN",
        side="BUY",
        quantity=5,  # Mismatch (req is 10)
        generation_id="gen1",
        available_capital=100000.0,
        approved=True,
    )
    res_bad = await service.submit_order(req, broker_client=client, risk_approval=bad_qty_proof)
    assert not res_bad.success
    assert res_bad.status == "REJECTED"
    assert "Risk approval proof mismatch" in res_bad.error


@pytest.mark.asyncio
async def test_quantity_overshoot_reclassifies_to_increase_exposure():
    """Verify SELL 100 declared as CLOSE_POSITION on net LONG 10 is reclassified to INCREASE_EXPOSURE due to overshoot."""
    service = CanonicalExecutionService()
    db.set_inventory(account_id="acct_over", uid="u_over", symbol="RELIANCE", net_quantity=10)
    db.set_operator_state("HALTED", reason="Test halt", scope="global")

    # Request SELL 100 on net LONG 10 (overshoot)
    req_overshoot = ExecutionRequest(
        uid="u_over",
        account_id="acct_over",
        strategy_id="strat1",
        generation_id="gen1",
        signal_id="sig_over",
        exchange="NSE",
        symbol="RELIANCE",
        side="SELL",
        quantity=100,
        exposure_effect=ExposureEffect.CLOSE_POSITION,
    )

    approval = RiskApproval(
        approval_id="a5",
        uid="u_over",
        account_id="acct_over",
        strategy_id="strat1",
        signal_id="sig_over",
        symbol="RELIANCE",
        side="SELL",
        quantity=100,
        generation_id="gen1",
        available_capital=100000.0,
    )

    res = await service.submit_order(req_overshoot, broker_client=MagicMock(), risk_approval=approval)
    assert not res.success
    assert res.status == "HALTED"  # Reclassified to INCREASE_EXPOSURE and blocked by HALTED!


@pytest.mark.asyncio
async def test_protection_lease_acquired_and_busy_rejection():
    """Verify place_protection acquires protection lease with correct signature and rejects when lease is busy."""
    from app.services.kite_engine import execution_lease
    service = CanonicalExecutionService()

    class MockGTTBroker:
        _account_id = "acct_prot1"
        async def place_gtt(self, **kwargs):
            return {"trigger_id": "GTT_999"}

    client = MockGTTBroker()

    # Register open position so protection placement passes open position check
    positions.register(
        positions.OpenPosition(
            uid="u_prot1", account_id="acct_prot1", symbol="INFY", exchange="NSE", qty=10, order_id="POS_1234567890", status=positions.OPEN
        )
    )

    # Pre-acquire lease in process
    token = execution_lease.acquire("protection", account_id="acct_prot1", uid="u_prot1", symbol="INFY")
    assert token is not None

    try:
        # Service call must fail with lease busy rejection
        res = await service.place_protection(
            uid="u_prot1",
            account_id="acct_prot1",
            position_id="POS_1234567890",
            protection_params={"symbol": "INFY", "trigger_price": 1400.0},
            broker_client=client,
        )
        assert not res.success
        assert res.status == "REJECTED"
        assert "lease busy" in res.error.lower()
    finally:
        execution_lease.release("protection", account_id="acct_prot1", uid="u_prot1", symbol="INFY", owner=token)


@pytest.mark.asyncio
async def test_durable_kill_switch_persistence_failure_raises():
    """Verify set_kill_switch(True) raises ControlPlaneUnavailableError when DB is unavailable."""
    monkeypatch_db = pytest.MonkeyPatch()
    try:
        monkeypatch_db.setattr(db, "_available", False)
        with pytest.raises(ControlPlaneUnavailableError):
            live_safety.set_kill_switch(True, reason="Emergency halt")
    finally:
        monkeypatch_db.undo()


# ── Slice 2.2 Specific Verification Tests ──

@pytest.mark.asyncio
async def test_slice_2_2_protection_requires_confirmed_open_position_and_matching_account():
    """Item 1: Require confirmed OPEN position + exact account ownership before any GTT."""
    service = CanonicalExecutionService()

    class MockBroker:
        _account_id = "acct_p1"
        async def place_gtt(self, **kwargs):
            return {"trigger_id": "GTT_111"}

    client = MockBroker()

    # 1. No position in store -> REJECTED
    res1 = await service.place_protection(
        uid="u_p1", account_id="acct_p1", position_id="POS_NONE",
        protection_params={"symbol": "TATAMOTORS", "trigger_price": 900.0},
        broker_client=client,
    )
    assert not res1.success
    assert res1.status == "REJECTED"
    assert "No confirmed OPEN position found" in res1.error

    # 2. Position is PENDING -> REJECTED
    positions.register(
        positions.OpenPosition(
            uid="u_p1", account_id="acct_p1", symbol="TATAMOTORS", exchange="NSE", qty=10, order_id="POS_PEND", status=positions.PENDING
        )
    )
    res2 = await service.place_protection(
        uid="u_p1", account_id="acct_p1", position_id="POS_PEND",
        protection_params={"symbol": "TATAMOTORS", "trigger_price": 900.0},
        broker_client=client,
    )
    assert not res2.success
    assert res2.status == "REJECTED"

    # 3. Account ownership mismatch -> REJECTED
    positions.register(
        positions.OpenPosition(
            uid="u_p1", account_id="acct_WRONG", symbol="TCS", exchange="NSE", qty=10, order_id="POS_TCS", status=positions.OPEN
        )
    )
    client_wrong = MockBroker()
    client_wrong._account_id = "acct_p1"
    res3 = await service.place_protection(
        uid="u_p1", account_id="acct_p1", position_id="POS_TCS",
        protection_params={"symbol": "TCS", "trigger_price": 3800.0},
        broker_client=client_wrong,
    )
    assert not res3.success
    assert res3.status == "REJECTED"
    assert "identity missing or mismatch" in res3.error.lower() or "account ownership mismatch" in res3.error.lower()


@pytest.mark.asyncio
async def test_slice_2_2_protection_params_derived_from_confirmed_position():
    """Item 2: Protection parameters derived from persisted confirmed inventory, not caller payload."""
    service = CanonicalExecutionService()
    received_kwargs = {}

    class MockGTTBroker:
        _account_id = "acct_p2"
        async def place_gtt(self, **kwargs):
            received_kwargs.update(kwargs)
            return {"trigger_id": "GTT_DERIVED_1"}

    client = MockGTTBroker()
    positions.register(
        positions.OpenPosition(
            uid="u_p2", account_id="acct_p2", symbol="NIFTY26SEPFUT", exchange="NFO",
            qty=50, direction="long", stop_premium=24500.0, order_id="POS_FUT_1", status=positions.OPEN
        )
    )

    # Caller sends arbitrary payload (e.g. qty=999, direction="short")
    res = await service.place_protection(
        uid="u_p2",
        account_id="acct_p2",
        position_id="POS_FUT_1",
        protection_params={"symbol": "NIFTY26SEPFUT", "quantity": 999, "direction": "short", "trigger_price": 24450.0},
        broker_client=client,
    )

    assert res.success
    assert res.status == "ACKNOWLEDGED"
    # Derived from OpenPosition: qty=50, direction="long", tradingsymbol="NIFTY26SEPFUT"
    assert received_kwargs.get("qty") == 50
    assert received_kwargs.get("direction") == "long"
    assert received_kwargs.get("tradingsymbol") == "NIFTY26SEPFUT"
    assert received_kwargs.get("trigger_premium") == 24450.0


@pytest.mark.asyncio
async def test_slice_2_2_protection_pending_included_in_startup_recovery():
    """Item 3: Include every live protection_pending position in recovery predicate."""
    service = CanonicalExecutionService()
    positions.register(
        positions.OpenPosition(
            uid="u_p3", account_id="acct_p3", symbol="WIPRO", exchange="NSE",
            qty=20, order_id="POS_WIPRO", status=positions.OPEN, protection_pending=True
        )
    )

    res = await service.startup_recovery(uid="u_p3", account_id="acct_p3")
    assert res["status"] == "success"
    assert res["recovery_state"] == "RECOVERY_REQUIRED"
    assert res["protection_pending"] is True


@pytest.mark.asyncio
async def test_slice_2_2_exit_projection_quantity_derives_from_fill_ledger():
    """Item 4: Exit code calculates quantity using signed fill ledger net_quantity rather than entry_requested_qty."""
    from app.services.kite_engine import execution_lifecycle, fill_ledger

    class MockBrokerClient:
        _account_id = "acct_p4"

    client = MockBrokerClient()

    # Record entry fill of 80 in fill_ledger
    fill_ledger.record(
        account_id="acct_p4", uid="u_p4", symbol="HDFCBANK", exchange="NSE", side="BUY",
        order_id="ORD_ENTRY_1", cumulative_quantity=80, cumulative_value=80 * 1500.0, source="entry"
    )

    # Position registered: entry requested 100, actually filled 80
    p = positions.register(
        positions.OpenPosition(
            uid="u_p4", account_id="acct_p4", symbol="HDFCBANK", exchange="NSE",
            qty=80, entry_requested_qty=100, order_id="ORD_ENTRY_1", status=positions.OPEN
        )
    )

    # Reserve and acknowledge an exit order intent for 20
    intent = order_journal.reserve(
        uid="u_p4", account_id="acct_p4", strategy_id="s1", generation_id="g1", signal_id="sig_exit",
        exchange="NSE", symbol="HDFCBANK", side="SELL", quantity=20,
        payload={"intent_type": "EXIT", "exposure_effect": "CLOSE_POSITION", "product": "NRML"},
    )
    claimed = order_journal.claim_submission(intent.intent_key)
    assert claimed
    order_journal.acknowledge(intent.intent_key, "ORD_EXIT_1")

    # Simulate broker fill postback for exit order (filled 20)
    order_update = {
        "order_id": "ORD_EXIT_1",
        "tradingsymbol": "HDFCBANK",
        "exchange": "NSE",
        "transaction_type": "SELL",
        "product": "NRML",
        "quantity": 20,
        "filled_quantity": 20,
        "average_price": 1600.0,
        "status": "COMPLETE",
        "tag": intent.tag,
    }

    handled = await execution_lifecycle.consume_order(client, "u_p4", order_update)
    assert handled

    # Canonical p.qty must be 60 (80 - 20), NOT 80 (100 - 20)
    p_updated = positions.get("u_p4", "HDFCBANK")
    assert p_updated is not None
    assert p_updated.qty == 60


@pytest.mark.asyncio
async def test_slice_2_2_risk_approval_required_for_exposure_increase():
    """Item 5: submit_order requires request-bound RiskApproval proof for exposure increases."""
    service = CanonicalExecutionService()
    req = ExecutionRequest(
        uid="u_p5", account_id="acct_p5", strategy_id="s1", generation_id="g1", signal_id="sig5",
        exchange="NSE", symbol="BAJFINANCE", side="BUY", quantity=5,
        exposure_effect=ExposureEffect.INCREASE_EXPOSURE,
    )

    # Passing risk_approved=True without RiskApproval object must be REJECTED for exposure increase
    res_no_proof = await service.submit_order(req, broker_client=MagicMock(), risk_approved=True)
    assert not res_no_proof.success
    assert res_no_proof.status == "REJECTED"
    assert "missing risk approval" in res_no_proof.error.lower()


@pytest.mark.asyncio
async def test_slice_2_2_capital_evidence_derived_from_risk_approval():
    """Item 6: Capital snapshot/required capital is taken from approved RiskApproval or authority."""
    service = CanonicalExecutionService()

    class MockBroker:
        _account_id = "acct_p6"
        async def place_order(self, **kwargs):
            return {"order_id": "ORD_CAP_1"}

    req = ExecutionRequest(
        uid="u_p6", account_id="acct_p6", strategy_id="s1", generation_id="g1", signal_id="sig6",
        exchange="NSE", symbol="AXISBANK", side="BUY", quantity=10,
        available_capital=100.0,  # Strategy input
        capital_required=50.0,
    )

    approval = RiskApproval(
        approval_id="app_cap1", uid="u_p6", account_id="acct_p6", strategy_id="s1", signal_id="sig6", symbol="AXISBANK", side="BUY", quantity=10,
        generation_id="g1", available_capital=500000.0, capital_required=15000.0,
    )

    res = await service.submit_order(req, broker_client=MockBroker(), risk_approval=approval)
    assert res.success

    # Verified capital from RiskApproval reaches journal intent
    intent = order_journal.find(uid="u_p6", account_id="acct_p6", order_id="ORD_CAP_1")
    assert intent is not None
    assert intent.capital_required == 15000.0


@pytest.mark.asyncio
async def test_slice_2_2_emergency_halt_db_failure_returns_degraded():
    """Item 7: emergency_halt catches DB persistence failure, retains in-memory halt, attempts square-off, returns DEGRADED_EMERGENCY_HALT."""
    from app.services.kite_engine import service as kite_svc

    monkeypatch_ls = pytest.MonkeyPatch()
    try:
        def mock_set_kill_switch(enabled, reason="", uid="default", reason_code="kill_switch"):
            raise ControlPlaneUnavailableError("DB connection lost during emergency halt")

        monkeypatch_ls.setattr(live_safety, "set_kill_switch", mock_set_kill_switch)

        res = await kite_svc.emergency_halt(client=MagicMock(), uid="u_p7", reason="DB Outage Emergency")
        assert res["status"] == "DEGRADED_EMERGENCY_HALT"
        assert res["degraded"] is True
        assert live_safety.kill_switch_state()["enabled"] is True
    finally:
        monkeypatch_ls.undo()


@pytest.mark.asyncio
async def test_slice_2_2_modify_order_blocked_during_halted_unless_risk_reducing():
    """Item 8: During HALTED/RECOVERY_REQUIRED, generic modifications are blocked."""
    service = CanonicalExecutionService()
    db.set_operator_state("HALTED", reason="Emergency operator halt", uid="u_p8", account_id="acct_p8")

    class MockBroker:
        _account_id = "acct_p8"
        async def modify_order(self, **kwargs):
            return True

    # 1. Price increase modification on BUY (exposure-increasing / price raising) -> HALTED
    res1 = await service.modify_order(
        uid="u_p8", account_id="acct_p8", order_id="ORD_MOD_1",
        changes={"price": 520.0, "side": "BUY"}, broker_client=MockBroker(),
    )
    assert not res1.success
    assert res1.status == "HALTED"

    # 2. Generic modification without risk_reducing flag -> HALTED
    res2 = await service.modify_order(
        uid="u_p8", account_id="acct_p8", order_id="ORD_MOD_2",
        changes={"order_type": "LIMIT"}, broker_client=MockBroker(),
    )
    assert not res2.success
    assert res2.status == "HALTED"

    # 3. Modification with caller risk_reducing flag -> STILL HALTED (caller-controlled flag removed in 2.2.1)
    res3 = await service.modify_order(
        uid="u_p8", account_id="acct_p8", order_id="ORD_MOD_3",
        changes={"price": 480.0, "side": "BUY", "risk_reducing": True}, broker_client=MockBroker(),
    )
    assert not res3.success
    assert res3.status == "HALTED"


@pytest.mark.asyncio
async def test_slice_2_2_typed_kite_error_imports_from_errors_module():
    """Item 9: Typed Kite errors imported from app.services.exchanges.kite.errors transition intent to REJECTED."""
    from app.services.exchanges.kite.errors import KiteMarginError

    service = CanonicalExecutionService()

    class RejectingBroker:
        _account_id = "acct_p9"
        async def place_order(self, **kwargs):
            raise KiteMarginError("Insufficient margin available")

    req = ExecutionRequest(
        uid="u_p9", account_id="acct_p9", strategy_id="s1", generation_id="g1", signal_id="sig9",
        exchange="NSE", symbol="INFY", side="BUY", quantity=100,
    )
    approval = RiskApproval(
        approval_id="app_p9", uid="u_p9", account_id="acct_p9", strategy_id="s1", signal_id="sig9", symbol="INFY", side="BUY", quantity=100, generation_id="g1", available_capital=100000.0,
    )

    res = await service.submit_order(req, broker_client=RejectingBroker(), risk_approval=approval)
    assert not res.success
    assert res.status == "REJECTED"
    assert "Insufficient margin" in res.error

    # A broker rejection must not latch RECOVERY_REQUIRED: the order never reached
    # the account, so there is nothing to reconcile. The scope stays untouched —
    # no row was written at all, which is what initialized == 0 says.
    ctrl = db.get_execution_control(scope="global", uid="u_p9", account_id="acct_p9")
    assert ctrl["initialized"] == 0
    assert ctrl["revision"] == 0


@pytest.mark.asyncio
async def test_slice_2_2_recovery_recalculates_inventory_post_broker_reconcile():
    """Item 10: Re-read strict inventory state after recover() in startup_recovery."""
    service = CanonicalExecutionService()

    class MockBroker:
        _account_id = "acct_p10"
        async def get_orders(self):
            return []

    res = await service.startup_recovery(uid="u_p10", account_id="acct_p10", broker_client=MockBroker())
    assert res["status"] == "success"
    assert res["recovery_state"] == "CLEAN"


# ── Slice 2.2.1 Micro-Guardrail Tests ──

@pytest.mark.asyncio
async def test_slice_2_2_1_risk_approval_signal_and_strategy_binding_and_expiry(monkeypatch):
    """Guardrail 1: RiskApproval must bind strategy_id and signal_id, and enforce 60s max age."""
    import time
    service = CanonicalExecutionService()

    class MockBroker:
        _account_id = "acct_221_1"
        async def place_order(self, **kwargs):
            return {"order_id": "ORD_221_1"}

    req = ExecutionRequest(
        uid="u_221_1", account_id="acct_221_1", strategy_id="snapback", generation_id="g1", signal_id="sig_221_1",
        exchange="NSE", symbol="INFY", side="BUY", quantity=10,
    )

    # 1. Missing strategy_id in RiskApproval -> REJECTED
    app_no_strat = RiskApproval(
        approval_id="app_1", uid="u_221_1", account_id="acct_221_1", strategy_id="", signal_id="sig_221_1",
        symbol="INFY", side="BUY", quantity=10, generation_id="g1", available_capital=100000.0,
    )
    res1 = await service.submit_order(req, broker_client=MockBroker(), risk_approval=app_no_strat)
    assert not res1.success
    assert res1.status == "REJECTED"
    assert "strategy_id" in res1.error.lower()

    # 2. Mismatched signal_id in RiskApproval -> REJECTED
    app_wrong_sig = RiskApproval(
        approval_id="app_2", uid="u_221_1", account_id="acct_221_1", strategy_id="snapback", signal_id="sig_OTHER",
        symbol="INFY", side="BUY", quantity=10, generation_id="g1", available_capital=100000.0,
    )
    res2 = await service.submit_order(req, broker_client=MockBroker(), risk_approval=app_wrong_sig)
    assert not res2.success
    assert res2.status == "REJECTED"
    assert "signal_id" in res2.error.lower()

    # 3. Expired RiskApproval (>60s old) -> REJECTED
    old_ts = int((time.time() - 100) * 1000)
    app_expired = RiskApproval(
        approval_id="app_3", uid="u_221_1", account_id="acct_221_1", strategy_id="snapback", signal_id="sig_221_1",
        symbol="INFY", side="BUY", quantity=10, generation_id="g1", available_capital=100000.0, timestamp_ms=old_ts,
    )
    res3 = await service.submit_order(req, broker_client=MockBroker(), risk_approval=app_expired)
    assert not res3.success
    assert res3.status == "REJECTED"
    assert "expired" in res3.error.lower()


@pytest.mark.asyncio
async def test_slice_2_2_1_capital_evidence_never_falls_back_to_request():
    """Guardrail 2: For exposure increases, never fall back to request.available_capital or request.capital_required."""
    service = CanonicalExecutionService()

    class MockBroker:
        _account_id = "acct_221_2"
        async def place_order(self, **kwargs):
            return {"order_id": "ORD_221_2"}

    req = ExecutionRequest(
        uid="u_221_2", account_id="acct_221_2", strategy_id="snapback", generation_id="g1", signal_id="sig_221_2",
        exchange="NSE", symbol="INFY", side="BUY", quantity=10, available_capital=500000.0, capital_required=10000.0,
    )

    # RiskApproval without capital evidence (available_capital=0.0) -> MUST BE REJECTED
    approval_no_cap = RiskApproval(
        approval_id="app_no_cap", uid="u_221_2", account_id="acct_221_2", strategy_id="snapback", signal_id="sig_221_2",
        symbol="INFY", side="BUY", quantity=10, generation_id="g1", available_capital=0.0,
    )

    res = await service.submit_order(req, broker_client=MockBroker(), risk_approval=approval_no_cap)
    assert not res.success
    assert res.status == "REJECTED"
    assert "Missing verified capital evidence" in res.error


@pytest.mark.asyncio
async def test_slice_2_2_1_blank_position_account_id_fails_protection_validation():
    """Guardrail 4: Position with blank account_id must fail live protection ownership validation."""
    service = CanonicalExecutionService()

    class MockBroker:
        _account_id = "acct_221_4"
        async def place_gtt(self, **kwargs):
            return {"trigger_id": "GTT_221_4"}

    # Position registered with empty account_id
    positions.register(
        positions.OpenPosition(
            uid="u_221_4", account_id="", symbol="INFY", exchange="NSE", qty=10, order_id="POS_BLANK_ACCT", status=positions.OPEN
        )
    )

    res = await service.place_protection(
        uid="u_221_4", account_id="acct_221_4", position_id="POS_BLANK_ACCT",
        protection_params={"symbol": "INFY", "trigger_price": 1400.0},
        broker_client=MockBroker(),
    )
    assert not res.success
    assert res.status == "REJECTED"
    assert "account identity missing or mismatch" in res.error.lower()


@pytest.mark.asyncio
async def test_slice_2_2_1_position_registry_read_failure_triggers_recovery_required(monkeypatch):
    """Guardrail 5: Position-registry read failure during recovery must force RECOVERY_REQUIRED."""
    service = CanonicalExecutionService()

    def mock_failing_open_positions(uid=None):
        raise RuntimeError("Registry read failure")

    monkeypatch.setattr(positions, "open_positions", mock_failing_open_positions)

    res = await service.startup_recovery(uid="u_221_5", account_id="acct_221_5")
    assert res["status"] == "success"
    assert res["recovery_state"] == "RECOVERY_REQUIRED"
    assert res["positions_read_error"] is True

