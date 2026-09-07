#!/usr/bin/env python3
"""Fail-closed ORB historical option walk-forward.

Usage:
    python backend/scripts/orb_historical_walk_forward.py corpus.json

Corpus JSON (either shape):
    {
      "bars": [ {timestamp, symbol, option_type, expiry, strike, open, high,
                 low, close, bid, ask, volume, open_interest, lot_size}, ... ],
      "signals": [ {"entry_index": 0, "risk_points": 2, "target_r": 2, "lots": 1}, ... ]
    }
    or engine-labeled:
    {
      "underlying_bars": [ {timestamp, open, high, low, close, volume}, ... ],
      "option_bars":    [ same option schema as bars above ]
    }

    Underlying without option_bars is refused — no invented premium.

Exit codes:
    0  folds produced (still NOT unattended-live eligible)
    1  evaluation error
    2  missing/invalid corpus — will not invent option P&L
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.engines.nifty_orb_historical import evaluate_historical_corpus


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    path = Path(argv[1])
    if not path.is_file():
        print(f"corpus not found: {path}", file=sys.stderr)
        return 2
    try:
        payload = json.loads(path.read_text())
        if not isinstance(payload, dict):
            raise ValueError("corpus must be a JSON object with bars and signals")
        report = evaluate_historical_corpus(payload)
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"evaluation failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
