"""Which instrument a lane actually trades, made a first-class fact.

The same SuperTrend signal is a different economic proposition depending on
what it is expressed in. Bought weekly options pay theta and need a move large
enough to beat the premium; a future pays neither and moves one-for-one. The
7.5-year study that found 0/60 long-option configurations net-positive
out-of-sample, and the same signal modestly positive when represented
delta-1, is the whole reason this module exists: the two results must never be
able to land in one sample.

``rule_hash`` already absorbs vehicle indirectly, because changing the vehicle
changes the rules that produced the hash. That is enough for isolation but not
enough for audit: an operator reading a manifest a year from now must be able
to see ``FUTURES`` without recomputing a hash. So the vehicle is also carried
explicitly, and :func:`assert_same_vehicle` refuses to pool two records that
disagree about it.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any, Final, Iterable, Mapping

from app.core.strategy_identity import stable_hash

__all__ = [
    "ExecutionVehicle",
    "VehiclePolicy",
    "VEHICLE_POLICY_VERSION",
    "VehicleError",
    "canonical_vehicle",
    "is_authoritative_live_vehicle",
    "assert_same_vehicle",
    "partition_by_vehicle",
    "vehicle_manifest_fields",
]

#: Bump when the *meaning* of a vehicle or its policy fields changes. Records
#: written under an older version keep their own stamp; they are not migrated,
#: because a migrated economic record is a rewritten one.
VEHICLE_POLICY_VERSION: Final[str] = "1"


class ExecutionVehicle(StrEnum):
    """What the lane actually sends to a venue."""

    OPTIONS_LONG = "OPTIONS_LONG"
    FUTURES = "FUTURES"
    DEEP_ITM_OPTIONS = "DEEP_ITM_OPTIONS"
    EQUITY = "EQUITY"
    #: Research only. A synthetic fill is a model output, never a broker fact,
    #: so this vehicle can never carry authoritative live execution evidence.
    PAPER_SYNTHETIC = "PAPER_SYNTHETIC"


#: Vehicles whose records may describe a real order at a real venue.
_LIVE_CAPABLE: Final[frozenset[ExecutionVehicle]] = frozenset(
    {
        ExecutionVehicle.OPTIONS_LONG,
        ExecutionVehicle.FUTURES,
        ExecutionVehicle.DEEP_ITM_OPTIONS,
        ExecutionVehicle.EQUITY,
    }
)


class VehicleError(ValueError):
    """An unknown vehicle, or two records that disagree about theirs."""


def canonical_vehicle(value: str | ExecutionVehicle | None) -> ExecutionVehicle:
    if isinstance(value, ExecutionVehicle):
        return value
    text = str(value or "").strip().upper()
    if not text:
        raise VehicleError("execution_vehicle is required; there is no default vehicle")
    try:
        return ExecutionVehicle(text)
    except ValueError:
        raise VehicleError(
            f"unknown execution_vehicle {value!r}; expected one of "
            f"{', '.join(v.value for v in ExecutionVehicle)}"
        ) from None


def is_authoritative_live_vehicle(value: str | ExecutionVehicle | None) -> bool:
    """May a record in this vehicle ever describe real broker execution?"""
    try:
        return canonical_vehicle(value) in _LIVE_CAPABLE
    except VehicleError:
        return False


@dataclass(frozen=True)
class VehiclePolicy:
    """The frozen, predeclared contract for how one lane reaches the market.

    Every field here is something a broker default would otherwise decide
    silently. ``product`` is the clearest case: inheriting NRML or MIS from the
    account turns an intraday lane into an overnight one on a day somebody
    changed a setting, and the evidence would not say so.
    """

    vehicle: ExecutionVehicle

    #: Exact instrument family, e.g. ``NIFTY_FUT`` or ``NIFTY_WEEKLY_OPT``.
    #: Frozen text, not a lookup: the manifest must name the instrument.
    instrument_class: str

    #: NRML / MIS / CNC. Never inferred from the broker account.
    product: str

    #: Predeclared expiry and roll behaviour. Required for futures; for options
    #: it records the DTE window the lane is allowed to select inside.
    roll_policy: str

    #: Does the vehicle permit shorting? A long-only options wrapper cannot
    #: express a bear signal, and that asymmetry is part of the economics.
    allows_short: bool = False

    #: Research-only policies can never be promoted to real capital, whatever
    #: their evidence says.
    research_only: bool = False

    notes: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "vehicle", canonical_vehicle(self.vehicle))
        for field in ("instrument_class", "product", "roll_policy"):
            if not str(getattr(self, field) or "").strip():
                raise VehicleError(
                    f"{field} is required: a vehicle policy that leaves it to the "
                    "broker default is not a frozen policy"
                )
        if self.vehicle is ExecutionVehicle.PAPER_SYNTHETIC and not self.research_only:
            raise VehicleError(
                "PAPER_SYNTHETIC is research only and cannot be marked otherwise"
            )

    @property
    def vehicle_contract_hash(self) -> str:
        """Hash over the whole policy. Changing any field is a new identity."""
        payload = asdict(self)
        payload["vehicle"] = self.vehicle.value
        payload["vehicle_policy_version"] = VEHICLE_POLICY_VERSION
        return stable_hash(payload)

    @property
    def live_capable(self) -> bool:
        return (not self.research_only) and self.vehicle in _LIVE_CAPABLE

    def as_row(self) -> dict[str, Any]:
        """The fields a manifest and every authoritative record must carry."""
        row = asdict(self)
        row["vehicle"] = self.vehicle.value
        row["execution_vehicle"] = self.vehicle.value
        row["vehicle_policy_version"] = VEHICLE_POLICY_VERSION
        row["vehicle_contract_hash"] = self.vehicle_contract_hash
        row["live_capable"] = self.live_capable
        return row


#: The vehicle each existing lane runs today. A lane absent from this map has
#: no declared vehicle, which is a refusal, not a default.
DEFAULT_LANE_VEHICLES: Final[Mapping[str, ExecutionVehicle]] = {
    "snapback:ultra_scalping": ExecutionVehicle.OPTIONS_LONG,
    "snapback:scalping": ExecutionVehicle.OPTIONS_LONG,
    "snapback:intraday": ExecutionVehicle.OPTIONS_LONG,
    "snapback:overnight": ExecutionVehicle.OPTIONS_LONG,
    "snapback:swing": ExecutionVehicle.OPTIONS_LONG,
    "supertrend:ultra_scalping": ExecutionVehicle.OPTIONS_LONG,
    "supertrend:scalping": ExecutionVehicle.OPTIONS_LONG,
    "supertrend:intraday": ExecutionVehicle.OPTIONS_LONG,
    "supertrend:overnight": ExecutionVehicle.OPTIONS_LONG,
    "supertrend:swing": ExecutionVehicle.OPTIONS_LONG,
}


def lane_vehicle(lane_key: str) -> ExecutionVehicle:
    try:
        return DEFAULT_LANE_VEHICLES[lane_key]
    except KeyError:
        raise VehicleError(
            f"lane {lane_key!r} has no declared execution vehicle; declare one "
            "before it can originate or record evidence"
        ) from None


def vehicle_manifest_fields(policy: VehiclePolicy) -> dict[str, Any]:
    """The three first-class fields the release manifest exposes."""
    return {
        "execution_vehicle": policy.vehicle.value,
        "vehicle_policy_version": VEHICLE_POLICY_VERSION,
        "vehicle_contract_hash": policy.vehicle_contract_hash,
    }


def record_vehicle(row: Mapping[str, Any]) -> ExecutionVehicle | None:
    """Read a record's vehicle, or ``None`` when it never declared one."""
    raw = row.get("execution_vehicle") or row.get("vehicle")
    if raw in (None, ""):
        return None
    return canonical_vehicle(raw)


