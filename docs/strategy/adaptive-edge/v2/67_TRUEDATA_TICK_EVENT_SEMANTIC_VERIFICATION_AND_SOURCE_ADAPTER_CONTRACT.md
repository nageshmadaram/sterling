# Adaptive Edge V2 — TrueData Tick/Event Semantic Verification and Source Adapter Contract

**Artifact:** A67  
**Status:** SPECIFICATION / EXTERNAL-SOURCE GATE  
**Version:** 2.1.0

## 1. Purpose

A67 prevents the system from treating an undocumented or ambiguous TrueData tick payload as if it were a canonical market event.

## 2. Source authority

For Adaptive Edge research and market-state construction:

```text
TrueData = sole market-data authority
```

For order submission, execution, position and square-off:

```text
Zerodha Kite = sole trading authority
```

No provider substitution is permitted.

## 3. Required source evidence

Before tick data enters the canonical dataset, the following must be established from TrueData documentation and/or an entitlement-verified sample:

1. payload schema;
2. symbol identity;
3. event timestamp field;
4. timestamp timezone;
5. timestamp precision;
6. whether the event represents a trade, quote, snapshot, or aggregate;
7. last-traded price semantics;
8. traded quantity semantics;
9. cumulative versus incremental volume semantics;
10. bid/ask semantics where present;
11. bid/ask quantity semantics where present;
12. open-interest semantics where present;
13. event ordering guarantees;
14. duplicate-event behavior;
15. correction/revision behavior;
16. disconnect/reconnect behavior;
17. historical replay semantics;
18. historical retention under the actual entitlement.

Any unresolved field remains UNKNOWN and cannot silently receive a derived interpretation.

## 4. Canonical raw event

The canonical event is conceptually:

```text
MarketEvent {
    provider
    instrument_id
    event_time
    availability_time
    sequence_or_provider_order
    event_type
    price
    quantity
    cumulative_volume
    bid
    ask
    bid_quantity
    ask_quantity
    open_interest
    source_reference
}
```

Fields that the provider does not supply remain absent/UNKNOWN. The adapter must not manufacture them.

## 5. Trade versus quote distinction

A trade event and a quote event are different observations.

The adapter must never infer:

```text
trade_quantity = bid_quantity + ask_quantity
```

or any equivalent unsupported transformation.

Likewise, bid/ask changes must not be treated as trades.

## 6. Volume semantics

Volume is only incremented when the provider semantics establish that the observation represents incremental traded quantity.

If the provider exposes cumulative volume:

```text
DeltaVolume_t = CumVolume_t - CumVolume_{t-1}
```

is permitted only after the cumulative-volume semantics and reset behavior are verified.

Negative deltas, reset boundaries, corrections, and missing predecessor observations require explicit policy; they must not be silently converted to zero.

## 7. Timestamp semantics

The adapter must preserve provider timestamps and separately record availability time where available.

The following are distinct:

```text
event_time
availability_time
ingestion_time
```

Research causality is governed by availability, not by local ingestion time.

If availability semantics cannot be established, the event cannot be used to prove a causal decision boundary.

## 8. Ordering

If the provider supplies a sequence identifier, it must be preserved.

If no sequence identifier exists, timestamp ordering is insufficient to establish a total order when multiple events share the same timestamp.

The adapter must therefore retain equal timestamps rather than arbitrarily sorting them into a fabricated sequence.

## 9. Duplicate events

A repeated payload is not automatically a duplicate semantic event.

Deduplication requires a documented provider identity or an explicit canonical event identity.

Hash-based deduplication may be used only as an operational integrity check and must not remove legitimate repeated events without provider-supported semantics.

## 10. Missing data

The adapter must not:

- forward-fill price;
- interpolate trades;
- invent volume;
- copy Kite data;
- convert missing ticks into neutral outcomes.

Missing source observations remain missing.

## 11. Contract multiplier and instrument metadata

Price and quantity cannot be interpreted economically without the applicable instrument contract metadata.

The tick adapter therefore outputs market observations only. Economic interpretation remains downstream of the canonical instrument contract.

## 12. Source adapter responsibilities

The adapter may:

```text
authenticate
request/receive provider data
parse documented fields
normalize transport representation
preserve provider timestamps
validate schema
produce canonical raw events
```

The adapter may not:

```text
create strategy signals
calculate probabilities
select options
submit orders
repair undocumented data
infer missing provider semantics
```

## 13. Verification test matrix

The source adapter must have tests for:

```text
valid tick
missing required timestamp
invalid timestamp
unknown event type
negative price
negative incremental quantity
cumulative-volume reset
duplicate timestamp
same-timestamp multiple events
missing bid/ask
missing OI
provider correction
session boundary
reconnect boundary
instrument mismatch
```

The exact acceptance criteria depend on the verified provider contract.

## 14. External dependency status

```text
TrueData tick entitlement              = UNKNOWN
TrueData tick payload                  = MUST VERIFY
Timestamp precision                    = MUST VERIFY
Event ordering                         = MUST VERIFY
Historical replay                      = MUST VERIFY
Retention                              = MUST VERIFY
Correction semantics                   = MUST VERIFY
```

## ARCHITECTURE STATUS

Frozen: provider ownership, canonical event boundary, separation of trade/quote semantics, no silent repair, availability-based causality.

## UNRESOLVED

All provider-specific semantics listed in Section 14.

## BLOCKERS

No tick-level research implementation may claim correctness until the provider contract is verified against the actual entitlement.

## NEXT ARTIFACT

A68 — TrueData/Kite Provider Boundary Conformance Test Matrix.
