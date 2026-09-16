#!/usr/bin/env python3
"""Back up the Snapback prospective evidence database.

Run after market close. Takes a consistent SQLite snapshot (never a byte copy of an
active WAL database), writes a checksum next to it, copies the frozen runtime record,
verifies the result, and prunes old backups.

Usage:
    python3 scripts/backup_snapback_observations.py
    python3 scripts/backup_snapback_observations.py --backup-root /media/usb/sterling
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.snapback_backup import (  # noqa: E402
    DEFAULT_KEEP_DAILY,
    DEFAULT_KEEP_WEEKLY,
    create_backup,
    prune_backups,
    verify_backup,
)

DEFAULT_SOURCE = os.environ.get(
    "STERLING_OBSERVATIONS_DB_PATH", "snapback_observations.db"
)
DEFAULT_BACKUP_ROOT = Path.home() / "Sterling" / "backups" / "snapback"
DEFAULT_FREEZE_RECORD = (
    Path(__file__).resolve().parents[2]
    / "docs"
    / "strategy"
    / "snapback"
    / "PROSPECTIVE_FREEZE_RECORD.md"
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-db", default=DEFAULT_SOURCE)
    parser.add_argument("--backup-root", default=str(DEFAULT_BACKUP_ROOT))
    parser.add_argument("--freeze-record", default=str(DEFAULT_FREEZE_RECORD))
    parser.add_argument("--keep-daily", type=int, default=DEFAULT_KEEP_DAILY)
    parser.add_argument("--keep-weekly", type=int, default=DEFAULT_KEEP_WEEKLY)
    parser.add_argument("--no-prune", action="store_true")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)
    freeze_record = Path(args.freeze_record)

    artifact = create_backup(
        source_db=Path(args.source_db),
        backup_root=Path(args.backup_root),
        freeze_record=freeze_record if freeze_record.exists() else None,
        now=now,
    )

    if not verify_backup(artifact.db_path, artifact.checksum_path):
        print(f"BACKUP FAILED verification: {artifact.db_path}", file=sys.stderr)
        return 2

    print(f"backup_db={artifact.db_path}")
    print(f"backup_sha256={artifact.checksum}")
    print(f"freeze_record={artifact.freeze_record_path}")

    if not args.no_prune:
        result = prune_backups(
            backup_root=Path(args.backup_root),
            keep_daily=args.keep_daily,
            keep_weekly=args.keep_weekly,
            now=now,
        )
        print(f"pruned={result.removed_count} kept={len(result.kept)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
