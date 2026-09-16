# Snapback Lifecycle Proof — 2026-09-16

Engineering proof, not economic proof. Nothing here is prospective evidence and
nothing here touches `prospective_freeze_1.db`.

---

## Part 1 — Historical observed replay (`study/snapback_observed_replay.py`)

### What it does and does not cover

The harness replays one opportunity from entry to exit against observed quotes, and
proves fill status, contract selection by delta, quantity in lots, statutory charges
and hedge accounting. It does **not** simulate the daily MTM loop, the session
counter, the horizon or the runner: it accepts a daily MTM series as supplied input
(`<SYMBOL>_<OPP>_DAILY_MTM`) rather than generating one. The horizon and runner rules
live in the prospective collector, so they are proved in Part 2 instead.

### Result — refusal without observed quotes

```
trade HIST-NOQUOTE   status INCONCLUSIVE
notes  "Missing observed bid/ask quote in store (modeled fallback strictly forbidden)"
pnl    0.0
```

A modeled premium was supplied (entry 180, exit 260) and was **not** used. No
synthetic fill.

### Result — accounting with observed quotes

```
trade HIST-QUOTED    status FILLED
strike 25500.0  qty 75 (lots x lot size)
gross option P&L   9000.00
statutory charges   515.33
hedge P&L        -15415.85
total             -6515.33
```

Costs are non-zero and the hedge moves against the option, as a hedge should: the long
future loses while the put gains on a falling spot.

### Signal sweep

A sweep over 10 liquid names with real daily bars (429 symbols are stored at
resolution `1d`) and a live market gate produced exactly one signal — LAURUSLABS,
`fade_up`, stretch 1.89. `evaluate_symbol()` reports only current signals within the
catch-up window, so it cannot manufacture a historical sequence; a multi-session
historical fill sequence would additionally need historical option bid/ask, which
Sterling does not hold. That is why Part 2 exists.

---

## Part 2 — Deterministic lifecycle scenarios

`backend/tests/unit/test_snapback_lifecycle_scenarios.py`, five tests, all passing.
They drive the real production functions — `process_prospective_intraday_risk()` and
`process_prospective_daily_mtm_and_exits()` — against fabricated fixtures. Fabricated
fixtures, never fabricated production evidence: they live in a temporary database that
is deleted with the test.

### Scenario A — stop, latch, liquidate, reconcile

```
entry 100.00 x 25, hedge 1 lot @ 24500
EOD mark 110.00      -> OPEN, daily_mtm written
EOD mark  95.00      -> OPEN, daily_mtm written
intraday 60.00       -> below 65.00 stop -> PREMIUM_STOP
                     -> EXIT_PENDING latched -> liquidated -> CLOSED
```

Verified: exactly two daily marks before the stop; outcome `exit_reason PREMIUM_STOP`;
option P&L exactly `(60 - 100) x 25`; costs strictly positive; and
`actual_total_pnl == option + futures - costs` to floating tolerance. The books
reconcile.

A companion test re-confirms the freeze-patch invariant: an `EXIT_PENDING` position
receives **no** EOD mark, no hedge rebalance and no session increment.

### Scenario B — horizon and runner

```
session 15 @ 160.00  (>= 1.5x entry) -> RUNNER, peak 160.00, still OPEN
later    @ 200.00                    -> peak ratchets to 200.00
later    @ 149.00    (<= 75% of peak) -> RUNNER_TRAIL_STOP -> CLOSED
```

Verified: `sessions_held >= 15`, `is_runner == 1`, peak tracked, and the exit priced
at the observed give-back bid.

The negative case is also pinned: at session 15 with the premium at 1.2x, the position
exits `HOLDING_HORIZON_EXPIRED` and is **not** promoted.

Note on the calendar: the 15th trading session after 2026-10-01 is 2026-10-23, not
2026-10-22 — 2026-10-02 and 2026-10-20 are NSE holidays. The first draft of this test
was off by one session and failed, which is the session counter doing its job.

### Scenario C — latched exit survives an unexecutable futures book

```
runner, peak 200.00
intraday 149.00 with an empty futures book (zero bid, no depth)
        -> RUNNER_TRAIL_STOP latched, EXIT_PENDING, NO outcome written
intraday 250.00 with a valid futures book
        -> CLOSED on the ORIGINAL latched bid 149.00, original reason
```

Verified: no outcome exists while the book is unexecutable, and the later, much better
250.00 bid does **not** rewrite the exit. Option P&L is `(149 - 100) x 25`. A latched
exit is a decision already made; the wait is only for executable liquidation evidence.

---

## Status after these rehearsals

```
Signal logic              PROVEN BY HISTORICAL/REPLAY TESTS
T+1 timing                PROVEN
Causal beta               PROVEN
Contract selection        PROVEN
Freshness refusal         PROVEN
Fill accounting           PROVEN BY INTEGRATION FIXTURE
Hedge accounting          PROVEN BY INTEGRATION FIXTURE
Stop/EXIT_PENDING         PROVEN
15-session horizon        PROVEN BY FIXTURE
Runner giveback           PROVEN
Cost accounting           PROVEN BY FIXTURE

Real prospective fills    NOT YET OBSERVED
Real prospective P&L      NOT YET OBSERVED
Economic gate             INCONCLUSIVE
LIVE                      BLOCKED
```

The only missing proof is actual future executable observations. No amount of further
code work can supply it.
