"""Canonical Execution Service for Sterling.

Serves as the single execution authority for all broker order submissions across
all strategy engines (Kite Engine, Opening Leaders, Intraday, Gamma Move, Adaptive Edge,
Navigator, manual API).

Enforces:
1. Strict order state machine: RESERVED -> SUBMITTING -> ACKNOWLEDGED / UNKNOWN -> PARTIAL / FILLED.
2. Durable intent journal reservation (kite_engine/order_journal.py).
3. Broker order recovery on transport uncertainty (never blind re-submission).
4. Durable 2D halt & recovery state checking via execution_control (operator_state & recovery_state).
5. Exposure effect classification (INCREASE_EXPOSURE, REDUCE_EXPOSURE, CLOSE_POSITION, PROTECT_POSITION, CANCEL_ORDER, MODIFY_ORDER).
"""
from dataclasses import dataclass
from enum import Enum
import inspect
import time
from typing import Any, Dict, Optional, Tuple

from app.core.logging import get_logger
from app.services import db
from app.services.kite_engine import order_journal
from app.services.kite_engine.execution_lifecycle import recover

log = get_logger(__name__)


class ExposureEffect(str, Enum):
    INCREASE_EXPOSURE = "INCREASE_EXPOSURE"
    REDUCE_EXPOSURE = "REDUCE_EXPOSURE"
    CLOSE_POSITION = "CLOSE_POSITION"
    PROTECT_POSITION = "PROTECT_POSITION"
    CANCEL_ORDER = "CANCEL_ORDER"
    MODIFY_ORDER = "MODIFY_ORDER"


@dataclass(frozen=True)
class ExecutionRequest:
    uid: str
    account_id: str
    strategy_id: str
    generation_id: str
    signal_id: str
    exchange: str
    symbol: str
    side: str                            # "BUY" | "SELL"
    quantity: int
    tag: str
    exposure_effect: ExposureEffect = ExposureEffect.INCREASE_EXPOSURE
    order_type: str = "MARKET"
    price: float = 0.0
    trigger_price: float = 0.0
    capital_required: float = 0.0
    payload: Optional[Dict[str, Any]] = None
    gtt_params: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class ExecutionResult:
    success: bool
    status: str          # "FILLED" | "ACKNOWLEDGED" | "SUBMITTED" | "UNKNOWN" | "REJECTED" | "HALTED"
    intent_key: str = ""
    order_id: str = ""
    error: str = ""
    filled_quantity: int = 0
    filled_price: float = 0.0
    protection_id: str = ""
    revision: int = 0


