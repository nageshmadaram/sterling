"""Validate that the configured TrueData entitlement can supply V2.1 research input.

Usage from the repository root:
    TRUEDATA_USERNAME=... TRUEDATA_PASSWORD=... \
    python scripts/adaptive_edge/validate_truedata_research_source.py \
      NIFTY-I 2026-01-01 2026-08-01

This script is read-only. It does not place broker orders or mutate research data.
"""
from __future__ import annotations

import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.market_data.truedata import TrueDataError, TrueDataHistoricalClient


REQUIRED_FIELDS = {"timestamp", "open", "high", "low", "close", "volume", "oi"}


def _parse_timestamp(value: str) -> datetime:
    """Parse provider timestamps while rejecting malformed values."""
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"invalid provider timestamp: {value!r}") from exc


def validate_rows(bars: list[dict[str, object]]) -> tuple[int, str, str]:
    """Validate structural integrity of the returned bar population."""
    if not bars:
        raise ValueError("TrueData returned no 1-minute bars")

    missing_by_row: set[str] = set()
    parsed_timestamps: list[datetime] = []
    for index, row in enumerate(bars):
        missing_by_row.update(REQUIRED_FIELDS.difference(row))
        if "timestamp" in row:
            try:
                parsed_timestamps.append(_parse_timestamp(str(row["timestamp"])))
            except ValueError as exc:
                raise ValueError(f"row {index}: {exc}") from exc

    if missing_by_row:
        raise ValueError(f"missing canonical bar fields: {sorted(missing_by_row)}")

    if len(parsed_timestamps) != len(bars):
        raise ValueError("one or more rows have no timestamp")

    awareness = {timestamp.tzinfo is not None for timestamp in parsed_timestamps}
    if len(awareness) > 1:
        raise ValueError("mixed timestamp timezone awareness is not permitted")

    duplicate_count = len(parsed_timestamps) - len(set(parsed_timestamps))
    if duplicate_count:
        raise ValueError(
            f"duplicate timestamps require deterministic source reconciliation: {duplicate_count}"
        )

    if parsed_timestamps != sorted(parsed_timestamps):
        raise ValueError("timestamps are not monotonically increasing")

    return (
        len(bars),
        str(bars[0]["timestamp"]),
        str(bars[-1]["timestamp"]),
    )


async def main() -> int:
    if len(sys.argv) != 4:
        print(
            "usage: validate_truedata_research_source.py SYMBOL START END",
            file=sys.stderr,
        )
        return 2

    username = os.getenv("TRUEDATA_USERNAME")
    password = os.getenv("TRUEDATA_PASSWORD")
    if not username or not password:
        print(
            "BLOCKED: TRUEDATA_USERNAME and TRUEDATA_PASSWORD are required",
            file=sys.stderr,
        )
        return 3

    symbol, start, end = sys.argv[1:]
    client = TrueDataHistoricalClient(username, password)
    try:
        bars = await client.get_bars(
            symbol,
            start,
            end,
            interval="1min",
            response_format="csv",
        )
    except TrueDataError as exc:
        print(f"BLOCKED: TrueData source validation failed: {exc}", file=sys.stderr)
        return 4
    finally:
        await client.aclose()

    try:
        count, first_timestamp, last_timestamp = validate_rows(bars)
    except ValueError as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 5

    print(f"bars={count}")
    print(f"first_timestamp={first_timestamp}")
    print(f"last_timestamp={last_timestamp}")
    print("duplicate_timestamps=0")
    print(f"fields={sorted(bars[0].keys())}")
    print("SOURCE_VALIDATION=PASS")
    print(
        "NOTE: timestamp availability semantics, entitlement evidence, "
        "session-calendar version, revisions, and source hash must still be "
        "recorded in the dataset manifest before research execution."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
