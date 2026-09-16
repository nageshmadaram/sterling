"""The hedge leg, recorded to the same standard as the option.

Futures are the side that quietly gets away with less scrutiny, because
historical futures data happens to exist while historical option data does not.
The quarantined replay shows where that leads: when a futures quote was missing
it substituted spot, and when the NIFTY lot size was missing it defaulted to 50 —
across a window in which the real lot size changed repeatedly. Both produced a
hedge P&L that looked like a measurement.

This module records what hedge was selected, by which production code, against
which real contract, and refuses to describe a hedge it cannot describe fully.

It does not select. ``snapback_hedge_contract.select_hedge_future`` is the
production rule and stays the only one — a recorder-specific hedge selector would
drift from it exactly as the replay's option selector drifted from production.

"Not required" is a recorded decision with a reason, never an absence of data. An
opportunity with no hedge row and an opportunity whose frozen rule waived the
hedge are indistinguishable once written down, and only one of them is safe.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date, datetime
from typing import Any, Iterable, Optional

from app.services.snapback_hedge_contract import (
    HedgeContract,
    HedgeContractError,
    select_hedge_future,
)

__all__ = [
    "HEDGE_EVIDENCE_SCHEMA_VERSION",
    "HEDGE_SELECTOR_VERSION",
    "HedgeEvidenceError",
    "HedgeSelectionRecord",
    "HEDGE_NOT_REQUIRED_REASONS",
    "HEDGE_SELECTION_UNKNOWN",
    "build_hedge_selection_record",
    "hedge_not_required_record",
    "hedge_evidence_is_authoritative",
]

HEDGE_EVIDENCE_SCHEMA_VERSION = "1"
HEDGE_SELECTOR_VERSION = "snapback_nearest_covering_future_v1"

#: Raised as a status, not an exception, so the opportunity is still recorded.
HEDGE_SELECTION_UNKNOWN = "HEDGE_SELECTION_UNKNOWN"

#: The only accepted justifications for running unhedged. Free text would let
#: "not required" mean whatever the caller wanted on the day.
HEDGE_NOT_REQUIRED_REASONS = frozenset({
    "FROZEN_RULE_UNHEDGED",
    "INDEX_UNDERLYING_SELF_HEDGED",
})


class HedgeEvidenceError(ValueError):
    """The hedge cannot be described truthfully, so it is not described."""


@dataclass(frozen=True)
class HedgeSelectionRecord:
    """One opportunity's hedge decision, complete or explicitly unknown."""

    schema_version: str

    opportunity_id: str

    hedge_required: bool
    hedge_reason: str

    underlying: str

    instrument_token: Optional[int]
    tradingsymbol: Optional[str]
    exchange: Optional[str]
    segment: Optional[str]

    expiry: Optional[str]
    lot_size: Optional[int]
    tick_size: Optional[float]

    beta_used: Optional[float]
    hedge_ratio: Optional[float]

    option_expiry: Optional[str]

    selector_version: str

    runtime_sha: str
    strategy_sha: str
    config_hash: str
    rule_hash: str

    selected_at: str

    @property
    def is_authoritative(self) -> bool:
        return hedge_evidence_is_authoritative(self)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def hedge_evidence_is_authoritative(record: HedgeSelectionRecord) -> bool:
    """A hedge counts when it is fully described, or explicitly not required.

    The hedge must also outlive the option it hedges — a future expiring first
    leaves an open position unhedged at futures expiry, which is exposure nobody
    chose to take.
    """
    if not record.hedge_required:
        return record.hedge_reason in HEDGE_NOT_REQUIRED_REASONS

    if record.hedge_reason == HEDGE_SELECTION_UNKNOWN:
        return False

    if not all((record.instrument_token, record.tradingsymbol, record.exchange, record.expiry)):
        return False
    if not record.lot_size or record.lot_size <= 0:
        return False

    if record.option_expiry and record.expiry < record.option_expiry:
        return False

    return True


