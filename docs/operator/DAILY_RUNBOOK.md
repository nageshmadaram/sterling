# Sterling — Daily Runbook

Three short routines: before the market, during it, and after it. Each step says
what to do when the answer is wrong.

New here? Read [START_HERE.md](START_HERE.md) first.

---

## Before the market

| # | Step | Command | If it is wrong |
|---|---|---|---|
| 1 | Start Sterling | `./sterlingctl start` | It refuses when `doctor` fails. That is correct. Fix the failure, do not force the start. |
| 2 | Broker login | as in [FAMILY_RUNBOOK.md](FAMILY_RUNBOOK.md) §4 | No login means no live broker facts. Sterling stays blocked; that is safe. |
| 3 | Health | `./sterlingctl doctor` | Exit 1 = failed. Exit 2 = **could not check**, which is not a pass. Either way: do not trade. |
| 4 | Release identity | `./sterlingctl verify` | Drift means the running code is not the frozen release. Stop and read the drift list before anything else. |
| 5 | Safety switch | `./sterlingctl safe status` | If SAFE_MODE is on, read the reason. Only turn it off once that exact cause is fixed. |
| 6 | Broker reality | open the broker app; compare positions and orders | Anything Sterling does not know about: run `./sterlingctl reconcile` before allowing new activity. |
| 7 | Yesterday's backup | `./sterlingctl restore-check` | A failure means the evidence is not durable. Block new entries and repair storage. |

Doctor must be clean **and** the broker must hold nothing unexpected before any
new strategy activity is allowed.

## During the market

Watch the system state, not the running P&L.

- A losing open trade is not a reason to intervene. The lane's rules are frozen,
  including its exits, and overriding them destroys the sample you are paying
  for in time.
- `BROKER_ERROR`, `DATA_ERROR` or `EVIDENCE_ERROR`: keep new risk blocked and
  follow the action shown on screen. Do not diagnose by guessing.
- If a real broker position disagrees with Sterling, **the broker is right**.
  Reconcile before anything else happens.
- If you do not know what state an order is in, do not resubmit it. Ask the
  broker what it holds.

To stop new trades immediately, at any moment:

```bash
./sterlingctl safe on "reason in your own words"
```

This never blocks exits, protection repair, or reconciliation. A blocked exit
would be more dangerous than the problem that triggered safe mode.

## After the market

| # | Step | Command |
|---|---|---|
| 1 | Confirm every non-overnight mode is flat | `./sterlingctl status` |
| 2 | Review unresolved positions and evidence gaps | `./sterlingctl report` |
| 3 | Keep today's completeness report | `./sterlingctl report > reports/$(date +%F).txt` |
| 4 | Back up the evidence | `./sterlingctl backup` |
| 5 | Prove the backup restores | `./sterlingctl restore-check` |

Then stop. Do not tidy the records.

- Do not delete a losing trade.
- Do not re-run the scanner to clear a failed session. **A gap is permanent by
  design.** A later clean rescan does not erase an earlier incomplete session,
  and the report will keep showing it.

## Reading the daily report

```
Date: 2026-09-18
Release: handoff-1.0 (fdecbba59b53)
Session: COMPLETE
Universe: 14/14 (100.0%)
Quote coverage: 98.0%
Signals: 3 found, 11 refused
Positions: 1 opened, 1 closed, 0 unresolved
Evidence gaps: none
Unattributed rows: 0
Usable as evidence: YES
```

| Line | What makes it wrong |
|---|---|
| `Session` | Anything but `COMPLETE` means the day does not count toward any lane's 60-session requirement. |
| `Universe` | Fewer evaluated than expected means the scan did not see the whole list. |
| `Quote coverage` | `unknown` is **not** 100%. It means the session could not say how many observations it needed. |
| `Unresolved` | Must be 0. An unresolved position is exposure nobody can account for. |
| `Evidence gaps` | Any code here makes the day unusable as evidence, permanently. |
| `Usable as evidence` | `NO` means today added nothing to any lane's sample, whatever the P&L says. |

The ten lane rows below it never pool. 299 Swing trades plus 1 Scalping trade is
not 300 Swing trades, and the report will never present it as one.

## When an alert arrives

Every alert states four things. If one is missing, that is a defect — report it.

1. **What happened**, in plain language.
2. **Whether new trading is blocked.**
3. **Which positions are affected** (or "none identified", which is not the same
   as "none").
4. **The next safe action.**

| Severity | Means | You |
|---|---|---|
| `INFO` | Routine. Daily start healthy, backup done, no signals. | Read it in the digest. |
| `WARNING` | Something needs attention today. Lane blocked, margin unavailable, repeated no-fill, wide spread. | Look at it during the day. |
| `CRITICAL` | Broker mismatch, unprotected fill, unresolved exposure, evidence DB failure, daily-loss breach. | Act now. New exposure is already blocked. |

**Every CRITICAL blocks new exposure automatically.** You do not need to react
fast to make Sterling safe; it is already safe. You need to react correctly.

One root cause arrives as **one** incident. A dead database also fails the
backup, the report and the session package, and those are listed underneath the
cause as "downstream of the above". Fix the cause; the effects go with it.

## Weekly, once a week

Run through [../evidence/PROMOTION_POLICY.md](../evidence/PROMOTION_POLICY.md)
§"Weekly review" and record the answers. Descriptive reporting is not promotion:
reading a good week does not move a lane forward.
