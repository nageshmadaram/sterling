# Adaptive Edge V2 — TrueData Canonical Market Data Schema

**Artifact:** A72  
**Status:** SPECIFICATION  
**Version:** 2.1.0

## 1. Purpose

A72 defines the canonical internal representation of TrueData market observations without inventing semantics that are not established by the repository's TrueData documentation.

The source repository contains TrueData Market Data API documentation and API test material. The repository README explicitly identifies the Market Data API REST documentation as the provider reference. The Postman collection contains separate requests for `getticks`, `getAllTicks`, `getbars`, and `getAllBarsforMin`, including a `getAllTicks` request using `interval=1sec`.

Therefore the canonical model preserves the distinction between provider tick data, provider one-second responses, and provider bar responses.

## 2. Authority

```text
TrueData -> all Adaptive Edge research market observations
Kite     -> trading/execution/order/trade/position state
```

Kite market observations must not be substituted into the TrueData research dataset.

## 3. Canonical entity types

The canonical layer contains three market-observation classes:

```text
TrueDataTick
TrueDataSecond
TrueDataBar
```

They must not be silently coerced into one another.

## 4. Canonical common envelope

Every observation must carry:

```text
MarketObservation {
    observation_id
    provider = "truedata"
    instrument_id
    provider_symbol
    event_time
    received_at
    source_kind
    source_request
    source_resolution
    raw_record_hash
    schema_version
    quality_flags
}
```

### Required meanings

`observation_id`
: Stable identity assigned by the canonical ingestion layer. It must be deterministic for historical replay.

`instrument_id`
: Canonical internal instrument identity. Provider symbols alone are not sufficient as the long-term cross-provider identity.

`provider_symbol`
: Exact TrueData symbol used by the acquisition request.

`event_time`
: Provider-supplied market-event timestamp. The adapter must preserve the provider value and timezone semantics.

`received_at`
: Local acquisition timestamp. It is diagnostic metadata and must not replace `event_time` for historical market ordering.

`source_kind`
: One of `TICK`, `SECOND`, `BAR`.

`source_request`
: Versioned identifier of the TrueData endpoint/request configuration that produced the record.

`source_resolution`
: Provider-declared resolution where applicable, such as `1sec` or a minute interval.

`raw_record_hash`
: Integrity hash of the unmodified source representation.

`schema_version`
: Version of this canonical schema.

`quality_flags`
: Explicit data-quality conditions. Flags cannot silently alter source values.

## 5. TrueDataTick

Conceptual schema:

```text
TrueDataTick {
    common_envelope
    tick_sequence: optional
    ltp: optional
    ltq: optional
    ttq: optional
    oi: optional
    bid: optional
    bid_quantity: optional
    ask: optional
    ask_quantity: optional
    provider_fields: opaque/documented extension map
}
```

The repository's TrueData material establishes the existence of tick-oriented requests and TrueData documentation establishes tick-related market fields, but each field's exact historical response semantics must remain tied to the provider schema version.

No field is manufactured when absent.

## 6. TrueDataSecond

Conceptual schema:

```text
TrueDataSecond {
    common_envelope
    interval = "1sec"
    provider_payload: documented fields only
}
```

The Postman collection explicitly contains a `getAllTicks` request with `interval=1sec`.

The canonical schema deliberately does NOT currently assert that this response is:

```text
OHLCV
```

or:

```text
one raw event per second
```

or:

```text
an aggregation of all raw ticks
```

Those semantics require direct verification from the provider response documentation/sample.

## 7. TrueDataBar

Conceptual schema:

```text
TrueDataBar {
    common_envelope
    interval
    open
    high
    low
    close
    volume: provider-defined
    oi: provider-defined/optional
    additional documented fields
}
```

The exact field set and interval vocabulary must be taken from the provider's documented response contract.

The canonical layer must not reinterpret provider fields without a versioned mapping.

## 8. Numeric validation

The ingestion layer must reject malformed numeric values where the provider contract defines the field as numeric.

It must not silently convert:

```text
null -> 0
missing -> 0
invalid -> previous value
```

unless a later, explicitly documented canonical transformation defines that behavior.

## 9. Volume semantics

The canonical schema distinguishes:

```text
LTQ
TTQ
```

and does not assume that either is interchangeable with bar volume.

