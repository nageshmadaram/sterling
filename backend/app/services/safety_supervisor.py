"""The one place that answers "may this action create new risk?".

Sterling accumulated four separate switches that each meant roughly "stop":
the durable ``safe_mode.json`` an operator sets with ``sterlingctl safe on``, the
``execution_control`` rows in the database, the in-process ``_KILL_SWITCH``, and
the Snapback family ``new_trades_halt.json``. Each was checked by a different
subset of the code. ``sterlingctl safe on`` wrote the first; the canonical
executor consulted the second and third. An operator who had engaged safe mode
could therefore watch an order reach the broker, which is the single worst
failure this system can have.

This module composes all of them into one decision. It does not replace the
individual stores — an operator still reads ``safe_mode.json`` with ``cat`` when
the service will not start, which is the whole point of that file — but nothing
outside this module may decide admission on its own.

The distinction every check preserves is between *opening* risk and *managing*
it. Exits, reductions, cancellations, protection repair and reconciliation are
never blocked. A safety system that refused to close a position would have
turned itself into a way to lose money.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

__all__ = [
    "ExposureIntent",
    "SafetySnapshot",
    "SafetySupervisor",
    "authorize",
    "snapshot",
]

#: The exposure effects that create new risk. Everything else reduces, protects
#: or merely observes, and is admitted whatever the safety state says.
_INCREASING: frozenset[str] = frozenset({"INCREASE_EXPOSURE"})

UNKNOWN = "UNKNOWN"


class ExposureIntent:
    """The vocabulary callers declare. Mirrors ``ExecutionRequest`` effects."""

    INCREASE_EXPOSURE = "INCREASE_EXPOSURE"
    REDUCE_EXPOSURE = "REDUCE_EXPOSURE"
    CLOSE_POSITION = "CLOSE_POSITION"
    PROTECT_POSITION = "PROTECT_POSITION"
    CANCEL_ORDER = "CANCEL_ORDER"
    MODIFY_ORDER = "MODIFY_ORDER"
    RECONCILE = "RECONCILE"


def safe_mode_path() -> Path:
    """Where the durable safety state lives, honouring the operator override."""
    configured = os.environ.get("STERLING_SAFE_MODE_FILE")
    if configured:
        return Path(configured)
    root = Path(os.environ.get("STERLING_ROOT") or Path(__file__).resolve().parents[3])
    return root / "data" / "safe_mode.json"


@dataclass(frozen=True)
class SafetySnapshot:
    """Everything the admission decision was made from, in one readable object.

    The component states are reported rather than enforced here: each already
    has an owner that fails closed in its own path (the health probe, the family
    gate, the release manifest check). What this object guarantees is that the
    four *admission* authorities agree, and that ``blockers`` names every reason
    an increase was refused rather than only the first one found.
    """

    operator_state: str = UNKNOWN
    recovery_state: str = UNKNOWN

    safe_mode: bool = True
    safe_mode_reason: str = ""
    safe_mode_triggers: tuple[str, ...] = field(default_factory=tuple)

    kill_switch: bool = False
    new_trades_halted: bool = False

    broker_state: str = UNKNOWN
    market_data_state: str = UNKNOWN
    evidence_state: str = UNKNOWN
    release_state: str = UNKNOWN
    risk_state: str = UNKNOWN

    blockers: tuple[str, ...] = field(default_factory=tuple)
    #: Human-readable text for each blocker, in the same order. Codes are for
    #: callers; this is what an operator reads.
    blocker_reasons: tuple[str, ...] = field(default_factory=tuple)

    @property
    def may_increase_exposure(self) -> bool:
        return not self.blockers

    @property
    def may_reduce_exposure(self) -> bool:
        # Always. Refusing to reduce would make a safety state a way to lose money.
        return True

    @property
    def may_protect(self) -> bool:
        return True

    @property
    def may_reconcile(self) -> bool:
        return True

    def as_dict(self) -> dict[str, Any]:
        return {
            "operator_state": self.operator_state,
            "recovery_state": self.recovery_state,
            "safe_mode": self.safe_mode,
            "safe_mode_reason": self.safe_mode_reason,
            "safe_mode_triggers": list(self.safe_mode_triggers),
            "kill_switch": self.kill_switch,
            "new_trades_halted": self.new_trades_halted,
            "broker_state": self.broker_state,
            "market_data_state": self.market_data_state,
            "evidence_state": self.evidence_state,
            "release_state": self.release_state,
            "risk_state": self.risk_state,
            "blockers": list(self.blockers),
            "blocker_reasons": list(self.blocker_reasons),
            "may_increase_exposure": self.may_increase_exposure,
            "may_reduce_exposure": self.may_reduce_exposure,
            "may_protect": self.may_protect,
            "may_reconcile": self.may_reconcile,
        }


@dataclass(frozen=True)
class SafetyVerdict:
    """The admission answer. Shaped like ``live_safety.SafetyDecision``."""

    allowed: bool
    reason: str = ""
    code: str = ""
    snapshot: Optional[SafetySnapshot] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "code": self.code,
            "snapshot": self.snapshot.as_dict() if self.snapshot else None,
        }


def _safe_mode_state():
    from app.services.safe_mode import SafeModeService

    return SafeModeService(safe_mode_path()).read()


def _family_halted() -> bool:
    """The Snapback family stop switch. Unreadable state already fails closed."""
    try:
        from app.services.snapback_family_ops import new_trades_halted

        return bool(new_trades_halted())
    except Exception:
        # The store itself decides what an unreadable state means; an import or
        # attribute failure here is a code problem, not an engaged switch, and
        # the durable authorities above already cover the dangerous cases.
        return False


class SafetySupervisor:
    """The single admission authority. Stateless; reads the durable stores."""

    def __init__(self, *, safe_mode_file: Path | str | None = None) -> None:
        self._safe_mode_file = Path(safe_mode_file) if safe_mode_file else None

    def _safe_mode(self):
        if self._safe_mode_file is None:
            return _safe_mode_state()
        from app.services.safe_mode import SafeModeService

        return SafeModeService(self._safe_mode_file).read()

    def snapshot(
        self,
        *,
        uid: str = "default",
        account_id: str = "default",
        positions: Iterable[Any] | None = None,
        strategy_id: str = "",
        ignore_intent_keys: tuple[str, ...] = (),
    ) -> SafetySnapshot:
        """Read every authority once and report what each of them says."""
        from app.services import live_safety

        blockers: list[str] = []
        reasons: list[str] = []

        try:
            state = self._safe_mode()
            safe_mode = bool(state.active)
            reason = state.reason
            triggers = tuple(state.triggers)
        except Exception as exc:  # noqa: BLE001 - an unreadable authority is a blocker
            safe_mode, reason, triggers = True, f"safe-mode read failed: {exc}", ()
        if safe_mode:
            blockers.append("safe_mode")
            reasons.append(f"SAFE_MODE engaged: {reason}")

        operator_state = recovery_state = UNKNOWN
        try:
            from app.services import db

            control = db.get_execution_control(uid=uid, account_id=account_id)
            operator_state = str(control.get("operator_state") or UNKNOWN)
            recovery_state = str(control.get("recovery_state") or UNKNOWN)
        except Exception:
            # live_safety below makes the same read and fails closed on it; the
            # snapshot only reports what it could observe.
            blockers.append("control_plane_unreadable")
            reasons.append("execution-control state could not be read")

        kill = bool(live_safety.kill_switch_state().get("enabled"))

        # The Snapback stop switch governs Snapback only. Applying it to every
        # strategy would let one family's operational pause halt another's
        # evidence collection, which is not what an operator asked for.
        halted = _family_halted()
        family_blocks = halted and str(strategy_id or "").lower() in ("", "snapback")
        if family_blocks:
            blockers.append("new_trades_halted")
            reasons.append("family stop switch: new trades halted")

        decision = live_safety.assert_safe_to_trade(
            list(positions or []),
            uid=uid,
            account_id=account_id,
            exposure_effect=ExposureIntent.INCREASE_EXPOSURE,
            ignore_intent_keys=ignore_intent_keys,
        )
        if not decision.allowed and (decision.code or "refused") not in blockers:
            blockers.append(decision.code or "refused")
            reasons.append(decision.reason or "refused")

        # A broker position Sterling never opened is exposure it cannot see the
        # risk of: no entry price it chose, no stop it armed, no monitor
        # watching. Adding new exposure on top of an unexplained one means the
        # account's total risk is unknown at the moment of the decision, so
        # admission is refused until it is resolved — and an unreadable broker
        # refuses too, because "we could not ask" is not "there is nothing
        # there". Protective actions are unaffected: this only gates increases.
        external_blocker, external_reason = self._external_exposure_blocker()
        if external_blocker and external_blocker not in blockers:
            blockers.append(external_blocker)
            reasons.append(external_reason)

        return SafetySnapshot(
            operator_state=operator_state,
            recovery_state=recovery_state,
            safe_mode=safe_mode,
            safe_mode_reason=reason,
            safe_mode_triggers=triggers,
            kill_switch=kill,
            new_trades_halted=halted,
            blockers=tuple(blockers),
            blocker_reasons=tuple(reasons),
        )

    @staticmethod
    def _external_exposure_blocker() -> tuple[str, str]:
        """Broker positions with no Sterling record, as an admission blocker.

        Reads the recorded observation, never the broker. Admission is on the
        path of every order decision: a check that needs the network is a check
        that fails in the conditions it exists for, and it would put a remote
        call inside a hot loop.
        """
        try:
            from app.services.external_positions import admission_blocker

            return admission_blocker()
        except Exception as exc:  # noqa: BLE001
            return ("external_exposure_unreadable",
                    f"broker exposure could not be established: {exc}")

    def authorize(
        self,
        exposure_effect: str = ExposureIntent.INCREASE_EXPOSURE,
        *,
        uid: str = "default",
        account_id: str = "default",
        positions: Iterable[Any] | None = None,
        strategy_id: str = "",
        idempotency_key: str | None = None,
        ignore_intent_keys: tuple[str, ...] = (),
    ) -> SafetyVerdict:
        """Decide whether one action may proceed.

        Anything that does not increase exposure is admitted without consulting
        the switches at all: there is no safety state in which closing a position
        is the wrong thing to allow.
        """
        effect = str(getattr(exposure_effect, "value", exposure_effect) or "").upper()
        if effect not in _INCREASING:
            return SafetyVerdict(True, reason="non-increasing action", code="")

        try:
            snap = self.snapshot(
                uid=uid,
                account_id=account_id,
                positions=positions,
                strategy_id=strategy_id,
                ignore_intent_keys=ignore_intent_keys,
            )
        except Exception as exc:  # noqa: BLE001
            return SafetyVerdict(
                False, f"Safety evaluation failed closed: {exc}", "safety_unknown"
            )

        if snap.blockers:
            code = snap.blockers[0]
            detail = "; ".join(snap.blocker_reasons or snap.blockers)
            return SafetyVerdict(
                False,
                f"New exposure refused: {detail}",
                code,
                snapshot=snap,
            )

        if idempotency_key:
            from app.services import live_safety

            prior = live_safety.check_idempotency(idempotency_key)
            if prior:
                return SafetyVerdict(
                    False,
                    f"Duplicate order — already placed as {prior}",
                    "duplicate_order",
                    snapshot=snap,
                )

        return SafetyVerdict(True, snapshot=snap)


_DEFAULT = SafetySupervisor()


def authorize(exposure_effect: str = ExposureIntent.INCREASE_EXPOSURE, **kwargs: Any) -> SafetyVerdict:
    """Module-level convenience over the default supervisor."""
    return _DEFAULT.authorize(exposure_effect, **kwargs)


def snapshot(**kwargs: Any) -> SafetySnapshot:
    return _DEFAULT.snapshot(**kwargs)
