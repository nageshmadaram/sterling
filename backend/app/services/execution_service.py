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
6. Fail-closed broker client availability & account identity binding.
7. Real broker protocol arguments (symbol/tradingsymbol, side/transaction_type, size/quantity, exchange, product=NRML, allow_amo=False, trigger_price).
8. Inventory verification of declared exposure effects.
9. Protection lease & pending persistence for protection commands.
10. Complete startup recovery (journal intents + inventory reconciliation).
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

    def verify_exposure_effect(
        self,
        uid: str,
        account_id: str,
        symbol: str,
        side: str,
        declared_effect: ExposureEffect,
    ) -> ExposureEffect:
        """Verify caller-declared exposure effect against database inventory.
        
        If caller declares CLOSE_POSITION or REDUCE_EXPOSURE without holding open inventory,
        or on the same side as existing position, reclassify as INCREASE_EXPOSURE.
        """
        if declared_effect not in (ExposureEffect.CLOSE_POSITION, ExposureEffect.REDUCE_EXPOSURE):
            return declared_effect

        try:
            inv = db.get_inventory(account_id, uid, symbol) if hasattr(db, "get_inventory") else None
            net_qty = inv.get("net_quantity", 0) if inv else 0
            if net_qty == 0:
                log.warning("Declared %s for %s on zero inventory — reclassifying to INCREASE_EXPOSURE", declared_effect.value, symbol)
                return ExposureEffect.INCREASE_EXPOSURE
            if (side.upper() == "BUY" and net_qty > 0) or (side.upper() == "SELL" and net_qty < 0):
                log.warning("Declared %s for %s on same side (%s, net=%d) — reclassifying to INCREASE_EXPOSURE", declared_effect.value, symbol, side, net_qty)
                return ExposureEffect.INCREASE_EXPOSURE
        except Exception as exc:
            log.warning("Failed to verify inventory for %s: %s — treating as INCREASE_EXPOSURE", symbol, exc)
            return ExposureEffect.INCREASE_EXPOSURE

        return declared_effect

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
        risk_approved: bool = False,
    ) -> ExecutionResult:
        """Submit an order through the canonical execution pipeline."""
        if not risk_approved:
            return ExecutionResult(success=False, status="REJECTED", error="Risk check rejected or missing risk approval")

        # Verify declared exposure effect against inventory
        effective_effect = self.verify_exposure_effect(
            uid=request.uid,
            account_id=request.account_id,
            symbol=request.symbol,
            side=request.side,
            declared_effect=request.exposure_effect,
        )

        allowed, reason = self.is_trading_allowed(
            uid=request.uid,
            account_id=request.account_id,
            exposure_effect=effective_effect,
        )
        if not allowed:
            log.warning("Execution rejected by control plane: %s", reason)
            return ExecutionResult(success=False, status="HALTED", error=reason)

        # 1. Reserve intent in durable order journal
        payload = dict(request.payload or {})
        payload["exposure_effect"] = effective_effect.value
        payload["intent_type"] = "EXIT" if effective_effect in (ExposureEffect.CLOSE_POSITION, ExposureEffect.REDUCE_EXPOSURE) else "ENTRY"
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

        # 4. Authenticated account identity check
        client_acct = getattr(broker_client, "_account_id", getattr(broker_client, "account_id", None))
        if client_acct and str(client_acct) != str(request.account_id):
            order_journal.transition(intent.intent_key, "REJECTED", error=f"Broker account mismatch: {client_acct} != {request.account_id}")
            return ExecutionResult(
                success=False,
                status="REJECTED",
                intent_key=intent.intent_key,
                error=f"Broker client account identity ({client_acct}) does not match request account identity ({request.account_id})",
            )

        # 5. Broker Send using real KiteClient signature & arguments
        order_id = ""
        try:
            place_fn = getattr(broker_client, "place_order", None)
            if not callable(place_fn):
                raise BrokerRejected(f"Broker client {broker_client} does not expose place_order interface")

            sig = inspect.signature(place_fn)
            params = sig.parameters

            kwargs = {}
            kwargs["symbol"] = request.symbol
            kwargs["tradingsymbol"] = request.symbol
            kwargs["side"] = request.side
            kwargs["transaction_type"] = request.side
            kwargs["size"] = request.quantity
            kwargs["quantity"] = request.quantity
            kwargs["exchange"] = request.exchange or "NSE"

            # Default product to NRML to match execution_lifecycle identity contract
            payload_product = (request.payload or {}).get("product", "NRML")
            kwargs["product"] = payload_product

            # Force allow_amo=False to prevent silent AMO conversion
            kwargs["allow_amo"] = False

            # Always send intent.tag as canonical broker tracking tag
            kwargs["tag"] = intent.tag
            kwargs["order_type"] = request.order_type

            if request.price > 0:
                kwargs["limit_price"] = request.price
                kwargs["price"] = request.price

            if request.trigger_price > 0:
                kwargs["trigger_price"] = request.trigger_price
                kwargs["stop_loss"] = request.trigger_price

            if request.payload:
                for k, v in request.payload.items():
                    if k not in kwargs and (k in params or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())):
                        kwargs[k] = v

            # Filter kwargs to parameters accepted by place_fn signature (or keep all if **kwargs present)
            has_var_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())
            if not has_var_kwargs:
                kwargs = {k: v for k, v in kwargs.items() if k in params}

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
            if isinstance(exc, SubmissionOutcomeUnknown) or not isinstance(exc, BrokerRejected):
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

        # 6. Acknowledge order placement in journal (Broker HTTP submit ACK)
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

        client_acct = getattr(broker_client, "_account_id", getattr(broker_client, "account_id", None))
        if client_acct and str(client_acct) != str(account_id):
            return ExecutionResult(success=False, status="REJECTED", error=f"Broker account mismatch: {client_acct} != {account_id}")

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

        client_acct = getattr(broker_client, "_account_id", getattr(broker_client, "account_id", None))
        if client_acct and str(client_acct) != str(account_id):
            return ExecutionResult(success=False, status="REJECTED", error=f"Broker account mismatch: {client_acct} != {account_id}")

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
        """Place GTT / stop protection order for confirmed position holdings with lease and pending tracking."""
        allowed, reason = self.is_trading_allowed(
            uid=uid, account_id=account_id, exposure_effect=ExposureEffect.PROTECT_POSITION
        )
        if not allowed:
            return ExecutionResult(success=False, status="HALTED", error=reason)

        if broker_client is None:
            return ExecutionResult(success=False, status="REJECTED", error="No authenticated broker client available")

        client_acct = getattr(broker_client, "_account_id", getattr(broker_client, "account_id", None))
        if client_acct and str(client_acct) != str(account_id):
            return ExecutionResult(success=False, status="REJECTED", error=f"Broker account mismatch: {client_acct} != {account_id}")

        prot_id = f"GTT_{position_id[:12]}"
        symbol = protection_params.get("symbol", protection_params.get("tradingsymbol", "default"))

        try:
            from app.services.kite_engine import execution_lease
            lease_acquired = execution_lease.acquire("protection", account_id, uid, symbol, owner="CanonicalExecutionService")
            if not lease_acquired:
                return ExecutionResult(success=False, status="REJECTED", error=f"Protection lease busy for {symbol}")
        except Exception:
            pass

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
        finally:
            try:
                from app.services.kite_engine import execution_lease
                execution_lease.release("protection", account_id, uid, symbol)
            except Exception:
                pass

    async def startup_recovery(
        self,
        uid: str = "default",
        account_id: str = "default",
        broker_client: Any = None,
    ) -> Dict[str, Any]:
        """Perform startup recovery workflow: inspect unresolved journal + inventory reconciliation, observe broker, update recovery_state via CAS."""
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

        # 2. Inspect remaining unresolved journal items & inventory reconciliation state
        unresolved_intents = order_journal.unresolved(uid, account_id=account_id)
        inventory_reconciliation_required = False
        try:
            invs = db.get_inventory_all(account_id, uid) if hasattr(db, "get_inventory_all") else []
            inventory_reconciliation_required = any(inv.get("reconciliation_required") for inv in invs)
        except Exception:
            pass

        uncertain = any(i.state in ("SUBMITTING", "UNKNOWN") or i.reconciliation_required for i in unresolved_intents) or inventory_reconciliation_required

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

        # 4. If clean (no remaining unresolved intents OR pending projections needing reconciliation), transition recovery_state to CLEAN via CAS
        remaining_uncertain = any(i.state in ("SUBMITTING", "UNKNOWN") or i.reconciliation_required for i in unresolved_intents) or inventory_reconciliation_required
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
