"""The daily evidence report: what happened, and what it is allowed to prove.

Evidence quality is decided before, and independently of, whether a trade made
money. That is not a stylistic preference — it is the one rule that stops an
audit from becoming a filter that keeps winners and discards losers as "bad
data". This module therefore never reads P&L, and a test asserts it cannot.

The counts that matter most are the ones about opportunities that never traded.
"84 completed trades" means nothing without the denominator; "100 signals, 95
listed, 92 market-authoritative, 89 risk-approved, 84 executed" describes a
system whose executability can be reasoned about.
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


def _load(store: Any, kind: str) -> list[dict[str, Any]]:
    try:
        return store.read(kind)
    except Exception:
        return []


def audit_date(root: Path, session_date: date) -> dict[str, Any]:
    """Count the day's evidence. Reads no prices and no outcomes."""
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

    from app.services.snapback_evidence_recorder import (
        BLOCKED_STATES, Outcome, State, derive_state, outcome_for_state,
    )
    from app.services.snapback_evidence_store import SnapbackEvidenceStore

    store = SnapbackEvidenceStore(root, session_date=session_date)

    lifecycle = _load(store, "lifecycle")
    selections = _load(store, "selections")
    hedges = _load(store, "hedge_selections")
    brokers = _load(store, "broker_events")
    universes = _load(store, "candidate_universes")

    # ─── fold the journal per opportunity ────────────────────────────────────
    by_opportunity: dict[str, list[dict[str, Any]]] = {}
    for row in lifecycle:
        by_opportunity.setdefault(row["opportunity_id"], []).append(row)

    final_states: dict[str, str] = {}
    for opportunity_id, rows in by_opportunity.items():
        final_states[opportunity_id] = max(rows, key=lambda r: r["sequence"])["state"]

    outcomes = Counter(outcome_for_state(s) for s in final_states.values())
    states = Counter(final_states.values())

    listed = Counter(row.get("listed_status", "MISSING") for row in selections)

    hedge_counts = Counter()
    for row in hedges:
        if not row.get("hedge_required"):
            hedge_counts["waived"] += 1
        elif row.get("instrument_token"):
            hedge_counts["authoritative"] += 1
        else:
            hedge_counts["unknown"] += 1

    broker_counts = Counter(row.get("event_type", "UNKNOWN") for row in brokers)

    # A trade is economically authoritative only when the whole chain is
    # provable. Reaching RECONCILED is necessary and not sufficient.
    authoritative = 0
    inconclusive = 0
    for opportunity_id, state in final_states.items():
        if state != State.RECONCILED:
            if state not in BLOCKED_STATES:
                inconclusive += 1
            continue
        selection = next((s for s in selections if s["opportunity_id"] == opportunity_id), None)
        hedge = next((h for h in hedges if h["opportunity_id"] == opportunity_id), None)
        chain_ok = (
            selection is not None
            and selection.get("listed_status") == "LISTED"
            and hedge is not None
            and (not hedge.get("hedge_required") or hedge.get("instrument_token"))
        )
        if chain_ok:
            authoritative += 1
        else:
            inconclusive += 1

    return {
        "date": session_date.isoformat(),
        "root": str(root),
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
        # The verdict is never computed here. This report counts evidence; the
        # promotion gate decides economics, and only on a full sample.
        "economic_verdict": "INCONCLUSIVE — the promotion gate evaluates the sample, not one day",
    }


def render_text(report: dict[str, Any]) -> str:
    lines = [
        "SNAPBACK EVIDENCE AUDIT",
        report["date"],
        "",
        f"Opportunities                    {report['opportunities']}",
        f"Candidate universes              {report['candidate_universes']}",
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