def assert_same_vehicle(rows: Iterable[Mapping[str, Any]], *, context: str = "") -> ExecutionVehicle | None:
    """Refuse to pool records that were executed through different vehicles.

    Returns the single shared vehicle, or ``None`` for an empty sample. A row
    that declares no vehicle is a refusal too: "probably options" is not a fact
    a promotion decision may rest on.
    """
    seen: set[ExecutionVehicle] = set()
    undeclared = 0
    for row in rows:
        vehicle = record_vehicle(row)
        if vehicle is None:
            undeclared += 1
            continue
        seen.add(vehicle)

    where = f" in {context}" if context else ""
    if undeclared:
        raise VehicleError(
            f"{undeclared} record(s){where} declare no execution_vehicle; they "
            "cannot be pooled with records that do"
        )
    if len(seen) > 1:
        names = ", ".join(sorted(v.value for v in seen))
        raise VehicleError(
            f"records{where} span more than one execution vehicle ({names}); "
            "vehicles are separate experiments and are never pooled"
        )
    return next(iter(seen), None)


def partition_by_vehicle(
    rows: Iterable[Mapping[str, Any]],
) -> dict[str, list[Mapping[str, Any]]]:
    """Split a mixed sample by vehicle. Undeclared rows land under ``""``."""
    out: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        vehicle = record_vehicle(row)
        out.setdefault(vehicle.value if vehicle else "", []).append(row)
    return out
