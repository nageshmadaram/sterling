"""Which observations may enter the authoritative prospective sample.

The board's three-session catch-up search is useful on screen and poisonous in
evidence. An observation is authoritative only when its signal bar is the latest
closed eligible session AND it fired after this dataset began. Everything else is
recorded as replay, with `authoritative = 0`, and is filtered out of every gate and
report input.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

log = logging.getLogger(__name__)

_IST = timezone(timedelta(hours=5, minutes=30))

AUTHORITATIVE_SOURCE = "PROSPECTIVE_PAPER"
CATCHUP_SOURCE = "LIVE_CATCHUP_REPLAY"
SHADOW_SOURCE = "SAME_DAY_SHADOW_REPLAY"

NON_AUTHORITATIVE_SOURCES = {CATCHUP_SOURCE, SHADOW_SOURCE}


@dataclass
class AuthorityVerdict:
    authoritative: bool
    source: str
    reasons: List[str] = field(default_factory=list)


def dataset_start() -> Optional[datetime]:
    """When the current authoritative dataset began."""
    raw = (os.environ.get("STERLING_DATASET_START") or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        log.warning("Snapback authority: STERLING_DATASET_START unparseable: %r", raw)
        return None


def classify_signal_authority(
    *,
    signal_timestamp_ms: int,
    latest_closed_session: date,
    dataset_start: Optional[datetime] = None,
) -> AuthorityVerdict:
    """Decide whether one signal may be recorded as authoritative evidence."""
    reasons: List[str] = []

    if not signal_timestamp_ms or int(signal_timestamp_ms) <= 0:
        return AuthorityVerdict(False, CATCHUP_SOURCE, ["unknown_signal_timestamp"])

    moment = datetime.fromtimestamp(int(signal_timestamp_ms) / 1000.0, tz=timezone.utc)
    session = moment.astimezone(_IST).date()

    if session > latest_closed_session:
        reasons.append("future_session")
    elif session < latest_closed_session:
        reasons.append("stale_catchup_session")

    if dataset_start is not None and moment < dataset_start:
        reasons.append("before_dataset_start")

    if reasons:
        return AuthorityVerdict(False, CATCHUP_SOURCE, reasons)
    return AuthorityVerdict(True, AUTHORITATIVE_SOURCE, [])


# An allow-list, not a deny-list. A source nobody has classified is unknown, and
# unknown is not evidence. Deny-lists silently admit every source added later.
AUTHORITATIVE_SOURCES = {AUTHORITATIVE_SOURCE}


@dataclass
class RowAuthority:
    authoritative: bool
    reasons: List[str] = field(default_factory=list)


def classify_row_authority(
    row: dict, *, expected_build_sha: Optional[str] = None,
) -> RowAuthority:
    """Whether one stored row may count toward the authoritative sample.

    Every missing fact is disqualifying. The 1.3 defect was a missing
    `authoritative` column defaulting to 1; the same philosophy applied one layer
    out would let an empty dict, an unset source, or a source introduced by some
    future writer count as evidence.
    """
    if not row:
        return RowAuthority(False, ["empty_row"])

    reasons: List[str] = []

    if "source" not in row or row.get("source") in (None, ""):
        reasons.append("missing_source")
        source = ""
    else:
        source = str(row["source"])
        if source in NON_AUTHORITATIVE_SOURCES:
            # A declared replay source. Excluded, but not a data-quality fault:
            # recording it was correct.
            reasons.append(f"replay_source:{source}")
        elif source not in AUTHORITATIVE_SOURCES:
            reasons.append(f"unknown_source:{source}")

    if "authoritative" not in row or row.get("authoritative") is None:
        reasons.append("missing_authoritative_flag")
    else:
        try:
            if int(row["authoritative"]) != 1:
                reasons.append("not_flagged_authoritative")
        except (TypeError, ValueError):
            reasons.append("unparseable_authoritative_flag")

    if expected_build_sha:
        build = str(row.get("runtime_build_sha") or "")
        if not build:
            reasons.append("missing_runtime_build_sha")
        elif build != expected_build_sha:
            reasons.append(f"build_mismatch:{build}")

    return RowAuthority(not reasons, reasons)


def is_authoritative_row(row: dict, *, expected_build_sha: Optional[str] = None) -> bool:
    """Whether a stored row may count toward the authoritative sample."""
    return classify_row_authority(
        dict(row or {}), expected_build_sha=expected_build_sha,
    ).authoritative


def unknown_source_rows(rows, *, expected_build_sha: Optional[str] = None) -> list:
    """Rows excluded because nobody has classified their source.

    Distinct from a declared replay: this is a writer the authority model does
    not know about, which is a data-quality problem worth surfacing rather than
    silently dropping.
    """
    offenders = []
    for row in (rows or []):
        verdict = classify_row_authority(dict(row), expected_build_sha=expected_build_sha)
        if any(r.startswith("unknown_source") for r in verdict.reasons):
            offenders.append(dict(row))
    return offenders


def filter_authoritative(rows, *, expected_build_sha: Optional[str] = None) -> list:
    return [r for r in (rows or []) if is_authoritative_row(dict(r), expected_build_sha=expected_build_sha)]
