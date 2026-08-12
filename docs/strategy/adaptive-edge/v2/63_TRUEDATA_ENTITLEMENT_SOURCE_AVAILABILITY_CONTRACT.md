# Adaptive Edge V2.1 — TrueData Entitlement and Source-Availability Verification Contract

**Artifact:** A63  
**Status:** SPECIFICATION / EXTERNAL-DEPENDENCY GATE  

## 1. Purpose

A63 defines the evidence required before Adaptive Edge may claim that a TrueData research dataset is available, complete, and suitable for a specified experiment.

Authentication success is not sufficient evidence.

## 2. Verification layers

```text
Credentials
   |
   v
Authentication
   |
   v
Entitlement
   |
   v
Transport capability
   |
   v
Historical coverage
   |
   v
Field availability
   |
   v
Timestamp semantics
   |
   v
Revision semantics
   |
   v
Dataset suitability
```

Each layer must be independently evidenced.

## 3. Required evidence record

```text
provider = TrueData
account/entitlement identifier = redacted or non-secret identifier
feed/product = exact entitled product
transport = REST / WebSocket / replay / other
instrument universe
symbol
resolution
requested date range
actual available date range
required fields
actual fields
request limits
retention policy
provider documentation version/date
verification timestamp
verification result
```

Credentials and secrets must never be committed to the repository or dataset manifest.

## 4. Tick-specific gate

Because TrueData publicly distinguishes tick history from minute-bar history, a tick experiment requires separate verification of tick-history entitlement and retention.

The current public TrueData documentation reports five trading days of default REST tick history and six months for minute bars. This is a provider-level statement, not proof of the user's account entitlement. The account-specific value must therefore be measured and recorded.

## 5. Coverage test

For requested interval `[start,end]`, define:

```text
coverage_start
coverage_end
missing_sessions
missing_intervals
```

The acquisition must not claim full coverage when any required session is absent.

A partial dataset must be explicitly classified:

```text
PARTIAL_SOURCE
```

and cannot silently enter a full-history experiment.

## 6. Field test

For every canonical required field:

```text
provider field exists
semantic definition known
unit known
nullability known
timestamp relationship known
```

Unknown semantics are blockers for features that depend on that field.

## 7. Timestamp verification

The verification procedure must compare provider timestamps against an independently known market session boundary for a small sample.

It must establish:

```text
timezone
session open
session close
intraday ordering
weekend behavior
holiday behavior
```

No timezone conversion is permitted before this verification is complete.

## 8. Availability semantics

The research system must distinguish:

```text
observation_time
provider publication/availability time
client receipt time
```

If the provider does not expose publication/availability time, that limitation must be recorded as UNKNOWN.

A backtest may not assume zero latency merely because timestamps are present.

## 9. Replay/revision verification

If TrueData provides replay or historical reconstruction, determine whether the historical stream is:

```text
immutable snapshot
corrected historical feed
revisioned dataset
UNKNOWN
```

The chosen policy must be recorded in the dataset manifest.

## 10. Request-limit verification

The acquisition system must record the provider's applicable historical request limits and ensure that chunking does not alter the resulting dataset.

Adjacent chunks must be checked for:

```text
duplicates
gaps
overlap
ordering changes
```

## 11. Suitability gate

A dataset is RESEARCH_READY only if:

```text
entitlement verified
AND
transport verified
AND
required coverage verified
AND
required fields verified
AND
timestamp semantics verified sufficiently for the experiment
AND
revision policy recorded
AND
source hash recorded
AND
manifest complete
```

Otherwise:

```text
RESEARCH_BLOCKED
```

## 12. Failure conditions

Fail closed for:

```text
expired/invalid entitlement
insufficient retention
missing required sessions
missing required fields
unknown critical timestamp semantics
unresolved duplicate records
unresolved revision behavior
source response inconsistent across repeated acquisition
```

## 13. Reproducibility test

The same request must be acquired twice when provider policy permits.

Compare:

```text
row count
first/last timestamp
canonical hash
missingness
field values
```

If the provider produces revisions, the difference must be explainable by the recorded revision policy.

## 14. Security

Never store:

```text
TrueData username
TrueData password
API secret
session token
```

in source control, manifests, logs, test fixtures, or error output.

## 15. Frozen architecture

```text
TrueData-only research source
Evidence-based entitlement gate
Explicit coverage verification
Explicit field verification
Explicit timestamp verification
Explicit revision policy
Content-addressed dataset
Fail-closed research readiness
```

## 16. UNKNOWN / TODO

```text
TODO: execute against the user's actual entitled TrueData account
TODO: record actual tick retention
TODO: record exact tick transport
TODO: verify exact fields and semantics
TODO: verify timestamp/availability behavior
TODO: verify revision/replay behavior
TODO: acquire production research dataset
```

## 17. ARCHITECTURE STATUS

**FROZEN:** verification protocol and research-readiness gate.

**UNRESOLVED:** account-specific provider facts.

**BLOCKERS:** no empirical strategy experiment may be declared valid until the account-specific TrueData evidence passes this gate.

**NEXT ARTIFACT:** A64 — Canonical Raw Tick Dataset Schema and Acquisition Manifest.
