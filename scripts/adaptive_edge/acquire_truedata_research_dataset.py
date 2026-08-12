"""Acquire and manifest a validated TrueData 1-minute research dataset.

Usage from the repository root:
    TRUEDATA_USERNAME=... TRUEDATA_PASSWORD=... \
    python scripts/adaptive_edge/acquire_truedata_research_dataset.py \
      NIFTY-I 2026-01-01 2026-08-01 \
      --source-timezone Asia/Kolkata \
      --session-calendar-version NSE-EQUITY-2026-v1 \
      --feature-set-version REQUIRED \
      --label-definition-version A26-ND-v1

The command is read-only with respect to the provider and writes only local
research artifacts. It does not place broker orders or mutate production state.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = REPOSITORY_ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.market_data.truedata import TrueDataError, TrueDataHistoricalClient
from validate_truedata_research_source import validate_rows


CANONICAL_FIELDS = (
    "timestamp",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "oi",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("symbol")
    parser.add_argument("start")
    parser.add_argument("end")
    parser.add_argument("--source-timezone", required=True)
    parser.add_argument("--session-calendar-version", required=True)
    parser.add_argument("--feature-set-version", required=True)
    parser.add_argument("--label-definition-version", required=True)
    parser.add_argument("--dataset-version")
    parser.add_argument(
        "--output-dir",
        default="data/adaptive_edge/research",
        help="Directory for the canonical CSV and manifest.",
    )
    return parser.parse_args()


def dataset_version(symbol: str, start: str, end: str) -> str:
    safe = lambda value: "".join(char.lower() if char.isalnum() else "-" for char in value).strip("-")
    return f"ae21-truedata-{safe(symbol)}-{safe(start)}-{safe(end)}-v1"


def write_canonical_csv(path: Path, bars: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CANONICAL_FIELDS, lineterminator="\n")
        writer.writeheader()
        for bar in bars:
            writer.writerow({field: bar[field] for field in CANONICAL_FIELDS})


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def missingness_summary(bars: list[dict[str, object]]) -> dict[str, int]:
    return {
        field: sum(1 for bar in bars if bar.get(field) in (None, ""))
        for field in CANONICAL_FIELDS
    }


def build_manifest(
    *,
    dataset_version_value: str,
    symbol: str,
    start: str,
    end: str,
    first_timestamp: str,
    last_timestamp: str,
    source_timezone: str,
    session_calendar_version: str,
    feature_set_version: str,
    label_definition_version: str,
    source_hash: str,
    row_count: int,
    missingness: dict[str, int],
) -> dict[str, object]:
    return {
        "dataset_version": dataset_version_value,
        "strategy_version": "2.1.0-proposed",
        "source": "TrueData",
        "provider_document_version": "TrueData Market Data API v2.6",
        "instrument": symbol,
        "bar_interval": "1min",
        "requested_start": start,
        "requested_end": end,
        "actual_first_timestamp": first_timestamp,
        "actual_last_timestamp": last_timestamp,
        "source_timezone": source_timezone,
        "availability_semantics_verified": False,
        "entitlement_verified": True,
        "historical_gap_policy": "FAIL_CLOSED",
        "duplicate_timestamp_policy": "FAIL_CLOSED",
        "revision_policy": "REQUIRED",
        "source_file_sha256": source_hash,
        "row_count": row_count,
        "missingness_summary": missingness,
        "session_calendar_version": session_calendar_version,
        "feature_set_version": feature_set_version,
        "label_definition_version": label_definition_version,
        "research_registry_version": "1.0.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


async def acquire(args: argparse.Namespace) -> int:
    username = os.getenv("TRUEDATA_USERNAME")
    password = os.getenv("TRUEDATA_PASSWORD")
    if not username or not password:
        print("BLOCKED: TRUEDATA_USERNAME and TRUEDATA_PASSWORD are required", file=sys.stderr)
        return 3

    client = TrueDataHistoricalClient(username, password)
    try:
        bars = await client.get_bars(
            args.symbol,
            args.start,
            args.end,
            interval="1min",
            response_format="csv",
        )
    except TrueDataError as exc:
        print(f"BLOCKED: TrueData acquisition failed: {exc}", file=sys.stderr)
        return 4
    finally:
        await client.aclose()

    try:
        count, first_timestamp, last_timestamp = validate_rows(bars)
    except ValueError as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 5

    version = args.dataset_version or dataset_version(args.symbol, args.start, args.end)
    output_dir = Path(args.output_dir)
    csv_path = output_dir / f"{version}.csv"
    manifest_path = output_dir / f"{version}.manifest.json"

    write_canonical_csv(csv_path, bars)
    manifest = build_manifest(
        dataset_version_value=version,
        symbol=args.symbol,
        start=args.start,
        end=args.end,
        first_timestamp=first_timestamp,
        last_timestamp=last_timestamp,
        source_timezone=args.source_timezone,
        session_calendar_version=args.session_calendar_version,
        feature_set_version=args.feature_set_version,
        label_definition_version=args.label_definition_version,
        source_hash=sha256(csv_path),
        row_count=count,
        missingness=missingness_summary(bars),
    )
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    print(f"DATASET_VERSION={version}")
    print(f"CSV={csv_path}")
    print(f"MANIFEST={manifest_path}")
    print(f"ROWS={count}")
    print(f"SHA256={manifest['source_file_sha256']}")
    print("ACQUISITION=PASS")
    print("NOTE: availability semantics and revision policy remain explicitly unverified until provider evidence is documented.")
    return 0


def main() -> int:
    return asyncio.run(acquire(parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
