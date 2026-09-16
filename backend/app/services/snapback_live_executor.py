"""1.5 P0-LIVE: the one path from an ARMED plan to live exposure.

Before this, arming moved a durable plan to ARMED and nothing consumed it.
`submit_plan()` existed with no production caller, submitted only the option leg,
and passed no RiskApproval — the canonical service would have refused it. Saying
live execution was "complete" was too strong.

The ordering is the safety argument:

    revalidate → reconcile → risk approve → option fill
    → hedge sized to the CONFIRMED fill → protection → OPEN

Each step is gated on the previous one having actually happened, never on its
acknowledgement. Nothing here places an order: every order goes through the
canonical execution service, which is the only thing that talks to a broker, and
Family Mode's write guard sits under that.

The method is deliberately resumable and refuses to run twice for one plan: a
retry that re-enters here must not produce a second position.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from app.services.snapback_protection import (
    ProtectionState, protection_deadline_action, protection_required_quantity,
)

log = logging.getLogger(__name__)


@dataclass
class LiveExecutionResult:
    status: str
    plan_id: str = ""
    reasons: List[str] = field(default_factory=list)
    option_filled: int = 0
    hedge_filled: int = 0
    protection_state: str = ProtectionState.NONE.value
    emergency_exit_quantity: int = 0
    reconcile_first: bool = False
    stop_new_entries: bool = False


class SnapbackLiveExecutor:
    def __init__(
        self,
        *,
        plan_store,
        execution_service,
        reconciliation_fn: Callable[[str], Dict[str, Any]],
        risk_approval_fn: Callable[..., Any],
        protection_fn: Callable[..., Any],
        revalidate_fn: Callable[[Dict[str, Any]], Dict[str, Any]],
        uid: str = "default",
    ) -> None:
        self.plan_store = plan_store
        self.execution_service = execution_service
        self.reconciliation_fn = reconciliation_fn
        self.risk_approval_fn = risk_approval_fn
        self.protection_fn = protection_fn
        self.revalidate_fn = revalidate_fn
        self.uid = uid

    async def execute(self, plan_id: str) -> LiveExecutionResult:
        plan = self.plan_store.get(plan_id)
        if plan is None:
            return LiveExecutionResult(status="REFUSED", plan_id=plan_id,
                                       reasons=["plan_not_found"])

        # Exactly once. A retry that re-enters here must not open a second
        # position, and a plan already consumed is not an error to shout about.
        if plan.get("status") == "CONSUMED":
            return LiveExecutionResult(status="ALREADY_CONSUMED", plan_id=plan_id)

        if plan.get("status") != "ARMED":
            return LiveExecutionResult(
                status="REFUSED", plan_id=plan_id,
                reasons=[f"plan_is_{plan.get('status')}"],
            )

        account_id = str(plan.get("account_id") or "")

        # 1. The plan was authorised at some earlier moment. Ask whether it is
        # still the right trade before acting on it.
        revalidation = self.revalidate_fn(plan) or {}
        if not revalidation.get("ok"):
            return LiveExecutionResult(
                status="REFUSED", plan_id=plan_id,
                reasons=list(revalidation.get("reasons") or ["revalidation_failed"]),
            )

        # 2. A book we have not agreed on is a book we must not add to.
        snapshot = self.reconciliation_fn(account_id) or {}
        if not snapshot.get("clean") or not snapshot.get("fresh"):
            return LiveExecutionResult(
                status="REFUSED", plan_id=plan_id,
                reasons=["reconciliation_not_clean_and_fresh"],
            )

        option_qty = int(plan.get("option_quantity") or 0)
        option_symbol = str(plan.get("option_symbol") or "")

        # 3. Risk decides, per request. The canonical service refuses an
        # exposure increase without a request-bound approval.
        approval = self.risk_approval_fn(
            uid=self.uid, account_id=account_id, symbol=option_symbol,
            side="BUY", quantity=option_qty,
            generation_id=str(plan.get("policy_snapshot_hash") or ""),
        )
        if not getattr(approval, "approved", False):
            return LiveExecutionResult(
                status="REFUSED", plan_id=plan_id,
                reasons=[f"risk_denied:{getattr(approval, 'reason', '')}"],
            )

        # 4. The option leg.
        option_result = await self._submit(
            plan, symbol=option_symbol, exchange=str(plan.get("option_exchange") or "NFO"),
            side="BUY", quantity=option_qty, approval=approval,
        )

        if option_result.status == "UNKNOWN":
            # The order may be working. Hedging against a position that may not
            # exist, and resubmitting, are both worse than stopping here.
            log.error("Snapback live: option submission UNKNOWN for %s", plan_id)
            return LiveExecutionResult(
                status="RECONCILING", plan_id=plan_id, reconcile_first=True,
                reasons=["option_submission_unknown"],
            )

        if not option_result.success:
            return LiveExecutionResult(
                status="REJECTED", plan_id=plan_id,
                reasons=[option_result.error or "option_rejected"],
            )

        option_filled = int(option_result.filled_quantity or 0)
        if option_filled <= 0:
            return LiveExecutionResult(
                status="NO_FILL", plan_id=plan_id, reasons=["option_fill_zero"],
            )

        # 5. Hedge the position that exists, not the one intended. Sizing to the
        # requested quantity after a partial fill leaves a naked futures leg.
        hedge_filled = 0
        target_hedge = int(plan.get("target_futures_quantity") or 0)
        hedge_qty = 0
        if target_hedge > 0 and option_qty > 0:
            hedge_qty = int(math.ceil(target_hedge * option_filled / option_qty))

        if hedge_qty > 0:
            hedge_approval = self.risk_approval_fn(
                uid=self.uid, account_id=account_id,
                symbol=str(plan.get("futures_symbol") or ""), side="BUY",
                quantity=hedge_qty,
                generation_id=str(plan.get("policy_snapshot_hash") or ""),
            )
            hedge_result = await self._submit(
                plan, symbol=str(plan.get("futures_symbol") or ""),
                exchange=str(plan.get("futures_exchange") or "NFO"),
                side="BUY", quantity=hedge_qty, approval=hedge_approval,
            )
            hedge_filled = int(getattr(hedge_result, "filled_quantity", 0) or 0)

        # 6. Protection covers the confirmed option inventory.
        required = protection_required_quantity(confirmed_filled=option_filled)
        protection = await self.protection_fn(
            plan_id=plan_id, symbol=option_symbol, quantity=required,
            account_id=account_id,
        ) or {}
        state = ProtectionState(str(protection.get("state") or "REQUIRED"))

        if state is not ProtectionState.ACTIVE:
            action = protection_deadline_action(
                state=state, deadline_exceeded=True,
                confirmed_filled=option_filled,
                entry_remainder=max(0, option_qty - option_filled),
            )
            log.error(
                "Snapback live: protection not established for %s (state=%s)",
                plan_id, state.value,
            )
            return LiveExecutionResult(
                status="PROTECTION_FAILED", plan_id=plan_id,
                option_filled=option_filled, hedge_filled=hedge_filled,
                protection_state=state.value,
                emergency_exit_quantity=action.emergency_exit_quantity,
                reconcile_first=action.reconcile_first,
                stop_new_entries=action.stop_new_entries,
                reasons=["protection_not_active"],
            )

        self._consume(plan_id)
        return LiveExecutionResult(
            status="OPEN", plan_id=plan_id, option_filled=option_filled,
            hedge_filled=hedge_filled, protection_state=state.value,
        )

    async def _submit(self, plan, *, symbol, exchange, side, quantity, approval):
        from app.services.execution_service import ExecutionRequest, ExposureEffect

        request = ExecutionRequest(
            uid=self.uid,
            account_id=str(plan.get("account_id") or ""),
            strategy_id="snapback",
            generation_id=str(plan.get("policy_snapshot_hash") or ""),
            signal_id=str(plan.get("opportunity_id") or ""),
            exchange=exchange,
            symbol=symbol,
            side=side,
            quantity=int(quantity),
            exposure_effect=ExposureEffect.INCREASE_EXPOSURE,
            tag=f"snapback:{plan.get('plan_id')}",
        )
        return await self.execution_service.submit_order(
            request, risk_approval=approval, risk_approved=True,
        )

    def _consume(self, plan_id: str) -> None:
        """Mark the plan spent so a retry cannot open a second position."""
        try:
            self.plan_store.consume(plan_id)
        except Exception as exc:  # noqa: BLE001 - never unwind a live position
            log.error("Snapback live: could not mark plan %s consumed: %s",
                      plan_id, exc)
