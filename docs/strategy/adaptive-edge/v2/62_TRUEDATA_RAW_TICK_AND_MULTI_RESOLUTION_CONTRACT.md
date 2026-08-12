# Adaptive Edge V2.1 — TrueData Raw Tick and Multi-Resolution Contract

**Artifact:** A62  
**Status:** SPECIFICATION / SOURCE-VERIFIED PARTIAL  
**Market-data authority:** TrueData only  
**Execution authority:** Zerodha Kite only  

## 1. Purpose

A62 defines the lowest-resolution market-data boundary that Adaptive Edge may preserve and consume. It separates provider-native tick observations from derived bars and prevents irreversible aggregation before research decisions are made.

The system must preserve the highest-resolution source that the entitled TrueData feed actually supplies. A strategy decision timeframe is a downstream research choice and must not be confused with source resolution.

## 2. Source authority

```text
Adaptive Edge market/research data = TrueData only
Adaptive Edge trading/execution    = Zerodha Kite only
```

Kite data must not substitute for TrueData market observations in research, feature construction, target construction, or historical replay.

## 3. Provider capability distinction

TrueData documentation distinguishes **tick data** from minute-bar data. TrueData documents real-time streaming for tick, 1-minute and 5-minute data, and historical REST availability for tick data separately from minute bars. Current public TrueData documentation states that default REST historical availability is five trading days for tick data and six months for 1/2/3/5/10/15/30/60-minute bars. The exact entitlement and retention available to this account must be recorded in the dataset manifest rather than assumed.

Therefore:

```text
Tick data != 1-second OHLC bar
Tick data != 1-minute bar
```

A tick is a provider observation/event. It must not be described as a one-second bar unless the provider contract explicitly defines it that way.

## 4. Canonical raw layer

The canonical raw layer is:

```text
TrueData provider observation
        |
        v
RawMarketObservation
```

Minimum canonical identity fields:

```text
source
provider_symbol
instrument_identity
observation_time
received_time
provider_sequence/reference if supplied
payload
source_schema_version
```

The exact provider sequence/reference field is UNKNOWN until confirmed from the entitled TrueData feed/documentation.

## 5. Tick payload

A raw tick may contain provider-supported fields such as price, volume, bid/ask and associated quantities. The exact field set must be taken from the entitled TrueData tick contract.

No field may be inferred merely because a corresponding field exists on a minute bar.

For every retained field the registry must record:

```text
field
provider field name
unit
semantic definition
observation timestamp
availability timestamp if known
nullable
revision behavior
source documentation
```

## 6. Timestamp semantics

At least three times must remain conceptually distinct:

```text
provider_observation_time
local_received_time
processing_time
```

Provider timestamp semantics are an external dependency and remain UNKNOWN until verified for the actual feed/account.

The system must never silently reinterpret a naive timestamp as UTC or Asia/Kolkata.

Mixed timezone-aware and timezone-naive observations are invalid until an explicit source policy exists.

## 7. Ordering

Canonical event ordering is by the provider's observation-time semantics, with provider sequence/reference used when the provider supplies one.

If two observations have identical timestamps and no deterministic provider ordering key exists, they remain an ordering ambiguity and must not be silently reordered.

## 8. No fabricated one-second layer

The following transformation is prohibited:

```text
minute OHLC
   -> invented second observations
```

Likewise, the system must not label tick observations as one-second observations unless the provider contract establishes that semantic.

If a one-second derived bar is required for research, it must be constructed deterministically from raw observations under a separately versioned aggregation contract.

## 9. Multi-resolution architecture

```text
                 TrueData raw ticks
                        |
                        v
              Canonical raw observations
                        |
          +-------------+-------------+
          |             |             |
          v             v             v
        tick        derived 1s     derived 1m
                        |
                        v
                  other bars
```

All derived resolutions are projections of the immutable raw layer.

A derived bar must contain provenance:

```text
source_dataset_version
aggregation_version
source_interval
start_time
end_time
included_observation_count
```

## 10. Aggregation rule

For a derived interval `[T0,T1)`:

```text
eligible observations satisfy:
    T0 <= observation_time < T1
```

No observation from a future interval may enter the current interval.

For price OHLC:

```text
Open  = first eligible price
High  = max eligible price
Low   = min eligible price
Close = last eligible price
```

The exact price field used for aggregation is an explicit configuration and must be sourced from the TrueData field semantics.

Volume and OI aggregation are **not** universally assumed:

```text
volume aggregation = field-specific / TODO
OI aggregation      = field-specific / TODO
```

OI may represent a point-in-time state rather than an additive quantity. It must therefore not be summed.

## 11. Quote fields

If bid/ask observations are available:

```text
mid = (bid + ask) / 2
spread = ask - bid
```

