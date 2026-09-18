"""What "a fresh authoritative sample" has to mean before it means anything.

The new release starts a new forward sample. The old rows are not deleted —
an evidence store is append-only, and years of recorded market behaviour is
worth keeping — but they are not the new sample either, and the difference has
to be checkable rather than asserted in a runbook.

Two questions live here.

The first is whether the sample has actually started at zero: no lane sessions,
no authoritative trades, no unresolved exposure, no identity drift, and an exact
release tag and runtime SHA. A start that is "nearly zero" is a start that
inherited something, and what it inherited is precisely what nobody will
remember when the number is read a year later.

The second is whether each row can say where it came from. Thirteen fields, all
required: without them a row cannot be attributed to a lane, re-derived from its
configuration, or compared against a later revision. A row missing any of them
is recorded as incomplete rather than quietly counted — and, because the point
is to catch this before the sample matters, the audit reports counts by field so
a missing writer shows up as a column rather than as one sad row.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Final, Iterable, Mapping, Sequence

__all__ = [
    "REQUIRED_ROW_FIELDS", "EVIDENCE_SCHEMA_VERSION",
    "missing_fields", "RowAudit", "audit_rows",
    "AuthoritativeStart", "evaluate_authoritative_start", "render_start",
]

#: Bumped when the required set below changes. A row carries the version it was
#: written under, so a reader can tell "this field did not exist yet" from "the
#: writer forgot it" — the two look identical in an empty column.
EVIDENCE_SCHEMA_VERSION: Final[str] = "3"

#: Every authoritative row must carry all of these. The order is the order the
#: specification lists them in, so the two can be read side by side.
REQUIRED_ROW_FIELDS: Final[tuple[str, ...]] = (
    "strategy_id",
    "strategy_version",
    "mode",
    "mode_version",
    "lane_key",
    "identity_hash",
    "runtime_sha",
    "release_tag",
    "config_hash",
    "rule_hash",
    "universe_hash",
    "evidence_schema_version",
    # Which vehicle produced the row, and which execution regime it belongs to.
    # The same signal in bought options and in futures is two experiments, and
    # a paper row and a broker row answer two different questions — neither
    # distinction survives if the row does not carry it.
    "execution_vehicle",
    "execution_regime",
)
#: Fourteen, matching the specification's list exactly. `evidence_class` is not
#: a fourteenth field: it is how the warehouse spells `execution_regime`, and
#: requiring both would have counted one column twice.

#: The warehouse stores the runtime SHA under its build-era name. Reading either
#: spelling keeps the rule about the row, not about the column name.
_ALIASES: Final[dict[str, tuple[str, ...]]] = {
    "runtime_sha": ("runtime_sha", "runtime_build_sha"),
    # The regime is the evidence class by another name: the warehouse stores
    # `evidence_class`, and PAPER/SHADOW/BROKER are exactly the three regimes.
    "execution_regime": ("execution_regime", "evidence_class"),
}


def _value(row: Mapping[str, Any], name: str) -> str:
    for key in _ALIASES.get(name, (name,)):
        raw = row.get(key)
        if raw is not None and str(raw).strip():
            return str(raw).strip()
    return ""


def missing_fields(row: Mapping[str, Any]) -> tuple[str, ...]:
    """Which required fields this row cannot answer. Empty means complete."""
    return tuple(name for name in REQUIRED_ROW_FIELDS if not _value(row, name))


@dataclass(frozen=True)
class RowAudit:
    """How many rows were complete, and which field each incomplete one lacked."""

    total: int
    complete: int
    missing_by_field: dict[str, int] = field(default_factory=dict)
    incomplete_examples: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.total > 0 and self.complete == self.total

    def as_dict(self) -> dict[str, Any]:
        return {"total": self.total, "complete": self.complete, "ok": self.ok,
                "missing_by_field": dict(self.missing_by_field),
                "incomplete_examples": list(self.incomplete_examples)}


def audit_rows(rows: Iterable[Mapping[str, Any]], *, id_field: str = "outcome_id",
               examples: int = 5) -> RowAudit:
    """Audit authoritative rows for complete provenance.

    Non-authoritative rows are skipped rather than failed: the requirement is
    about what counts toward a gate, and a non-authoritative row counts toward
    nothing.
    """
    total = complete = 0
    missing_by_field: dict[str, int] = {}
    incomplete: list[str] = []

    for row in rows:
        if not int(row.get("authoritative") or 0):
            continue
        total += 1
        gaps = missing_fields(row)
        if not gaps:
            complete += 1
            continue
        for name in gaps:
            missing_by_field[name] = missing_by_field.get(name, 0) + 1
        if len(incomplete) < examples:
            incomplete.append(str(row.get(id_field) or "<no id>"))

    return RowAudit(total=total, complete=complete,
                    missing_by_field=missing_by_field,
                    incomplete_examples=tuple(incomplete))


@dataclass(frozen=True)
class StartCondition:
    name: str
    satisfied: bool | None
    detail: str = ""

    @property
    def verdict(self) -> str:
        return {True: "ok", False: "NOT ZERO", None: "UNKNOWN"}[self.satisfied]


@dataclass(frozen=True)
class AuthoritativeStart:
    """Whether this store is a fresh authoritative sample for this release."""

    release_tag: str
    runtime_sha: str
    conditions: tuple[StartCondition, ...]
    row_audit: RowAudit | None = None

    @property
    def started_clean(self) -> bool:
        """True only when every condition was checked and every one is zero."""
        return bool(self.conditions) and all(c.satisfied is True for c in self.conditions)

    @property
    def unknowns(self) -> tuple[StartCondition, ...]:
        return tuple(c for c in self.conditions if c.satisfied is None)

    def as_dict(self) -> dict[str, Any]:
        return {
            "release_tag": self.release_tag,
            "runtime_sha": self.runtime_sha,
            "started_clean": self.started_clean,
            "conditions": [{"name": c.name, "satisfied": c.satisfied,
                            "verdict": c.verdict, "detail": c.detail}
                           for c in self.conditions],
            "row_audit": self.row_audit.as_dict() if self.row_audit else None,
        }


def evaluate_authoritative_start(
    *,
    lane_sessions: int | None,
    authoritative_trades: int | None,
    unresolved_exposure: int | None,
    identity_drift: int | None,
    release_tag: str,
    runtime_sha: str,
    rows: Sequence[Mapping[str, Any]] | None = None,
) -> AuthoritativeStart:
    """Judge a store against the authoritative-start contract.

    Every count is nullable and a null is UNKNOWN, never zero. "We could not
    read the unresolved exposure" and "there is no unresolved exposure" are the
    two answers this whole module exists to keep apart.
    """
    def _zero(name: str, value: int | None) -> StartCondition:
        if value is None:
            return StartCondition(name, None, "could not be read")
        if value == 0:
            return StartCondition(name, True, "0")
        return StartCondition(name, False, str(value))

    conditions = [
        _zero("lane_sessions", lane_sessions),
        _zero("authoritative_trades", authoritative_trades),
        _zero("unresolved_exposure", unresolved_exposure),
        _zero("identity_drift", identity_drift),
        StartCondition("release_tag", bool(release_tag.strip()) or None,
                       release_tag or "no immutable release tag"),
        StartCondition("runtime_sha",
                       True if len(runtime_sha.strip()) == 40 else
                       (None if not runtime_sha.strip() else False),
                       runtime_sha or "no runtime SHA recorded"),
    ]

    return AuthoritativeStart(
        release_tag=release_tag,
        runtime_sha=runtime_sha,
        conditions=tuple(conditions),
        row_audit=audit_rows(rows) if rows is not None else None,
    )


def render_start(start: AuthoritativeStart) -> str:
    lines = ["AUTHORITATIVE START"]
    for condition in start.conditions:
        lines.append(f"  {condition.name:<24}{condition.verdict:<10}{condition.detail}")

    audit = start.row_audit
    if audit is not None:
        lines.append("")
        lines.append(f"  authoritative rows       {audit.total}")
        lines.append(f"  complete provenance      {audit.complete}")
        for name, count in sorted(audit.missing_by_field.items(), key=lambda kv: -kv[1]):
            lines.append(f"    missing {name:<26}{count}")
        if audit.incomplete_examples:
            lines.append("    examples: " + ", ".join(audit.incomplete_examples))

    lines.append("")
    if start.started_clean:
        lines.append("This store is a fresh authoritative sample for this release.")
    elif start.unknowns:
        lines.append("NOT a verified fresh start: "
                     + ", ".join(c.name for c in start.unknowns)
                     + " could not be read. Unknown is not zero.")
    else:
        carried = [c.name for c in start.conditions if c.satisfied is False]
        lines.append("NOT a fresh start: " + ", ".join(carried)
                     + " carried something over from before this release.")
    return "\n".join(lines)
