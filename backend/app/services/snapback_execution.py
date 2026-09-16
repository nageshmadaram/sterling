"""Snapback live execution adapter.

A thin layer over the canonical authorities. It deliberately owns no risk store of its
own: the previous in-memory reservation dictionary described itself as durable
while living in one process, so a restart forgot every reservation it had made.

Reservations, approvals, journalling and broker submission all belong to
CanonicalExecutionService and the canonical RiskEngine. The plan below is server-owned:
the browser receives a plan_id and a revision, never a quantity, price, contract or
account it can influence.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)


class PlanRevisionError(RuntimeError):
    """The plan changed since the operator last saw it."""


class PlanNotFoundError(RuntimeError):
    """No such server-owned plan."""


@dataclass(frozen=True)
class SnapbackExecutionPlan:
    plan_id: str
    opportunity_id: str
    account_id: str

    option_exchange: str
    option_symbol: str
    option_quantity: int

    futures_exchange: str
    futures_symbol: str
    target_futures_quantity: int

    max_option_price: float
    hedge_price_limit: float

    risk_amount: float
    cash_required: float
    margin_required: float

    policy_snapshot_hash: str
    config_hash: str

    revision: int = 1

    def as_client_view(self) -> Dict[str, Any]:
        """What a browser may see: identity and revision, never authority."""
        return {
            "plan_id": self.plan_id,
            "opportunity_id": self.opportunity_id,
            "revision": self.revision,
        }


def assert_plan_revision(*, current_revision: int, expected_revision: int) -> None:
    """Refuse to act on a plan the operator has not actually seen."""
    if int(current_revision) != int(expected_revision):
        raise PlanRevisionError(
            f"plan revision {expected_revision} is stale; current revision is "
            f"{current_revision}"
        )


# submit_plan() lived here. It was never called in production, submitted only the
# option leg, and passed no RiskApproval, so the canonical service would have
# refused it at the broker boundary anyway. An unsafe, plausible-looking entry
# point sitting beside a safe one invites the wrong call site.
#
# The single live path is app/services/snapback_live_executor.py: it revalidates,
# reconciles, obtains a real risk approval, hedges the CONFIRMED fill and
# establishes protection before a position counts as open.
