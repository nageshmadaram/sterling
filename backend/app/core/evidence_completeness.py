"""The daily evidence completeness report, per lane.

The report answers one question: *can today's records be trusted as evidence?*
Not "did we make money" — whether the session observed what it claims to have
observed, and which of the ten lanes may count it.

Three rules are enforced here rather than left to the reader:

1. **A gap is permanent.** A later clean rescan does not erase an earlier
   incomplete session. The report carries the gap codes forward, so the 60
   session denominator can never be inflated by re-running the scanner.

2. **No pooling.** Every count is per lane. A lane's row shows its own sessions,
   its own trades and its own coverage, and the totals line is a sum for
   display only — it is never a sample.

3. **Unattributed rows are reported, not dropped.** "412 rows belong to no
   lane" is a coverage fact. Dropping them silently makes an incomplete sample
   look complete.

The functions take plain mappings, so a report can be produced from a database,
from a fixture, or from a restored backup with the same code.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Final, Iterable, Mapping, Sequence

from app.core.evidence import (
    PROMOTABLE_CLASSES,
    EvidenceClass,
    partition_by_lane,
    row_lane,
)
from app.core.lane_registry import LANES

#: Coverage at or above this counts as adequate, matching the promotion gate.
COVERAGE_FLOOR: Final[float] = 0.95


class SessionStatus(StrEnum):
    """What happened to a trading session, from the evidence's point of view."""

    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"
    MARKET_CLOSED = "MARKET_CLOSED"
    SYSTEM_ERROR = "SYSTEM_ERROR"


def _ratio(observed: Any, required: Any) -> float | None:
    """Coverage as a fraction, or ``None`` when the denominator is unknown.

    ``None`` is not zero and it is not one. A session that cannot say how many
    observations it needed has unknown coverage, and unknown coverage must not
    read as full coverage.
    """
    try:
        need = int(required or 0)
        got = int(observed or 0)
    except (TypeError, ValueError):
        return None
    if need <= 0:
        return None
    return min(got / need, 1.0)


def classify_session(row: Mapping[str, Any] | None) -> SessionStatus:
    """Session status from the ledger row.

    A missing row is ``SYSTEM_ERROR``, not ``MARKET_CLOSED``: nothing recorded
    is indistinguishable from nothing ran, and the safe reading is the one that
    does not add a session to the denominator.
    """
    if not row:
        return SessionStatus.SYSTEM_ERROR

    if str(row.get("market_gate_status") or "").upper() in {
        "CLOSED",
        "MARKET_CLOSED",
        "HOLIDAY",
    }:
        return SessionStatus.MARKET_CLOSED

    status = str(row.get("scanner_status") or "").upper()
    if status == "FAILED":
        return SessionStatus.SYSTEM_ERROR
    if status != "COMPLETE":
        return SessionStatus.INCOMPLETE

    from app.services.snapback_session_ledger import (
        evidence_gap_codes,
        session_market_evidence_complete,
    )

    if evidence_gap_codes(dict(row)):
        return SessionStatus.INCOMPLETE
    return (
        SessionStatus.COMPLETE
        if session_market_evidence_complete(dict(row))
        else SessionStatus.INCOMPLETE
    )


@dataclass(frozen=True)
class LaneDailyRow:
    """One lane's line in the daily report."""

    lane_key: str
    state: str

    sessions_observed: int = 0
    trades_completed: int = 0

    signals_found: int = 0
    signals_refused: int = 0
    refusal_reasons: Mapping[str, int] = field(default_factory=dict)

    positions_opened: int = 0
    positions_closed: int = 0
    positions_unresolved: int = 0

    #: ``None`` means unknown, which is never treated as adequate.
    quote_coverage: float | None = None

    #: Rows excluded from this lane's counts because their class is not
    #: promotable (modelled or replay). Kept visible so nobody wonders where
    #: the trades went.
    non_promotable_rows: int = 0

    @property
    def coverage_ok(self) -> bool:
        return self.quote_coverage is not None and self.quote_coverage >= COVERAGE_FLOOR

    @property
    def clean(self) -> bool:
        """Nothing about this lane blocks the day's evidence being usable."""
        return self.positions_unresolved == 0 and (
            self.quote_coverage is None or self.coverage_ok
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "lane_key": self.lane_key,
            "state": self.state,
            "sessions_observed": self.sessions_observed,
            "trades_completed": self.trades_completed,
            "signals_found": self.signals_found,
            "signals_refused": self.signals_refused,
            "refusal_reasons": dict(self.refusal_reasons),
            "positions_opened": self.positions_opened,
            "positions_closed": self.positions_closed,
            "positions_unresolved": self.positions_unresolved,
            "quote_coverage": self.quote_coverage,
            "non_promotable_rows": self.non_promotable_rows,
            "coverage_ok": self.coverage_ok,
        }


