"""The envelope the first real order has to fit inside.

Section 17 describes the first activation of real capital, and it is
deliberately the smallest possible event: one lane, the minimum executable
quantity, a fixed daily-loss budget, a fixed gross exposure, no automatic
scaling and no averaging down. The point is not that a small loss is
acceptable. It is that the first order is a test of the *system* — the
protection, the reconciliation, the evidence trail — and a test whose failure
costs real money should cost as little of it as possible.

Every limit here is read from configuration and every one refuses when it is
missing. A default daily-loss budget would be this module inventing how much of
a family's money it is allowed to lose.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Final, Mapping

__all__ = [
    "LiveMinimumEnvelope", "LiveMinimumVerdict", "configured_envelope",
    "check_order", "DAILY_LOSS_ENV", "GROSS_EXPOSURE_ENV",
]

DAILY_LOSS_ENV: Final[str] = "STERLING_LIVE_MINIMUM_DAILY_LOSS_INR"
GROSS_EXPOSURE_ENV: Final[str] = "STERLING_LIVE_MINIMUM_GROSS_EXPOSURE_INR"


@dataclass(frozen=True)
class LiveMinimumEnvelope:
    """The configured first-capital limits. ``None`` means not configured."""

    daily_loss_budget: float | None = None
    gross_exposure_limit: float | None = None

    @property
    def configured(self) -> bool:
        return (self.daily_loss_budget is not None
                and self.gross_exposure_limit is not None)

    def as_dict(self) -> dict[str, Any]:
        return {
            "daily_loss_budget": self.daily_loss_budget,
            "gross_exposure_limit": self.gross_exposure_limit,
            "configured": self.configured,
        }


def _positive(source: Mapping[str, str], name: str) -> float | None:
    raw = (source.get(name) or "").strip()
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def configured_envelope(env: Mapping[str, str] | None = None) -> LiveMinimumEnvelope:
    source = env if env is not None else os.environ
    return LiveMinimumEnvelope(
        daily_loss_budget=_positive(source, DAILY_LOSS_ENV),
        gross_exposure_limit=_positive(source, GROSS_EXPOSURE_ENV),
    )


@dataclass(frozen=True)
class LiveMinimumVerdict:
    """Whether one order fits the envelope, and every reason it does not."""

    allowed: bool
    blockers: tuple[str, ...] = ()
    envelope: LiveMinimumEnvelope = LiveMinimumEnvelope()

    def as_dict(self) -> dict[str, Any]:
        return {"allowed": self.allowed, "blockers": list(self.blockers),
                "envelope": self.envelope.as_dict()}


def check_order(
    *,
    quantity: int,
    minimum_executable_quantity: int | None,
    order_value: float | None,
    open_gross_exposure: float | None,
    realised_loss_today: float | None,
    is_averaging_down: bool | None,
    envelope: LiveMinimumEnvelope | None = None,
    # Section 21.1: the state of the system the order is being sent from. Each
    # is tri-state and each None refuses, because an order sent from a machine
    # whose safety state could not be read is not a safe order, it is an
    # unexamined one.
    lane_permission: bool | None = None,
    release_certification: bool | None = None,
    account_binding: bool | None = None,
    safety_state: bool | None = None,
    recovery_state: bool | None = None,
    broker_session: bool | None = None,
    market_freshness: bool | None = None,
    unresolved_exposure: int | None = None,
    daily_loss_remaining: float | None = None,
) -> LiveMinimumVerdict:
    """Does this order fit inside the first-capital envelope?

    Every input is nullable and every ``None`` refuses. An order whose value
    could not be computed, against an exposure that could not be read, is not a
    small order — it is an unmeasured one, and the whole point of LIVE_MINIMUM
    is that the first one is measured.

    All blockers are returned, never only the first: an operator fixing one
    condition at a time, discovering the next only after the next attempt, is
    how a careful person ends up disabling checks.
    """
    limits = envelope if envelope is not None else configured_envelope()
    blockers: list[str] = []

    if not limits.configured:
        blockers.append("LIVE_MINIMUM_NOT_CONFIGURED")

    if minimum_executable_quantity is None:
        blockers.append("MINIMUM_QUANTITY_UNKNOWN")
    elif quantity != minimum_executable_quantity:
        # Not "at most": exactly one lot. A larger first order is a different
        # experiment, and a smaller one cannot be executed at all.
        blockers.append("QUANTITY_ABOVE_MINIMUM")

    if order_value is None:
        blockers.append("ORDER_VALUE_UNKNOWN")
    if open_gross_exposure is None:
        blockers.append("GROSS_EXPOSURE_UNKNOWN")
    if (order_value is not None and open_gross_exposure is not None
            and limits.gross_exposure_limit is not None
            and order_value + open_gross_exposure > limits.gross_exposure_limit):
        blockers.append("GROSS_EXPOSURE_EXCEEDED")

    if realised_loss_today is None:
        blockers.append("DAILY_LOSS_UNKNOWN")
    elif (limits.daily_loss_budget is not None
          and realised_loss_today >= limits.daily_loss_budget):
        blockers.append("DAILY_LOSS_BUDGET_SPENT")

    if is_averaging_down is None:
        blockers.append("AVERAGING_DOWN_UNKNOWN")
    elif is_averaging_down:
        blockers.append("AVERAGING_DOWN_PROHIBITED")

    for value, unknown_code, refusal_code in (
        (lane_permission, "LANE_PERMISSION_UNKNOWN", "LANE_NOT_PERMITTED"),
        (release_certification, "RELEASE_CERTIFICATION_UNKNOWN", "RELEASE_NOT_CERTIFIED"),
        (account_binding, "ACCOUNT_BINDING_UNKNOWN", "ACCOUNT_BINDING_NOT_READY"),
        (safety_state, "SAFETY_STATE_UNKNOWN", "SAFETY_BLOCKS_EXPOSURE"),
        (recovery_state, "RECOVERY_STATE_UNKNOWN", "RECOVERY_REQUIRED"),
        (broker_session, "BROKER_SESSION_UNKNOWN", "BROKER_SESSION_NOT_READY"),
        (market_freshness, "MARKET_FRESHNESS_UNKNOWN", "MARKET_DATA_STALE"),
    ):
        if value is None:
            blockers.append(unknown_code)
        elif not value:
            blockers.append(refusal_code)

    if unresolved_exposure is None:
        blockers.append("UNRESOLVED_EXPOSURE_UNKNOWN")
    elif unresolved_exposure:
        blockers.append("UNRESOLVED_EXPOSURE_OPEN")

    if daily_loss_remaining is None:
        blockers.append("DAILY_LOSS_REMAINING_UNKNOWN")
    elif daily_loss_remaining <= 0:
        blockers.append("DAILY_LOSS_BUDGET_SPENT")

    return LiveMinimumVerdict(
        allowed=not blockers, blockers=tuple(blockers), envelope=limits
    )
