"""Derive the per-lane verdicts the capital-permission decision needs.

`may_send_entry` asks eleven questions and refuses on every `None`. Three of
them were being answered `None` because nothing computed them: whether the
lane's economic gate passed, whether its identity still matches the frozen
manifest, and whether its shadow execution is acceptable. "Not measured" was
true when the shadow path wrote nothing; it is not true any more, and a
permanent `None` is indistinguishable from a check nobody wrote.

Each function here returns `True`, `False`, or `None`, and `None` keeps its
meaning: the question could not be answered. Nothing here converts a missing
sample into a pass.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path
from typing import Any, Mapping

log = logging.getLogger(__name__)

__all__ = [
    "lane_identity_matches_frozen",
    "lane_promotion_passed",
    "shadow_gate_passed",
    "operational_gate_passed",
    "lane_verdicts",
    "MIN_SHADOW_INTENTS",
]

#: A shadow sample smaller than this says nothing about executability. It is the
#: same trade count the economic gate requires, because the question — "could
#: this lane actually have been executed?" — needs as many observations as the
#: question of whether it was profitable.
MIN_SHADOW_INTENTS: int = 300


def lane_identity_matches_frozen(lane_key: str) -> bool | None:
    """Does this lane still match the manifest the release froze?

    `None` when there is no frozen manifest to compare against: an unfrozen
    release has no identity to match, which is not the same as matching.
    """
    try:
        from app.core.release_manifest import read_manifest, verify_manifest

        stored = read_manifest()
        if stored is None:
            return None
        verdict = verify_manifest(stored)
    except Exception as exc:  # noqa: BLE001
        log.warning("lane gate: manifest unreadable for %s: %s", lane_key, exc)
        return None

    if lane_key in verdict.missing_lanes or lane_key in verdict.unexpected_lanes:
        return False
    return not any(drift.scope == lane_key for drift in verdict.identity_drift)


def _authoritative_rows(lane_key: str, db_path: str | Path | None = None) -> list[Mapping[str, Any]] | None:
    path = Path(db_path or os.environ.get("STERLING_OBSERVATIONS_DB_PATH")
                or "snapback_observations.db")
    if not path.exists():
        return None
    try:
        with sqlite3.connect(str(path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM outcomes WHERE authoritative = 1 AND lane_key = ?",
                (lane_key,),
            ).fetchall()
    except Exception as exc:  # noqa: BLE001
        log.warning("lane gate: evidence unreadable for %s: %s", lane_key, exc)
        return None
    return [dict(r) for r in rows]


def lane_promotion_passed(lane_key: str, db_path: str | Path | None = None) -> bool | None:
    """Has this lane passed its economic gate on BROKER evidence?

    Broker rows, not the pooled sample: the question real capital asks is what
    happened with real capital. A lane with no broker rows has not passed — but
    it has not failed either, so an unreadable store answers `None` while an
    empty one answers False with an explicit INCONCLUSIVE verdict underneath.
    """
    rows = _authoritative_rows(lane_key, db_path)
    if rows is None:
        return None
    try:
        from app.core.lane_promotion import evaluate_lane_regimes

        verdict = evaluate_lane_regimes(rows, lane_key)["by_regime"]["broker"]
    except Exception as exc:  # noqa: BLE001
        log.warning("lane gate: promotion evaluation failed for %s: %s", lane_key, exc)
        return None
    return str(verdict.get("verdict") or "").upper() == "PASS"


def shadow_gate_passed(lane_key: str, *, store: Any = None) -> bool | None:
    """Is this lane's shadow execution evidence acceptable?

    Acceptable means three things, and all of them are about execution rather
    than profit: enough intents to say anything at all, every intent measured
    against a book that was actually observed, and no intent whose margin or
    contract could not be resolved. A no-fill is not a failure of the gate — it
    is a real answer about executability, and the lane's economics will carry
    the cost of it.

    `None` when nothing has been recorded: silence is not evidence either way.
    """
    try:
        from app.services.shadow_execution import ShadowStore
        from app.core.shadow_record import summarize_all
        from app.services.shadow_execution import _records_from_rows
    except Exception as exc:  # noqa: BLE001
        log.warning("lane gate: shadow module unavailable: %s", exc)
        return None

    shadow_store = store if store is not None else ShadowStore()
    try:
        sessions = shadow_store.sessions()
    except Exception as exc:  # noqa: BLE001
        log.warning("lane gate: shadow store unreadable: %s", exc)
        return None
    if not sessions:
        return None

    rows: list[Mapping[str, Any]] = []
    for session_date in sessions:
        try:
            rows.extend(r for r in shadow_store.read(session_date)
                        if str(r.get("lane_key")) == lane_key)
        except Exception as exc:  # noqa: BLE001
            log.warning("lane gate: shadow session %s unreadable: %s", session_date, exc)
            return None

    if not rows:
        return None

    metrics = summarize_all(_records_from_rows(rows)).get(lane_key)
    if metrics is None:
        return None

    if metrics.intents < MIN_SHADOW_INTENTS:
        return False
    if metrics.unobserved or metrics.margin_unknown or metrics.contracts_unavailable:
        return False
    if metrics.protection_unknown:
        return False
    return True


def operational_gate_passed(lane_key: str) -> bool | None:
    """Is the deployment itself fit to carry this lane's orders?

    This is the question the economic and shadow gates cannot ask: the numbers
    can be excellent and the machine still be running unreleased code, from an
    unverified address, with a backup nobody has ever restored. Section 20.2
    keeps it separate for that reason.

    Answered from the doctor rather than from a second implementation of the
    same checks — one place to add a check, one place for it to be wrong.
    """
    try:
        from app.core.operator_report import doctor_from_preflight, lane_doctor_checks
        from app.services.snapback_preflight import run_preflight

        report = doctor_from_preflight(run_preflight().checks, extra=lane_doctor_checks())
    except Exception as exc:  # noqa: BLE001
        log.warning("lane gate: operational doctor could not run for %s: %s", lane_key, exc)
        return None

    if report.unknowns:
        return None
    return not report.failures


def lane_verdicts(lane_key: str):
    """The three independent verdicts for one lane, composed but not merged."""
    from app.core.lane_verdicts import compose_lane_verdicts

    economic = lane_promotion_passed(lane_key)
    shadow = shadow_gate_passed(lane_key)
    operational = operational_gate_passed(lane_key)

    reasons: list[str] = []
    if economic is None:
        reasons.append("economic: the lane's authoritative sample could not be read")
    if shadow is None:
        reasons.append("shadow: no shadow record has been written for this lane")
    if operational is None:
        reasons.append("operational: at least one deployment check could not be run")

    return compose_lane_verdicts(
        lane_key, economic=economic, shadow_execution=shadow,
        operational=operational, reasons=tuple(reasons),
    )
