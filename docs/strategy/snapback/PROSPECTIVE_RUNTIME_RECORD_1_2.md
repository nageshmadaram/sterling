# Snapback Prospective Runtime Record — 1.2

Supersedes the runtime half of `PROSPECTIVE_RUNTIME_RECORD.md` (1.1). The strategy
identity in `PROSPECTIVE_FREEZE_RECORD.md` is unchanged and remains authoritative
for what Snapback decides.

## Historical strategy identity — UNCHANGED

```
strategy commit (manifest)  5a1354202e2c960c66b7003fce9cb80abd152008
config_hash                 6ecbeb53e9768a91
rule_hash                   e03ddf75f29463a8
manifest version            snapback_reality_v1.2
cost schedule               zerodha_fno_costs_2026_04  (630a4de9ad143e2c)
historical freeze tag       snapback-prospective-freeze-1.0  (9e989dd91...)  NOT MOVED
prior runtime tag           snapback-prospective-runtime-1.1 (136a9b719...)  NOT MOVED
```

No parameter was tuned in this release. Lookback 20, stretch 1.5 ATR, RV cap 70%,
bearish market filter, EMA 50, delta 0.70, DTE 40-60, 15-session horizon, 35%
premium stop, 1.5x runner, 25% give-back, index-futures hedge.

## Executable runtime identity — NEW

```
runtime tag        snapback-prospective-runtime-1.2   at 572972ac679610d5474f9a0e1a170b8ee6b7e65a
running build      266fd02181732f73489a0a1d7a7394453062ea0c   (one post-tag fix, below)
experiment id      prospective_runtime_1_2
evidence database  data/snapback/prospective_runtime_1_2.db   (fresh, empty of observations)
evidence schema    user_version = 1
allocation capital INR 1,000,000
bind host          127.0.0.1
```

The build SHA is read from the repository at runtime, never taken from an
environment variable. A declared expectation that does not match HEAD halts
startup. Every evidence row carries `runtime_build_sha` and an `authoritative`
flag, and the database now carries its own identity in `evidence_meta`: a file
written by a different build, config or capital allocation is no longer
indistinguishable from this one.

### One fix after the tag

`snapback-prospective-runtime-1.2` was cut at `572972ac6`. Verifying the tagged
build against the live service — not against its tests — found that the preflight
family-account probe read `health["bound"]`, a key `family_account_health()` has
never returned. A correctly bound account therefore reported as unbound and
`/health/ready` answered 503 permanently. Fixed in `266fd0218`, with two
regression tests that exercise the default probe rather than an injected one.

The tag is NOT moved. The running build is `266fd0218`.

## What changed since 1.1

Eight blocks. None of them changes what the strategy decides; all of them change
whether the evidence about that decision can be believed.

**1. Cost ledger and promotion authority.** Costs are an immutable per-leg ledger
derived at execution, and closing economics are derived from it rather than passed
in by a caller. `PromotionService` is the only production caller of the
authoritative gate.

**2. Broker reconciliation and live lifecycle.** The hedge is sized to the
CONFIRMED fill, not the intended one. Only an `ABSENT` submission is safe to
resubmit; anything else stays UNKNOWN, which is the honest answer.

**3. Durable alert outbox.** Creating an alert and delivering it are separate
facts. A scheduled send that never completed no longer looks like a notification
somebody received. Bounded backoff, a DEAD state, and `alert_delivery_failed` in
health when dead alerts exist.

**4. Startup preflight and evidence identity.** Twelve checks — build identity,
clean worktree, config identity, database `quick_check`/schema/writability,
evidence meta, calendar, family account, allocation capital, clock sync, disk
headroom, backup writability, lifecycle. Every probe is injectable, and a probe
that raises counts as failed rather than passed. `/health/live` does no I/O;
`/health/ready` answers 503 so a supervisor backs off instead of restarting.

**5. Post-market isolation.** The chain runs via `asyncio.to_thread`, so
seconds-to-minutes of SQLite work no longer stalls liveness and alert delivery.
Reports read the verified backup snapshot, so they describe one moment rather than
a smear across whatever was being written. Packages are built under `.staging/`
and moved into place with `os.replace`, with a checksum for every file.

**6. Release gate and fault injection.** `.github/workflows/snapback-release-gate.yml`
is absolute rather than a merge-base comparison: the whole Snapback suite, release
invariants, a committed-credential scan, and the single-authority check. Nine
fault-injection drills cover a full disk mid-write, a corrupt database, a locked
database, a calendar outage, a backup failure, an alert transport outage, a broker
outage, a quote outage and an unsynchronised clock. The Family Operations screen —
which had eleven passing tests and no route, so it could not be opened at all — is
now a tab, with an end-to-end spec driving it.

**7. Deployment hardening.** Family Mode suppressed three auto-runners and left two
running; the Kite engine auto-scan and the Value-Flow Navigator both reach the
broker, so there were three ways for exposure to appear on the family account
rather than one. Both are now behind the same guard, and the suppression list is a
single contract that `main.py` is tested against. Loopback by default. systemd
units with a liveness watchdog (never readiness, which is allowed to be false on a
holiday) and a 90-second stop window so a transaction in flight can finish.

**8. Audit and freeze.** This document.

## Verification at freeze

```
backend      4541 passed, 6 skipped
frontend     1535 passed (141 files)
secret scan  no committed credentials
worktree     clean
```

Live service after restart onto this build:

```
/api/v1/health/live                     200  alive
/api/v1/health/ready                    all twelve checks pass
/api/v1/snapback/prospective/health     HEALTHY, build 572972ac6…, no unresolved errors
listener                                127.0.0.1:8000
```

Graceful shutdown was observed, not assumed: the earlier restart logged
`Snapback runtime stopped (lifespan_shutdown)` before the process exited, so a
deliberate stop is distinguishable from a crash in the evidence afterwards.

## Known gaps carried into this release

These are disclosed rather than closed. None of them is on the paper-evidence
path, and closing any of them is a change to live execution, which this release
does not perform.

1. The arm endpoint `POST /api/v1/snapback/plans/{plan_id}/arm` and its durable
   plan store are not built.
2. Broker protection states (spec 2.16) and the live exit sequence (spec 2.17)
   are unimplemented.
3. `study/snapback_forward_gate.py` remains in `ALLOWED_GATE_CALLERS` as a
   test-facing shim. It is not a production authority, but it is a second place
   the gate can be reached from.
4. Per-phase cost cardinality (G24) is not asserted.
5. The composed `run_preflight()` gates `/health/ready` only. `main.py` still
   gates the runner on the narrower `run_startup_preflight()`.
6. Branch protection on `main` requiring `snapback-release-gate` is a repository
   admin action and has not been applied here.

## What this release does not claim

Nothing about edge. No session of prospective evidence has been collected against
this runtime; the database is empty by design. The promotion gate still requires
at least 60 fully observed sessions, at least 300 completed trades, a positive
day-clustered lower 95% CI, positivity under 2x cost stress, drawdown within 10%,
at least 95% quote coverage and zero unresolved exposure. None of those are met,
and the honest verdict today is INCONCLUSIVE.

The strategy has not been shown to have an edge. It has been made capable of
producing evidence that would show whether it does.
