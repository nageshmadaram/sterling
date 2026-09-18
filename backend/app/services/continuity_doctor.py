"""Can somebody other than the developer keep this system alive?

Two operational dependencies decide that, and neither lives in the strategy
code. The first is the registered static egress address: Zerodha requires API
order placement to come from a static IP registered against the developer app,
so a host whose outbound address drifted can generate signals all day and be
refused at the one moment it matters. The second is the continuity checklist —
who owns the developer console, where the encrypted secrets are, who the
authorised future operator is — which is not code at all, and is therefore the
first thing lost when the person who set it up is unavailable.

Both are checked here, and both fail closed. An egress verification whose age
cannot be determined is not current, and an unacknowledged checklist item is
not satisfied. The point of this module is that neither can be true and
invisible at the same time.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Final, Mapping

__all__ = [
    "EgressStatus",
    "ContinuityItem",
    "ContinuityReport",
    "CONTINUITY_CHECKLIST",
    "egress_status",
    "continuity_report",
    "order_placement_ready",
    "render_continuity",
]

#: The address registered with the broker for this deployment.
EXPECTED_EGRESS_ENV: Final[str] = "STERLING_STATIC_EGRESS_IP"
#: What the host's outbound address was last observed to be, and when. Written
#: by the deployment, never guessed by the application.
OBSERVED_EGRESS_ENV: Final[str] = "STERLING_OBSERVED_EGRESS_IP"
OBSERVED_AT_ENV: Final[str] = "STERLING_EGRESS_VERIFIED_AT"

#: A verification older than this is stale. An IP can change on a reboot or a
#: lease renewal, so "we checked once in June" is not a current fact.
MAX_EGRESS_AGE = timedelta(days=7)

CONTINUITY_FILE: Final[str] = "data/manifests/continuity.json"


def _root() -> Path:
    configured = os.environ.get("STERLING_ROOT")
    return Path(configured) if configured else Path(__file__).resolve().parents[3]


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class EgressStatus:
    """Whether this host may be trusted to place API orders."""

    expected: str | None
    observed: str | None
    observed_at: str | None
    verified: bool
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "expected": self.expected,
            "observed": self.observed,
            "observed_at": self.observed_at,
            "verified": self.verified,
            "reason": self.reason,
        }


def egress_status(
    *,
    env: Mapping[str, str] | None = None,
    now: Callable[[], datetime] = _now,
) -> EgressStatus:
    """Compare the registered address with the last observed one.

    Never performs a network call: an application that asks the internet what
    its own address is has just made a safety check depend on a third party
    being up. The deployment records the observation; this reads it.
    """
    source = env if env is not None else os.environ
    expected = (source.get(EXPECTED_EGRESS_ENV) or "").strip() or None
    observed = (source.get(OBSERVED_EGRESS_ENV) or "").strip() or None
    observed_at = (source.get(OBSERVED_AT_ENV) or "").strip() or None

    if expected is None:
        return EgressStatus(
            expected, observed, observed_at, False,
            f"{EXPECTED_EGRESS_ENV} is unset: no static address is registered for this deployment",
        )
    if observed is None:
        return EgressStatus(
            expected, observed, observed_at, False,
            f"{OBSERVED_EGRESS_ENV} is unset: the host's outbound address was never verified",
        )
    if observed != expected:
        return EgressStatus(
            expected, observed, observed_at, False,
            "the host's outbound address does not match the registered static IP; "
            "broker order placement will be refused",
        )
    if not observed_at:
        return EgressStatus(
            expected, observed, observed_at, False,
            f"{OBSERVED_AT_ENV} is unset: the verification has no date, so it cannot be current",
        )
    try:
        stamp = datetime.fromisoformat(observed_at)
    except ValueError:
        return EgressStatus(
            expected, observed, observed_at, False,
            f"{OBSERVED_AT_ENV}={observed_at!r} is not an ISO timestamp",
        )
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    age = now() - stamp
    if age > MAX_EGRESS_AGE:
        return EgressStatus(
            expected, observed, observed_at, False,
            f"the egress verification is {age.days} days old (limit {MAX_EGRESS_AGE.days})",
        )
    return EgressStatus(expected, observed, observed_at, True, f"verified {age.days}d ago")


@dataclass(frozen=True)
class ContinuityItem:
    """One thing that must be true outside the code for a handoff to work."""

    key: str
    question: str
    #: Who must answer it; nothing here can be answered by the software.
    owner: str = "operator"
    acknowledged: bool = False
    answer: str = ""
    updated_at: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "question": self.question,
            "owner": self.owner,
            "acknowledged": self.acknowledged,
            "answer": self.answer,
            "updated_at": self.updated_at,
        }


#: §11.3. Deliberately questions, not statements: an item reads as unanswered
#: until somebody writes the answer down.
CONTINUITY_CHECKLIST: Final[tuple[ContinuityItem, ...]] = (
    ContinuityItem("nominee", "Are the broker/depository nominee details current, and the transmission requirements known?"),
    ContinuityItem("authorised_operator", "Who is the authorised future operator, and which account will they use?"),
    ContinuityItem("developer_console", "Who owns the broker developer console, and how is access recovered?"),
    ContinuityItem("static_ip", "Who owns the static IP / VPS / ISP arrangement, and when does it renew?"),
    ContinuityItem("secrets", "Where are the encrypted secrets stored, and how does the authorised operator recover them?"),
    ContinuityItem("code_and_infra", "Who holds GitHub, domain, server and alert-channel access?"),
    ContinuityItem("no_secrets_in_repo", "Is it confirmed that no password, TOTP seed or PIN is in the repository or backup bundle?"),
)


@dataclass(frozen=True)
class ContinuityReport:
    egress: EgressStatus
    items: tuple[ContinuityItem, ...] = field(default_factory=tuple)
    binding: Mapping[str, Any] = field(default_factory=dict)

    @property
    def outstanding(self) -> tuple[ContinuityItem, ...]:
        return tuple(i for i in self.items if not i.acknowledged)

    @property
    def handoff_ready(self) -> bool:
        return not self.outstanding and bool(self.binding.get("account_binding_ready"))

    def as_dict(self) -> dict[str, Any]:
        return {
            "egress": self.egress.as_dict(),
            "items": [i.as_dict() for i in self.items],
            "outstanding": [i.key for i in self.outstanding],
            "handoff_ready": self.handoff_ready,
            "account_binding": dict(self.binding),
        }


def _checklist_path(path: Path | str | None = None) -> Path:
    return Path(path) if path else _root() / CONTINUITY_FILE


def load_checklist(path: Path | str | None = None) -> tuple[ContinuityItem, ...]:
    """Merge recorded answers onto the declared checklist.

    The declared list wins on membership: an answer file that omits an item
    leaves it unanswered rather than removing the question.
    """
    file = _checklist_path(path)
    recorded: dict[str, Mapping[str, Any]] = {}
    if file.exists():
        try:
            payload = json.loads(file.read_text(encoding="utf-8"))
            for row in payload.get("items", []):
                key = str(row.get("key") or "")
                if key:
                    recorded[key] = row
        except (OSError, json.JSONDecodeError):
            # An unreadable answer file means nothing is answered. It must not
            # mean the previous in-memory answers survive.
            recorded = {}

    out: list[ContinuityItem] = []
    for item in CONTINUITY_CHECKLIST:
        row = recorded.get(item.key)
        if not row:
            out.append(item)
            continue
        answer = str(row.get("answer") or "").strip()
        out.append(
            ContinuityItem(
                key=item.key,
                question=item.question,
                owner=str(row.get("owner") or item.owner),
                # An item is acknowledged only when it carries an actual answer.
                acknowledged=bool(row.get("acknowledged")) and bool(answer),
                answer=answer,
                updated_at=str(row.get("updated_at") or ""),
            )
        )
    return tuple(out)


def record_answer(
    key: str,
    answer: str,
    *,
    owner: str = "operator",
    path: Path | str | None = None,
) -> ContinuityItem:
    """Write one checklist answer. Refuses anything that looks like a secret."""
    from app.core.broker_account_binding import assert_no_secrets

    declared = {i.key: i for i in CONTINUITY_CHECKLIST}
    if key not in declared:
        raise KeyError(f"unknown continuity item {key!r}")
    if not answer.strip():
        raise ValueError("a continuity answer cannot be blank; that is the unanswered state")
    assert_no_secrets({key: answer})

    file = _checklist_path(path)
    items = {i.key: i.as_dict() for i in load_checklist(path)}
    items[key] = ContinuityItem(
        key=key,
        question=declared[key].question,
        owner=owner,
        acknowledged=True,
        answer=answer.strip(),
        updated_at=_now().isoformat(),
    ).as_dict()

    file.parent.mkdir(parents=True, exist_ok=True)
    file.write_text(
        json.dumps({"items": list(items.values())}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return ContinuityItem(**items[key])


def continuity_report(path: Path | str | None = None) -> ContinuityReport:
    from app.services.account_binding_service import binding_health

    return ContinuityReport(
        egress=egress_status(),
        items=load_checklist(path),
        binding=binding_health(),
    )


def order_placement_ready(
    *,
    binding_live_ready: bool | None = None,
    broker_session_valid: bool | None = None,
    api_order_permission_verified: bool | None = None,
    safety_normal: bool | None = None,
    lane_capital_state: str = "",
) -> tuple[bool, list[str]]:
    """§12's ORDER_PLACEMENT_READY, with every ``None`` refusing.

    Returned as ``(ready, blockers)`` rather than a bare boolean so the caller
    can tell an operator which of the six conditions is missing.
    """
    from app.core.capital_permission import LIVE_LANE_STATES

    blockers: list[str] = []

    if binding_live_ready is None:
        from app.services.account_binding_service import binding_health

        health = binding_health()
        binding_live_ready = bool(health.get("account_binding_live_ready"))
    if not binding_live_ready:
        blockers.append("authorised_account_binding")

    status = egress_status()
    if not status.verified:
        blockers.append("static_egress_verified")

    if not broker_session_valid:
        blockers.append("broker_session_valid")
    if not api_order_permission_verified:
        blockers.append("api_order_permission_verified")
    if not safety_normal:
        blockers.append("safety_supervisor_normal")
    if str(lane_capital_state or "").strip().upper() not in LIVE_LANE_STATES:
        blockers.append("lane_capital_state")

    return (not blockers), blockers


def render_continuity(report: ContinuityReport) -> str:
    lines = ["CONTINUITY", ""]
    egress = report.egress
    lines.append(f"{'static egress':<22}{'VERIFIED' if egress.verified else 'NOT VERIFIED'}")
    if egress.reason:
        lines.append(f"{'':<22}{egress.reason}")
    binding = report.binding
    lines.append(
        f"{'account binding':<22}"
        + (str(binding.get("account_binding_id") or "NONE"))
        + ("  (live ready)" if binding.get("account_binding_live_ready") else "")
    )
    lines.extend(["", "Checklist (answers live outside the code):"])
    for item in report.items:
        mark = "answered" if item.acknowledged else "OUTSTANDING"
        lines.append(f"  {item.key:<22}{mark:<14}{item.answer or item.question}")
    lines.append("")
    lines.append(
        "Handoff is ready." if report.handoff_ready
        else f"{len(report.outstanding)} item(s) outstanding; the handoff is not complete."
    )
    return "\n".join(lines)
