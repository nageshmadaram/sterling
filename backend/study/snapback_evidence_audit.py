"""The daily broker-evidence report: what happened, and what it is allowed to prove.

Evidence quality is decided before, and independently of, whether a trade made
money.  This module therefore never reads P&L.  It is deliberately fail-closed:
a corrupt or unreadable evidence partition is an audit failure, never an empty
partition, and reaching a final state called RECONCILED is necessary but not
sufficient to call a broker-executed chain authoritative.

The prospective paper-economic gate is separate and reads the observation
warehouse.  This auditor is for the append-only broker/lifecycle evidence model;
it must never manufacture broker facts for paper observations.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any, Optional

__all__ = ["audit_date", "render_text", "main"]


def _load(store: Any, kind: str, errors: list[str]) -> list[dict[str, Any]]:
    """Load one partition without converting corruption into an empty day."""
    try:
        return store.read(kind)
    except Exception as exc:
        errors.append(f"{kind}: {type(exc).__name__}: {exc}")
        return []


def audit_date(root: Path, session_date: date) -> dict[str, Any]:
    """Count one day's broker evidence. Reads no prices and no outcomes/P&L."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

    from app.services.snapback_evidence_recorder import (
        BLOCKED_STATES, Outcome, State, outcome_for_state,
    )
    from app.services.snapback_evidence_store import SnapbackEvidenceStore
    from app.services.snapback_hedge_evidence import HEDGE_NOT_REQUIRED_REASONS

    store = SnapbackEvidenceStore(root, session_date=session_date)
    load_errors: list[str] = []

    lifecycle = _load(store, "lifecycle", load_errors)
    selections = _load(store, "selections", load_errors)
    hedges = _load(store, "hedge_selections", load_errors)
    brokers = _load(store, "broker_events", load_errors)
    universes = _load(store, "candidate_universes", load_errors)

    # ─── fold the journal per opportunity ────────────────────────────────────
    by_opportunity: dict[str, list[dict[str, Any]]] = {}
    for row in lifecycle:
        opportunity_id = str(row.get("opportunity_id") or "")
        if not opportunity_id:
            load_errors.append("lifecycle: row missing opportunity_id")
            continue
        by_opportunity.setdefault(opportunity_id, []).append(row)

    final_states: dict[str, str] = {}
    for opportunity_id, rows in by_opportunity.items():
        try:
            ordered = sorted(rows, key=lambda r: int(r["sequence"]))
            sequences = [int(r["sequence"]) for r in ordered]
            if sequences != list(range(len(sequences))):
                load_errors.append(
                    f"lifecycle:{opportunity_id}: non-contiguous sequence {sequences}"
                )
            final_states[opportunity_id] = str(ordered[-1]["state"])
        except Exception as exc:
            load_errors.append(
                f"lifecycle:{opportunity_id}: {type(exc).__name__}: {exc}"
            )

    outcomes = Counter(outcome_for_state(s) for s in final_states.values())
    states = Counter(final_states.values())

    listed = Counter(row.get("listed_status", "MISSING") for row in selections)

    hedge_counts = Counter()
    for row in hedges:
        required = bool(row.get("hedge_required"))
        if not required:
            if row.get("hedge_reason") in HEDGE_NOT_REQUIRED_REASONS:
                hedge_counts["waived"] += 1
            else:
                hedge_counts["unknown"] += 1
        elif row.get("instrument_token"):
            hedge_counts["authoritative"] += 1
        else:
            hedge_counts["unknown"] += 1

    broker_counts = Counter(row.get("event_type", "UNKNOWN") for row in brokers)

    # A broker-executed trade is authoritative only when the append-only path
    # proves the critical milestones. A single final RECONCILED row cannot stand
    # in for market evidence, fills, protection and exit.
    authoritative = 0
    inconclusive = 0
    required_common = {
        State.MARKET_EVIDENCE_READY,
        State.OPTION_FILLED,
        State.PROTECTION_ACTIVE,
        State.EXIT_FILLED,
        State.RECONCILED,
    }

    for opportunity_id, state in final_states.items():
        if state != State.RECONCILED:
            if state not in BLOCKED_STATES:
                inconclusive += 1
            continue

        selection = next(
            (s for s in selections if s.get("opportunity_id") == opportunity_id), None
        )
        hedge = next(
            (h for h in hedges if h.get("opportunity_id") == opportunity_id), None
        )
        path_states = {str(r.get("state")) for r in by_opportunity.get(opportunity_id, [])}

        hedge_ok = False
        hedge_state_ok = False
        if hedge is not None:
            if bool(hedge.get("hedge_required")):
                hedge_ok = bool(hedge.get("instrument_token"))
                hedge_state_ok = State.HEDGE_FILLED in path_states
            else:
                hedge_ok = hedge.get("hedge_reason") in HEDGE_NOT_REQUIRED_REASONS
                hedge_state_ok = State.HEDGE_WAIVED in path_states

        chain_ok = (
            selection is not None
            and selection.get("listed_status") == "LISTED"
            and hedge_ok
            and hedge_state_ok
            and required_common.issubset(path_states)
        )
        if chain_ok and not load_errors:
            authoritative += 1
        else:
            inconclusive += 1

    # If storage was unreadable, no positive authority claim survives. Counts of
    # rows we could read remain useful diagnostics but are not evidence of absence.
    if load_errors:
        authoritative = 0
        inconclusive = max(inconclusive, len(final_states))

    return {
        "date": session_date.isoformat(),
        "root": str(root),
        "audit_status": "FAIL" if load_errors else "PASS",
        "load_errors": load_errors,
        "opportunities": len(final_states),
        "candidate_universes": len(universes),
        "selection": {
            "LISTED": listed.get("LISTED", 0),
            "NOT_LISTED": listed.get("NOT_LISTED", 0),
            "UNKNOWN": listed.get("UNKNOWN", 0),
        },
        "hedge": {
            "authoritative": hedge_counts["authoritative"],
            "waived": hedge_counts["waived"],
            "unknown": hedge_counts["unknown"],
        },
        "broker": dict(sorted(broker_counts.items())),
        "outcomes": dict(sorted(outcomes.items())),
        "final_states": dict(sorted(states.items())),
        "blocked": sum(v for k, v in states.items() if k in BLOCKED_STATES),
        "economic_authoritative": authoritative,
        "inconclusive": inconclusive,
        "economic_verdict": "INCONCLUSIVE — the promotion gate evaluates the sample, not one day",
    }


