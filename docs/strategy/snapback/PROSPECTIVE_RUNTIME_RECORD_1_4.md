# Snapback Prospective Runtime Record — 1.4

Supersedes `PROSPECTIVE_RUNTIME_RECORD_1_3.md`. 1.3 was a correct release that a
real observation invalidated within minutes of starting — which is what the
observation gate is for.

## Historical strategy identity — UNCHANGED

```
strategy commit (manifest)  5a1354202e2c960c66b7003fce9cb80abd152008
config_hash                 6ecbeb53e9768a91
rule_hash                   e03ddf75f29463a8
manifest version            snapback_reality_v1.2
cost schedule               zerodha_fno_costs_2026_04  (630a4de9ad143e2c)
```

No parameter has been tuned across any of these releases.

## Tag lineage — NONE OF THESE MOVE

```
snapback-prospective-freeze-1.0    9e989dd910995deb5e77385b983e5992c58883c0
snapback-prospective-runtime-1.1   136a9b7195cdb7047d3707fada98e3f53fde6765
snapback-prospective-runtime-1.2   572972ac679610d5474f9a0e1a170b8ee6b7e65a
snapback-prospective-runtime-1.3   bed3e6298d6d4504ab6fb415a896e365c3dffb00
snapback-prospective-runtime-1.4   resolve with:
                                     git rev-parse snapback-prospective-runtime-1.4^{commit}
```

## Why 1.3 lasted one runner tick

1.3 was tagged with all six audit items closed and GitHub CI green at the tagged
head. Its fresh evidence database was then inspected before declaring it clean,
and it was not clean: the runner's first tick had written a LAURUSLABS signal
whose bar closed on **2026-09-11** — five sessions earlier — with
`source = LIVE_CATCHUP_REPLAY` and `authoritative = 1`.

Nothing was miscounted. `is_authoritative_row()` inspects the source as well as
the flag, so the gate and every report already excluded it. But the column exists
precisely to record whether a row may enter the sample, and it said the opposite
of the source sitting beside it. A database that says two things at once is one
query away from saying the wrong one.

The cause was a fail-open default. `record_opportunity()` never named the
`authoritative` column in its INSERT, and the schema declares it
`NOT NULL DEFAULT 1`. **Omission conferred authority** — the exact inversion of
fail-closed, in the one column the whole experiment's validity rests on.

A second hole sat behind it: `STERLING_DATASET_START` was never set, so
`classify_signal_authority()`'s `before_dataset_start` rule could not fire at
all. The only thing keeping an older signal out of the sample was the
single-session recency check — one rule guarding the boundary that decides
whether this experiment is prospective at all.

Neither test suite caught either. The tests injected the writer or asserted the
reader; nothing asserted what the database actually contained after the runner
ran. It was found by looking at the rows.

## The fix

- `resolve_authoritative_flag()` derives the column from the source. A replay
  source can never be authoritative, whatever a caller asks for. The asymmetry is
  deliberate: wrongly including replayed history corrupts the sample, while
  wrongly excluding one signal only makes it smaller.
- `contradictory_authority_rows()` lets an operator ask whether any stored row
  lies. Preflight fails `RECOVERY_REQUIRED` when one does, so a contaminated
  database cannot quietly continue collecting.
- `STERLING_DATASET_START` is now a required preflight check, rejected if it
  carries no timezone, and carried in the deployment template.

## The fix, demonstrated

Not asserted — run. Same build, same scanner, a fresh database:

```
session 2026-09-16   universe_expected 200   universe_scanned 200
                     symbol_failures 0       signals_authoritative 0

opportunities: 1
  OPP-LAURUSLABS-1789120800000   source=LIVE_CATCHUP_REPLAY   authoritative=0
```

The scanner finds the same September 11 signal and now classifies it correctly.
The row is retained deliberately: it is a true record of what the scanner saw,
marked as what it is. The invariant that matters is not "zero rows" but **zero
authoritative rows**, and that is what holds.

## Executable runtime identity

```
runtime tag        snapback-prospective-runtime-1.4
build SHA          git rev-parse snapback-prospective-runtime-1.4^{commit}
experiment id      prospective_runtime_1_4
evidence database  data/snapback/prospective_runtime_1_4.db
dataset start      2026-09-17T00:00:00+05:30
evidence schema    user_version = 1
allocation capital INR 1,000,000
bind host          127.0.0.1
```

## Verification at freeze

```
backend      4643 passed, 6 skipped
frontend     1539 passed (142 files)
GitHub CI    green at 3fa0119fb (the fix); re-run at the tagged head
secret scan  no committed credentials
worktree     clean
live         /health/live 200 · /health/ready 200 READY, all 14 checks
             runner scanned 200/200, 0 authoritative signals
```

## Remaining open items

1. **Branch protection was bypassed once.** `main` now requires a pull request
   and the release gates, but `enforce_admins` is off, so the P0 fix above was
   pushed directly and GitHub logged the bypass. That was a deliberate choice for
   an emergency fix; routine changes should go through a PR.
2. The live execution path is implemented but has never run against a broker. Its
   correctness rests on unit tests and the specification, not observation. It
   cannot run while evidence is INCONCLUSIVE.
3. The 1.3 evidence database is discarded, not cleaned. It contains one
   contradictory row and must not be used.

## What this release does not claim

Nothing about edge. Zero authoritative prospective observations exist. The
promotion gate requires at least 60 fully observed sessions, at least 300
completed trades, a positive day-clustered lower 95% CI, positivity under 2×
cost stress, drawdown within 10%, at least 95% quote coverage and zero unresolved
exposure. None are met.

The verdict is INCONCLUSIVE. The strategy has not been shown to have an edge.

## The lesson from 1.2 and 1.3

Both were tagged on green suites and both were wrong. 1.2 was caught by running
the tagged build against the live service; 1.3 by reading the rows the runner
actually wrote. A passing test suite says the code does what its tests describe.
It does not say the system is doing the right thing. Before starting the clock,
look at the data.

## After this tag

Do not commit while the confirmatory sample is collected, unless a real
observation exposes a genuine P0 — and that warrants a new runtime tag and a new
experiment, as this one did.
