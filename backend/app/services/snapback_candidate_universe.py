"""The menu the selector had, recorded before anyone asks what it chose.

Storing only the contracts near the chosen one makes the evidence circular: the
selector decides which contracts survive to audit its own decision. So this
module persists the entire eligible listed universe for an opportunity — every
real contract that satisfied the frozen eligibility rule — and the choice is
recorded separately.

Everything here comes from a real instrument master. Nothing is reconstructed.
The quarantined observed replay generated an arithmetic strike ladder and
derived expiries from "last Thursday", and both inventions are indistinguishable
from real contracts once written down. A missing master is INCONCLUSIVE; it is
never a ladder.

The hash is load-bearing. An auditor recomputes it from the persisted contracts
and compares it to the one stored in the selection record; if they differ, the
universe was edited after the fact and the trade is not authoritative whatever
its P&L says.

One thing this module exists to measure: ``pick_for`` computes a strike from
``strike_for_delta`` and rounds it to the instrument's strike step. It never
consults a chain. Whether that computed strike was actually listed is unknown
today, and becomes answerable only once the real universe is on record beside it.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, asdict
from datetime import date, datetime
from typing import Any, Iterable, Optional, Sequence

__all__ = [
    "CandidateContract",
    "CandidateUniverse",
    "CandidateUniverseError",
    "SOURCE_INSTRUMENT_MASTER",
    "build_candidate_universe",
    "universe_hash",
]

SOURCE_INSTRUMENT_MASTER = "KITE_INSTRUMENT_MASTER"

#: Bumped whenever the eligibility rule or the hashed shape changes, so an old
#: universe can never be silently compared against a new rule.
UNIVERSE_SCHEMA_VERSION = 1


class CandidateUniverseError(ValueError):
    """The universe cannot be built from real data, so there is no universe."""


@dataclass(frozen=True)
class CandidateContract:
    """One really listed contract, exactly as the exchange published it."""

    instrument_token: int
    tradingsymbol: str
    exchange: str
    segment: str
    instrument_type: str          # "CE" | "PE" | "FUT"
    expiry: str                   # ISO date, as published
    strike: float
    lot_size: int
    tick_size: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _as_iso_date(value: Any) -> Optional[str]:
    """Accept what an instrument master actually contains; invent nothing."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value).strip()
    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        return None


def _dte(expiry: str, as_of: date) -> int:
    return (date.fromisoformat(expiry) - as_of).days


