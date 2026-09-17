# Sterling — Mandatory Failure Drills

No handoff is signed off until every drill below has been **executed** and its
result recorded. "We believe it would do the right thing" is not a result.

Each drill has a pass condition that is observable from the operator surface —
the status line, the report, the broker app — not from reading code.

Record outcomes in [RECOVERY_DRILL.md](RECOVERY_DRILL.md) with the release tag
and date they were run against. A drill passed on an old build says nothing
about this one.

---

## A. System drills

| # | Failure injected | Pass condition | Fails if |
|---|---|---|---|
| A1 | Market feed made stale | `DATA_ERROR`; new entries blocked; existing positions still managed | Any new entry is admitted, or an open position stops being managed |
| A2 | Broker disconnected | `BROKER_ERROR` or `SAFE_MODE`; reconnect **and** reconcile required before new risk | New risk allowed on reconnect alone |
| A3 | Duplicate submit retried | The same intent is returned; exactly one order exists | A second order reaches the broker |
| A4 | Partial fill | Filled quantity tracked exactly; protection sized from the **actual** fill | Protection sized from the intended quantity |
| A5 | Protection not confirmed | Position marked `UNPROTECTED`; repaired or exited within the safety window | It is left open and unprotected past the window |
| A6 | Restart with an open broker position | Startup in SAFE_MODE; reconciliation runs; monitoring restored before new entries | Sterling resumes trading before reconciling |
| A7 | Unknown pending order | Broker is queried; state resolved; nothing resubmitted | An order is resubmitted |
| A8 | Evidence DB corrupted or removed | `EVIDENCE_ERROR`; no new entries; backup/repair workflow offered | Trading continues while records are not durable |
| A9 | Disk space exhausted | New evidence writes and new trading blocked **before** data is silently lost | A write is dropped without a gap code |
| A10 | Clock/timestamp anomaly | Freshness cannot be trusted; new entries blocked | Stale data is treated as fresh |

### How to run them safely

- Run every drill in PAPER, with real-money entries disabled — which they are.
- A2, A6 and A7 need a broker session. Use a session with no open real
  positions, or run them outside market hours.
- A8 and A9 touch storage. Take a backup first and prove it with
  `./sterlingctl restore-check` **before** you break anything.
- A9 is easiest against a small loopback filesystem, not the real disk.

### Automated coverage

Some of these are asserted in the test suite and re-run with it:

```bash
cd backend && PYTHONWARNINGS=ignore .venv/bin/python -m pytest \
  tests/unit/test_snapback_recovery_drills.py \
  tests/unit/test_backup_manifest_restore_proof.py -q
```

A green test proves the code path. It does not prove the **operator** can reach
a safe state, which is what section B is for. Both are required.

---

## B. Operator sign-off drills

The future operator performs these **from the runbook and the command line
alone**. No code editing, no help from a developer, no reading source.

| # | Drill | Pass condition |
|---|---|---|
| B1 | Start and check | Starts Sterling and correctly says whether the state is NORMAL or blocked |
| B2 | Manual SAFE_MODE | Blocks new risk, and can explain that existing positions are still managed |
| B3 | Broker mismatch | Inspects the broker, runs `reconcile`, and **refuses** to override an unresolved state |
| B4 | Market-data error | States that stale data means no new trading, without being prompted |
| B5 | Restart with a position | Recovers while new entries stay blocked |
| B6 | Backup check | Verifies the latest backup and runs `restore-check` |
| B7 | Emergency exit path | Identifies actual broker exposure and follows the documented exit or escalation path |

If any drill requires Python, Git or SQLite knowledge to complete, the drill
has failed — and what failed is the **documentation**, not the operator.

---

## C. Evidence drills

Less dramatic, and the most often skipped.

| # | Drill | Pass condition |
|---|---|---|
| C1 | Interrupt a session mid-scan | The day is `INCOMPLETE` in the report, with a gap code |
| C2 | Re-run the scanner for that same day | The day stays unusable. **A clean rescan must not erase the earlier gap.** |
| C3 | Insert a row with no lane | It appears as an unattributed row, and is counted in no lane |
| C4 | Insert a modelled row into a lane | It is excluded from that lane's trade count and reported as non-promotable |
| C5 | Change a strategy rule and re-freeze | `./sterlingctl verify` reports identity drift, naming the lane and the moved hash |

C2 and C5 are the two that protect the sample. C2 stops a bad day being washed
away; C5 stops two different rule sets pooling into one experiment that looks
big enough to promote.

---

## D. Sign-off record

| Drill set | Release tag | Run by | Date | Result |
|---|---|---|---|---|
| A1–A10 | | | | |
| B1–B7 | | | | |
| C1–C5 | | | | |

Handoff is not complete until all three rows read PASS against the **current**
release tag.
