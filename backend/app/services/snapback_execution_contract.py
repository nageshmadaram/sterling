"""The contract Sterling actually bought, recorded apart from the one it computed.

Snapback selects twice, and the two are not the same decision.

At signal time ``snapback.contracts.pick_for`` solves for a strike in closed
form and rounds it to the published strike step. That is a *theoretical target*:
no chain is consulted, and whether the exchange lists it is a separate question,
recorded by :mod:`app.services.snapback_selection_record`.

On the next session the prospective collector chooses the contract that is really
bought. It walks the actual option chain, discards candidates failing the frozen
eligibility rules — monthly expiry inside the DTE window, a quote present, spread
within ``max_spread_pct``, premium at or above ``min_option_premium``, open
interest at or above ``min_option_oi`` — and takes the eligible candidate whose
delta is closest to ``target_delta``.

Collapsing these into one record would misstate what happened in both
directions. It would credit the closed-form rule with a chain-aware choice it
never made, and it would hide the distance between what the rule wanted and what
the market offered — which is precisely the execution-reality quantity this
release exists to measure.

Nothing here selects. The collector's decision is passed in and recorded; this
module has no eligibility logic of its own, so it cannot drift from the rule it
documents.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any, Optional, Sequence

__all__ = [
    "EXECUTION_CONTRACT_SCHEMA_VERSION",
    "ExecutionContractError",
    "CandidateEvaluation",
    "ExecutionContractRecord",
    "NO_ELIGIBLE_CANDIDATE",
    "build_execution_contract_record",
    "no_eligible_candidate_record",
]

EXECUTION_CONTRACT_SCHEMA_VERSION = "1"

#: Every candidate failed the frozen eligibility rules. A real outcome, recorded
#: rather than raised: an opportunity that vanishes is one nobody can count.
NO_ELIGIBLE_CANDIDATE = "NO_ELIGIBLE_CANDIDATE"


class ExecutionContractError(ValueError):
    """The execution contract cannot be described truthfully."""


@dataclass(frozen=True)
class CandidateEvaluation:
    """One contract the collector looked at, and why it was kept or dropped.

    The rejected ones matter as much as the chosen one. "No trade today" is
    uninformative; "every candidate was rejected for spread" is a finding about
    whether this strategy is executable at all.
    """

    symbol: str
    expiry: str
    strike: float
    option_type: str
    dte: int
    is_monthly: bool
    theoretical_delta: float
    instrument_token: str
    lot_size: int

    eligible: bool
    rejection_reasons: tuple[str, ...]

    best_bid: Optional[float]
    best_ask: Optional[float]
    spread_pct: Optional[float]
    open_interest: Optional[int]

    #: |delta| distance from target. Null when the candidate never got that far.
    delta_distance: Optional[float]

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ExecutionContractRecord:
    """What was bought, what was considered, and how far it sat from the target."""

    schema_version: str
    opportunity_id: str

    # ─── the frozen rule's theoretical target ────────────────────────────────
    theoretical_strike: Optional[float]
    theoretical_delta_target: float
    theoretical_expiry: Optional[str]

    # ─── the frozen eligibility rules applied to the real chain ──────────────
    min_dte: int
    max_dte: int
    max_spread_pct: float
    min_option_premium: float
    min_option_oi: int

    # ─── the contract actually chosen ────────────────────────────────────────
    selected: bool
    reason: str

    executed_symbol: Optional[str]
    executed_instrument_token: Optional[str]
    executed_expiry: Optional[str]
    executed_strike: Optional[float]
    executed_option_type: Optional[str]
    executed_dte: Optional[int]
    executed_lot_size: Optional[int]
    executed_delta: Optional[float]

    executed_best_bid: Optional[float]
    executed_best_ask: Optional[float]
    executed_spread_pct: Optional[float]
    executed_open_interest: Optional[int]

    # ─── the gap between intention and reality ───────────────────────────────
    #: Signed, executed minus theoretical. The number that says how far the
    #: frozen rule's target was from what the market would actually sell.
    strike_gap: Optional[float]
    delta_gap: Optional[float]

    candidates_considered: int
    candidates_eligible: int
    candidates: tuple[CandidateEvaluation, ...]

    # ─── provenance ──────────────────────────────────────────────────────────
    runtime_sha: str
    strategy_sha: str
    config_hash: str
    rule_hash: str
    evaluated_at: str

    @property
    def is_authoritative(self) -> bool:
        """A record is usable when a contract was chosen and fully described."""
        if not self.selected:
            return False
        return all((
            self.executed_symbol, self.executed_instrument_token,
            self.executed_expiry, self.executed_strike is not None,
            self.executed_lot_size,
        ))

    def as_dict(self) -> dict[str, Any]:
        blob = asdict(self)
        blob["candidates"] = [c.as_dict() for c in self.candidates]
        return blob


def _provenance(**kw: str) -> dict[str, str]:
    for name, value in kw.items():
        if not value:
            raise ExecutionContractError(
                f"{name} is required; a record without provenance proves nothing"
            )
    return kw


def build_execution_contract_record(
    *,
    opportunity_id: str,
    candidates: Sequence[CandidateEvaluation],
    chosen: Optional[CandidateEvaluation],
    theoretical_strike: Optional[float],
    theoretical_expiry: Optional[str],
    target_delta: float,
    min_dte: int,
    max_dte: int,
    max_spread_pct: float,
    min_option_premium: float,
    min_option_oi: int,
    runtime_sha: str,
    strategy_sha: str,
    config_hash: str,
    rule_hash: str,
    evaluated_at: datetime,
    reason: str = "",
) -> ExecutionContractRecord:
    """Record the collector's decision. Performs no selection of its own."""
    provenance = _provenance(
        runtime_sha=runtime_sha, strategy_sha=strategy_sha,
        config_hash=config_hash, rule_hash=rule_hash,
    )

    if chosen is not None and not chosen.eligible:
        # Choosing an ineligible candidate would mean the frozen filters were
        # bypassed somewhere; refusing here makes that impossible to record as
        # if it were normal.
        raise ExecutionContractError(
            f"the chosen candidate {chosen.symbol} is marked ineligible: "
            f"{', '.join(chosen.rejection_reasons)}"
        )

    eligible = sum(1 for c in candidates if c.eligible)

    strike_gap = delta_gap = None
    if chosen is not None:
        if theoretical_strike is not None:
            strike_gap = float(chosen.strike) - float(theoretical_strike)
        delta_gap = abs(chosen.theoretical_delta) - float(target_delta)

    return ExecutionContractRecord(
        schema_version=EXECUTION_CONTRACT_SCHEMA_VERSION,
        opportunity_id=opportunity_id,
        theoretical_strike=theoretical_strike,
        theoretical_delta_target=float(target_delta),
        theoretical_expiry=theoretical_expiry,
        min_dte=int(min_dte), max_dte=int(max_dte),
        max_spread_pct=float(max_spread_pct),
        min_option_premium=float(min_option_premium),
        min_option_oi=int(min_option_oi),
        selected=chosen is not None,
        reason=reason or ("SELECTED" if chosen is not None else NO_ELIGIBLE_CANDIDATE),
        executed_symbol=chosen.symbol if chosen else None,
        executed_instrument_token=chosen.instrument_token if chosen else None,
        executed_expiry=chosen.expiry if chosen else None,
        executed_strike=chosen.strike if chosen else None,
        executed_option_type=chosen.option_type if chosen else None,
        executed_dte=chosen.dte if chosen else None,
        executed_lot_size=chosen.lot_size if chosen else None,
        executed_delta=chosen.theoretical_delta if chosen else None,
        executed_best_bid=chosen.best_bid if chosen else None,
        executed_best_ask=chosen.best_ask if chosen else None,
        executed_spread_pct=chosen.spread_pct if chosen else None,
        executed_open_interest=chosen.open_interest if chosen else None,
        strike_gap=strike_gap,
        delta_gap=delta_gap,
        candidates_considered=len(candidates),
        candidates_eligible=eligible,
        candidates=tuple(candidates),
        evaluated_at=evaluated_at.isoformat(),
        **provenance,
    )


def no_eligible_candidate_record(
    *,
    opportunity_id: str,
    candidates: Sequence[CandidateEvaluation],
    theoretical_strike: Optional[float],
    theoretical_expiry: Optional[str],
    target_delta: float,
    min_dte: int,
    max_dte: int,
    max_spread_pct: float,
    min_option_premium: float,
    min_option_oi: int,
    runtime_sha: str,
    strategy_sha: str,
    config_hash: str,
    rule_hash: str,
    evaluated_at: datetime,
) -> ExecutionContractRecord:
    """Every candidate was rejected. Recorded, because it is a real outcome."""
    return build_execution_contract_record(
        opportunity_id=opportunity_id, candidates=candidates, chosen=None,
        theoretical_strike=theoretical_strike, theoretical_expiry=theoretical_expiry,
        target_delta=target_delta, min_dte=min_dte, max_dte=max_dte,
        max_spread_pct=max_spread_pct, min_option_premium=min_option_premium,
        min_option_oi=min_option_oi, runtime_sha=runtime_sha,
        strategy_sha=strategy_sha, config_hash=config_hash, rule_hash=rule_hash,
        evaluated_at=evaluated_at, reason=NO_ELIGIBLE_CANDIDATE,
    )
