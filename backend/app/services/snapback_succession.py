"""Succession: handing Sterling to the next operator.

Sterling is built to outlive its author, so it must be able to change hands
cleanly. It must NOT be built to keep trading an account after its holder has
died: transactions in a deceased person's account are not lawful. A nominee or
legal heir claims the assets, has them transmitted to their OWN account, and
Sterling is then rebound to that account with a fresh broker authorization.

The division that matters:

    the strategy's evidence history belongs to Sterling
    broker inventory belongs to the legal account holder

Mixing them either destroys the research record or attributes somebody else's
positions to it. So a rebind preserves evidence read-only and resets every piece
of account-specific live state.

Nothing here touches a broker or moves an asset. It produces the plan an operator
follows, and refuses the states in which a handover would be unsafe.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List

log = logging.getLogger(__name__)


class SuccessionRefused(RuntimeError):
    """The system is not in a state where a handover can be done safely."""


OPERATOR_INSTRUCTIONS = """\
Sterling does not continue trading the previous holder's account.

1. Stop the service. Do not place further orders on the previous account.
2. The nominee or legal heir claims the assets through the broker's own
   transmission process, into their own account. Sterling plays no part in this
   and must not be used to trade the account meanwhile.
3. The new operator opens or uses their own broker account and generates their
   own API credentials. Never reuse the previous holder's password, API key or
   access token — they identify a person, not a role.
4. Set STERLING_FAMILY_USER_ID and STERLING_FAMILY_ACCOUNT_ID to the new
   account, and put the new credentials in the environment file.
5. Start the service. Preflight will refuse to run until the new account
   reconciles clean and flat, with no unknown orders or positions.

The research and evidence history is preserved read-only. It describes the
strategy, not the account, and stays valid across the handover.
"""


@dataclass
class SuccessionPlan:
    new_user_id: str
    new_account_id: str
    previous_account_id: str = ""
    requires_fresh_broker_authorization: bool = True
    reuses_previous_credentials: bool = False
    evidence_preserved_read_only: bool = True
    account_state_reset: bool = True
    confirmed_by: str = ""
    planned_at: str = ""
    steps: List[str] = field(default_factory=list)
    operator_instructions: str = OPERATOR_INSTRUCTIONS


def rebind_family_account(
    *,
    new_user_id: str,
    new_account_id: str,
    open_positions: int,
    unresolved_orders: int,
    confirmed_by: str,
) -> SuccessionPlan:
    """Plan a handover to a new operator and account.

    Refuses while the previous account still holds exposure or has orders whose
    outcome is unknown: rebinding then would leave real positions owned by an
    account Sterling has stopped watching.
    """
    if not str(confirmed_by).strip():
        raise SuccessionRefused(
            "a succession must be confirmed by a named person; "
            "this is not something to do accidentally"
        )

    if not str(new_user_id).strip() or not str(new_account_id).strip():
        raise SuccessionRefused("the new user and account must both be named")

    previous = os.environ.get("STERLING_FAMILY_ACCOUNT_ID", "") or ""
    if previous and str(new_account_id) == previous:
        raise SuccessionRefused(
            f"the new account {new_account_id} is the previous one; a succession "
            f"moves Sterling to a different legal account holder"
        )

    if int(open_positions) > 0:
        raise SuccessionRefused(
            f"{open_positions} position(s) still hold exposure on the previous "
            f"account; flatten them before handing Sterling over"
        )

    if int(unresolved_orders) > 0:
        raise SuccessionRefused(
            f"{unresolved_orders} order(s) have unknown outcomes on the previous "
            f"account; reconcile them before handing Sterling over"
        )

    plan = SuccessionPlan(
        new_user_id=str(new_user_id),
        new_account_id=str(new_account_id),
        previous_account_id=previous,
        confirmed_by=str(confirmed_by),
        planned_at=datetime.now(timezone.utc).isoformat(),
        steps=[
            "halt new entries and confirm zero exposure",
            "confirm no unresolved orders",
            "legal transmission of assets to the new holder's own account",
            "new broker credentials issued to the new holder",
            "rebind STERLING_FAMILY_USER_ID and STERLING_FAMILY_ACCOUNT_ID",
            "preflight and reconcile the new account as flat",
            "resume observation only after reconciliation is clean",
        ],
    )

    log.warning(
        "Snapback succession planned: %s -> %s, confirmed by %s",
        previous or "(unbound)", new_account_id, confirmed_by,
    )
    return plan