A derived incremental volume may only be created after the provider semantics of the source field, reset behavior, corrections, and session boundaries are verified.

No negative cumulative-volume difference may be silently converted to zero.

## 10. Open interest

Open interest is retained as a separate field.

It must never be summed across ticks as though it were traded volume.

No interpretation of OI change is frozen by A72.

## 11. Bid/ask

Bid, ask, bid quantity, and ask quantity remain quote observations.

They must not be converted into trade observations.

If a source response does not contain these fields, the canonical representation records them as unavailable rather than inferred.

## 12. Tick sequence

If TrueData provides a provider sequence identifier, it must be preserved exactly.

Equal `event_time` values therefore remain distinct observations when their provider identities differ.

The ingestion layer must never create a synthetic sequence merely to force total ordering.

## 13. Timestamps

The canonical layer retains at least:

```text
event_time
received_at
```

A future implementation may add `availability_time` if the provider supplies or permits its derivation under a documented contract.

`received_at` is never used to rewrite `event_time`.

## 14. Instrument identity

Canonical identity is separate from provider symbol identity.

Conceptually:

```text
CanonicalInstrument
    |
    +-- TrueData symbol
    +-- Kite instrument identity
    +-- exchange
    +-- instrument type
    +-- expiry
    +-- strike
    +-- multiplier/lot metadata when sourced
```

The actual mapping is a separate instrument-master artifact.

A missing mapping is a hard ingestion failure for data that must participate in strategy research.

## 15. Provenance

Every canonical observation must be traceable to its raw source artifact:

```text
provider response
    -> raw record
    -> raw hash
    -> canonical observation
    -> dataset version
```

No canonical observation may exist without provenance.

## 16. Derived data rule

Derived data is represented separately from source data.

```text
TrueDataTick
TrueDataSecond
TrueDataBar
```

must never be overwritten by derived transformations.

If a one-second representation is derived internally from raw ticks in a later artifact, it receives a new `source_kind=DERIVED_SECOND` and retains references to the contributing source observations.

## 17. Failure conditions

Ingestion fails closed for:

```text
unknown required field mapping
invalid timestamp
unresolvable instrument
corrupt source record
provider response schema mismatch
ambiguous source resolution
missing provenance
non-deterministic identity
unsupported provider response version
```

Missing optional provider fields do not necessarily fail ingestion; they remain unavailable and are reflected in quality flags.

## 18. Frozen architecture

```text
TrueData is the research source.
Provider source classes remain distinct.
Raw provider records are immutable.
Canonical records require provenance.
Missing data is not converted to zero.
Quote fields are not treated as trades.
OI is not treated as volume.
Provider timestamps are preserved.
Provider identity is preserved.
Derived representations cannot overwrite source observations.
```

## 19. Learned/configurable parameters

None.

A72 is a data representation contract, not a strategy-parameter artifact.

## 20. External dependencies

```text
Exact TrueData tick response schema              = VERIFY
Exact getAllTicks(interval=1sec) response schema = VERIFY
Exact getticks response schema                   = VERIFY
Exact bar response schema                        = VERIFY
Timestamp timezone/precision                    = VERIFY
Sequence semantics                              = VERIFY
Volume semantics                                = VERIFY
OI semantics                                    = VERIFY
Historical endpoint limits                      = VERIFY
Actual entitlement                              = VERIFY
```

## 21. Adversarial review

### Leakage

The schema itself does not permit future values to be generated. Causality remains enforced by the event-time/availability contract.

### Semantic fabrication

The schema intentionally leaves unresolved provider semantics as UNKNOWN rather than converting field names into assumed meanings.

### Provider substitution

No Kite field is part of the TrueData canonical research observation.

### Data repair

No implicit interpolation, forward filling, zero filling, or synthetic event generation is permitted.

### Reproducibility

Raw record hashes and versioned source references make canonicalization auditable.

## ARCHITECTURE STATUS

Canonical TrueData observation envelope and source-class separation are frozen.

## UNRESOLVED

Provider-specific response semantics listed in Section 20.

## BLOCKERS

The implementation adapter must not be declared complete until the exact response schemas in the repository's TrueData documentation have been mapped field-by-field.

## NEXT ARTIFACT

A73 — TrueData Endpoint-by-Endpoint Field Mapping and Response Contract.