class CanonicalExecutionService:
    """Single execution authority for all broker order placements."""

    def __init__(self):
        pass

    def is_trading_allowed(
        self,
        scope: str = "global",
        uid: str = "default",
        account_id: str = "default",
        exposure_effect: ExposureEffect = ExposureEffect.INCREASE_EXPOSURE,
    ) -> Tuple[bool, str]:
        try:
            ctrl = db.get_execution_control(scope=scope, uid=uid, account_id=account_id)
        except Exception as exc:
            return False, f"Control plane unavailable (fail closed): {exc}"

        op_state = ctrl.get("operator_state") or ctrl.get("state", "HALTED")
        rec_state = ctrl.get("recovery_state", "CLEAN")

        if exposure_effect == ExposureEffect.INCREASE_EXPOSURE:
            if rec_state == "RECOVERY_REQUIRED":
                return False, "Trading blocked: RECOVERY_REQUIRED (broker reconciliation pending)"
            if op_state == "HALTED":
                return False, f"Trading is HALTED: {ctrl.get('reason', 'System halted')}"

        return True, "OK"

    async def submit_order(
        self,
        request: ExecutionRequest,
        broker_client: Any = None,
        risk_approved: bool = True,
    ) -> ExecutionResult:
        """Submit an order through the canonical execution pipeline."""
        if not risk_approved:
            return ExecutionResult(success=False, status="REJECTED", error="Risk check rejected order submission")

        allowed, reason = self.is_trading_allowed(
            uid=request.uid,
            account_id=request.account_id,
            exposure_effect=request.exposure_effect,
        )
        if not allowed:
            log.warning("Execution rejected by control plane: %s", reason)
            return ExecutionResult(success=False, status="HALTED", error=reason)

        # 1. Reserve intent in durable order journal
        payload = dict(request.payload or {})
        if request.tag:
            payload["tag"] = request.tag

        try:
            intent = order_journal.reserve(
                uid=request.uid,
                account_id=request.account_id,
                strategy_id=request.strategy_id,
                generation_id=request.generation_id,
                signal_id=request.signal_id,
                exchange=request.exchange,
                symbol=request.symbol,
                side=request.side,
                quantity=request.quantity,
                capital_required=request.capital_required,
                payload=payload,
            )
        except Exception as reserve_err:
            return ExecutionResult(success=False, status="REJECTED", error=f"Journal reservation failed: {reserve_err}")

        # If already submitted/acknowledged/filled, return existing status
        if intent.state in ("SUBMITTED", "FILLED", "PARTIAL", "ACKNOWLEDGED"):
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

        # 3. Broker Send
        order_id = ""
        if broker_client is not None:
            try:
                place_fn = getattr(broker_client, "place_order", None)
                if callable(place_fn):
                    kwargs = {
                        "variety": getattr(broker_client, "VARIETY_REGULAR", "regular"),
                        "exchange": request.exchange,
                        "tradingsymbol": request.symbol,
                        "transaction_type": request.side,
                        "quantity": request.quantity,
                        "product": getattr(broker_client, "PRODUCT_MIS", "MIS"),
                        "order_type": request.order_type,
                        "price": request.price,
                        "trigger_price": request.trigger_price,
                        "tag": request.tag,
                    }
                    if inspect.iscoroutinefunction(place_fn):
                        res = await place_fn(**kwargs)
                    else:
                        res = place_fn(**kwargs)

                    if isinstance(res, dict):
                        order_id = str(res.get("order_id", ""))
                    else:
                        order_id = str(res)
            except Exception as exc:
                exc_str = str(exc)
                if any(w in exc_str.lower() for w in ("timeout", "connection", "network", "uncertain", "504", "502")):
                    order_journal.submission_uncertain(intent.intent_key, exc_str)
                    try:
                        ctrl = db.get_execution_control(uid=request.uid, account_id=request.account_id)
                        db.set_execution_control(
                            operator_state=ctrl.get("operator_state", "RUNNING"),
                            recovery_state="RECOVERY_REQUIRED",
                            reason_code="BROKER_TRANSPORT_UNCERTAINTY",
                            reason=f"Broker submit uncertain for intent {intent.intent_key}: {exc_str}",
                            uid=request.uid,
                            account_id=request.account_id,
                        )
                    except Exception as db_err:
                        log.warning("Failed to set RECOVERY_REQUIRED in DB: %s", db_err)

                    return ExecutionResult(
                        success=False,
                        status="UNKNOWN",
                        intent_key=intent.intent_key,
                        error=f"Broker submission uncertain: {exc_str}",
                    )
                else:
                    order_journal.transition(intent.intent_key, "REJECTED", error=exc_str)
                    return ExecutionResult(
                        success=False,
                        status="REJECTED",
                        intent_key=intent.intent_key,
                        error=f"Broker placement rejected: {exc_str}",
                    )
        else:
            order_id = f"MOCK_{intent.intent_key[:12]}"

        # 4. Acknowledge order placement in journal
        ack_intent = order_journal.acknowledge(intent.intent_key, order_id)

        # 5. Protection Placement if requested
        protection_id = ""
        if request.gtt_params and broker_client is not None:
            try:
                prot_res = await self.place_protection(
                    uid=request.uid,
                    account_id=request.account_id,
                    position_id=order_id,
                    protection_params=request.gtt_params,
                    broker_client=broker_client,
                )
                if prot_res.success:
                    protection_id = prot_res.order_id
            except Exception as prot_err:
                log.warning("Protection placement failed for order %s: %s", order_id, prot_err)

        return ExecutionResult(
            success=True,
            status="ACKNOWLEDGED",
            intent_key=intent.intent_key,
            order_id=order_id,
            filled_quantity=ack_intent.filled_quantity if ack_intent else 0,
            protection_id=protection_id,
        )

    submit = submit_order  # Alias for backward compatibility

    async def modify_order(
        self,
        uid: str,
        account_id: str,
        order_id: str,
        changes: Dict[str, Any],
        broker_client: Any = None,
    ) -> ExecutionResult:
        """Modify an existing broker order."""
        allowed, reason = self.is_trading_allowed(
            uid=uid, account_id=account_id, exposure_effect=ExposureEffect.MODIFY_ORDER
        )
        if not allowed:
            return ExecutionResult(success=False, status="HALTED", error=reason)

        if broker_client is not None:
            try:
                modify_fn = getattr(broker_client, "modify_order", None)
                if callable(modify_fn):
                    kwargs = {"order_id": order_id, **changes}
                    if inspect.iscoroutinefunction(modify_fn):
                        await modify_fn(**kwargs)
                    else:
                        modify_fn(**kwargs)
                    return ExecutionResult(success=True, status="ACKNOWLEDGED", order_id=order_id)
            except Exception as exc:
                return ExecutionResult(success=False, status="REJECTED", order_id=order_id, error=str(exc))

        return ExecutionResult(success=True, status="ACKNOWLEDGED", order_id=order_id)

    async def cancel_order(
        self,
        uid: str,
        account_id: str,
        order_id: str,
        broker_client: Any = None,
        variety: str = "regular",
    ) -> ExecutionResult:
        """Cancel an open order (permitted even during HALTED state)."""
        allowed, reason = self.is_trading_allowed(
            uid=uid, account_id=account_id, exposure_effect=ExposureEffect.CANCEL_ORDER
        )
        if not allowed:
            return ExecutionResult(success=False, status="HALTED", error=reason)

        if broker_client is not None:
            try:
                cancel_fn = getattr(broker_client, "cancel_order", None)
                if callable(cancel_fn):
                    if inspect.iscoroutinefunction(cancel_fn):
                        await cancel_fn(variety=variety, order_id=order_id)
                    else:
                        cancel_fn(variety=variety, order_id=order_id)
            except Exception as exc:
                return ExecutionResult(success=False, status="REJECTED", order_id=order_id, error=str(exc))

        return ExecutionResult(success=True, status="ACKNOWLEDGED", order_id=order_id)

    async def place_protection(
        self,
        uid: str,
        account_id: str,
        position_id: str,
        protection_params: Dict[str, Any],
        broker_client: Any = None,
    ) -> ExecutionResult:
        """Place GTT / stop protection order (permitted even during HALTED state)."""
        allowed, reason = self.is_trading_allowed(
            uid=uid, account_id=account_id, exposure_effect=ExposureEffect.PROTECT_POSITION
        )
        if not allowed:
            return ExecutionResult(success=False, status="HALTED", error=reason)

        prot_id = f"GTT_{position_id[:12]}"
        if broker_client is not None:
            try:
                place_gtt_fn = getattr(broker_client, "place_gtt", None)
                if callable(place_gtt_fn):
                    if inspect.iscoroutinefunction(place_gtt_fn):
                        res = await place_gtt_fn(**protection_params)
                    else:
                        res = place_gtt_fn(**protection_params)
                    if isinstance(res, dict):
                        prot_id = str(res.get("trigger_id", prot_id))
            except Exception as exc:
                return ExecutionResult(success=False, status="REJECTED", error=str(exc))

        return ExecutionResult(success=True, status="ACKNOWLEDGED", order_id=prot_id)

    async def startup_recovery(
        self,
        uid: str = "default",
        account_id: str = "default",
        broker_client: Any = None,
    ) -> Dict[str, Any]:
        """Perform startup recovery workflow: inspect unresolved journal, observe broker, update recovery_state via CAS."""
        db.init()

        try:
            ctrl = db.get_execution_control(uid=uid, account_id=account_id)
        except Exception as exc:
            return {"status": "error", "reason": f"Control plane uninitialized: {exc}"}

        curr_rev = ctrl.get("revision", 0)
        op_state = ctrl.get("operator_state", "HALTED")

        # 1. Inspect unresolved journal items
        unresolved_intents = order_journal.unresolved(uid, account_id=account_id)
        uncertain = any(i.state in ("RESERVED", "SUBMITTING", "UNKNOWN") or i.reconciliation_required for i in unresolved_intents)

        if uncertain:
            try:
                ctrl = db.set_execution_control(
                    operator_state=op_state,
                    recovery_state="RECOVERY_REQUIRED",
                    reason_code="UNRESOLVED_JOURNAL_INTENTS",
                    reason=f"Startup found {len(unresolved_intents)} unresolved order journal intent(s)",
                    uid=uid,
                    account_id=account_id,
                )
                curr_rev = ctrl.get("revision", curr_rev)
            except Exception as exc:
                log.warning("Failed to set RECOVERY_REQUIRED: %s", exc)

        # 2. Broker Reconciliation: OBSERVE ONLY, NEVER RESUBMIT
        if broker_client is not None:
            await recover(broker_client, uid=uid)
            unresolved_intents = order_journal.unresolved(uid, account_id=account_id)

        # 3. If clean (no remaining unresolved intents needing reconciliation), transition recovery_state to CLEAN via CAS
        remaining_uncertain = any(i.state in ("RESERVED", "SUBMITTING", "UNKNOWN") or i.reconciliation_required for i in unresolved_intents)
        if not remaining_uncertain:
            try:
                db.set_execution_control(
                    operator_state=op_state,     # Keep operator_state unchanged!
                    recovery_state="CLEAN",
                    reason_code="RECONCILIATION_CLEAN",
                    reason="Startup recovery clean — broker orders reconciled",
                    uid=uid,
                    account_id=account_id,
                    expected_revision=curr_rev,
                    last_reconciled_ms=int(time.time() * 1000),
                )
            except Exception as exc:
                log.warning("CAS update to CLEAN failed in startup_recovery: %s", exc)

        return {
            "status": "success",
            "operator_state": op_state,
            "recovery_state": "CLEAN" if not remaining_uncertain else "RECOVERY_REQUIRED",
            "unresolved_count": len(unresolved_intents),
        }

    async def recover_unresolved(self, uid: str, account_id: str, client: Any = None) -> Dict[str, Any]:
        """Recover and project unresolved orders for account."""
        return await self.startup_recovery(uid=uid, account_id=account_id, broker_client=client)


# Global singleton instance
execution_service = CanonicalExecutionService()

