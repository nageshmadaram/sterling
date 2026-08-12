# Adaptive Edge V2 — Canonical Tick Event Schema and Deterministic Aggregator Implementation Contract

**Artifact:** A71  
**Version:** 2.1.0  
**Status:** SPECIFICATION / IMPLEMENTATION CONTRACT

## 1. Purpose

A71 defines the lossless boundary between verified TrueData tick observations and derived research resolutions.

It does not invent provider semantics. A provider field may enter the canonical representation only with a verified meaning.

## 2. Provider authority

```text
TrueData -> market/research observations
Kite     -> trading/execution observations
```

Kite data is never used to complete, repair, or enrich the canonical TrueData research stream.

## 3. Verified TrueData tick fields

TrueData documentation identifies the real-time tick stream fields including:

```text
Symbol ID
Date-Time / Timestamp
LTP
LTQ
ATP
TTQ
Open
High
Low
Previous Close
OI
Previous Open Interest Close
Day's Turnover
Special Tag
Tick Sequence No
Bid / Bid Qty / Ask / Ask Qty when bid-ask is enabled
```

The canonical adapter must preserve provider values and provider field names in provenance metadata.

## 4. Canonical event

The internal event is:

```text
CanonicalTickEvent {
    source_provider
    provider_symbol_id
    canonical_instrument_id
    event_time
    availability_time
    tick_sequence
    ltp
    ltq
    atp
    ttq
    open
    high
    low
    previous_close
    oi
    previous_oi_close
    turnover
    special_tag
    bid
    bid_qty
    ask
    ask_qty
    source_payload_hash
}
```

`availability_time` is distinct from `event_time`. If provider documentation does not establish a reliable availability timestamp, it remains UNKNOWN and the event cannot be used to prove a strict causal availability boundary.

## 5. Field semantics

### LTP

Used as the provider's last-traded-price observation. No alternative price is substituted.

### LTQ

Retained as provider-reported last-traded quantity. It must not be assumed to equal an independently reconstructed trade size unless the provider semantics establish that relationship.

### TTQ

Retained as provider-reported total traded quantity. It is cumulative only if the provider contract establishes that semantic.

### Tick sequence

Preserved as provider ordering metadata. It must not be fabricated when unavailable.

### Bid / ask

Retained as quote observations when bid-ask data are enabled. Quote fields are never interpreted as trade events.

### OI

Retained as provider-reported open interest. It is not summed as traded volume.

### ATP / turnover / OHLC

Retained as provider fields. They are not silently recomputed unless a separate canonical formula explicitly requires a derived representation.

## 6. Raw-event immutability

The raw canonical event is immutable after ingestion.

Normalization may change transport representation but may not alter the observed economic value.

A correction from the provider is a new source observation or explicit correction event, not an in-place historical mutation without provenance.

## 7. Timestamp ordering

Ordering precedence is:

```text
provider sequence number, when documented and available
        |
        v
provider event timestamp
```

Equal timestamps remain distinct events unless a provider-defined event identity establishes that they are duplicates.

The adapter must never use local arrival order as economic event order unless the provider contract explicitly defines it as such.

## 8. Deterministic aggregation

Derived bars are functions of canonical events only:

```text
Bar_r(I) = Aggregate({e | e.event_time ∈ I})
```

where `r` is the declared resolution and `I` is a deterministic interval under the declared session/timezone/calendar contract.

For trade-price bars, the canonical OHLC rule is:

```text
open  = first eligible trade price
high  = max eligible trade price
low   = min eligible trade price
close = last eligible trade price
```

These values must not be reconstructed from a coarser bar.

## 9. Volume aggregation

A derived traded-volume field requires verified quantity semantics.

If the source provides cumulative total traded quantity and its reset behavior is verified:

```text
interval_volume = last_TTQ - first_TTQ_before_interval
```

with explicit session/reset/correction handling.

If the source provides valid incremental trade quantities instead, aggregation may sum those quantities.

The implementation must not choose between these formulas without the verified source semantics.

## 10. Open interest aggregation

OI is a state variable, not traded volume.

The aggregator must not sum OI.

