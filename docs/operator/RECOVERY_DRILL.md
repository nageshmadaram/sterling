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

## 12. Fault injection (Block 6, 2026-09-16)

Each drill below injects a dependency failure that a healthy-looking system gives
no warning about. Automated in `backend/tests/unit/test_snapback_fault_injection.py`.

**Disk full during an outcome write.** PASS. The insert raises and the transaction
leaves no partial outcome row behind, so a half-written close cannot be read later
as a completed trade.

**Corrupt evidence database.** PASS. `check_database()` reports `writable: False`
and preflight blocks startup rather than appending to a damaged file.

**Locked database.** PASS. The write probe never reports a pass alongside
`writable: False`; a busy writer surfaces as a failed probe, not an empty result.

**Calendar outage.** PASS. The post-market cycle returns `CALENDAR_UNAVAILABLE`.
It does not assume a trading day, and it does not assume a holiday.

**Backup failure (no space left on device).** PASS. The session is not marked
complete and no state file is written, so the next cycle retries rather than
recording the day as packaged.

**Alert transport outage.** PASS. Backup and report still complete and the cycle
records `alert_dispatch_failed`. Evidence is the product; notification is not.

**Broker outage during reconciliation.** PASS. The snapshot is `clean: False`.
No broker answer is not a clean book; it is no information.

**Quote outage.** PASS. A required quote that never arrived lowers coverage to
50% in the two-event case rather than being excluded from the denominator. A
missing quote is never priced at zero.

**Unsynchronised clock.** PASS. Preflight fails on the `clock` check. Every
freshness and session-boundary decision is made against this clock.

---

## How to repeat these drills

```bash
cd backend
python3 -m pytest tests/unit/test_snapback_recovery_drills.py -v
python3 -m pytest tests/unit/test_snapback_backup.py -v
python3 -m pytest tests/unit/test_snapback_fault_injection.py -v
python3 -m pytest tests/unit/test_snapback_preflight.py -v
```

The live drills (7, 8, 9) are performed against a running backend using the endpoints
named above.
