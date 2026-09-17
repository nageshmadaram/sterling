# Sterling — Start Here

You do not need to read code, know Python, or understand options to run Sterling
safely. You need this page, and the four pages it points to.

If you only remember one thing, remember this:

> **If Sterling is not NORMAL, the correct action is never "make it trade".
> The correct action is "keep new exposure blocked until the uncertainty is
> resolved."**

---

## 1. What Sterling is

Sterling watches a fixed list of Indian stocks and indices and looks for two
specific setups:

- **Snapback** — a sharp move away from a stock's own recent range that tends to
  snap back.
- **Triple SuperTrend** — three trend lines of different speeds agreeing on a
  direction at the same time.

Each strategy is run in five **modes**, which differ only in how long a trade is
meant to be held:

| Mode | Expected hold | Hard exit |
|---|---|---|
| Ultra Scalping | 1–10 minutes | 20 minutes |
| Scalping | 10–45 minutes | 90 minutes |
| Intraday | 45 minutes – 4 hours | same session, always |
| Overnight | 1–3 sessions | 5 sessions |
| Swing | 3–10 sessions | 15 sessions |

Two strategies × five modes = **ten lanes**. A lane is a separate experiment
with its own records and its own verdict. Results never mix between lanes.

## 2. What Sterling is doing today

**Recording, not trading.** Real-money entries are disabled in the code itself,
not by a setting, and no lane has earned the right to turn them on.

Run `./sterlingctl lanes` to see the current state of all ten. Most will say
`no` under "trades?", with a reason. That is the system working, not a fault.

## 3. The nine commands

Everything is one command. Run them from the Sterling folder.

```bash
./sterlingctl status          # what Sterling is doing right now
./sterlingctl doctor          # every safety check; non-zero exit means DO NOT TRADE
./sterlingctl start           # runs doctor first, refuses to start if it fails
./sterlingctl stop
./sterlingctl safe on         # block new trades immediately
./sterlingctl safe off --ack  # allow them again (only after you fixed the cause)
./sterlingctl reconcile       # compare Sterling's view against the broker's
./sterlingctl backup          # checksummed backup with a manifest
./sterlingctl restore-check   # prove the latest backup actually restores
```

Four more exist for evidence and release identity:

```bash
./sterlingctl manifest        # which build and which ten lane identities are frozen
./sterlingctl verify          # has anything drifted from the frozen release?
./sterlingctl report          # today's evidence completeness report
./sterlingctl lanes           # the ten lanes and whether each may open a trade
```

## 4. What the statuses mean

| Status | Meaning | What you do |
|---|---|---|
| `NORMAL` | All mandatory health checks passed | Observe. Nothing. |
| `SAFE_MODE` | New risk is blocked by safety policy | Do not override. Read the reason. Reconcile if told to. |
| `DATA_ERROR` | Market data cannot support new exposure | Do not trade. Check the feed and the market session. |
| `BROKER_ERROR` | Broker state or connectivity is uncertain | Open the broker directly. Reconcile. Keep new risk blocked. |
| `EVIDENCE_ERROR` | The evidence store cannot be trusted | Stop new entries. Protect open positions. Repair storage. |
| `RECOVERY_REQUIRED` | A restart or state mismatch needs reconciliation | Run `reconcile`. Confirm zero unknown exposure. |

A `doctor` exit code of **2** means a check could not be run at all. That is not
a pass. Treat it exactly like a failure.

## 5. What you must never do

- Never turn SAFE_MODE off to "get a trade in".
- Never resubmit an order whose state you do not know. Ask the broker first.
- Never increase quantity after losses.
- Never edit a frozen strategy's rules. A changed rule is a **new** lane, with a
  new identity and a new sample that starts at zero.
- Never delete or edit a losing trade or a failed session. Failures are the
  evidence.

## 6. Where to go next

| You want to | Read |
|---|---|
| Run the normal day | [DAILY_RUNBOOK.md](DAILY_RUNBOOK.md) |
| Something is wrong right now | [EMERGENCY_CARD.md](EMERGENCY_CARD.md) |
| Recover from a crash, mismatch or corrupt database | [RECOVERY.md](RECOVERY.md) |
| Find out who holds which login | [ACCESS_CONTINUITY.md](ACCESS_CONTINUITY.md) |
| Understand broker login and the daily family routine | [FAMILY_RUNBOOK.md](FAMILY_RUNBOOK.md) |
| Know when a lane is allowed real money | [../evidence/PROMOTION_POLICY.md](../evidence/PROMOTION_POLICY.md) |
| See what each lane actually is | [../evidence/LANE_MANIFESTS.md](../evidence/LANE_MANIFESTS.md) |

## 7. What "handoff-ready" does not mean

It does not mean any lane is profitable. It does not mean live trading should be
switched on. It means Sterling is ready to find out the truth without hiding
failures, and to stay safe while doing so.
