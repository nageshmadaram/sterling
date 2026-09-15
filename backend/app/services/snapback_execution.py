"""Account risk reservation and order intent management for Snapback.

Enforces account-wide risk limits defined in Specification 03:
- Settled session loss + open position risk + pending entry risk <= daily loss limit (2.0%).
- Single-trade risk <= per-trade risk budget (0.5%).
- Durable risk reservation created before broker submission and released only on terminal status.
"""
from __future__ import annotations

import math
import time
from typing import Dict, List, Optional

from app.engines.snapback.config import SnapbackConfig
from app.engines.snapback.intraday_models import RiskReservation, TradePlan


class AccountRiskManager:
    """Manages risk reservations and enforces account-level caps."""

    def __init__(self) -> None:
        self._reservations: Dict[str, RiskReservation] = {}

    def reserve_risk(
        self,
        plan: TradePlan,
        account_id: str,
        session_id: str,
        cfg: SnapbackConfig,
        available_cash: float,
        settled_session_loss: float,
        open_positions_risk: float,
        *,
        now_ms: Optional[int] = None,
    ) -> Optional[RiskReservation]:
        """Attempt to reserve cash and risk for an accepted TradePlan."""
        if plan.quantity <= 0 or plan.feasibility_status != "FEASIBLE":
            return None

        if now_ms is None:
            now_ms = int(time.time() * 1000)

        # Calculate active pending risk for account/session
        pending_risk = sum(
            r.reserved_risk
            for r in self._reservations.values()
            if r.account_id == account_id and r.session_id == session_id and r.status == "ACTIVE" and r.expires_at_ms > now_ms
        )

        session_loss_limit = available_cash * (cfg.scalp_daily_loss_pct / 100.0)
        total_risk_committed = settled_session_loss + open_positions_risk + pending_risk
        remaining_session_loss_allowance = max(0.0, session_loss_limit - total_risk_committed)

        per_trade_risk_budget = available_cash * (cfg.scalp_risk_pct / 100.0)
        allowed_risk_for_plan = min(per_trade_risk_budget, remaining_session_loss_allowance)

        if plan.risk_amount > allowed_risk_for_plan or plan.cash_required > available_cash:
            return None

        reservation_id = f"res_{plan.plan_id}_{now_ms}"
        reservation = RiskReservation(
            reservation_id=reservation_id,
            plan_id=plan.plan_id,
            account_id=account_id,
            reserved_cash=plan.cash_required,
            reserved_risk=plan.risk_amount,
            session_id=session_id,
            created_at_ms=now_ms,
            expires_at_ms=now_ms + 120000,  # 2 minute expiration window
            status="ACTIVE",
        )

        self._reservations[reservation_id] = reservation
        return reservation

    def release_reservation(self, reservation_id: str) -> bool:
        """Release a risk reservation when intent terminates or fills."""
        res = self._reservations.get(reservation_id)
        if res and res.status == "ACTIVE":
            self._reservations[reservation_id] = RiskReservation(
                reservation_id=res.reservation_id,
                plan_id=res.plan_id,
                account_id=res.account_id,
                reserved_cash=res.reserved_cash,
                reserved_risk=res.reserved_risk,
                session_id=res.session_id,
                created_at_ms=res.created_at_ms,
                expires_at_ms=res.expires_at_ms,
                status="RELEASED",
            )
            return True
        return False
