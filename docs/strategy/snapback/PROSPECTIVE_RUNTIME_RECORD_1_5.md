# Snapback Prospective Runtime Record — 1.5

The final engineering release. Supersedes 1.4, which an independent audit found
carried seven material defects — four of them P0 — none of which could be seen
from a green test suite.

## Historical strategy identity — UNCHANGED

```
strategy commit (manifest)  5a1354202e2c960c66b7003fce9cb80abd152008
config_hash                 6ecbeb53e9768a91
rule_hash                   e03ddf75f29463a8
manifest version            snapback_reality_v1.2
```

No parameter has been tuned in any release since the original freeze.

## Tag lineage — NONE OF THESE MOVE

```
snapback-prospective-freeze-1.0    9e989dd910995deb5e77385b983e5992c58883c0
snapback-prospective-runtime-1.1   136a9b7195cdb7047d3707fada98e3f53fde6765
snapback-prospective-runtime-1.2   572972ac679610d5474f9a0e1a170b8ee6b7e65a
snapback-prospective-runtime-1.3   bed3e6298d6d4504ab6fb415a896e365c3dffb00
snapback-prospective-runtime-1.4   fee388afb9bb56af801296f938a2a27e6013a482
snapback-prospective-runtime-1.5   git rev-parse snapback-prospective-runtime-1.5^{commit}
```

## What 1.5 closes

**P0-CAPITAL — a missing reconciliation read as a clean one.** Readiness did
`snapshot is None or snapshot.clean`, so "we have never checked" and "we checked
and the books agree" were the same answer. Behind the economic gate that is a
live race: a restarted process with PASSED evidence and a connected broker could
read LIVE_ELIGIBLE before any reconciliation had run. Absent, stale (TTL 60s),
wrong-account, unparseable and unreadable now all mean not clean. This also fixed
a real `TypeError`: the arm endpoint already called `latest_reconciliation` with
an account argument the function did not accept, invisible because the endpoint
test replaced the admission function wholesale.

**P0-CAPITAL — reconciliation compared the wrong book.** It read the *paper*
prospective warehouse and asserted `sterling_intents=[]`. That would have
reported a real live position as an unknown external one, a paper position as a
missing broker one, and claimed nothing was in flight without consulting the
order journal. It now reads the canonical position store and unresolved order
journal, and an unreadable position store raises rather than returning an empty
list. `PROTECTION_MISSING` was declared as a mismatch code and never emitted; it
now fires for any live position without *ACTIVE* protection.

**P0-EVIDENCE — authority was fail-open everywhere except the one writer fixed
in 1.4.** A missing flag defaulted to 1, a missing source to PROSPECTIVE_PAPER,
and the exclusion list was a deny-list, so an empty dict counted as evidence and
any future source would too. Now an allow-list over explicit facts, with the
schema defaulting to 0 across every table and all thirteen INSERT sites deriving
the value from their own source. Schema version 2.

**P0-EVIDENCE — promotion did not prove build identity on its inputs.** Only
outcomes were checked, so a 1.4 outcome could be priced by a 1.3 cost event.
Every input class is now filtered on the expected build, and an exclusion is an
error rather than a silent drop.

**P0-EVIDENCE — G24 inferred hedging from P&L.** A hedge that opened and closed
flat nets to zero, so a real hedge looked like no hedge and its two cost events
stopped being required — understating costs on exactly the trades where the
hedge did its job. Hedging is now read from execution artifacts.

**P0-EVIDENCE — the cost model was wrong for part of the universe.** The
schedule took no exchange while the universe includes SENSEX, whose options
resolve on BFO; and brokerage was a percentage for options, which Zerodha charges
flat. A one-lot option trade of ₹10,000 turnover was charged ₹3 instead of ₹20 —
₹17 per leg of paper P&L the strategy never had, on exactly the small orders a
family-sized allocation places. `snapback_costs_v2.py` is a new schedule version,
not an edit: the old one is preserved so earlier results stay reproducible.