def _provenance(**kw: str) -> dict[str, str]:
    for name, value in kw.items():
        if not value:
            raise HedgeEvidenceError(f"{name} is required; a record without provenance proves nothing")
    return kw


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()[:10] if not isinstance(value, datetime) else value.isoformat()
    return str(value)


def build_hedge_selection_record(
    *,
    opportunity_id: str,
    underlying: str,
    option_expiry: date,
    chain: Iterable[Any],
    runtime_sha: str,
    strategy_sha: str,
    config_hash: str,
    rule_hash: str,
    selected_at: datetime,
    exchange: str = "NFO",
    segment: Optional[str] = None,
    tick_size: Optional[float] = None,
    beta_used: Optional[float] = None,
    hedge_ratio: Optional[float] = None,
) -> HedgeSelectionRecord:
    """Record the hedge production would pick, or record that it could not.

    A chain with no covering future is not an error here: it is an opportunity
    whose hedge is UNKNOWN, which must be recorded and must block, rather than
    vanish because an exception propagated somewhere else.
    """
    provenance = _provenance(
        runtime_sha=runtime_sha, strategy_sha=strategy_sha,
        config_hash=config_hash, rule_hash=rule_hash,
    )

    base = dict(
        schema_version=HEDGE_EVIDENCE_SCHEMA_VERSION,
        opportunity_id=opportunity_id,
        hedge_required=True,
        underlying=underlying.upper(),
        segment=segment,
        tick_size=tick_size,
        beta_used=beta_used,
        hedge_ratio=hedge_ratio,
        option_expiry=_iso(option_expiry),
        selector_version=HEDGE_SELECTOR_VERSION,
        selected_at=selected_at.isoformat(),
        **provenance,
    )

    try:
        contract: HedgeContract = select_hedge_future(
            chain, option_expiry=option_expiry, name=underlying, exchange=exchange,
        )
    except HedgeContractError:
        # No covering future is a recorded UNKNOWN, not a lost opportunity. It
        # must block, and blocking silently is how exposure goes unnoticed.
        return HedgeSelectionRecord(
            hedge_reason=HEDGE_SELECTION_UNKNOWN,
            instrument_token=None, tradingsymbol=None, exchange=None,
            expiry=None, lot_size=None,
            **base,
        )

    return HedgeSelectionRecord(
        hedge_reason="SELECTED",
        instrument_token=contract.instrument_token,
        tradingsymbol=contract.tradingsymbol,
        exchange=contract.exchange,
        expiry=contract.expiry.isoformat(),
        lot_size=contract.lot_size,
        **base,
    )


def hedge_not_required_record(
    *,
    opportunity_id: str,
    underlying: str,
    reason: str,
    runtime_sha: str,
    strategy_sha: str,
    config_hash: str,
    rule_hash: str,
    selected_at: datetime,
    option_expiry: Optional[date] = None,
) -> HedgeSelectionRecord:
    """Record a deliberate decision not to hedge, with an accepted reason."""
    if reason not in HEDGE_NOT_REQUIRED_REASONS:
        raise HedgeEvidenceError(
            f"unaccepted hedge waiver {reason!r}; expected one of {sorted(HEDGE_NOT_REQUIRED_REASONS)}"
        )

    return HedgeSelectionRecord(
        schema_version=HEDGE_EVIDENCE_SCHEMA_VERSION,
        opportunity_id=opportunity_id,
        hedge_required=False,
        hedge_reason=reason,
        underlying=underlying.upper(),
        instrument_token=None, tradingsymbol=None, exchange=None, segment=None,
        expiry=None, lot_size=None, tick_size=None,
        beta_used=None, hedge_ratio=None,
        option_expiry=_iso(option_expiry),
        selector_version=HEDGE_SELECTOR_VERSION,
        selected_at=selected_at.isoformat(),
        **_provenance(
            runtime_sha=runtime_sha, strategy_sha=strategy_sha,
            config_hash=config_hash, rule_hash=rule_hash,
        ),
    )