only when both values are simultaneously valid under the provider's quote semantics.

A stale bid or ask must not be paired with a newer observation without an explicit staleness policy.

## 12. Feature construction boundary

Features consume canonical observations or deterministic derived states:

```text
Raw TrueData observations
        |
        v
Canonical event/state
        |
        v
Feature snapshot
```

A feature snapshot must identify:

```text
feature_snapshot_id
decision_time
source_dataset_version
source_observation_watermark
aggregation_version
feature_version
```

## 13. Decision-time watermark

At decision time `t`, the feature builder may consume only observations whose availability is established as `<= t`.

Observation timestamp alone is insufficient if the provider's delivery/availability semantics demonstrate latency or delayed publication.

Therefore:

```text
eligible observation:
    availability_time <= decision_time
```

where `availability_time` is UNKNOWN until provider semantics are verified.

## 14. Historical availability constraint

TrueData's current public documentation reports substantially shorter default historical retention for tick data than for minute bars. Therefore the research dataset must not assume that a long historical tick dataset can be downloaded through the same REST endpoint used for minute bars.

Required resolution:

```text
actual entitlement
actual retention
historical transport
date coverage
request limits
symbol coverage
```

These become dataset-manifest facts.

## 15. Research-data acquisition strategy

The acquisition system must support two distinct source classes:

```text
RAW_TICK
DERIVED_BAR
```

A research experiment must declare which class it consumes.

For experiments requiring intra-bar path information, the raw tick dataset is preferred when entitled and available.

For experiments whose mathematical definition is explicitly bar-based, the derived bar dataset may be used, provided the aggregation version is frozen.

## 16. Reproducibility

Given:

```text
same raw dataset
same aggregation version
same feature version
same label version
same calendar version
```

the derived dataset must be byte-for-byte reproducible after canonical serialization.

## 17. Failure conditions

Fail closed on:

```text
missing required source fields
unknown timestamp semantics
mixed timestamp timezone semantics
duplicate observations without deterministic identity
ambiguous event ordering
provider revision without recorded revision policy
missing required raw source coverage
unverified source entitlement
unverified source availability semantics
```

Do not repair research data silently.

## 18. Adversarial attack

### Tick vs one-second confusion

Invalid:

```text
TrueData tick
    ==
1-second bar
```

unless explicitly documented.

### Look-ahead through bar close

Invalid:

```text
09:15:30 decision
    -> consume 09:15:00-09:15:59 completed bar
```

because the bar is not complete at 09:15:30.

The feature must use only observations available by 09:15:30.

### Aggregation leakage

Invalid:

```text
bar(T)
    contains observation after decision_time
```

### OI summation

Invalid:

```text
OI_bar = sum(tick_OI)
```

unless the provider explicitly defines OI as an additive flow. OI is treated as state by default and remains source-defined.

### Silent fallback

Invalid:

```text
TrueData unavailable
    -> Kite quote
```

### Historical availability fabrication

Invalid:

```text
requested 12 months of tick data
    -> assume TrueData can supply it
```

The actual entitlement/retention must be measured and recorded.

## 19. Frozen architecture

```text
TrueData = sole market/research authority
Raw observations are immutable research inputs
Raw tick and derived bars are distinct data classes
Derived resolutions are deterministic projections
Kite is excluded from market-data research inputs
No invented second-level observations
Causal availability is mandatory
Dataset provenance is mandatory
```

## 20. Learned/configurable quantities

Not frozen:

```text
decision resolution
feature aggregation interval
rolling windows
quote staleness threshold
research symbol universe
specific tick-derived features
specific bar-derived features
```

These are research/configuration quantities and require validation.

## 21. TODO / UNKNOWN

```text
TODO: verify exact entitled TrueData tick historical transport
TODO: verify actual tick retention for this account
TODO: verify exact tick payload fields
TODO: verify provider timestamp semantics
TODO: verify provider sequence/order semantics
TODO: verify revision/correction behavior
TODO: verify historical request limits for the entitled plan
TODO: verify whether full market-feed replay is available to the account
TODO: map exact provider fields into RawMarketObservation
```

## 22. ARCHITECTURE STATUS

**FROZEN:** raw-first, TrueData-only market boundary; tick and bar separation; deterministic multi-resolution derivation; causal availability; provenance; fail-closed behavior.

**UNRESOLVED:** exact entitled tick transport, retention, fields, timestamp semantics, sequence semantics, revision behavior and research resolution.

**BLOCKERS:** empirical tick research cannot begin until actual TrueData tick entitlement/availability is verified and a real raw dataset is acquired.

**NEXT ARTIFACT:** A63 — TrueData Entitlement and Source-Availability Verification Contract.
