# Adaptive Edge V2.1 — Tick-to-Bar Deterministic Aggregation Contract

**Artifact:** A65  
**Status:** SPECIFICATION / IMPLEMENTATION-READY  

## 1. Purpose

A65 defines how immutable TrueData tick observations become deterministic derived bars without introducing future information.

## 2. Input

```text
RESEARCH_READY raw tick dataset
```

The source dataset version and SHA-256 are mandatory inputs.

## 3. Output

A derived bar dataset is identified by:

```text
parent_dataset_version
aggregation_version
interval
calendar_version
bar_schema_version
```

## 4. Interval semantics

For interval size `Δ` and canonical boundary `T`:

```text
bar(T, Δ) = { tick_i : T <= t_i < T + Δ }
```

The right endpoint is exclusive.

This prevents one tick from entering two adjacent bars.

## 5. Price OHLC

For a valid price field:

```text
Open  = price(first eligible tick)
High  = max(price_i)
Low   = min(price_i)
Close = price(last eligible tick)
```

The price field is fixed by the provider contract and aggregation configuration.

## 6. Volume

Volume aggregation is field-semantic dependent.

If TrueData supplies cumulative volume:

```text
bar_volume = last_cumulative_volume - prior_boundary_cumulative_volume
```

subject to documented reset/session rules.

If TrueData supplies per-tick traded volume:

```text
bar_volume = sum(tick_volume)
```

The system must not choose between these interpretations without provider evidence.

## 7. Open interest

Open interest is not summed.

Unless provider semantics explicitly require another transformation:

```text
bar_OI = last valid OI observation in the interval
```

If no valid OI exists, bar OI is NULL rather than zero.

## 8. Bid/ask

If quote observations are present:

```text
bar_bid = last valid bid
bar_ask = last valid ask
```

subject to a separately defined quote-staleness policy.

A bar must not manufacture a bid/ask from OHLC prices.

## 9. Empty intervals

If an interval contains no eligible ticks:

```text
bar = MISSING
```

It must not be represented as:

```text
OHLC = prior close
volume = 0
```

unless a downstream feature explicitly defines that as a derived state.

## 10. Decision-time boundary

A feature at time `t` may consume only a bar whose required observations are available by `t`.

A still-forming bar must never be treated as a completed bar.

For a completed-bar feature:

```text
bar_end <= decision_time
```

For event-level features:

```text
observation_availability_time <= decision_time
```

## 11. Session boundaries

Bars must be generated against an explicit versioned market calendar.

The calendar is not inferred from observed ticks.

Unknown/incorrect calendar version is a research blocker.

## 12. Timezone

Aggregation operates in one explicit canonical timezone after provider timestamp semantics are verified.

No implicit local-machine timezone is permitted.

## 13. Duplicate observations

Duplicate raw observations must be resolved before aggregation according to the raw-data identity contract.

Aggregation must not silently deduplicate by price/time alone.

## 14. Revision handling

A revised raw dataset produces a new derived dataset version.

Derived bars are never mutated in place for an already published research run.

## 15. Reproducibility invariant

For identical:

```text
parent_dataset_hash
aggregation_version
interval
calendar_version
provider-field mapping
```

aggregation must produce identical canonical bytes.

## 16. Adversarial tests

The implementation must test:

```text
one tick exactly at interval start
one tick exactly at interval end
multiple ticks with same timestamp
out-of-order raw input
missing interval
gap across session boundary
cumulative volume reset
missing OI
stale quote
DST/timezone boundary if applicable
revisioned parent dataset
```

## 17. Frozen architecture

```text
raw ticks are immutable
bars are deterministic projections
right-open intervals
no future observations
no fabricated empty bars
versioned calendar
versioned aggregation
versioned provider-field mapping
```

## 18. Configurable/research quantities

```text
bar intervals
feature-specific completed-bar policy
quote staleness threshold
calendar version
```

These must be declared per experiment and cannot be silently tuned after observing holdout results.

## 19. UNKNOWN / TODO

```text
exact TrueData cumulative/per-tick volume semantics
exact tick field names
exact provider timestamp semantics
exact provider revision semantics
actual calendar artifact/version
```

## 20. ARCHITECTURE STATUS

**FROZEN:** deterministic aggregation architecture and causal boundary.

**UNRESOLVED:** provider-specific field semantics and actual calendar/source evidence.

**BLOCKERS:** implementation cannot be finalized until A63 provider verification resolves the provider-field semantics.

**NEXT ARTIFACT:** A66 — Research Resolution Selection and Information-Preservation Protocol.
