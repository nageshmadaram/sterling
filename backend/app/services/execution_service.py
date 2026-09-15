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
class RiskApproval:
    approval_id: str
    uid: str
    account_id: str
    symbol: str
    side: str
    quantity: int
    generation_id: str
    approved: bool = True
    reason: str = "Risk decision approved"
    timestamp_ms: int = 0


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
    available_capital: Optional[float] = None
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
        quantity: int,
        declared_effect: ExposureEffect,
    ) -> ExposureEffect:
        """Verify caller-declared exposure effect against database inventory including quantity overshoot checks.
        
        If caller declares CLOSE_POSITION or REDUCE_EXPOSURE without holding open inventory,
        or on the same side as existing position, or for quantity > net_quantity,
        reclassify as INCREASE_EXPOSURE.
        """
        if declared_effect not in (ExposureEffect.CLOSE_POSITION, ExposureEffect.REDUCE_EXPOSURE):
            return declared_effect

        try:
            inv = db.get_inventory_strict(account_id, uid, symbol) if hasattr(db, "get_inventory_strict") else None
            net_qty = inv.get("net_quantity", 0) if inv else 0
            if net_qty == 0:
                log.warning("Declared %s for %s on zero inventory — reclassifying to INCREASE_EXPOSURE", declared_effect.value, symbol)
                return ExposureEffect.INCREASE_EXPOSURE

            is_reducing = (side.upper() == "SELL" and net_qty > 0) or (side.upper() == "BUY" and net_qty < 0)
            if not is_reducing:
                log.warning("Declared %s for %s on same side (%s, net=%d) — reclassifying to INCREASE_EXPOSURE", declared_effect.value, symbol, side, net_qty)
                return ExposureEffect.INCREASE_EXPOSURE

            if quantity > abs(net_qty):
                log.warning("Declared %s for %s quantity %d > net %d — reclassifying to INCREASE_EXPOSURE due to overshoot", declared_effect.value, symbol, quantity, net_qty)
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
        risk_approval: Optional[RiskApproval] = None,
        risk_approved: bool = False,
    ) -> ExecutionResult:
        """Submit an order through the canonical execution pipeline."""
        if risk_approval is not None:
            valid_approval = (
                risk_approval.approved is True
                and risk_approval.uid == request.uid
                and risk_approval.account_id == request.account_id
                and risk_approval.symbol.upper() == request.symbol.upper()
                and risk_approval.side.upper() == request.side.upper()
                and risk_approval.quantity == request.quantity
                and risk_approval.generation_id == request.generation_id
            )
            if not valid_approval:
                return ExecutionResult(success=False, status="REJECTED", error="Risk approval proof mismatch or invalid")
        elif not risk_approved:
            return ExecutionResult(success=False, status="REJECTED", error="Risk check rejected or missing risk approval proof")

        # Verify declared exposure effect against inventory & quantity overshoot
        effective_effect = self.verify_exposure_effect(
            uid=request.uid,
            account_id=request.account_id,
            symbol=request.symbol,
            side=request.side,
            quantity=request.quantity,
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
                available_capital=request.available_capital,
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

        # 4. Strict authenticated account identity check
        client_acct = getattr(broker_client, "_account_id", getattr(broker_client, "account_id", None))
        if not client_acct or str(client_acct).strip() != str(request.account_id).strip():
            order_journal.transition(intent.intent_key, "REJECTED", error=f"Broker account identity missing or mismatch: {client_acct} != {request.account_id}")
            return ExecutionResult(
                success=False,
                status="REJECTED",
                intent_key=intent.intent_key,
                error=f"Broker client account identity ({client_acct}) missing or does not match request account identity ({request.account_id})",
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
            is_kite_rejection = False
            try:
                from app.services.exchanges.kite.exceptions import KiteOrderError, KiteInputError, KiteMarginError
                if isinstance(exc, (KiteOrderError, KiteInputError, KiteMarginError)):
                    is_kite_rejection = True
            except ImportError:
                pass

            if isinstance(exc, BrokerRejected) or is_kite_rejection:
                order_journal.transition(intent.intent_key, "REJECTED", error=str(exc))
                return ExecutionResult(
                    success=False,
                    status="REJECTED",
                    intent_key=intent.intent_key,
                    error=f"Broker placement rejected: {exc}",
                )
            else:
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
        """Modify an existing broker order, checking exposure-increasing risk."""
        req_qty = int(changes.get("quantity") or changes.get("size") or 0)
        effect = ExposureEffect.INCREASE_EXPOSURE if req_qty > 0 else ExposureEffect.MODIFY_ORDER

        allowed, reason = self.is_trading_allowed(
            uid=uid, account_id=account_id, exposure_effect=effect
        )
        if not allowed:
            return ExecutionResult(success=False, status="HALTED", error=reason)

        if broker_client is None:
            return ExecutionResult(success=False, status="REJECTED", error="No authenticated broker client available")

        client_acct = getattr(broker_client, "_account_id", getattr(broker_client, "account_id", None))
        if not client_acct or str(client_acct).strip() != str(account_id).strip():
            return ExecutionResult(success=False, status="REJECTED", error=f"Broker account identity missing or mismatch: {client_acct} != {account_id}")

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
        if not client_acct or str(client_acct).strip() != str(account_id).strip():
            return ExecutionResult(success=False, status="REJECTED", error=f"Broker account identity missing or mismatch: {client_acct} != {account_id}")

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
        """Place GTT / stop protection order with durable protection_pending fence and cross-process lease."""
        allowed, reason = self.is_trading_allowed(
            uid=uid, account_id=account_id, exposure_effect=ExposureEffect.PROTECT_POSITION
        )
        if not allowed:
            return ExecutionResult(success=False, status="HALTED", error=reason)

        if broker_client is None:
            return ExecutionResult(success=False, status="REJECTED", error="No authenticated broker client available")

        client_acct = getattr(broker_client, "_account_id", getattr(broker_client, "account_id", None))
        if not client_acct or str(client_acct).strip() != str(account_id).strip():
            return ExecutionResult(success=False, status="REJECTED", error=f"Broker account identity missing or mismatch: {client_acct} != {account_id}")

        prot_id = f"GTT_{position_id[:12]}"
        symbol = protection_params.get("symbol", protection_params.get("tradingsymbol", "default"))

        from app.services.kite_engine import execution_lease, positions
        p = positions.get(uid, symbol)
        if p and p.protection_pending:
            return ExecutionResult(success=False, status="REJECTED", error=f"Protection placement pending/uncertain for {symbol}; reconcile before retry")

        with execution_lease.guard(execution_lease.PROTECTION, account_id=account_id, uid=uid, symbol=symbol) as token:
            if token is None:
                return ExecutionResult(success=False, status="REJECTED", error=f"Protection lease busy for {symbol}")

            if p:
                p.protection_pending = True
                positions.persist_strict(uid)

            try:
                place_gtt_fn = getattr(broker_client, "place_gtt", None)
                if callable(place_gtt_fn):
                    if inspect.iscoroutinefunction(place_gtt_fn):
                        res = await place_gtt_fn(**protection_params)
                    else:
                        res = place_gtt_fn(**protection_params)
                    if isinstance(res, dict):
                        prot_id = str(res.get("trigger_id", prot_id))

                    if p:
                        try:
                            p.gtt_id = int(prot_id) if str(prot_id).isdigit() else 0
                        except ValueError:
                            pass
                        p.protection_pending = False
                        positions.persist_strict(uid)

                    return ExecutionResult(success=True, status="ACKNOWLEDGED", order_id=prot_id)
                else:
                    return ExecutionResult(success=False, status="REJECTED", error="Broker client does not support place_gtt")
            except Exception as exc:
                if p:
                    p.protection_pending = True
                    positions.persist_strict(uid)
                return ExecutionResult(success=False, status="UNKNOWN", error=f"Protection outcome uncertain: {exc}")

    async def startup_recovery(
        self,
        uid: str = "default",
        account_id: str = "default",
        broker_client: Any = None,
    ) -> Dict[str, Any]:
        """Perform startup recovery workflow: inspect unresolved journal + pending projections + inventory reconciliation, observe broker, update recovery_state via CAS."""
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

        # 2. Inspect remaining unresolved journal items, pending projections & strict inventory reconciliation state
        unresolved_intents = order_journal.unresolved(uid, account_id=account_id)
        pending_projections = order_journal.pending_projection(uid, account_id) if hasattr(order_journal, "pending_projection") else []
        inventory_reconciliation_required = False
        try:
            invs = db.get_inventory_all_strict(account_id, uid)
            inventory_reconciliation_required = any(inv.get("reconciliation_required") for inv in invs)
        except Exception as inv_err:
            log.warning("Strict inventory read failed in startup_recovery: %s", inv_err)
            inventory_reconciliation_required = True

        uncertain = len(unresolved_intents) > 0 or len(pending_projections) > 0 or inventory_reconciliation_required

        if uncertain:
            try:
                ctrl = db.set_recovery_state(
                    recovery_state="RECOVERY_REQUIRED",
                    reason_code="UNRESOLVED_JOURNAL_INTENTS",
                    reason=f"Startup found unresolved state: {len(unresolved_intents)} journal intent(s), {len(pending_projections)} pending projection(s)",
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
            pending_projections = order_journal.pending_projection(uid, account_id) if hasattr(order_journal, "pending_projection") else []

        # 4. If clean (no remaining unresolved intents, pending projections, or inventory errors), transition recovery_state to CLEAN via CAS
        remaining_uncertain = len(unresolved_intents) > 0 or len(pending_projections) > 0 or inventory_reconciliation_required
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
            "pending_projections_count": len(pending_projections),
        }

    async def recover_unresolved(self, uid: str, account_id: str, client: Any = None) -> Dict[str, Any]:
        """Recover and project unresolved orders for account."""
        return await self.startup_recovery(uid=uid, account_id=account_id, broker_client=client)


# Global singleton instance
execution_service = CanonicalExecutionService()
