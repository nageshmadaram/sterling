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


def is_authoritative_row(row: dict, *, expected_build_sha: Optional[str] = None) -> bool:
    """Whether a stored row may count toward the authoritative sample."""
    if row is None:
        return False

    flag = row.get("authoritative", 1)
    try:
        if int(flag) != 1:
            return False
    except (TypeError, ValueError):
        return False

    source = str(row.get("source") or AUTHORITATIVE_SOURCE)
    if source in NON_AUTHORITATIVE_SOURCES:
        return False

    if expected_build_sha:
        build = str(row.get("runtime_build_sha") or "")
        if build and build != expected_build_sha:
            return False

    return True


def filter_authoritative(rows, *, expected_build_sha: Optional[str] = None) -> list:
    return [r for r in (rows or []) if is_authoritative_row(dict(r), expected_build_sha=expected_build_sha)]
