"""What the frozen rule computed, and whether that contract actually existed.

These are two different truths and the record keeps them apart. Collapsing them
is how a selection becomes unauditable: a stored contract that reads as real
because it was stored, when in fact nothing ever checked that the exchange listed
it. Snapback has been trading on that assumption since inception — ``pick_for``
solves for a strike in closed form, rounds it to the published step, and treats
"on the grid" as "listed" — and nothing has measured how often that holds.

    COMPUTED CONTRACT     what frozen Snapback mathematically chose
    LISTEDNESS RESOLUTION whether it existed in the observed instrument master

Authority follows from the second, never from the first:

    LISTED      computed contract matches a real observed contract exactly
                -> may proceed to the market-evidence stage
    NOT_LISTED  an observed master exists and the computed contract is absent
                -> entry must NOT become authoritative; recorded as a
                   selection-reality failure
    UNKNOWN     no valid observed universe or master
                -> INCONCLUSIVE; entry must NOT become authoritative

NOT_LISTED is never repaired by snapping to the nearest listed strike. That would
change which contract Snapback buys, i.e. change the strategy mid-experiment. A
challenger may define nearest-listed selection explicitly, but it does so under a
new rule hash and a new evidence identity.

Both failure states block equally. They are kept distinct because they mean
different things to an operator: NOT_LISTED says the rule chose a contract that
does not exist, UNKNOWN says we could not see the exchange.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Any, Optional

from app.services.snapback_contract_selection import (
    LISTED_NO,
    LISTED_UNKNOWN,
    LISTED_YES,
    SelectionResult,
)

__all__ = [
    "SelectionRecord",
    "SelectionRecordError",
    "SELECTION_REALITY_FAILURE",
    "SELECTION_REALITY_INCONCLUSIVE",
    "build_selection_record",
    "selection_is_authoritative",
    "selection_block_reason",
]

#: Distinct reasons, because they tell an operator different things.
SELECTION_REALITY_FAILURE = "SELECTION_NOT_LISTED"
SELECTION_REALITY_INCONCLUSIVE = "SELECTION_UNIVERSE_UNKNOWN"


class SelectionRecordError(ValueError):
    """The record cannot be built truthfully, so it is not built."""


@dataclass(frozen=True)
class SelectionRecord:
    """One opportunity's selection, its inputs, and its reality check."""

    opportunity_id: str

    # ─── frozen selector inputs ──────────────────────────────────────────────
    spot: float
    assumed_iv: float
    valuation_ts: Optional[str]
    target_delta: float
    min_dte: int
    max_dte: int
    option_type: str

    # ─── deterministic frozen selector output ────────────────────────────────
    computed_expiry: Optional[str]
    computed_strike: float
    computed_delta: float

    # ─── observed universe identity ──────────────────────────────────────────
    candidate_universe_hash: Optional[str]
    instrument_master_snapshot_id: Optional[str]

    # ─── reality check ───────────────────────────────────────────────────────
    listed_status: str
    matched_instrument_token: Optional[int]
    matched_tradingsymbol: Optional[str]
    matched_exchange: Optional[str]
    matched_lot_size: Optional[int]
    matched_tick_size: Optional[float]

    # ─── provenance ──────────────────────────────────────────────────────────
    selector_version: str
    runtime_sha: str
    strategy_sha: str
    config_hash: str
    rule_hash: str
    created_at: str

    @property
    def is_authoritative(self) -> bool:
        return selection_is_authoritative(self.listed_status)

    @property
    def block_reason(self) -> Optional[str]:
        return selection_block_reason(self.listed_status)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def selection_is_authoritative(listed_status: str) -> bool:
    """Only an exact match against an observed master confers authority."""
    return listed_status == LISTED_YES


def selection_block_reason(listed_status: str) -> Optional[str]:
    """Why an entry may not proceed, or None when it may."""
    if listed_status == LISTED_YES:
        return None
    if listed_status == LISTED_NO:
        return SELECTION_REALITY_FAILURE
    if listed_status == LISTED_UNKNOWN:
        return SELECTION_REALITY_INCONCLUSIVE
    raise SelectionRecordError(f"unknown listed_status {listed_status!r}")


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def build_selection_record(
    result: SelectionResult,
    *,
    min_dte: int,
    max_dte: int,
    runtime_sha: str,
    strategy_sha: str,
    config_hash: str,
    rule_hash: str,
    created_at: datetime,
    valuation_ts: Optional[datetime] = None,
    instrument_master_snapshot_id: Optional[str] = None,
) -> SelectionRecord:
    """Assemble the record. Refuses to invent any part of its own provenance."""
    if result.listed not in (LISTED_YES, LISTED_NO, LISTED_UNKNOWN):
        raise SelectionRecordError(f"unknown listed status {result.listed!r}")

    # A record whose provenance is blank cannot later prove which code produced
    # it, which is the whole purpose of storing it.
    for name, value in (
        ("runtime_sha", runtime_sha),
        ("strategy_sha", strategy_sha),
        ("config_hash", config_hash),
        ("rule_hash", rule_hash),
    ):
        if not value:
            raise SelectionRecordError(f"{name} is required; a record without provenance proves nothing")

    # A LISTED verdict must carry the real contract it matched, or it is not a
    # match — it is an assertion.
    if result.listed == LISTED_YES and result.selected_instrument_token is None:
        raise SelectionRecordError("LISTED requires the matched instrument token")

    # Equally, a non-match must not carry a contract it did not match.
    if result.listed != LISTED_YES and result.selected_instrument_token is not None:
        raise SelectionRecordError(f"{result.listed} must not carry a matched instrument")

    if result.listed != LISTED_UNKNOWN and not result.candidate_universe_hash:
        raise SelectionRecordError(
            "a listedness verdict requires the candidate universe hash it was decided against"
        )

    return SelectionRecord(
        opportunity_id=result.opportunity_id,
        spot=result.spot,
        assumed_iv=result.assumed_iv,
        valuation_ts=_iso(valuation_ts),
        target_delta=result.target_delta,
        min_dte=int(min_dte),
        max_dte=int(max_dte),
        option_type=result.option_type,
        computed_expiry=result.expiry,
        computed_strike=result.selected_strike,
        computed_delta=result.computed_delta,
        candidate_universe_hash=result.candidate_universe_hash,
        instrument_master_snapshot_id=instrument_master_snapshot_id,
        listed_status=result.listed,
        matched_instrument_token=result.selected_instrument_token,
        matched_tradingsymbol=result.selected_tradingsymbol,
        matched_exchange=result.selected_exchange,
        matched_lot_size=result.selected_lot_size,
        matched_tick_size=result.selected_tick_size,
        selector_version=result.selector_version,
        runtime_sha=runtime_sha,
        strategy_sha=strategy_sha,
        config_hash=config_hash,
        rule_hash=rule_hash,
        created_at=created_at.isoformat(),
    )
