"""Which authorised account Sterling is trading through, as durable metadata.

Zerodha's guidance is that transactions must not continue in a deceased
person's account: a nominee or legal heir claims the holdings and trades from
their own account. That is an operational constraint on this system, and the
wrong way to meet it is to hand somebody else the original credentials. The
right way is to make the account a bound, replaceable, recorded identity that
strategy mathematics does not depend on.

Two rules are load-bearing here:

* The strategy identity does not change when the account changes. A frozen lane
  that has collected 140 sessions keeps its identity and its sample across a
  migration, because nothing about the rules moved.
* The execution evidence *does* change segment. A fill in account A is not a
  fill in account B, and reassigning one to the other would be inventing a
  broker fact. :func:`segment_id` names the boundary.

No secret ever enters this record. The field guard below refuses at
construction rather than at review time, because a password that reaches a
manifest has already been written to disk and to every backup made since.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Final, Mapping

__all__ = [
    "BindingStatus",
    "BrokerAccountBinding",
    "BindingError",
    "SecretInBindingError",
    "BINDING_MANIFEST_DIR",
    "new_binding_id",
    "segment_id",
]

#: Metadata only. Never secrets; the loader asserts it.
BINDING_MANIFEST_DIR: Final[str] = "data/manifests/account_bindings"

#: Anything whose name looks like a credential. The check is on names because a
#: value check cannot tell a PIN from a client id, and a false refusal here is
#: cheap while a false pass is permanent.
_SECRET_NAME = re.compile(
    r"(pass(word|code)?|pin|otp|totp|secret|token|seed|mpin|api_key|private)",
    re.IGNORECASE,
)


class BindingError(ValueError):
    """The binding is missing a required fact, or contradicts itself."""


class SecretInBindingError(BindingError):
    """Somebody tried to store a credential in the account metadata."""


class BindingStatus(StrEnum):
    """Where a binding is in its life."""

    #: Created, not yet proven against the broker.
    DECLARED = "DECLARED"
    #: Read-only/doctor/reconciliation checks passed; may run SHADOW.
    VERIFIED = "VERIFIED"
    #: The account Sterling currently acts through.
    ACTIVE = "ACTIVE"
    #: Retired. Its fills stay attributed to it forever.
    DEACTIVATED = "DEACTIVATED"


#: Statuses in which Sterling may talk to the broker at all.
_USABLE: Final[frozenset[BindingStatus]] = frozenset(
    {BindingStatus.VERIFIED, BindingStatus.ACTIVE}
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_binding_id(broker: str, client_id: str, created_at: str | None = None) -> str:
    """A stable, readable id. Contains no secret — client id is not one."""
    stamp = (created_at or _now())[:10].replace("-", "")
    return f"{broker.strip().lower()}-{client_id.strip().lower()}-{stamp}"


@dataclass(frozen=True)
class BrokerAccountBinding:
    """One authorised broker/demat account Sterling is permitted to act through."""

    binding_id: str
    broker: str

    #: The legal owner of the account. Recorded because the continuity question
    #: is a legal one, not a technical one.
    legal_account_holder: str

    #: Broker client code (e.g. a Kite user id). Identifying, not secret.
    client_id: str

    #: What this account may be used for, e.g. ``read_only``, ``shadow``,
    #: ``live_minimum``. Narrower than the broker's own permissions on purpose.
    account_scope: str

    #: Which Sterling deployment holds the binding. Two deployments pointing at
    #: one account is a reconciliation hazard, and this makes it visible.
    deployment_id: str

    #: Name of the registered static-egress profile (not the address itself:
    #: an IP in the repo is a deployment secret hiding in plain text).
    static_egress_profile: str = ""

    created_at: str = field(default_factory=_now)
    activated_at: str | None = None
    deactivated_at: str | None = None
    status: BindingStatus = BindingStatus.DECLARED

    notes: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "status", BindingStatus(self.status))
        for name in (
            "binding_id",
            "broker",
            "legal_account_holder",
            "client_id",
            "account_scope",
            "deployment_id",
        ):
            if not str(getattr(self, name) or "").strip():
                raise BindingError(f"{name} is required on a broker account binding")
        if self.status is BindingStatus.ACTIVE and not self.activated_at:
            raise BindingError("an ACTIVE binding must record activated_at")
        if self.status is BindingStatus.DEACTIVATED and not self.deactivated_at:
            raise BindingError("a DEACTIVATED binding must record deactivated_at")
        if self.deactivated_at and self.status is BindingStatus.ACTIVE:
            raise BindingError("a binding cannot be ACTIVE and deactivated at once")

    @property
    def usable(self) -> bool:
        return self.status in _USABLE and not self.deactivated_at

    @property
    def live_ready(self) -> bool:
        """May real orders go out through this binding?

        Being ACTIVE is not enough. The scope has to say so, and the static
        egress profile has to be named, because Zerodha requires a registered
        static IP for API order placement.
        """
        return (
            self.status is BindingStatus.ACTIVE
            and "live" in self.account_scope.lower()
            and bool(self.static_egress_profile.strip())
        )

    @property
    def segment(self) -> str:
        return segment_id(self)

    def as_row(self) -> dict[str, Any]:
        row = asdict(self)
        row["status"] = self.status.value
        row["segment"] = self.segment
        row["usable"] = self.usable
        row["live_ready"] = self.live_ready
        return row

    def activated(self, when: str | None = None) -> "BrokerAccountBinding":
        if self.deactivated_at:
            raise BindingError(
                f"binding {self.binding_id} was deactivated at {self.deactivated_at} "
                "and cannot be reactivated; create a new binding"
            )
        stamp = when or _now()
        return _replace(self, status=BindingStatus.ACTIVE, activated_at=self.activated_at or stamp)

    def verified(self) -> "BrokerAccountBinding":
        if self.status is BindingStatus.DEACTIVATED:
            raise BindingError("a deactivated binding cannot be verified")
        return _replace(self, status=BindingStatus.VERIFIED)

    def deactivated(self, when: str | None = None) -> "BrokerAccountBinding":
        stamp = when or _now()
        return _replace(
            self, status=BindingStatus.DEACTIVATED, deactivated_at=self.deactivated_at or stamp
        )


def _replace(binding: BrokerAccountBinding, **changes: Any) -> BrokerAccountBinding:
    payload = asdict(binding)
    payload["status"] = binding.status
    payload.update(changes)
    return BrokerAccountBinding(**payload)


def segment_id(binding: BrokerAccountBinding | Mapping[str, Any] | str) -> str:
    """The execution-evidence segment a binding owns.

    Every broker fact is stamped with this. It is what stops a reconciliation
    from reading account A's fills as account B's after a migration.
    """
    if isinstance(binding, str):
        return f"acct:{binding}"
    if isinstance(binding, BrokerAccountBinding):
        return f"acct:{binding.binding_id}"
    key = binding.get("binding_id")
    if not key:
        raise BindingError("cannot derive an evidence segment without a binding_id")
    return f"acct:{key}"


def assert_no_secrets(payload: Mapping[str, Any]) -> None:
    """Refuse any field that looks like a credential, at write time."""
    offenders = sorted(k for k in payload if _SECRET_NAME.search(str(k)))
    if offenders:
        raise SecretInBindingError(
            "account binding metadata must never carry credentials; refusing "
            f"field(s): {', '.join(offenders)}"
        )


def binding_from_row(row: Mapping[str, Any]) -> BrokerAccountBinding:
    """Rebuild a binding from stored metadata, refusing smuggled secrets."""
    assert_no_secrets(row)
    known = {f for f in BrokerAccountBinding.__dataclass_fields__}
    payload = {k: v for k, v in row.items() if k in known}
    return BrokerAccountBinding(**payload)
