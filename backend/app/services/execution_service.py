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
6. Fail-closed broker client availability.
7. Canonical intent tagging (intent.tag sent to broker).
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


class BrokerRejected(Exception):
    """Order explicitly rejected by broker API."""
    pass


class SubmissionOutcomeUnknown(Exception):
    """Broker order submission transport or post-send status unknown."""
    pass


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
    tag: str = ""
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
        from app.services import live_safety
        decision = live_safety.assert_safe_to_trade([], uid=uid, account_id=account_id, exposure_effect=exposure_effect.value)
        if not decision.allowed:
            return False, decision.reason
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
            payload["user_tag"] = request.tag

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

        # 3. Fail closed if no authenticated broker client is provided
        if broker_client is None:
            order_journal.transition(intent.intent_key, "REJECTED", error="No authenticated broker client available")
            return ExecutionResult(
                success=False,
                status="REJECTED",
                intent_key=intent.intent_key,
                error="No authenticated broker client available for live order submission",
            )

        # 4. Broker Send using real KiteClient signature
        order_id = ""
        try:
            place_fn = getattr(broker_client, "place_order", None)
            if not callable(place_fn):
                raise BrokerRejected(f"Broker client {broker_client} does not expose place_order interface")

            sig = inspect.signature(place_fn)
            params = sig.parameters

            kwargs = {}
            if "symbol" in params:
                kwargs["symbol"] = request.symbol
            elif "tradingsymbol" in params:
                kwargs["tradingsymbol"] = request.symbol

            if "side" in params:
                kwargs["side"] = request.side
            elif "transaction_type" in params:
                kwargs["transaction_type"] = request.side

            if "size" in params:
                kwargs["size"] = request.quantity
            elif "quantity" in params:
                kwargs["quantity"] = request.quantity

            # Always send intent.tag as the canonical broker tracking tag
            kwargs["tag"] = intent.tag
            if "order_type" in params:
                kwargs["order_type"] = request.order_type
            if request.price > 0 and "limit_price" in params:
                kwargs["limit_price"] = request.price
            elif request.price > 0 and "price" in params:
                kwargs["price"] = request.price
            if request.trigger_price > 0 and "trigger_price" in params:
                kwargs["trigger_price"] = request.trigger_price

            if request.payload:
                for k, v in request.payload.items():
                    if k not in kwargs and (k in params or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())):
                        kwargs[k] = v

            if inspect.iscoroutinefunction(place_fn):
                res = await place_fn(**kwargs)
            else:
                res = place_fn(**kwargs)

            if isinstance(res, dict):
                order_id = str(res.get("order_id", ""))
            else:
                order_id = str(res)

            if not order_id:
                raise SubmissionOutcomeUnknown("Broker place_order returned empty order_id")

        except Exception as exc:
            if isinstance(exc, SubmissionOutcomeUnknown) or any(w in str(exc).lower() for w in ("timeout", "connection", "network", "uncertain", "504", "502", "reset", "disconnected")):
                order_journal.submission_uncertain(intent.intent_key, str(exc))
                try:
                    db.set_recovery_state(
                        recovery_state="RECOVERY_REQUIRED",
                        reason_code="BROKER_TRANSPORT_UNCERTAINTY",
                        reason=f"Broker submit uncertain for intent {intent.intent_key}: {exc}",
                        uid=request.uid,
                        account_id=request.account_id,
                    )
                except Exception as db_err:
                    log.warning("Failed to set RECOVERY_REQUIRED in DB: %s", db_err)

                return ExecutionResult(
                    success=False,
                    status="UNKNOWN",
                    intent_key=intent.intent_key,
                    error=f"Broker submission uncertain: {exc}",
                )
            else:
                order_journal.transition(intent.intent_key, "REJECTED", error=str(exc))
                return ExecutionResult(
                    success=False,
                    status="REJECTED",
                    intent_key=intent.intent_key,
                    error=f"Broker placement rejected: {exc}",
                )

        # 5. Acknowledge order placement in journal (Broker HTTP submit ACK)
        ack_intent = order_journal.acknowledge(intent.intent_key, order_id)

        return ExecutionResult(
            success=True,
            status="ACKNOWLEDGED",
            intent_key=intent.intent_key,
            order_id=order_id,
            filled_quantity=ack_intent.filled_quantity if ack_intent else 0,
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

        if broker_client is None:
            return ExecutionResult(success=False, status="REJECTED", error="No authenticated broker client available")

        try:
            modify_fn = getattr(broker_client, "modify_order", None)
            if callable(modify_fn):
                kwargs = {"order_id": order_id, **changes}
                if inspect.iscoroutinefunction(modify_fn):
                    await modify_fn(**kwargs)
                else:
                    modify_fn(**kwargs)
                return ExecutionResult(success=True, status="ACKNOWLEDGED", order_id=order_id)
            else:
                return ExecutionResult(success=False, status="REJECTED", error="Broker client does not support modify_order")
        except Exception as exc:
            return ExecutionResult(success=False, status="REJECTED", order_id=order_id, error=str(exc))

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

        if broker_client is None:
            return ExecutionResult(success=False, status="REJECTED", error="No authenticated broker client available")

        try:
            cancel_fn = getattr(broker_client, "cancel_order", None)
            if callable(cancel_fn):
                if inspect.iscoroutinefunction(cancel_fn):
                    await cancel_fn(variety=variety, order_id=order_id)
                else:
                    cancel_fn(variety=variety, order_id=order_id)
                return ExecutionResult(success=True, status="ACKNOWLEDGED", order_id=order_id)
            else:
                return ExecutionResult(success=False, status="REJECTED", error="Broker client does not support cancel_order")
        except Exception as exc:
            return ExecutionResult(success=False, status="REJECTED", error=str(exc))

    async def place_protection(
        self,
        uid: str,
        account_id: str,
        position_id: str,
        protection_params: Dict[str, Any],
        broker_client: Any = None,
    ) -> ExecutionResult:
        """Place GTT / stop protection order for confirmed position holdings."""
        allowed, reason = self.is_trading_allowed(
            uid=uid, account_id=account_id, exposure_effect=ExposureEffect.PROTECT_POSITION
        )
        if not allowed:
            return ExecutionResult(success=False, status="HALTED", error=reason)

        if broker_client is None:
            return ExecutionResult(success=False, status="REJECTED", error="No authenticated broker client available")

        prot_id = f"GTT_{position_id[:12]}"
        try:
            place_gtt_fn = getattr(broker_client, "place_gtt", None)
            if callable(place_gtt_fn):
                if inspect.iscoroutinefunction(place_gtt_fn):
                    res = await place_gtt_fn(**protection_params)
                else:
                    res = place_gtt_fn(**protection_params)
                if isinstance(res, dict):
                    prot_id = str(res.get("trigger_id", prot_id))
                return ExecutionResult(success=True, status="ACKNOWLEDGED", order_id=prot_id)
            else:
                return ExecutionResult(success=False, status="REJECTED", error="Broker client does not support place_gtt")
        except Exception as exc:
            return ExecutionResult(success=False, status="REJECTED", error=str(exc))

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

        # 1. Clean orphaned RESERVED intents (no submission claim won, no broker request made)
        unresolved_intents = order_journal.unresolved(uid, account_id=account_id)
        for i in unresolved_intents:
            if i.state == "RESERVED":
                order_journal.transition(i.intent_key, "CANCELLED", error="Startup recovery expired orphaned reservation")

        # 2. Inspect remaining unresolved journal items (SUBMITTING, UNKNOWN, reconciliation_required)
        unresolved_intents = order_journal.unresolved(uid, account_id=account_id)
        uncertain = any(i.state in ("SUBMITTING", "UNKNOWN") or i.reconciliation_required for i in unresolved_intents)

        if uncertain:
            try:
                ctrl = db.set_recovery_state(
                    recovery_state="RECOVERY_REQUIRED",
                    reason_code="UNRESOLVED_JOURNAL_INTENTS",
                    reason=f"Startup found {len(unresolved_intents)} unresolved order journal intent(s)",
                    uid=uid,
                    account_id=account_id,
                )
                curr_rev = ctrl.get("revision", curr_rev)
            except Exception as exc:
                log.warning("Failed to set RECOVERY_REQUIRED: %s", exc)

        # 3. Broker Reconciliation: OBSERVE ONLY, NEVER RESUBMIT
        if broker_client is not None:
            await recover(broker_client, uid=uid)
            unresolved_intents = order_journal.unresolved(uid, account_id=account_id)

        # 4. If clean (no remaining unresolved intents needing reconciliation), transition recovery_state to CLEAN via CAS
        remaining_uncertain = any(i.state in ("SUBMITTING", "UNKNOWN") or i.reconciliation_required for i in unresolved_intents)
        if not remaining_uncertain:
            try:
                db.set_recovery_state(
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
            "operator_state": ctrl.get("operator_state", "RUNNING"),
            "recovery_state": "CLEAN" if not remaining_uncertain else "RECOVERY_REQUIRED",
            "unresolved_count": len(unresolved_intents),
        }

    async def recover_unresolved(self, uid: str, account_id: str, client: Any = None) -> Dict[str, Any]:
        """Recover and project unresolved orders for account."""
        return await self.startup_recovery(uid=uid, account_id=account_id, broker_client=client)


# Global singleton instance
execution_service = CanonicalExecutionService()
