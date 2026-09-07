#!/usr/bin/env python3
"""Compare ORB board tickets to Auto fills for a paper soak.

Usage:
    python backend/scripts/orb_paper_soak_compare.py board.json fills.json

Each board row is a scan signal (`trade` + `signal`). Each fill is an
`execute_scan` executed entry. Exit 0 when every fill's ticket_fingerprint
matches a board row; otherwise print mismatches and exit 1.

This is the soak check from the ORB product contract: Auto places the same
ticket the board showed.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.nifty_orb_lifecycle import SAME_TICKET_FIELDS, ticket_fields, ticket_fingerprint


def _load(path: str) -> list[dict]:
    raw = json.loads(Path(path).read_text())
    if isinstance(raw, dict):
        return list(raw.get("signals") or raw.get("executed") or raw.get("fills") or [])
    return list(raw)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    board, fills = _load(argv[1]), _load(argv[2])
    by_fp = {}
    for row in board:
        plan, signal = row.get("trade") or {}, row.get("signal") or {}
        if not plan:
            continue
        fp = row.get("ticket_fingerprint") or ticket_fingerprint(plan, signal)
        by_fp[fp] = ticket_fields(plan)
    mismatches = []
    for fill in fills:
        if fill.get("status") not in {None, "executed"}:
            continue
        fp = fill.get("ticket_fingerprint")
        if not fp:
            mismatches.append({"reason": "fill missing ticket_fingerprint"})
            continue
        board_ticket = by_fp.get(fp)
        fill_ticket = fill.get("ticket") or ticket_fields(fill.get("plan") or fill)
        if board_ticket is None:
            mismatches.append({"fill": fp, "reason": "no matching board ticket"})
            continue
        drift = {k: (board_ticket.get(k), fill_ticket.get(k)) for k in SAME_TICKET_FIELDS if board_ticket.get(k) != fill_ticket.get(k)}
        if drift:
            mismatches.append({"fingerprint": fp, "drift": drift})
    if not mismatches:
        print(f"ok: {len(fills)} fills match {len(by_fp)} board tickets")
        return 0
    print(json.dumps(mismatches, indent=2))
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
