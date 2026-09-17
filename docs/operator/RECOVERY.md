# Sterling — Recovery Runbook

One page per failure. Each says what you should see, what to do, and what would
make it worse.

For drills already executed against a specific build, see
[RECOVERY_DRILL.md](RECOVERY_DRILL.md). For the drills that must pass before
handoff sign-off, see [FAILURE_DRILLS.md](FAILURE_DRILLS.md).

---

## 1. Restart with an open broker position

**Expected:** Sterling starts in SAFE_MODE, reconciles against the broker, and
restores position monitoring before it will consider any new entry.

1. `./sterlingctl status` — expect `SAFE_MODE` or `RECOVERY_REQUIRED`.
2. `./sterlingctl reconcile`.
3. Read the mismatch list. Every line names an instrument and both quantities.
4. Confirm in the broker app that Sterling's view now matches reality.
5. Only when it is clean: `./sterlingctl safe off --ack`.

**Makes it worse:** clearing SAFE_MODE before reconciling. The restart did not
tell Sterling what happened while it was down; the broker did not tell it
either, until you asked.

## 2. Broker mismatch

**The broker is the source of truth. Always.**

1. `./sterlingctl safe on "broker mismatch"`.
2. Open the broker app and write down every position and open order.
3. `./sterlingctl reconcile` and compare against what you wrote down.
4. If Sterling holds a position the broker does not: the position is gone.
   Do not re-open it.
5. If the broker holds a position Sterling does not: it is real exposure. Make
   sure it is protected, or exit it.
6. If an order's state is unknown: **query the broker**. Never resubmit.

**Makes it worse:** resubmitting. A duplicate order turns one unknown position
into two real ones.

## 3. Partial fill

**Expected:** Sterling tracks the exact filled quantity and sizes protection
from the real fill, never from the quantity it asked for.

1. Confirm the filled quantity in the broker app.
2. `./sterlingctl reconcile`.
3. Confirm protection covers the **filled** quantity, not the intended one.
4. If protection cannot be confirmed, treat it as §5.

**Makes it worse:** assuming the full fill. Protection sized for 75 when 25
filled leaves the position over-hedged; the reverse leaves it naked.

## 4. Stale market feed

**Expected:** `DATA_ERROR`, new entries blocked, existing positions still
managed.

1. Check whether the market is actually open.
2. Check the data connection.
3. Do not clear the error to "test" whether it is still stale. Freshness is
   measured, not guessed.
4. Existing positions keep being managed throughout. That is by design.

**Makes it worse:** trading on the last known price. A stale quote and a quiet
market look identical and behave completely differently.

## 5. Protection not confirmed

**Expected:** the position is marked `UNPROTECTED`, and the safety window starts
counting.

1. Repair protection immediately: re-place the protective order.
2. If it cannot be placed within the safety window, **exit the position**.
3. Record which happened.

**Makes it worse:** waiting to see. An unprotected real position is the single
largest source of avoidable loss in this system.

## 6. Evidence database corrupt or unavailable

**Expected:** `EVIDENCE_ERROR`, no new entries, existing positions still
protected.

1. `./sterlingctl safe on "evidence store"`.
2. `./sterlingctl restore-check` — does the latest backup restore cleanly?
3. If yes, restore it into place following §7.
4. If no, try the previous backup. Keep going back until one passes.
5. Everything between the last good backup and now is a **permanent evidence
   gap**. Record it. Do not re-run the scanner to manufacture replacement rows —
   a rescan produces different data and silently rewrites history.

**Makes it worse:** trading while records are not durable. The trades would
happen, and no lane could ever count them.

## 7. Restoring a backup

```bash
./sterlingctl restore-check    # proves the latest backup before you rely on it
```

`restore-check` restores into a temporary directory, opens every database,
verifies schema and compares row counts against the manifest. It never touches
the live database.

To restore for real:

1. Stop Sterling. `./sterlingctl stop`
2. Move the damaged database aside — **move, never delete**. It may still hold
   rows the backup does not.
3. Copy the backup database into place.
4. `./sterlingctl doctor`.
5. Start Sterling and reconcile before allowing new entries.

A backup that has never been restored is not a backup. It is a hypothesis.

## 8. Disk full

**Expected:** new evidence writes and new trading are blocked **before** any
silent data loss.

1. Free space. Old backups are the usual answer; keep at least the two most
   recent that have passed `restore-check`.
2. `./sterlingctl doctor`.
3. Check the daily report for gap codes covering the period.

**Makes it worse:** deleting evidence to make room.

## 9. Clock or timestamp anomaly

**Expected:** freshness cannot be trusted, so new entries are blocked.

1. Check the machine's clock and timezone.
2. Fix the clock, then restart Sterling.
3. Records written during the anomaly are suspect. Check the daily report for
   the affected sessions and leave any gap codes in place.

**Makes it worse:** "correcting" timestamps in the database. Every freshness
check and every holding-time measurement is derived from them.

## 10. Duplicate submit after a retry

**Expected:** the same intent is recognised and returned. No second order is
created.

If you see two orders for one decision, that is a defect, not an operator
error. Block new risk, reconcile, exit or protect the surplus position, and
escalate to the technical contact with the intent ID and both order IDs.
