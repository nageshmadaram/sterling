# Sterling — Recovery Drill Record

Drills performed 2026-09-16 against runtime tag `snapback-prospective-freeze-1.0`
(commit `9e989dd910995deb5e77385b983e5992c58883c0`).

Each drill below was executed, not assumed. Automated drills live in
`backend/tests/unit/test_snapback_recovery_drills.py` and re-run with the suite.

---

## 1. Backup and restore

**Drill.** Take a backup while the database is open in WAL mode, then restore it.

**Result.** PASS. The snapshot uses SQLite's online backup API, passes
`PRAGMA integrity_check`, and restores to a complete database (12 tables, integrity
`ok`). The source file is byte-identical before and after.

## 2. Corrupt backup must not destroy a good database

**Drill.** Corrupt the backup bytes after the checksum is written, then restore over
a live database holding real evidence.

**Result.** PASS. `restore_backup()` verifies the checksum before touching the
destination, raises `BackupIntegrityError`, and the destination still holds its
original rows.

## 3. Crash with an OPEN paper position

**Drill.** Persist an OPEN position, discard the process, reopen the database.

**Result.** PASS. Status, quantity and hedge lots are all recovered unchanged.

## 4. Crash with an EXIT_PENDING position

**Drill.** Latch an exit (`PREMIUM_STOP`, bid 61.5), discard the process, reopen.

**Result.** PASS. The exit reason and the trigger bid both survive, and the position
is still returned by `get_active_paper_positions()` so the intraday processor can
finish liquidation. The EOD phase still refuses to touch it.

## 5. Corrupt evidence database

**Drill.** Replace the prospective database with non-SQLite bytes and run startup.

**Result.** PASS. Startup preflight reports `RECOVERY_REQUIRED` and the runner does
not start. No blank database is created, and the corrupt file is left on disk for
forensic recovery.

## 6. Invalid manifest or config

**Drill.** Force manifest verification to fail; separately force the config store
unreachable and the config hash to drift.

**Result.** PASS. Each case returns `HALTED` and blocks the runner. This is the same
check that would have caught the 2026-09-16 defect where a failed legacy migration
silently left Snapback disabled while the runner looked alive.

## 7. Kill switch

**Drill.** `POST /api/v1/snapback/family/stop-new-trades`, read the family view, then
resume.

**Result.** PASS. The family view reports `system_status: HALTED`,
`new_trades_halted: true`, `live_blocked: true`. Open positions and exit-pending
counts are unchanged, and the runner skips only the entry phase — the intraday risk
monitor and end-of-day reconciliation still run. Unreadable halt state fails closed
(treated as halted).

## 8. Start without broker login

**Drill.** Probe health with the broker disconnected.

**Result.** PASS. `DEGRADED`, mode stays `PAPER`, `broker_disconnected` is listed, no
crash. Stale market data is not alerted while the market is closed.

## 9. Backend liveness

**Drill.** The previous backend was found wedged — the worker process was alive at 0%
CPU while every route, including `/docs`, timed out.

**Result.** FIXED. The supervisor probes `/docs` every 60 seconds and restarts the
backend after three consecutive failures. A process that merely exists is no longer
treated as healthy.

## 10. Post-market chain failure

**Drill.** Point the backup at a missing source database, run the post-market cycle,
then repair and rerun.

**Result.** PASS. The failed run reports `PARTIAL_FAILURE`, still runs the health and
alert phase, emits `backup_failed`, and does **not** write the completion state. The
repaired rerun completes and records the session.

## 11. No live order path

**Drill.** Static check that the prospective runner never calls broker order methods,
plus the family live gate contract.

**Result.** PASS. The runner contains no `place_order` / `modify_order` /
`cancel_order` call. The family gate denies every exposure-increasing live action
unless evidence is `PASSED`, state is LIVE_ELIGIBLE, RiskEngine approves, the system
is HEALTHY and the broker is reconciled. A manual override cannot clear those
reasons. `EXIT`, `FLATTEN` and `RECONCILE` remain permitted while halted.

---

## How to repeat these drills

```bash
cd backend
python3 -m pytest tests/unit/test_snapback_recovery_drills.py -v
python3 -m pytest tests/unit/test_snapback_backup.py -v
```

The live drills (7, 8, 9) are performed against a running backend using the endpoints
named above.