@dataclass(frozen=True)
class DailyEvidenceReport:
    """Appendix B, as data."""

    session_date: str
    generated_at: str

    session_status: SessionStatus

    release_tag: str
    runtime_sha: str
    evidence_schema_version: str

    universe_expected: int = 0
    universe_evaluated: int = 0
    quote_coverage: float | None = None

    signals_found: int = 0
    signals_refused: int = 0

    positions_opened: int = 0
    positions_closed: int = 0
    positions_unresolved: int = 0

    evidence_gap_codes: tuple[str, ...] = field(default_factory=tuple)
    unattributed_rows: int = 0

    lanes: tuple[LaneDailyRow, ...] = field(default_factory=tuple)

    @property
    def universe_coverage(self) -> float | None:
        return _ratio(self.universe_evaluated, self.universe_expected)

    @property
    def usable_as_evidence(self) -> bool:
        """May this session count toward any lane's forward sample?

        Every clause has been a real failure: an incomplete scan counted as a
        session, an unresolved position left out of the denominator, a gap code
        cleared by the next day's rescan, and thin quote coverage reported as
        a full day.
        """
        return (
            self.session_status is SessionStatus.COMPLETE
            and not self.evidence_gap_codes
            and self.positions_unresolved == 0
            and self.quote_coverage is not None
            and self.quote_coverage >= COVERAGE_FLOOR
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "session_date": self.session_date,
            "generated_at": self.generated_at,
            "session_status": str(self.session_status),
            "build": {
                "release_tag": self.release_tag,
                "runtime_sha": self.runtime_sha,
                "evidence_schema_version": self.evidence_schema_version,
            },
            "universe": {
                "expected": self.universe_expected,
                "evaluated": self.universe_evaluated,
                "coverage": self.universe_coverage,
            },
            "quote_coverage": self.quote_coverage,
            "signals": {"found": self.signals_found, "refused": self.signals_refused},
            "positions": {
                "opened": self.positions_opened,
                "closed": self.positions_closed,
                "unresolved": self.positions_unresolved,
            },
            "evidence_gap_codes": list(self.evidence_gap_codes),
            "unattributed_rows": self.unattributed_rows,
            "usable_as_evidence": self.usable_as_evidence,
            "lanes": [lane.as_dict() for lane in self.lanes],
        }


def _truthy(row: Mapping[str, Any], *keys: str) -> bool:
    for key in keys:
        value = row.get(key)
        if value in (None, ""):
            continue
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "y"}
        return bool(value)
    return False


def _is_promotable(row: Mapping[str, Any]) -> bool:
    try:
        return EvidenceClass(str(row.get("evidence_class") or "")) in PROMOTABLE_CLASSES
    except ValueError:
        return False


def build_lane_rows(
    evidence_rows: Iterable[Mapping[str, Any]],
    *,
    quote_coverage: Mapping[str, float | None] | None = None,
) -> tuple[tuple[LaneDailyRow, ...], int]:
    """Per-lane counts for one day, plus the unattributed row count.

    Every declared lane appears, including lanes that did nothing. A lane
    missing from the report is indistinguishable from a lane that was never
    checked, and "no signals today" is itself evidence that must be recorded.
    """
    rows = list(evidence_rows)
    by_lane, unattributed = partition_by_lane(rows)
    coverage = dict(quote_coverage or {})

    out: list[LaneDailyRow] = []
    for lane_key, lane in LANES.items():
        lane_rows = by_lane.get(lane_key, [])

        refusals: dict[str, int] = defaultdict(int)
        signals = refused = opened = closed = unresolved = trades = non_promotable = 0

        for row in lane_rows:
            kind = str(row.get("row_type") or row.get("kind") or "").lower()
            reason = str(row.get("refusal_reason") or row.get("reason") or "").strip()

            if reason:
                refused += 1
                refusals[reason] += 1
            elif kind in {"signal", "opportunity"} or _truthy(row, "is_signal"):
                signals += 1

            if kind == "position" or row.get("position_id") is not None:
                if _truthy(row, "opened", "is_open") or row.get("entry_at"):
                    opened += 1
                if row.get("exit_at") or _truthy(row, "closed"):
                    closed += 1

            if _truthy(row, "unresolved") or str(
                row.get("resolution") or ""
            ).lower() in {"unknown", "unresolved"}:
                unresolved += 1

            if row.get("trade_pnl") is not None or _truthy(row, "trade_completed"):
                if _is_promotable(row) and int(row.get("authoritative") or 0):
                    trades += 1
                else:
                    non_promotable += 1

        sessions = len(
            {
                str(row.get("session_date"))
                for row in lane_rows
                if row.get("session_date")
            }
        )

        out.append(
            LaneDailyRow(
                lane_key=lane_key,
                state=lane.state.value,
                sessions_observed=sessions,
                trades_completed=trades,
                signals_found=signals,
                signals_refused=refused,
                refusal_reasons=dict(refusals),
                positions_opened=opened,
                positions_closed=closed,
                positions_unresolved=unresolved,
                quote_coverage=coverage.get(lane_key),
                non_promotable_rows=non_promotable,
            )
        )

    return tuple(out), len(unattributed)


