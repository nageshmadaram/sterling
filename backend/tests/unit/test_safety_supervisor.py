"""One safety authority, and it is checked at the broker boundary.

Sterling used to keep four switches that each meant "stop" and were each read by
a different part of the code. The two properties worth testing are that engaging
any one of them refuses new exposure everywhere, and that engaging one *after* a
submission has already passed admission still stops the order — because the
window between "admitted" and "sent" is exactly when an operator reaches for the
switch.
"""
from __future__ import annotations

import pytest

from app.services import db, live_safety
from app.services.execution_service import (
    CanonicalExecutionService,
    ExecutionRequest,
    ExposureEffect,
    RiskApproval,
)
from app.services.safe_mode import SafeModeService, SafeModeTrigger
from app.services.safety_supervisor import ExposureIntent, SafetySupervisor


@pytest.fixture(autouse=True)
def _isolated_state(tmp_path, monkeypatch):
    db_file = str(tmp_path / "supervisor.db")
    monkeypatch.setenv("STERLING_DB_PATH", db_file)
    monkeypatch.setattr(db, "_DB_PATH", db_file)
    monkeypatch.setattr(db, "_available", True)
    db.init()

    safe_file = tmp_path / "safe_mode.json"
    monkeypatch.setenv("STERLING_SAFE_MODE_FILE", str(safe_file))
    SafeModeService(safe_file).initialise(operator_ack=True, note="test")

    monkeypatch.setenv("STERLING_NEW_TRADES_HALT_PATH", str(tmp_path / "halt.json"))

    live_safety.reset_all_for_tests()
    db.set_execution_control(
        operator_state="RUNNING",
        recovery_state="CLEAN",
        reason="startup recovery complete",
        actor="tests",
    )
    yield safe_file
    live_safety.reset_all_for_tests()


def test_a_clean_system_admits_new_exposure(_isolated_state):
    assert SafetySupervisor().authorize(ExposureIntent.INCREASE_EXPOSURE).allowed is True


def test_safe_mode_refuses_new_exposure(_isolated_state):
    SafeModeService(_isolated_state).engage(
        trigger=SafeModeTrigger.OPERATOR, reason="operator stop"
    )

    verdict = SafetySupervisor().authorize(ExposureIntent.INCREASE_EXPOSURE)

    assert verdict.allowed is False
    assert verdict.code == "safe_mode"
    assert "operator stop" in verdict.reason


@pytest.mark.parametrize(
    "effect",
    [
        ExposureIntent.REDUCE_EXPOSURE,
        ExposureIntent.CLOSE_POSITION,
        ExposureIntent.PROTECT_POSITION,
        ExposureIntent.CANCEL_ORDER,
        ExposureIntent.RECONCILE,
    ],
)
def test_safe_mode_never_blocks_managing_what_is_already_open(_isolated_state, effect):
    SafeModeService(_isolated_state).engage(
        trigger=SafeModeTrigger.OPERATOR, reason="operator stop"
    )

    assert SafetySupervisor().authorize(effect).allowed is True


def test_a_missing_safety_state_refuses_new_exposure(_isolated_state):
    _isolated_state.unlink()

    verdict = SafetySupervisor().authorize(ExposureIntent.INCREASE_EXPOSURE)

    assert verdict.allowed is False
    assert verdict.code == "safe_mode"
    assert verdict.snapshot is not None
    assert SafeModeTrigger.SAFETY_STATE_UNAVAILABLE in verdict.snapshot.safe_mode_triggers


def test_the_durable_halt_and_safe_mode_are_one_answer(_isolated_state):
    """The operator switch and the control plane must not disagree."""
    db.set_operator_state(operator_state="HALTED", reason="maintenance")

    verdict = SafetySupervisor().authorize(ExposureIntent.INCREASE_EXPOSURE)
    assert verdict.allowed is False

    snap = SafetySupervisor().snapshot()
    assert snap.operator_state == "HALTED"
    assert snap.may_increase_exposure is False
    assert snap.may_reduce_exposure is True
    assert snap.may_protect is True
    assert snap.may_reconcile is True


def test_the_family_stop_switch_blocks_snapback_only(_isolated_state):
    from app.services.snapback_family_ops import set_new_trades_halted

    set_new_trades_halted(True, reason="operator pause")

    assert (
        SafetySupervisor()
        .authorize(ExposureIntent.INCREASE_EXPOSURE, strategy_id="snapback")
        .allowed
        is False
    )
    assert (
        SafetySupervisor()
        .authorize(ExposureIntent.INCREASE_EXPOSURE, strategy_id="supertrend")
        .allowed
        is True
    )


def test_live_safety_inherits_the_same_authority(_isolated_state):
    """Every existing caller of assert_safe_to_trade sees SAFE_MODE too."""
    SafeModeService(_isolated_state).engage(
        trigger=SafeModeTrigger.PROTECTION_MISSING, reason="GTT missing"
    )

    decision = live_safety.assert_safe_to_trade([], exposure_effect="INCREASE_EXPOSURE")
    assert decision.allowed is False
    assert decision.code == "safe_mode"

    assert live_safety.assert_safe_to_trade(
        [], exposure_effect="CLOSE_POSITION"
    ).allowed is True


class _RecordingBroker:
    """Engages SAFE_MODE the moment the executor asks for its account identity.

    That read happens after admission and after the journal reservation, so it
    lands the operator's switch precisely inside the window the old single check
    left open.
    """

    def __init__(self, safe_file):
        self._safe_file = safe_file
        self.sent = []

    @property
    def _account_id(self):
        SafeModeService(self._safe_file).engage(
            trigger=SafeModeTrigger.OPERATOR, reason="operator stop mid-flight"
        )
        return "acct_race"

    async def place_order(self, **kwargs):
        self.sent.append(kwargs)
        return {"order_id": "SHOULD-NOT-EXIST"}


@pytest.mark.asyncio
async def test_safe_mode_engaged_after_admission_still_stops_the_order(_isolated_state):
    broker = _RecordingBroker(_isolated_state)
    request = ExecutionRequest(
        uid="u_race",
        account_id="acct_race",
        strategy_id="supertrend",
        generation_id="g1",
        signal_id="sig1",
        exchange="NFO",
        symbol="NIFTY26SEP24000CE",
        side="BUY",
        quantity=75,
        exposure_effect=ExposureEffect.INCREASE_EXPOSURE,
    )
    approval = RiskApproval(
        approval_id="a1",
        uid="u_race",
        account_id="acct_race",
        strategy_id="supertrend",
        signal_id="sig1",
        symbol="NIFTY26SEP24000CE",
        side="BUY",
        quantity=75,
        generation_id="g1",
        available_capital=500_000.0,
    )

    result = await CanonicalExecutionService().submit_order(
        request, broker_client=broker, risk_approval=approval
    )

    assert broker.sent == []
    assert result.success is False
    assert result.status == "HALTED"
    assert "SAFE_MODE" in result.error