def universe_hash(
    contracts: Sequence[CandidateContract],
    *,
    underlying: str,
    option_type: str,
    as_of: str,
    min_dte: int,
    max_dte: int,
) -> str:
    """Deterministic over content, independent of the order rows arrived in.

    Includes the eligibility bounds, not just the contracts: the same list of
    contracts filtered under different rules is not the same evidence, and a hash
    that ignored the rule would let one universe vouch for another.
    """
    payload = {
        "schema_version": UNIVERSE_SCHEMA_VERSION,
        "underlying": underlying,
        "option_type": option_type,
        "as_of": as_of,
        "min_dte": int(min_dte),
        "max_dte": int(max_dte),
        "contracts": sorted(
            (
                [
                    int(c.instrument_token),
                    c.tradingsymbol,
                    c.exchange,
                    c.segment,
                    c.instrument_type,
                    c.expiry,
                    # Repr-stable: float formatting must not move the hash.
                    f"{float(c.strike):.4f}",
                    int(c.lot_size),
                    f"{float(c.tick_size):.4f}",
                ]
                for c in contracts
            )
        ),
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CandidateUniverse:
    """Every eligible listed contract for one opportunity, and its fingerprint."""

    opportunity_id: str
    underlying: str
    option_type: str
    as_of: str
    min_dte: int
    max_dte: int
    contracts: tuple[CandidateContract, ...]
    instrument_master_date: str
    source: str
    evidence_class: str
    candidate_universe_hash: str

    @property
    def expiries(self) -> tuple[str, ...]:
        return tuple(sorted({c.expiry for c in self.contracts}))

    @property
    def strikes(self) -> tuple[float, ...]:
        return tuple(sorted({float(c.strike) for c in self.contracts}))

    def contains_strike(self, strike: float, *, expiry: Optional[str] = None) -> bool:
        """Was this strike actually listed? The question ``pick_for`` assumes."""
        for c in self.contracts:
            if expiry is not None and c.expiry != expiry:
                continue
            if abs(float(c.strike) - float(strike)) < 1e-6:
                return True
        return False

    def verify_hash(self) -> bool:
        """True when the stored fingerprint still matches the stored contracts."""
        return self.candidate_universe_hash == universe_hash(
            self.contracts,
            underlying=self.underlying,
            option_type=self.option_type,
            as_of=self.as_of,
            min_dte=self.min_dte,
            max_dte=self.max_dte,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "opportunity_id": self.opportunity_id,
            "underlying": self.underlying,
            "option_type": self.option_type,
            "as_of": self.as_of,
            "min_dte": self.min_dte,
            "max_dte": self.max_dte,
            "instrument_master_date": self.instrument_master_date,
            "source": self.source,
            "evidence_class": self.evidence_class,
            "candidate_universe_hash": self.candidate_universe_hash,
            "contract_count": len(self.contracts),
            "contracts": [c.as_dict() for c in self.contracts],
        }


def build_candidate_universe(
    *,
    opportunity_id: str,
    underlying: str,
    option_type: str,
    as_of: date | str,
    instruments: Iterable[dict[str, Any]],
    instrument_master_date: date | str,
    min_dte: int,
    max_dte: int,
    evidence_class: str = "OBSERVED_MARKET",
    exchanges: Optional[Sequence[str]] = None,
) -> CandidateUniverse:
    """Filter a real instrument master down to what the frozen rule permits.

    Rows missing an expiry, a strike, a lot size or a token are dropped rather
    than repaired: a contract we cannot fully describe cannot be part of a proof
    that the selector had the right menu. An empty result raises, because "no
    eligible contracts" and "we failed to read the master" must not both arrive
    as an empty list that later reads as a clean universe.
    """
    from app.services.snapback_evidence_class import parse as parse_evidence_class

    if option_type not in ("CE", "PE"):
        raise CandidateUniverseError(f"option_type must be CE or PE, got {option_type!r}")
    if max_dte < min_dte:
        raise CandidateUniverseError(f"max_dte ({max_dte}) is below min_dte ({min_dte})")

    as_of_date = as_of if isinstance(as_of, date) else date.fromisoformat(str(as_of)[:10])
    master_date = _as_iso_date(instrument_master_date)
    if master_date is None:
        raise CandidateUniverseError("instrument_master_date is required; an undated master proves nothing")

    allowed = {e.upper() for e in exchanges} if exchanges else None
    kept: list[CandidateContract] = []
    seen: set[int] = set()

    for row in instruments:
        if str(row.get("instrument_type") or "").upper() != option_type:
            continue
        if str(row.get("name") or row.get("underlying") or "").upper() != underlying.upper():
            continue

        exchange = str(row.get("exchange") or "").upper()
        if allowed is not None and exchange not in allowed:
            continue

        expiry = _as_iso_date(row.get("expiry"))
        token = row.get("instrument_token")
        strike = row.get("strike")
        lot_size = row.get("lot_size")
        if expiry is None or token is None or strike is None or lot_size is None:
            continue

        try:
            dte = _dte(expiry, as_of_date)
        except ValueError:
            continue
        if not (min_dte <= dte <= max_dte):
            continue

        try:
            contract = CandidateContract(
                instrument_token=int(token),
                tradingsymbol=str(row.get("tradingsymbol") or ""),
                exchange=exchange,
                segment=str(row.get("segment") or ""),
                instrument_type=option_type,
                expiry=expiry,
                strike=float(strike),
                lot_size=int(lot_size),
                tick_size=float(row.get("tick_size") or 0.05),
            )
        except (TypeError, ValueError):
            continue

        if contract.instrument_token in seen:
            continue
        seen.add(contract.instrument_token)
        kept.append(contract)

    if not kept:
        raise CandidateUniverseError(
            f"no eligible listed {option_type} contracts for {underlying} at {as_of_date.isoformat()} "
            f"within {min_dte}-{max_dte} DTE; this is INCONCLUSIVE, not an empty universe"
        )

    contracts = tuple(sorted(kept, key=lambda c: (c.expiry, c.strike, c.instrument_token)))
    as_of_iso = as_of_date.isoformat()

    return CandidateUniverse(
        opportunity_id=opportunity_id,
        underlying=underlying.upper(),
        option_type=option_type,
        as_of=as_of_iso,
        min_dte=int(min_dte),
        max_dte=int(max_dte),
        contracts=contracts,
        instrument_master_date=master_date,
        source=SOURCE_INSTRUMENT_MASTER,
        evidence_class=parse_evidence_class(evidence_class),
        candidate_universe_hash=universe_hash(
            contracts,
            underlying=underlying.upper(),
            option_type=option_type,
            as_of=as_of_iso,
            min_dte=int(min_dte),
            max_dte=int(max_dte),
        ),
    )