**P0-LIVE — nothing consumed an ARMED plan.** `submit_plan()` had no production
caller, submitted only the option leg, and passed no RiskApproval.
`snapback_live_executor.py` is now the single path, and its ordering is the
safety argument: revalidate → reconcile → risk approve → option fill → hedge
sized to the CONFIRMED fill → protection → OPEN. `submit_plan()` is deleted
rather than left beside it.

**Evidence semantics.** The ≥60 session check counted distinct trade entry days,
so a fully observed session with no trade did not count. It now uses the observed
session count; entry dates keep their real job as the bootstrap's day clustering.

**Operations and product.** A promotion verdict whose record cannot be stored
degrades to INCONCLUSIVE. Family Mode ignores the clock and dirty-worktree escape
hatches. Family Mode carries exactly one strategy — Snapback — and refuses to arm
any other, because every other engine here is unvalidated, uncalibrated, or
measured as economically negative.

**Succession.** `snapback_succession.py` implements the handover. Sterling is
built to outlive its author and deliberately not built to keep trading an account
after its holder has died. A rebind is refused while any exposure or unresolved
order remains, requires a named confirmation and a genuinely different account,
never reuses the previous holder's credentials, and preserves evidence read-only
while resetting all account-specific state.

## Verification at freeze

```
backend      4751 passed, 6 skipped
frontend     1539 passed (142 files)
GitHub CI    green at the tagged head, all three browsers
secret scan  no committed credentials
worktree     clean
```

Release rehearsal, all three checks the audit named:

```
old catch-up signal  → stored LIVE_CATCHUP_REPLAY / authoritative=0,
                       no contradictory rows, excluded from the gate input
BFO SENSEX option    → BSE transaction rate, flat Rs 20 brokerage,
                       priced differently from the same trade on NFO
PASSED + no recon    → refused at readiness, at the live gate, and at the executor
```

## What CI caught that local testing did not

A test asserting `_default_worktree_clean()` returns False under Family Mode
passed locally only because the developing worktree happened to be dirty. On CI
the tree is clean, so it failed. The contract is that Family Mode does not
consult the hatch at all; that is what is asserted now. A test whose outcome
depends on the machine it runs on proves nothing about the code.

## Remaining open items

1. **The live path has never run against a broker.** Its correctness rests on
   unit tests, a broker spy and the specification. It cannot run while evidence
   is INCONCLUSIVE, which is the point, but it is untested against reality.
2. **Branch protection is bypassable by admins.** `main` requires the release
   gates and a pull request, but `enforce_admins` is off and this release was
   pushed directly. Process debt, not an evidence-validity problem.
3. **Option prices in the historical study are modeled**, not replayed from real
   option books. This is the largest remaining uncertainty in the strategy's
   case, and no amount of runtime engineering addresses it.

## What this release does not claim

Nothing about edge.

Snapback's historical result — 746 fixed-OOS trades over 2,232 sessions, +8.38%
mean per entry day, day-clustered 95% CI +3.03% to +15.14%, permutation p=.0164 —
is the strongest evidence in this repository by a wide margin, and it is still
not proof. It fails deflated Sharpe and calendar-year consistency, and it rests
on modeled option premiums.

The distribution matters as much as the mean. Roughly 39% of trades won, the
median trade lost, and the top 1% of trades produced more than all the profit.
A strategy shaped like that can be sound over years and still be punishing over
months. **It should not be treated as a salary**, and Sterling should not be a
household's only income even if it eventually passes.

At the prospective rate of roughly 0.33 trades per session, the gate's 300-trade
requirement is several years of observation. That threshold should not be lowered
because time is short: lowering it converts a deadline into someone else's
financial risk.

## After this tag

Two tracks, and they must not be merged into one claim.

**Track A — prospective observation.** Let the frozen runtime collect. No
parameter edits, no strategy changes after a winning or losing trade.

**Track B — execution-reality validation.** Replay the already-frozen signal
dates through real listed contracts, real option prices, real spreads, exchange-
correct charges and the exact family allocation. This creates no new independent
alpha evidence — the dates are already known — but it attacks the biggest
remaining assumption directly.

Change code only if real prospective operation exposes a P0-CAPITAL,
P0-EVIDENCE or P0-OPERABILITY defect, and treat that as warranting a new runtime
tag and a new experiment, as 1.3 did.
