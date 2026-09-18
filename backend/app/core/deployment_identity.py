"""What the deployed host actually observed about itself.

Section 11 is careful about one thing: a static IP is not a configuration flag.
A deployment can be configured to have a static address and still be answering
from a different one after a router reconnect, and the broker's allowlist cares
about the second fact. So this records observations — the host that saw them,
the address it saw, when, and which router generation it was on — and compares
them against what is registered.

Nothing here performs a network call. An application that asks the internet
what its own address is has made a safety check depend on a third party being
up, and has learned nothing about what the broker sees anyway. The deployment
takes the observation; this stores and judges it.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Final

__all__ = [
    "DeploymentObservation", "DeploymentIdentityStore", "IdentityVerdict",
    "verify_deployment_identity", "MAX_OBSERVATION_AGE", "IDENTITY_FILE",
]

IDENTITY_FILE: Final[str] = "data/manifests/deployment_identity.json"

#: An observation older than this is history, not a current fact. A lease can
#: renew, a router can reboot, an ISP can move a customer between pools.
MAX_OBSERVATION_AGE: Final[timedelta] = timedelta(days=7)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _root() -> Path:
    import os

    configured = os.environ.get("STERLING_ROOT")
    return Path(configured) if configured else Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class DeploymentObservation:
    """One reading of where this deployment answers from."""

    host_id: str
    outbound_ip: str
    observed_at: str
    router_generation: str = ""
    runtime_sha: str = ""
    release_tag: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "host_id": self.host_id,
            "outbound_ip": self.outbound_ip,
            "observed_at": self.observed_at,
            "router_generation": self.router_generation,
            "runtime_sha": self.runtime_sha,
            "release_tag": self.release_tag,
        }


class DeploymentIdentityStore:
    """Append-only history of observations, newest last."""

    def __init__(self, path: Path | str | None = None) -> None:
        self.path = Path(path) if path else _root() / IDENTITY_FILE

    def read(self) -> list[DeploymentObservation]:
        if not self.path.exists():
            return []
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 - a corrupt file is not an observation
            return []
        return [
            DeploymentObservation(
                host_id=str(row.get("host_id") or ""),
                outbound_ip=str(row.get("outbound_ip") or ""),
                observed_at=str(row.get("observed_at") or ""),
                router_generation=str(row.get("router_generation") or ""),
                runtime_sha=str(row.get("runtime_sha") or ""),
                release_tag=str(row.get("release_tag") or ""),
            )
            for row in payload.get("observations", [])
        ]

    def latest(self) -> DeploymentObservation | None:
        history = self.read()
        return history[-1] if history else None

    def record(
        self,
        *,
        host_id: str,
        outbound_ip: str,
        router_generation: str = "",
        observed_at: str | None = None,
        runtime_sha: str = "",
        release_tag: str = "",
    ) -> DeploymentObservation:
        """Append one observation. Nothing is ever overwritten.

        The history is the point: "the address changed on the 3rd" is a fact an
        operator needs, and it is invisible in a file that only holds the
        current value.
        """
        if not str(host_id).strip() or not str(outbound_ip).strip():
            raise ValueError("an observation must name the host and the address it saw")

        observation = DeploymentObservation(
            host_id=str(host_id).strip(),
            outbound_ip=str(outbound_ip).strip(),
            observed_at=observed_at or _now().isoformat(),
            router_generation=str(router_generation).strip(),
            runtime_sha=str(runtime_sha).strip(),
            release_tag=str(release_tag).strip(),
        )
        history = [*self.read(), observation]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps({"updated_at": _now().isoformat(),
                        "observations": [o.as_dict() for o in history]},
                       indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return observation


@dataclass(frozen=True)
class IdentityVerdict:
    """May this host be trusted to reach the broker under its registered address?"""

    #: True, False, or None when it could not be determined.
    verified: bool | None
    reason: str
    observation: DeploymentObservation | None = None
    expected_ip: str = ""
    changed_from: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "verified": self.verified,
            "reason": self.reason,
            "expected_ip": self.expected_ip,
            "changed_from": self.changed_from,
            "observation": self.observation.as_dict() if self.observation else None,
        }


def verify_deployment_identity(
    *,
    expected_ip: str | None = None,
    store: DeploymentIdentityStore | None = None,
    now: datetime | None = None,
) -> IdentityVerdict:
    """Compare the latest observation with the registered static address."""
    import os

    registered = (expected_ip if expected_ip is not None
                  else os.environ.get("STERLING_STATIC_EGRESS_IP", "")).strip()
    identity_store = store or DeploymentIdentityStore()
    history = identity_store.read()
    latest = history[-1] if history else None
    moment = now or _now()

    if not registered:
        return IdentityVerdict(
            None, "no static address is registered for this deployment", latest)
    if latest is None:
        return IdentityVerdict(
            None, "no deployment observation has been recorded", None, registered)

    try:
        seen_at = datetime.fromisoformat(latest.observed_at)
    except ValueError:
        return IdentityVerdict(
            None, f"observation timestamp is unreadable: {latest.observed_at!r}",
            latest, registered)
    if seen_at.tzinfo is None:
        seen_at = seen_at.replace(tzinfo=timezone.utc)

    previous = history[-2] if len(history) > 1 else None
    changed_from = (previous.outbound_ip
                    if previous and previous.outbound_ip != latest.outbound_ip else "")

    if latest.outbound_ip != registered:
        return IdentityVerdict(
            False,
            f"this host answers from {latest.outbound_ip}, not the registered "
            f"{registered}; broker automation must stay blocked until it is re-approved",
            latest, registered, changed_from)

    if moment - seen_at > MAX_OBSERVATION_AGE:
        days = (moment - seen_at).days
        return IdentityVerdict(
            None,
            f"the address was last verified {days} days ago, which is not a current fact",
            latest, registered, changed_from)

    if changed_from:
        # It matches now, but it moved. That is worth saying out loud: the
        # broker's allowlist may have been wrong in between.
        return IdentityVerdict(
            True,
            f"{latest.outbound_ip} matches, having changed from {changed_from}",
            latest, registered, changed_from)

    return IdentityVerdict(True, f"{latest.outbound_ip} matches the registered address",
                           latest, registered)
