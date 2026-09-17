"""The identity every authoritative record must carry.

An evidence row without an identity is worthless: nobody can say later which
rules produced it. Worse, rows from two different rule sets pool into one
sample and manufacture significance that was never measured. So identity is
mandatory, immutable, and hashed.

Changing any strategy rule or mode rule produces a NEW identity. It never
edits an existing one — a Track-A frozen lane must stay frozen while a
challenger runs beside it.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any, Mapping

from app.core.horizon import HorizonMode, canonical_mode
from app.core.focus import FOCUSABLE

#: 16 hex characters, matching the existing Snapback manifest hashes so old and
#: new rows are visually comparable in the same report.
_HASH_LEN = 16

#: Bumped whenever the shape of an authoritative row changes.
EVIDENCE_SCHEMA_VERSION = "3"

#: A — the frozen prospective strategy, never tuned after outcomes are seen.
#: B — execution reality: spread, depth, fillability, slippage, margin.
#: C — challengers. A challenger gets its own identity and must never overwrite
#:     or pool with the Track-A lane it is challenging.
VALID_TRACKS: frozenset[str] = frozenset({"A", "B", "C"})


def challenger_id(base_mode_version: str, slug: str) -> str:
    """``snapback_swing_v1`` + ``c01_dte30_45`` -> ``snapback_swing_v1_c01_dte30_45``.

    The slug must say what changed. A challenger called ``c02`` is unreadable
    six months later, which is when someone asks why two lanes disagree.
    """
    cleaned = slug.strip().lower()
    if not cleaned:
        raise IdentityError("a challenger needs a slug naming what it changed")
    if not cleaned.startswith("c") or "_" not in cleaned:
        raise IdentityError(
            f"challenger slug {slug!r} must look like c01_<what_changed>"
        )
    return f"{base_mode_version}_{cleaned}"


class IdentityError(ValueError):
    """The identity is incomplete or internally inconsistent."""


def stable_hash(payload: Any) -> str:
    """Deterministic short hash of any JSON-encodable payload.

    ``sort_keys`` matters more than it looks: without it two identical configs
    built in a different field order would hash differently and split one
    experiment into two undersized ones.
    """
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:_HASH_LEN]


@dataclass(frozen=True)
class StrategyModeIdentity:
    """Identifies one ``(strategy, mode)`` lane at one exact rule revision."""

    strategy_id: str
    strategy_version: str
    mode: HorizonMode
    mode_version: str

    runtime_sha: str
    release_tag: str

    config_hash: str
    rule_hash: str

    #: Which instruments the lane was allowed to look at. Two runs of identical
    #: rules over different universes are different experiments: widening the
    #: universe mid-sample adds opportunities the earlier trades never had, and
    #: pooling them overstates the sample.
    universe_hash: str = ""

    #: Which track this identity belongs to. A challenger must never overwrite
    #: or pool with the frozen prospective strategy it is challenging.
    track: str = "A"

    evidence_schema_version: str = EVIDENCE_SCHEMA_VERSION
    #: The original spelling, when this lane was reached through a legacy alias.
    #: Kept for traceability; never used for grouping.
    legacy_mode: str | None = None

    def __post_init__(self) -> None:
        if self.strategy_id not in FOCUSABLE:
            raise IdentityError(
                f"strategy_id {self.strategy_id!r} is not one of {sorted(FOCUSABLE)}"
            )
        if not isinstance(self.mode, HorizonMode):
            # Normalising here rather than refusing would let a legacy spelling
            # reach the hash, so the caller must canonicalise first.
            raise IdentityError(
                f"mode must be a HorizonMode, got {type(self.mode).__name__}; "
                "call app.core.horizon.canonical_mode() first"
            )
        for field in (
            "strategy_version",
            "mode_version",
            "runtime_sha",
            "release_tag",
            "config_hash",
            "rule_hash",
        ):
            if not str(getattr(self, field) or "").strip():
                raise IdentityError(f"{field} is required and must be non-empty")
        if self.runtime_sha.upper() == "UNKNOWN":
            raise IdentityError(
                "runtime_sha is UNKNOWN: a row that cannot name the build that "
                "produced it is not authoritative evidence"
            )

    @property
    def lane_key(self) -> str:
        """``snapback:swing`` — the grouping key for every promotion report."""
        return f"{self.strategy_id}:{self.mode.value}"

    @property
    def identity_hash(self) -> str:
        """One hash over everything that defines this experiment.

        ``legacy_mode`` is excluded on purpose: it is provenance, not a rule,
        and two rows that differ only by how the caller spelled the mode are
        the same experiment.
        """
        payload = asdict(self)
        payload.pop("legacy_mode", None)
        payload["mode"] = self.mode.value
        return stable_hash(payload)

    @property
    def is_challenger(self) -> bool:
        return self.track == "C"

    def as_row(self) -> dict[str, Any]:
        """The columns an authoritative evidence row must carry."""
        row = asdict(self)
        row["mode"] = self.mode.value
        row["lane_key"] = self.lane_key
        row["identity_hash"] = self.identity_hash
        return row


def build_identity(
    *,
    strategy_id: str,
    strategy_version: str,
    mode: str | HorizonMode,
    mode_version: str,
    runtime_sha: str,
    release_tag: str,
    config: Mapping[str, Any] | None = None,
    rules: Mapping[str, Any] | None = None,
    config_hash: str | None = None,
    rule_hash: str | None = None,
    universe: Any = None,
    universe_hash: str | None = None,
    track: str = "A",
    challenger: str = "",
) -> StrategyModeIdentity:
    """Build an identity, canonicalising the mode and hashing the rules.

    Pass either the raw ``config``/``rules`` mappings (they get hashed here) or
    pre-computed hashes from an engine that already maintains its own manifest.
    """
    from app.core.horizon import legacy_mode_of

    resolved_mode = canonical_mode(mode)
    legacy = legacy_mode_of(mode) if isinstance(mode, str) else None

    if config_hash is None:
        if config is None:
            raise IdentityError("supply either config or config_hash")
        config_hash = stable_hash(dict(config))
    if rule_hash is None:
        if rules is None:
            raise IdentityError("supply either rules or rule_hash")
        rule_hash = stable_hash(dict(rules))
    if universe_hash is None:
        universe_hash = stable_hash(sorted(universe)) if universe is not None else ""
    if track not in VALID_TRACKS:
        raise IdentityError(
            f"track must be one of {sorted(VALID_TRACKS)}, got {track!r}"
        )
    if challenger:
        mode_version = f"{mode_version}_{challenger}"

    return StrategyModeIdentity(
        strategy_id=strategy_id.strip().lower(),
        strategy_version=strategy_version,
        mode=resolved_mode,
        mode_version=mode_version,
        runtime_sha=runtime_sha,
        release_tag=release_tag,
        config_hash=config_hash,
        rule_hash=rule_hash,
        universe_hash=universe_hash,
        track=track,
        legacy_mode=legacy,
    )
