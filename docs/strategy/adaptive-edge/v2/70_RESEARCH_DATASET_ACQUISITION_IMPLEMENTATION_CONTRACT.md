# Adaptive Edge V2 — Research Dataset Acquisition Implementation Contract

**Artifact:** A70  
**Status:** SPECIFICATION  
**Version:** 2.1.0

## 1. Purpose

A70 translates the frozen TrueData research-source specification into an implementation boundary without inventing provider semantics.

TrueData is the sole market-data authority for Adaptive Edge research. Zerodha Kite is not a historical research-data fallback.

## 2. Verified provider facts

The following are verified from current provider documentation:

### TrueData

TrueData real-time tick streaming exposes Symbol ID, timestamp, LTP, LTQ, ATP, TTQ, OHLC, previous close, OI, previous OI close, turnover, special tags, tick sequence number, bid, bid quantity, ask, and ask quantity.

TrueData documents historical REST availability as:

```text
Tick data       -> last 5 trading days by default
1-60 min bars   -> last 6 months by default
Daily bars      -> 10+ years by default
```

Historical REST request limits are separately documented for tick and minute-bar data.

Therefore the implementation must not assume long-range tick history from REST.

### Zerodha Kite

Kite Connect documents separate order and trade APIs. Successful order placement returns an order identifier but does not guarantee execution. Executed trades are exposed separately and an order may generate multiple trades.

Kite therefore remains the execution authority, not the research-data authority.

## 3. Acquisition modes

The implementation must distinguish:

```text
MODE_A = TrueData historical REST
MODE_B = TrueData historical/replay capability, if entitled and documented
MODE_C = TrueData real-time capture
```

No mode may be silently substituted for another.

## 4. Historical acquisition rule

For a requested interval:

1. verify the requested resolution;
2. verify entitlement;
3. verify provider-supported retention for that mode;
4. acquire only from TrueData;
5. preserve raw provider payload;
6. validate schema;
7. validate timestamps and sequence information;
8. calculate content hash;
9. produce a versioned manifest;
10. only then produce canonical derived data.

If the provider cannot supply the requested historical interval, acquisition fails closed.

## 5. Raw tick preservation

Where tick data are available, the raw event must preserve provider fields without semantic compression before validation.

At minimum the adapter must retain, where supplied:

```text
symbol_id
timestamp
LTP
LTQ
ATP
TTQ
OHLC
prev_close
OI
prev_OI_close
turnover
special_tag
tick_sequence_no
bid
bid_qty
ask
ask_qty
```

Missing provider fields remain missing.

## 6. Canonicalization

Canonicalization may normalize representation but may not alter economic meaning.

Permitted:

```text
field-name normalization
numeric type normalization
timestamp representation normalization after timezone verification
provider metadata attachment
schema validation
```

Forbidden:

```text
forward filling
interpolation
fabricated ticks
Kite substitution
invented sequence ordering
invented trade classification
```

## 7. Timestamp contract

The dataset must retain:

```text
event_time
availability_time, when documented/available
ingestion_time
provider_sequence, when supplied
```

Research causality uses the documented availability boundary. If availability cannot be established for a historical artifact, the dataset may be used only under an explicitly conservative research policy that does not claim event-time availability equivalence.

## 8. Derived resolutions

Derived resolutions are produced only from validated finer-grained source observations.

```text
raw tick -> 1s/5s/10s/30s/1m/... derived representation
```

The candidate resolution set is controlled by the preregistered research registry.

A 1-second bar is a derived representation unless TrueData documentation explicitly identifies a native 1-second bar feed available to the actual entitlement.

## 9. Provenance

Every dataset artifact must have a manifest containing:

```text
dataset_version
source_provider
source_api/mode
provider_documentation_version_or_reference
instrument
requested_start
requested_end
actual_start
actual_end
source_timezone
time_resolution
raw_schema_version
canonical_schema_version
feature_set_version
label_definition_version
research_registry_version
row/event_count
missingness_summary
content_sha256
creation_timestamp
```

## 10. Reproducibility

A dataset version is immutable after publication.

If source data are reacquired, the result receives a new dataset version and hash. Existing research results remain associated with their original dataset version.

## 11. Failure conditions

Acquisition fails when:

- entitlement is absent;
- requested history exceeds verified provider availability;
- provider schema is incompatible;
- timestamps are malformed;
- sequence semantics are contradictory;
- duplicate handling cannot be justified;
- source payload cannot be reproduced or hashed;
- data are silently repaired;
- another provider is substituted.

## 12. Frozen / configurable / unknown

### Frozen architecture

- TrueData-only research source;
- raw-before-derived preservation;
- immutable dataset versioning;
- provenance manifest;
- fail-closed acquisition;
- deterministic derivation;
- Kite excluded from historical research acquisition.

### Configuration requiring validation

- requested instrument;
- date interval;
- resolution;
- acquisition mode;
- session-calendar version;
- research registry version.

### UNKNOWN

```text
actual account tick entitlement
actual long-range tick availability
actual replay entitlement
actual provider availability semantics for historical replay
actual correction/revision behavior
```

## 13. Adversarial review

### Data-availability bias

If tick data exist only for recent periods while minute data exist for longer periods, comparing models on different populations would confound resolution with time period. Resolution comparisons therefore require a common eligible population or an explicitly declared nested-population design.

### Selection bias

A failed acquisition cannot be replaced with another provider without changing the experiment.

### Look-ahead

A historical bar cannot be treated as available at its event timestamp unless the provider's availability semantics justify that assumption.

### Survivorship

Instrument eligibility must be determined independently of future performance.

## ARCHITECTURE STATUS

Frozen: acquisition boundary, provider ownership, provenance, immutable dataset versions, raw-first preservation, fail-closed behavior.

## UNRESOLVED

Actual TrueData entitlement and the exact historical/replay mode available to the account.

## BLOCKERS

A real tick-level acquisition run requires entitlement verification. No synthetic dataset may be substituted.

## NEXT ARTIFACT

A71 — Canonical Tick Event Schema and Deterministic Aggregator Implementation Contract.