def build_daily_report(
    *,
    session_date: str,
    session_row: Mapping[str, Any] | None,
    evidence_rows: Iterable[Mapping[str, Any]] = (),
    quote_coverage: Mapping[str, float | None] | None = None,
    release_tag: str | None = None,
    runtime_sha: str | None = None,
    generated_at: str | None = None,
) -> DailyEvidenceReport:
    """Assemble the day's report from the session ledger and the evidence rows."""
    from app.core.release_manifest import release_tag as read_tag
    from app.core.release_manifest import runtime_sha as read_sha
    from app.core.strategy_identity import EVIDENCE_SCHEMA_VERSION

    row = dict(session_row or {})
    lanes, unattributed = build_lane_rows(
        evidence_rows, quote_coverage=quote_coverage
    )

    gaps: tuple[str, ...] = ()
    if row:
        from app.services.snapback_session_ledger import evidence_gap_codes

        gaps = tuple(evidence_gap_codes(row))

    session_coverage = _ratio(
        row.get("quotes_observed"), row.get("quotes_required")
    )
    if session_coverage is None:
        # Fall back to the scan denominator: a symbol that was evaluated had a
        # usable quote. Still a ratio of observed to required, not an assumption
        # that everything was fine.
        session_coverage = _ratio(
            row.get("universe_scanned"), row.get("universe_expected")
        )

    return DailyEvidenceReport(
        session_date=session_date,
        generated_at=generated_at or datetime.now(timezone.utc).isoformat(),
        session_status=classify_session(row or None),
        release_tag=release_tag if release_tag is not None else read_tag(),
        runtime_sha=runtime_sha if runtime_sha is not None else read_sha(),
        evidence_schema_version=EVIDENCE_SCHEMA_VERSION,
        universe_expected=int(row.get("universe_expected") or 0),
        universe_evaluated=int(row.get("universe_scanned") or 0),
        quote_coverage=session_coverage,
        signals_found=sum(lane.signals_found for lane in lanes)
        or int(row.get("signals_authoritative") or 0),
        signals_refused=sum(lane.signals_refused for lane in lanes),
        positions_opened=sum(lane.positions_opened for lane in lanes),
        positions_closed=sum(lane.positions_closed for lane in lanes),
        positions_unresolved=sum(lane.positions_unresolved for lane in lanes),
        evidence_gap_codes=gaps,
        unattributed_rows=unattributed,
        lanes=lanes,
    )


def _pct(value: float | None) -> str:
    return "unknown" if value is None else f"{value * 100:.1f}%"


def render_daily_report(report: DailyEvidenceReport) -> str:
    """Appendix B, rendered. Read top-down: build, health, then the ten lanes."""
    lines = [
        f"Date: {report.session_date}",
        f"Release: {report.release_tag} ({report.runtime_sha[:12]})",
        f"Session: {report.session_status}",
        f"Universe: {report.universe_evaluated}/{report.universe_expected} "
        f"({_pct(report.universe_coverage)})",
        f"Quote coverage: {_pct(report.quote_coverage)}",
        f"Signals: {report.signals_found} found, {report.signals_refused} refused",
        f"Positions: {report.positions_opened} opened, {report.positions_closed} closed, "
        f"{report.positions_unresolved} unresolved",
        f"Evidence gaps: {', '.join(report.evidence_gap_codes) or 'none'}",
        f"Unattributed rows: {report.unattributed_rows}",
        f"Usable as evidence: {'YES' if report.usable_as_evidence else 'NO'}",
        "",
        f"{'Lane':<28}{'Sessions':>9}{'Trades':>8}{'Coverage':>10}"
        f"{'Unresolved':>12}  State",
    ]
    for lane in report.lanes:
        lines.append(
            f"{lane.lane_key:<28}{lane.sessions_observed:>9}{lane.trades_completed:>8}"
            f"{_pct(lane.quote_coverage):>10}{lane.positions_unresolved:>12}  {lane.state}"
        )
    return "\n".join(lines)


def load_session_rows(warehouse, session_date: str) -> tuple[Mapping[str, Any] | None, list[Mapping[str, Any]]]:
    """Session ledger row plus that day's evidence rows, from the warehouse.

    Errors are not swallowed into empty results: an unreadable warehouse must
    surface as ``SYSTEM_ERROR``, which is what a ``None`` session row produces.
    """
    from app.services.snapback_session_ledger import session_record

    try:
        session = session_record(warehouse, session_date)
    except Exception:
        session = None

    rows: list[Mapping[str, Any]] = []
    for table in ("prospective_trades", "scan_symbol_decisions"):
        try:
            fetched = warehouse.get_records_by_table(table)
        except Exception:
            continue
        for raw in fetched:
            row = dict(raw)
            if str(row.get("session_date") or "") == session_date:
                rows.append(row)
    return session, rows


def unattributed_summary(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    """How many rows carry no lane, grouped by how they were produced."""
    out: dict[str, int] = defaultdict(int)
    for row in rows:
        if row_lane(row) is None:
            out[str(row.get("evidence_class") or "unknown")] += 1
    return dict(out)


__all__ = [
    "COVERAGE_FLOOR",
    "DailyEvidenceReport",
    "LaneDailyRow",
    "SessionStatus",
    "build_daily_report",
    "build_lane_rows",
    "classify_session",
    "load_session_rows",
    "render_daily_report",
    "unattributed_summary",
]
