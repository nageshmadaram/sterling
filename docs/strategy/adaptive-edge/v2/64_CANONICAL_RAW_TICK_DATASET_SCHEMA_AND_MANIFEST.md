# Adaptive Edge V2.1 — Canonical Raw Tick Dataset Schema and Manifest

**Artifact:** A64  
**Status:** SPECIFICATION / IMPLEMENTATION-READY  

## 1. Purpose

A64 defines the immutable research artifact produced after TrueData entitlement verification succeeds.

The raw dataset is the primary market-data evidence. Derived bars and features are projections and must retain provenance to this artifact.

## 2. Dataset identity

Every dataset receives:

```text
dataset_version
source_provider
source_product
instrument
requested_start
requested_end
actual_start
actual_end
resolution = TICK
schema_version
provider_document_version
source_hash
created_at
```

## 3. Canonical row identity

Each raw observation must have a deterministic identity.

Preferred:

```text
provider_event_id
```

when supplied.

Otherwise a deterministic identity must be constructed from the documented provider fields without pretending that identical observations are distinct events.

The identity construction itself must be versioned.

## 4. Canonical row

The canonical schema is:

```text
observation_id
provider_symbol
instrument_id
observation_time
received_time
price
volume
open_interest
bid_price
bid_quantity
ask_price
ask_quantity
source_sequence
source_payload_hash
schema_version
```

Fields that are not supplied by the entitled TrueData feed remain NULL/UNKNOWN and are not fabricated.

## 5. Semantic requirements

### price

The exact TrueData tick price field must be documented.

### volume

The exact TrueData volume semantics must be documented before cumulative/delta transformations are performed.

### open_interest

OI is treated as a point-in-time state unless provider documentation establishes another semantic.

### bid/ask

Bid and ask are Level-1 observations if supplied by the feed. Their freshness and timestamp relationship must be preserved.

### source_sequence

UNKNOWN until the actual feed demonstrates a deterministic provider sequence/reference.

## 6. Serialization

Canonical serialization must:

```text
use fixed column order
use explicit UTF-8
use explicit newline convention
use deterministic null representation
use deterministic timestamp representation
use deterministic numeric representation
```

The resulting bytes are hashed with SHA-256.

## 7. Manifest

The manifest must contain:

```text
dataset_version
schema_version
source_provider
source_product
provider_document_version
entitlement_evidence_id
instrument
resolution
requested_range
actual_range
source_timezone
timestamp_semantics_status
availability_semantics_status
revision_policy_status
row_count
missingness_summary
duplicate_count
ordering_status
source_file_sha256
canonicalization_version
aggregation_parent = null
feature_set_version = not-applicable
label_definition_version = not-applicable
created_at
```

## 8. Derived dataset lineage

For any derived bar dataset:

```text
derived_dataset
    -> parent_dataset_version
    -> parent_source_hash
    -> aggregation_version
```

A derived dataset without parent lineage is not a canonical research artifact.

## 9. Data-quality states

```text
SOURCE_UNVERIFIED
SOURCE_VERIFIED
PARTIAL_SOURCE
RESEARCH_READY
INVALID
SUPERSEDED
```

Only `RESEARCH_READY` may be consumed by a declared experiment.

## 10. Corrections and supersession

A corrected source dataset must not overwrite a previous research artifact.

Instead:

```text
old_dataset
    -> SUPERSEDED_BY
new_dataset
```

Both hashes remain auditable.

## 11. Session gaps

A missing market interval must be represented explicitly in dataset metadata.

Do not synthesize ticks to fill gaps.

Session/calendar classification is separate from raw-data completeness.

## 12. Causal watermark

The dataset must not claim that `observation_time` equals `availability_time` unless verified.

For research requiring real-time causal simulation, the manifest must include the availability-semantics status.

## 13. Security

Credentials, API tokens and secrets are never part of the dataset or manifest.

## 14. Invariants

```text
I64.1 dataset hash matches canonical bytes
I64.2 observation identity is deterministic
I64.3 raw rows are never silently modified
I64.4 derived datasets reference parent dataset
I64.5 no future observation enters a historical state
I64.6 missing observations are not fabricated
I64.7 source revisions create new dataset versions
I64.8 research experiments consume only RESEARCH_READY datasets
```

## 15. Failure conditions

Reject the artifact when:

```text
hash mismatch
invalid schema
ambiguous row identity
unresolved timestamp ordering
corrupt numeric field
unresolved source revision
missing mandatory manifest evidence
```

## 16. Frozen architecture

```text
immutable raw source
content-addressed dataset
versioned schema
explicit provenance
explicit supersession
fail-closed research consumption
```

## 17. UNKNOWN / TODO

```text
exact TrueData tick payload fields
exact provider event identity/sequence field
exact tick timestamp semantics
actual entitlement-specific retention
actual dataset
```

## 18. ARCHITECTURE STATUS

**FROZEN:** canonical raw-data model, provenance, hashing, versioning, lineage and failure semantics.

**UNRESOLVED:** provider-specific field/identity semantics and actual entitled dataset.

**BLOCKERS:** actual source verification remains required.

**NEXT ARTIFACT:** A65 — Tick-to-Bar Deterministic Aggregation Contract.