For a derived bar, the canonical OI representation must be explicitly selected from provider-supported semantics, such as the last valid observation in the interval, only after the provider contract establishes that this is the intended state representation.

If OI semantics are unresolved, OI remains UNKNOWN in the derived representation.

## 11. Quote aggregation

Bid/ask are quote states.

A derived bar may retain a declared quote snapshot, for example the last valid quote available within the interval, only if the research artifact explicitly requires it.

No bid/ask field is created from trade prices.

## 12. Empty intervals

An interval containing no eligible source observations is not automatically a zero-volume bar.

The default canonical state is:

```text
NO_OBSERVATION
```

Any session-specific empty-bar policy must be explicitly defined before use by a downstream feature.

## 13. Causality

For decision time `t`, a derived bar may be consumed only when all observations required to construct that bar are available by the declared decision boundary.

A bar whose interval extends beyond `t` is not complete at `t` and cannot be consumed as a completed bar.

This prevents intra-bar look-ahead.

## 14. Multi-resolution derivation

The architecture supports:

```text
raw tick
  -> derived 1-second
  -> derived 5-second
  -> derived 10-second
  -> derived 15-second
  -> derived 30-second
  -> derived 1-minute
  -> other preregistered resolutions
```

The candidate set is controlled by A66 and the research registry.

A resolution cannot be introduced after holdout inspection.

## 15. Determinism

Given identical:

```text
raw source artifact
provider semantic contract version
instrument mapping
timezone
session calendar
aggregation configuration
```

the derived dataset must be byte-for-byte reproducible after canonical serialization.

## 16. Provenance

Every derived observation must retain:

```text
source_dataset_version
source_hash
instrument_id
resolution
interval_start
interval_end
aggregation_contract_version
source_event_count
first_source_sequence
last_source_sequence
```

This permits reconstruction from raw source events.

## 17. Failure conditions

Aggregation must fail closed when:

1. required source semantics are unresolved;
2. instrument identity is ambiguous;
3. timestamp timezone is unresolved;
4. sequence ordering is contradictory;
5. duplicate identity cannot be resolved;
6. a required volume semantic is unknown;
7. source correction changes the immutable dataset without a new version;
8. aggregation produces non-deterministic output;
9. a completed bar would contain post-decision observations;
10. a downstream consumer attempts to use an unresolved field as if it were observed.

## 18. Architecture versus parameters

### Frozen architecture

- TrueData-only source;
- immutable raw canonical events;
- deterministic aggregation;
- provider sequence preservation;
- explicit timestamp/availability boundary;
- no interpolation;
- no Kite substitution;
- provenance for every derived observation;
- resolution selection governed by A66.

### Learned / validated

None. Aggregation semantics are not learned.

### Configuration requiring validation

```text
candidate resolutions
session calendar
timezone
empty-interval policy
quote snapshot policy
OI representation policy
```

### External dependencies

```text
TrueData entitlement
TrueData exact historical tick payload
availability semantics
correction/replay semantics
```

## 19. Adversarial review

### Attack: same-timestamp ticks

If multiple ticks share a timestamp, sorting only by timestamp can change the close and volume. Preserve sequence information and never fabricate ordering.

### Attack: TTQ reset

A session reset can make a naive difference negative. Negative volume is not converted to zero; the reset boundary must be recognized from the source/session contract.

### Attack: quote contamination

Bid/ask changes are not trades. They cannot create volume or alter trade OHLC.

### Attack: incomplete bar

A 1-minute bar ending at 09:16 cannot be used at 09:15:30 as if its final close were known.

### Attack: coarse-to-fine reconstruction

OHLC cannot reconstruct the intra-bar path. No synthetic ticks are generated from bars.

### Attack: provider substitution

Kite quotes/fills never repair TrueData observations.

## ARCHITECTURE STATUS

Frozen: canonical tick boundary, deterministic aggregation direction, provenance, causality, provider separation.

## UNRESOLVED

Provider-specific availability/correction/replay semantics remain external until verified for the actual entitlement.

## BLOCKERS

A production tick adapter cannot be certified until the actual TrueData payload and entitlement are exercised against this contract.

## NEXT ARTIFACT

A72 — TrueData Historical/Replay Acquisition Adapter Contract and Test Specification.
