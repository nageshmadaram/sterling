"""Three verdicts, independently, before a lane may touch capital.

Section 20.2 is a refusal to let one good answer stand in for three. A lane can
be profitable on paper and unexecutable in the real book. It can be executable
and running on a host nobody has verified. It can be both and still be pointed
at an account that is not the one the family authorised.

So the economic, shadow-execution and operational questions are answered
separately and reported separately. Each is tri-state, and `None` refuses in
exactly the way `False` does — with a different reason, because "we could not
tell" and "no" send an operator to different places.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final

__all__ = ["Verdict", "LaneVerdicts", "compose_lane_verdicts", "render_lane_verdicts"]


class Verdict(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


def _verdict(value: bool | None) -> Verdict:
    if value is None:
        return Verdict.UNKNOWN
    return Verdict.PASS if value else Verdict.FAIL


@dataclass(frozen=True)
class LaneVerdicts:
    """One lane's three answers, and whether it may be considered for capital."""

    lane_key: str
    economic: Verdict
    shadow_execution: Verdict
    operational: Verdict
    reasons: tuple[str, ...] = field(default_factory=tuple)

    @property
    def eligible_for_live_minimum(self) -> bool:
        """All three PASS. UNKNOWN never permits, and never averages out."""
        return all(v is Verdict.PASS for v in
                   (self.economic, self.shadow_execution, self.operational))

    @property
    def blockers(self) -> tuple[str, ...]:
        out = []
        for name, verdict in (("economic", self.economic),
                              ("shadow_execution", self.shadow_execution),
                              ("operational", self.operational)):
            if verdict is not Verdict.PASS:
                out.append(f"{name}={verdict.value}")
        return tuple(out)

    def as_dict(self) -> dict[str, Any]:
        return {
            "lane_key": self.lane_key,
            "economic_verdict": self.economic.value,
            "shadow_execution_verdict": self.shadow_execution.value,
            "operational_verdict": self.operational.value,
            "eligible_for_live_minimum": self.eligible_for_live_minimum,
            "blockers": list(self.blockers),
            "reasons": list(self.reasons),
        }


def compose_lane_verdicts(
    lane_key: str,
    *,
    economic: bool | None,
    shadow_execution: bool | None,
    operational: bool | None,
    reasons: tuple[str, ...] = (),
) -> LaneVerdicts:
    """Combine three independently-derived answers without merging them."""
    return LaneVerdicts(
        lane_key=lane_key,
        economic=_verdict(economic),
        shadow_execution=_verdict(shadow_execution),
        operational=_verdict(operational),
        reasons=reasons,
    )


def render_lane_verdicts(verdicts: LaneVerdicts) -> str:
    lines = [
        f"{verdicts.lane_key}",
        f"  economic          {verdicts.economic.value}",
        f"  shadow execution  {verdicts.shadow_execution.value}",
        f"  operational       {verdicts.operational.value}",
    ]
    for reason in verdicts.reasons:
        lines.append(f"    {reason}")
    lines.append("")
    if verdicts.eligible_for_live_minimum:
        lines.append("  All three verdicts PASS: this lane may be reviewed for LIVE_MINIMUM.")
    else:
        lines.append("  NOT eligible: " + ", ".join(verdicts.blockers))
    return "\n".join(lines)
