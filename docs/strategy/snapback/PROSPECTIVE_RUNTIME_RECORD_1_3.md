# Snapback Prospective Runtime Record — 1.3

The release the prospective experiment actually runs on. Supersedes
`PROSPECTIVE_RUNTIME_RECORD_1_2.md`, which documented a tag that never
identified a running build.

## Historical strategy identity — UNCHANGED

```
strategy commit (manifest)  5a1354202e2c960c66b7003fce9cb80abd152008
config_hash                 6ecbeb53e9768a91
rule_hash                   e03ddf75f29463a8
manifest version            snapback_reality_v1.2
cost schedule               zerodha_fno_costs_2026_04  (630a4de9ad143e2c)
```

No parameter has been tuned in this release or the two before it. Lookback 20,
stretch 1.5 ATR, RV cap 70%, bearish market filter, EMA 50, delta 0.70, DTE
40–60, 15-session horizon, 35% premium stop, 1.5× runner, 25% give-back,
index-futures hedge.

## Tag lineage — NONE OF THESE MOVE

```
snapback-prospective-freeze-1.0    9e989dd910995deb5e77385b983e5992c58883c0
snapback-prospective-runtime-1.1   136a9b7195cdb7047d3707fada98e3f53fde6765
snapback-prospective-runtime-1.2   572972ac679610d5474f9a0e1a170b8ee6b7e65a
snapback-prospective-runtime-1.3   the commit this document was committed in
```

`runtime-1.2` is kept deliberately. It records exactly where the preflight
family-account defect was found — after tagging, by running the tagged build
against the live service rather than against its own tests. Moving it would
destroy that provenance.

## Why 1.2 was not the freeze

An independent re-check found three things wrong with the 1.2 claim, all of them
correct:

1. `runtime-1.2` pointed at `572972ac6` while a genuine code fix (`266fd0218`)
   landed after it. The tag did not identify the running executable.
2. The dedicated `snapback-release-gate` passed, but the repository's normal CI
   failed on the same head: Playwright failed in all three browsers.
3. The freeze record itself still listed six open gaps, which is not a freeze.

Because `runtime_build_sha` is the whole git HEAD, even a documentation-only
commit after a tag changes runtime identity. That is why this document is
committed *before* `runtime-1.3` is cut, not after.

## What was closed for 1.3

**The Family screen was unreachable.** Removing a swallowed
`.click().catch(() => {})` from the end-to-end helper turned a misleading failure
into the real one: `RAIL_ITEMS` had no `more` entry, so `MorePane` — Family,
Bids, Funds, Mutual Funds, Alerts — could only be reached by changing the default
section in settings or dispatching a `kite-nav-click` event. An operator could not
click to the Family screen at all. The catch() had been reporting this as "the
Family tab does not exist". Fixed, with stable test ids, and green in Chromium,
Firefox and WebKit in GitHub CI.

**The runner was gated on the wrong preflight.** `run_startup_preflight()` checks
config identity only; clock sync, disk headroom, schema version and evidence-meta
identity lived in `run_preflight()` behind `/health/ready`. Readiness could
answer 503 while the runner started anyway and wrote evidence under exactly those
conditions. Readiness the runner ignores is a label, not a gate. `main.py` now
reads the composed result.

**Cost cardinality (G24) is enforced.** Summing the ledger per trade catches a
wrong total but not a missing leg whose cost was small, a duplicate that
double-charges, or an orphan attached to a leg that never executed — each changes
the cost stress the gate applies without moving the sum enough to notice. Every
executed leg now needs exactly one cost event, and a violation raises rather than
adjusting a number quietly. Whether a trade hedged is read from the outcome,
never inferred from the presence of a hedge cost, which would let an orphan
charge prove its own legitimacy.

**The gate shim is a delegate.** `study/snapback_forward_gate.py` was a second
direct caller of the gate and kept its own copy of the verdict mapping, as did
`PromotionService`. The mapping moved into the authority as `verdict_for()`, the
shim reaches the gate only through `evaluate_with_verdict()`, and it is out of
`ALLOWED_GATE_CALLERS`.

**The live-only gaps are implemented.** A durable plan store with revisions and
idempotent arming; `POST /api/v1/snapback/plans/{plan_id}/arm` distinguishing
404/409/410/403; protection states `NONE → REQUIRED → SUBMITTING → ACTIVE →
MODIFY_PENDING/RECONCILING`; and the partial-fill-aware exit sequence. Built now
because building them later would change the runtime SHA mid-sample. None of them
can execute today: live arming is refused while evidence is INCONCLUSIVE.

**Unattended boot.** Everything either side of Zerodha's daily 2FA now happens
with nothing typed. Two defects surfaced by running the boot path rather than
reading it: `STERLING_SECRET_KEY` was missing from the documented setup, so the
stored Kite secret was encrypted under a dev key published in the source; and
`kite_accounts.bootstrap()` must precede `seed_from_env()`, or the account
persists without `refresh_token_enc` and unattended renewal can never work.

## Executable runtime identity

```
runtime tag        snapback-prospective-runtime-1.3
build SHA          resolve with:
                     git rev-parse snapback-prospective-runtime-1.3^{commit}
                   A document cannot state the hash of the commit that contains
                   it, so the tag is the authority and this file points at it.
                   STERLING_EXPECTED_BUILD_SHA must equal that value exactly.
experiment id      prospective_runtime_1_3
evidence database  data/snapback/prospective_runtime_1_3.db
evidence schema    user_version = 1
allocation capital INR 1,000,000
bind host          127.0.0.1
```

## Verification at freeze

```
backend      4630 passed, 6 skipped
frontend     1539 passed (142 files)
GitHub CI    all green at the tagged head
             Backend 3.12 · Backend 3.13 · Frontend Type Check · Vitest
             E2E Playwright chromium · firefox · webkit
             Parity Matrix directional · kite · ORB Safety Gate
             Snapback release gate · Sterling Release Gate
secret scan  no committed credentials
worktree     clean
```

## Remaining open items

1. Branch protection on `main` requires the two release gates and a pull
   request. `enforce_admins` is deliberately left off so a genuine P0 can be
   fixed without a blocked emergency; that is a policy choice, not an oversight.
2. The live execution path is implemented but has never run against a broker.
   Its correctness rests on unit tests and the specification, not on observation.
   It cannot run until evidence is PASSED.

## What this release does not claim

Nothing about edge. Zero prospective sessions have been collected against this
runtime; the database is empty by design. The promotion gate requires at least 60
fully observed sessions, at least 300 completed trades, a positive day-clustered
lower 95% CI, positivity under 2× cost stress, drawdown within 10%, at least 95%
quote coverage and zero unresolved exposure. None are met.

The honest verdict is INCONCLUSIVE. The strategy has not been shown to have an
edge. It has been made capable of producing evidence that would show whether it
does, and of failing loudly rather than quietly if it cannot.

## After this tag

Do not commit while the confirmatory sample is being collected, unless a real
observation exposes a genuine P0 — and that warrants a new runtime tag and a new
experiment, not an amendment to this one.
