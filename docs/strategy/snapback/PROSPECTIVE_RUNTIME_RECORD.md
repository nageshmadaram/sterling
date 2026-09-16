# Snapback Prospective Runtime Record — 1.1

Supersedes the runtime half of `PROSPECTIVE_FREEZE_RECORD.md`. The strategy identity
in that document is unchanged and remains authoritative for what Snapback decides.

## Historical strategy identity — UNCHANGED

```
strategy commit (manifest)  5a1354202e2c960c66b7003fce9cb80abd152008
config_hash                 6ecbeb53e9768a91
rule_hash                   e03ddf75f29463a8
manifest version            snapback_reality_v1.2
historical freeze tag       snapback-prospective-freeze-1.0  (9e989dd91...)  NOT MOVED
```

Frozen parameters are untouched: lookback 20, stretch 1.5 ATR, RV cap 70%, bearish
market filter, EMA 50, delta 0.70, DTE 40-60, 15-session horizon, 35% premium stop,
1.5x runner, 25% give-back, index-futures hedge.

## Executable runtime identity — NEW

```
runtime tag        snapback-prospective-runtime-1.1
build SHA          92b860d40c93b7aa775646ba0b89b55db10de359
expected build     STERLING_EXPECTED_BUILD_SHA, verified at startup
cost schedule      zerodha_fno_costs_2026_04
```

The build SHA is read from the repository at runtime, never taken from an environment
variable, and a declared expectation that does not match HEAD halts startup. Every
evidence row carries `runtime_build_sha` and an `authoritative` flag.

## Why a new runtime, and a new database

The running code had moved well past the original freeze while reports still claimed
the old SHA, so evidence could have been attributed to a build that never executed it.
Between the freeze and this release the following P0 defects were found and fixed, any
one of which would have distorted a month of evidence:

1. a legacy migration failure silently disabled the strategy (config store unavailable
   -> defaults OFF) while the runner looked alive;
2. the causal hedge beta imported a function that does not exist, so every non-NIFTY
   entry was skipped;
3. catch-up rows from older sessions were recorded as authoritative paper evidence;
4. quote refusals were never persisted, so coverage could not see missing evidence;
5. table read failures and missing economics became zeros;
6. drawdown was measured on a curve that lost every closed trade, against a capital
   base ten times too large;
7. entry costs charged STT on an option buy and guessed hedge margin at 12% of
   notional;
8. the family live gate had no production call site.

## Dataset

```
database        data/snapback/prospective_runtime_1_1.db
dataset begins  2026-09-16T11:44:27Z / 2026-09-16T17:14:27+05:30 IST
previous DB     archived empty under data/snapback/archive/ (zero rows, nothing lost)
mode            PAPER / OBSERVATION ONLY
broker orders   DISABLED
parameters      NO CHANGES PERMITTED
```

Only observations created after this timestamp, by this build, flagged authoritative,
qualify for the authoritative sample.

## Status

```
Strategy parameters       FROZEN / unchanged
Rule hash                 unchanged
Historical strategy tag   untouched
Runtime implementation    P0-hardened build 92b860d4
Observed cost schedule    zerodha_fno_costs_2026_04
Capacity model            broker-observed, fail-closed
Authoritative provenance  exact build SHA per row
Catch-up contamination    impossible
Missing evidence          never converted to zero
Portfolio drawdown        real equity curve, frozen capital base
Live broker boundary      family gate enforced in submit_order()
Decision audit trail      append-only
Evidence DB               clean, empty, new runtime start
Economic verdict          INCONCLUSIVE
LIVE                      BLOCKED
```