def render_text(report: dict[str, Any]) -> str:
    lines = [
        "SNAPBACK EVIDENCE AUDIT",
        report["date"],
        "",
        f"Audit status                     {report['audit_status']}",
        f"Opportunities                    {report['opportunities']}",
        f"Candidate universes              {report['candidate_universes']}",
    ]
    if report["load_errors"]:
        lines += ["", "LOAD ERRORS:"]
        lines += [f"  {err}" for err in report["load_errors"]]

    lines += [
        "",
        "Selection:",
        f"  LISTED                         {report['selection']['LISTED']}",
        f"  NOT_LISTED                     {report['selection']['NOT_LISTED']}",
        f"  UNKNOWN                        {report['selection']['UNKNOWN']}",
        "",
        "Hedge:",
        f"  authoritative                  {report['hedge']['authoritative']}",
        f"  waived                         {report['hedge']['waived']}",
        f"  unknown                        {report['hedge']['unknown']}",
        "",
        "Broker:",
    ]
    for event_type, count in report["broker"].items():
        lines.append(f"  {event_type:<30} {count}")
    if not report["broker"]:
        lines.append("  (none)")

    lines += ["", "Outcomes:"]
    for outcome, count in report["outcomes"].items():
        lines.append(f"  {outcome:<30} {count}")
    if not report["outcomes"]:
        lines.append("  (none)")

    lines += [
        "",
        f"Blocked before execution         {report['blocked']}",
        f"Economic-authoritative trades    {report['economic_authoritative']}",
        f"Inconclusive                     {report['inconclusive']}",
        "",
        f"Economic verdict                 {report['economic_verdict']}",
    ]
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Audit one session's Snapback evidence.")
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--root", default=None, help="evidence root (default: the configured lake)")
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)

    if args.root:
        root = Path(args.root)
    else:
        try:
            from kitelake.volume import resolve_root
            root = Path(resolve_root())
        except Exception:
            print("no lake root available; pass --root", file=sys.stderr)
            return 2

    report = audit_date(root, date.fromisoformat(args.date))
    print(json.dumps(report, indent=1) if args.as_json else render_text(report))
    return 0 if report["audit_status"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(main())
