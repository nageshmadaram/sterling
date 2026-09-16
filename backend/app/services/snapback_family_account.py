"""Exactly one broker account, named in configuration.

`all_accounts()[0]` means a disconnected family account silently hands the session to
whichever other account happens to be connected — somebody else's money, with no
error anywhere. Identity is configured; connectedness is observed; they are different
facts and are never traded off against each other.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)

USER_ENV = "STERLING_FAMILY_USER_ID"
ACCOUNT_ENV = "STERLING_FAMILY_ACCOUNT_ID"


class FamilyAccountBindingError(RuntimeError):
    """The configured family account is missing, unknown, or someone else's."""


@dataclass(frozen=True)
class FamilyAccountBinding:
    user_id: str
    account_id: str


def binding_configured() -> bool:
    return bool(os.environ.get(USER_ENV)) and bool(os.environ.get(ACCOUNT_ENV))


def configured_binding() -> FamilyAccountBinding:
    user_id = (os.environ.get(USER_ENV) or "").strip()
    account_id = (os.environ.get(ACCOUNT_ENV) or "").strip()

    if not user_id or not account_id:
        raise FamilyAccountBindingError(
            f"Family Mode requires {USER_ENV} and {ACCOUNT_ENV}; refusing to pick an account"
        )
    return FamilyAccountBinding(user_id=user_id, account_id=account_id)


def resolve_family_account():
    """The exact configured account, whatever its session state."""
    from app.services.exchanges.kite import accounts

    binding = configured_binding()
    account = accounts.get(binding.user_id, binding.account_id)

    if account is None:
        raise FamilyAccountBindingError(
            f"configured family account {binding.account_id} not found for user "
            f"{binding.user_id}"
        )

    owner = str(getattr(account, "user_id", "") or "")
    if owner and owner != binding.user_id:
        raise FamilyAccountBindingError(
            f"account {binding.account_id} belongs to {owner}, not {binding.user_id}"
        )

    return account


async def acquire_family_client():
    """A client for the bound account, or None when its broker session is expired."""
    from app.services.exchanges.kite import accounts

    account = resolve_family_account()
    if not getattr(account, "connected", False):
        return None
    return await accounts.acquire_client(account)


def _mask(account_id: str) -> str:
    if len(account_id) <= 8:
        return account_id
    return f"{account_id[:5]}…{account_id[-4:]}"


def family_account_health(uid: str = "default") -> Dict[str, Any]:
    """Identity and connectedness, reported separately."""
    if not binding_configured():
        return {
            "family_account_configured": False,
            "family_account_identity_ok": False,
            "family_account_id": None,
        }

    try:
        account = resolve_family_account()
    except FamilyAccountBindingError as exc:
        log.warning("Family account binding invalid: %s", exc)
        return {
            "family_account_configured": True,
            "family_account_identity_ok": False,
            "family_account_id": None,
            "family_account_error": str(exc),
        }

    return {
        "family_account_configured": True,
        "family_account_identity_ok": True,
        "family_account_id": _mask(str(getattr(account, "id", ""))),
    }
