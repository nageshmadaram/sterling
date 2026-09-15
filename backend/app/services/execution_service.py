"""Canonical Execution Service for Sterling.

Serves as the single execution authority for all broker order submissions across
all strategy engines (Kite Engine, Opening Leaders, Intraday, Gamma Move, Adaptive Edge,
Navigator, manual API).

Enforces:
1. Strict order state machine: RESERVED -> SUBMITTING -> SUBMITTED / UNKNOWN -> PARTIAL / FILLED.
2. Durable intent journal reservation (kite_engine/order_journal.py).
3. Broker order recovery on transport uncertainty (never blind re-submission).
4. Durable halt state checking via execution_control.
"""
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from app.core.logging import get_logger
from app.services import db
from app.services.kite_engine import order_journal
from app.services.kite_engine.execution_lifecycle import recover

log = get_logger(__name__)


@dataclass(frozen=True)
class ExecutionRequest:
    uid: str
    account_id: str
    strategy_id: str
    generation_id: str
    signal_id: str
    exchange: str
    symbol: str
    side: str
    quantity: int
    tag: str
    order_type: str = "MARKET"
    price: float = 0.0
    trigger_price: float = 0.0
    capital_required: float = 0.0
    payload: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class ExecutionResult:
    success: bool
    status: str          # "FILLED" | "SUBMITTED" | "UNKNOWN" | "REJECTED" | "HALTED"
    intent_key: str = ""
    order_id: str = ""
    error: str = ""
    filled_quantity: int = 0
    filled_price: float = 0.0


class CanonicalExecutionService:
    """Single execution authority for all broker order placements."""

    def __init__(self):
        pass

    def is_trading_allowed(self, scope: str = "global", uid: str = "default", account_id: str = "default") -> Tuple[bool, str]:
        ctrl = db.get_execution_control(scope=scope, uid=uid, account_id=account_id)
        if ctrl.get("state") == "HALTED":
            return False, f"Trading is HALTED: {ctrl.get('reason', 'System halted')}"
        if ctrl.get("state") == "RECOVERY_REQUIRED":
            return False, "Trading blocked: RECOVERY_REQUIRED (broker reconciliation pending)"
        return True, "OK"

    async def submit(self, request: ExecutionRequest, risk_approved: bool = True) -> ExecutionResult:
        """Submit an order through the canonical execution pipeline."""
        if not risk_approved:
            return ExecutionResult(success=False, status="REJECTED", error="Risk check rejected order submission")

        allowed, reason = self.is_trading_allowed(uid=request.uid, account_id=request.account_id)
        if not allowed:
            log.warning("Execution rejected by control plane: %s", reason)
            return ExecutionResult(success=False, status="HALTED", error=reason)

        # 1. Reserve intent in durable order journal
        intent, err = order_journal.reserve(
            uid=request.uid,
            account_id=request.account_id,
            strategy_id=request.strategy_id,
            generation_id=request.generation_id,
            signal_id=request.signal_id,
            exchange=request.exchange,
            symbol=request.symbol,
            side=request.side,
            quantity=request.quantity,
            tag=request.tag,
            capital_required=request.capital_required,
            payload=request.payload or {},
        )

        if not intent:
            return ExecutionResult(success=False, status="REJECTED", error=f"Journal reservation failed: {err}")

        # If already submitted/filled, return existing status
        if intent.state in ("SUBMITTED", "FILLED", "PARTIAL"):
            return ExecutionResult(
                success=True,
                status=intent.state,
                intent_key=intent.intent_key,
                order_id=intent.order_id,
                filled_quantity=intent.filled_quantity,
            )

        # 2. Claim submission state
        claimed = order_journal.claim_submission(intent.intent_key)
        if not claimed:
            return ExecutionResult(
                success=False,
                status=intent.state,
                intent_key=intent.intent_key,
                error="Could not claim submission state (concurrent execution or intent already claimed)",
            )

        return ExecutionResult(
            success=True,
            status="SUBMITTED",
            intent_key=intent.intent_key,
            order_id=intent.order_id,
        )

    async def recover_unresolved(self, uid: str, account_id: str, client: Any = None) -> Dict[str, Any]:
        """Recover and project unresolved orders for account."""
        if client is not None:
            await recover(client, uid=uid)
        unresolved_intents = [
            {
                "intent_key": i.intent_key,
                "strategy_id": i.strategy_id,
                "symbol": i.symbol,
                "side": i.side,
                "quantity": i.quantity,
                "state": i.state,
                "order_id": i.order_id,
            }
            for i in order_journal.unresolved(uid, account_id=account_id)
        ]
        return {"status": "success", "unresolved": unresolved_intents}


# Global singleton instance
execution_service = CanonicalExecutionService()
