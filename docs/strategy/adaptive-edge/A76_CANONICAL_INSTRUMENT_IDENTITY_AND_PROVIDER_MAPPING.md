# A76 — Canonical Instrument Identity and Provider Mapping Contract

## 1. Purpose

Define how Adaptive Edge identifies one economic instrument across TrueData research data and Zerodha Kite trading data without treating provider-specific identifiers as globally interchangeable.

This artifact solves a critical join problem:

```text
TrueData observation
        |
        v
canonical economic instrument
        |
        v
Kite tradable instrument
```

The mapping must be deterministic, auditable, time-aware, and fail closed.

## 2. Why this artifact exists

TrueData and Kite are independent provider namespaces. A string such as a symbol or tradingsymbol is not sufficient proof that two records represent the same contract.

Kite's official instrument master provides `instrument_token`, `exchange_token`, `tradingsymbol`, `name`, `expiry`, `strike`, `tick_size`, `lot_size`, `instrument_type`, `segment`, and `exchange`. The instrument dump is generated daily. citeturn0search0turn0search1

TrueData repository evidence contains a dedicated Symbol Master collection and reference endpoints for traded symbols, expiries and option-chain data. The exact provider field semantics remain an evidence-gated dependency. fileciteturn1306file0L1-L5

Therefore provider identifiers are stored as provider-specific attributes, not as the canonical identity itself.

## 3. Canonical identity

The canonical identity represents the economic contract independently of either provider.

Conceptually:

```text
CanonicalInstrumentIdentity {
    instrument_class
    underlying_identity
    exchange
    segment
    instrument_type
    expiry
    strike
    option_side
    contract_multiplier
}
```

Not every field applies to every instrument class.

Examples of classes:

```text
EQUITY
INDEX
FUTURE
OPTION
```

The exact universe is controlled by the strategy artifact and is not invented here.

## 4. Provider identities

A canonical instrument may have zero, one, or multiple provider representations.

```text
CanonicalInstrument
    |
    +-- TrueDataIdentity
    |
    +-- KiteIdentity
```

### TrueData identity

The following are reserved for provider evidence:

```text
truedata_symbol
truedata_instrument_id
truedata_exchange
truedata_contract_fields
truedata_metadata_version
```

Exact field names and identifier semantics are `UNKNOWN` until verified from the TrueData Symbol Master/response fixtures.

### Kite identity

The Kite identity may contain:

```text
kite_instrument_token
kite_exchange_token
kite_tradingsymbol
kite_exchange
kite_segment
kite_instrument_type
kite_expiry
kite_strike
kite_tick_size
kite_lot_size
```

These correspond to fields documented by Kite's instrument master. citeturn0search0

## 5. Economic identity vs provider identity

The canonical economic identity must not be derived from a provider token.

```text
WRONG:
canonical_id = kite_instrument_token

WRONG:
canonical_id = truedata_symbol

WRONG:
canonical_id = exchange + tradingsymbol
```

Provider identifiers can change, expire, or exist only within a provider namespace.

The canonical identity instead represents the economic contract attributes required to distinguish contracts.

For an option, the minimum conceptual identity is:

```text
underlying
+ exchange/market
+ expiry
+ strike
+ call/put side
```

For a future:

```text
underlying
+ exchange/market
+ expiry
```

For a non-derivative instrument:

```text
issuer/index identity
+ exchange/market
+ instrument class
```

The exact normalization of underlying names is unresolved until provider mappings are verified.

## 6. Time validity

Instrument mappings are time-dependent.

A mapping record therefore requires:

```text
mapping_version
valid_from
valid_to
source_snapshot
verification_time
```

A provider instrument-master row must not be treated as valid indefinitely merely because the symbol is familiar.

This matters especially for derivatives because contracts expire and new contracts are introduced.

Kite's instrument dump is generated once per day, so the application must treat the dump as a dated reference snapshot rather than a timeless master. citeturn0search0

## 7. Mapping relation

The mapping relation is:

```text
TrueDataIdentity
       |
       | verified mapping
       v
CanonicalInstrumentIdentity
       ^
       | verified mapping
       |
KiteIdentity
```

A mapping is valid only when all required identity attributes agree or a documented provider-specific transformation establishes equivalence.

No fuzzy matching is permitted for execution-critical identity.

## 8. Matching rules

### Rule M1 — Exact provider identity

A provider-native identifier may identify a record only inside its provider namespace.

### Rule M2 — Deterministic canonicalization

Every provider-to-canonical mapping must be a deterministic transformation with recorded provenance.

### Rule M3 — Contract attribute agreement

For derivatives, required contract attributes must agree:

```text
underlying
exchange/market
expiry
strike where applicable
option side where applicable
instrument type
```

### Rule M4 — No symbol-only matching

A symbol string alone cannot authorize a cross-provider mapping.

### Rule M5 — No execution on ambiguous mapping

If multiple Kite instruments satisfy the candidate mapping, execution authorization is denied.

### Rule M6 — No research substitution

Kite metadata cannot be used to fill missing TrueData research semantics unless a later artifact explicitly permits that operation for a non-market-data reference field.

### Rule M7 — No future contract leakage

Historical research may use only the instrument mapping that was valid for the relevant historical timestamp and dataset snapshot.

### Rule M8 — Expired contracts remain identifiable

An expired contract may remain in historical research data even though it is not currently executable.

Research existence and current tradability are separate states.

## 9. Option contract identity

For an option:

```text
OptionIdentity = (
    underlying,
    market,
    expiry,
    strike,
    side
)
```

where:

```text
side ∈ {CALL, PUT}
```

This is a conceptual identity, not a claim that TrueData or Kite uses these exact field names.

The option-selection artifact may later impose additional constraints such as liquidity, expiry eligibility, strike selection, lot size, or tick size. Those are not part of identity and must not be conflated with identity.

## 10. Underlying identity

The option's underlying is a separate canonical entity.

```text
Option
   |
   +---- underlying ---> CanonicalUnderlying
```

An option must not be linked to an underlying merely by parsing its display symbol unless the provider contract establishes that transformation.

The underlying mapping must therefore be explicit:

```text
provider option identity
    -> provider underlying identity
    -> canonical underlying identity
```

## 11. Trading identity

The final executable instrument must contain enough Kite information to construct an actual order request.

Kite's order APIs use exchange and tradingsymbol for order operations, while its instrument master provides the instrument token and contract metadata. citeturn0search3turn0search0

Therefore:

```text
Strategy-selected canonical instrument
            |
            v
verified Kite identity
            |
            v
execution order
```

No strategy decision may directly construct an order from an unverified display symbol.

## 12. Position identity

Kite position records contain `tradingsymbol`, `exchange`, and `instrument_token`, among other position fields. citeturn0search2

Therefore position reconciliation must resolve:

```text
Kite position
    -> Kite instrument identity
    -> canonical instrument identity
```

The system must not assume that a local position object is correct merely because an order was submitted.

## 13. State model

An instrument can move through:

```text
DISCOVERED
    -> VERIFIED
    -> ELIGIBLE
    -> SELECTED
    -> EXECUTABLE
    -> EXPIRED
    -> RETIRED
```

These states are not interchangeable.

### DISCOVERED
Provider metadata exists but cross-provider identity has not been proven.

### VERIFIED
Required identity attributes have been validated.

### ELIGIBLE
The instrument satisfies a later strategy-specific eligibility contract.

### SELECTED
The strategy has chosen the instrument for a specific opportunity.

### EXECUTABLE
A valid Kite identity and current trading conditions exist.

### EXPIRED
The contract's expiry has passed.

### RETIRED
The provider mapping is no longer valid or has been superseded.

## 14. State transition invariants

```text
DISCOVERED -> VERIFIED
    only after identity verification

VERIFIED -> ELIGIBLE
    only after strategy eligibility evaluation

ELIGIBLE -> SELECTED
    only after deterministic selection

SELECTED -> EXECUTABLE
    only after current Kite identity verification

EXECUTABLE -> EXPIRED
    when contract expiry is reached

Any ambiguous mapping
    -> cannot become EXECUTABLE
```

Forbidden:

```text
DISCOVERED -> EXECUTABLE
SELECTED -> EXECUTABLE without Kite verification
expired -> new execution
ambiguous -> execution
```

## 15. Data dependencies

| Dependency | Source | Owner | Update | Consumer | Failure |
|---|---|---|---|---|---|
| TrueData instrument identity | TrueData Symbol Master | TrueData adapter | provider-defined | research mapping | fail closed |
| TrueData expiry/reference data | TrueData reference endpoints | TrueData adapter | provider-defined | research mapping | fail closed |
| Kite instrument identity | Kite instrument dump | Kite adapter | daily documented dump | execution mapping | fail closed |
| Kite order identity | Kite order/trade APIs | Kite adapter | event/request driven | execution/reconciliation | fail closed |
| Kite position identity | Kite positions API | Kite adapter | reconciliation cycle | position/accounting | fail closed |
| canonical mapping | Sterling mapping layer | Sterling | deterministic | research + execution boundary | deny mapping |

Kite's official instrument documentation confirms the daily instrument dump and its contract fields. citeturn0search0

## 16. Failure conditions

Execution or strategy-critical mapping must fail closed on:

```text
missing provider identity
missing required contract attribute
ambiguous candidate mapping
conflicting expiry
conflicting strike
conflicting option side
conflicting exchange
conflicting instrument type
stale mapping snapshot
expired contract for new entry
Kite instrument absent
Kite identity mismatch
provider schema uncertainty
```

No fallback to a "closest" contract is allowed unless a future artifact explicitly defines a deterministic selection policy.

## 17. Frozen vs learned/configurable

### ARCHITECTURE FROZEN

```text
Provider namespaces remain separate.
Canonical economic identity is provider-independent.
TrueData is research-data authority.
Kite is execution/trading authority.
Cross-provider mappings are explicit and auditable.
Mapping is time-aware.
Symbol-only matching is prohibited.
Ambiguous mappings cannot execute.
Expired contracts remain valid historical identities but are not new-entry executable.
```

### LEARNED

None.

Instrument identity is not a learned quantity.

### CONFIGURABLE / VALIDATED LATER

```text
eligible exchanges
eligible instrument classes
expiry-selection policy
strike-selection policy
liquidity filters
contract ranking
execution eligibility thresholds
```

These belong to later artifacts.

## 18. UNKNOWN / TODO

TrueData-specific details remain explicitly unresolved:

```text
exact Symbol Master response fields
TrueData permanent instrument identifier, if any
TrueData exchange identifier semantics
TrueData option-side field semantics
TrueData strike representation
TrueData expiry representation
TrueData underlying identifier semantics
TrueData contract lifecycle/version semantics
TrueData-to-Kite deterministic mapping key
```

These must be resolved from the repository's TrueData Symbol Master documentation and controlled response fixtures before implementing the production mapper.

## 19. Hostile review

### Symbol collision
Two providers can use different symbols for the same contract. Provider namespace separation handles this.

### Same symbol, different contract
A derivative symbol can change meaning across expiry/series. Contract attributes are therefore mandatory.

### Expiry rollover
A current contract must never be substituted into a historical row simply because its symbol resembles the historical one.

### Stale Kite master
The daily dump is dated; therefore mapping must retain snapshot provenance.

### Ambiguous mapping
The correct response is denial, not heuristic selection.

### Provider data vs execution data
TrueData establishes the research observation. Kite establishes whether a corresponding executable contract currently exists. Neither provider silently overrides the other.

### Position reconciliation
An order submission is not sufficient to create a position. Kite's actual order/trade/position state remains authoritative for trading state. citeturn0search2turn0search3

## 20. Completion criteria

A cross-provider mapping becomes `VERIFIED` only when:

```text
[ ] TrueData source record captured
[ ] TrueData identity fields verified
[ ] canonical identity derived deterministically
[ ] Kite instrument record captured
[ ] required contract attributes agree
[ ] mapping timestamp/snapshot recorded
[ ] ambiguity check passes
[ ] expired/current status determined
[ ] provenance recorded
[ ] regression fixture exists
```

`VERIFIED != ELIGIBLE != SELECTED != EXECUTABLE`.

## 21. Status

```text
ARTIFACT: A76

ARCHITECTURE STATUS:
  COMPLETE

FROZEN:
  provider-independent economic identity
  explicit TrueData/Kite namespaces
  time-aware mappings
  fail-closed execution mapping
  no symbol-only matching

UNRESOLVED:
  exact TrueData Symbol Master response semantics
  exact deterministic TrueData-to-Kite mapping key

BLOCKERS:
  None for architecture.
  Production mapping implementation is blocked until TrueData identity fields are verified.

NEXT ARTIFACT:
  A77 — Session Calendar, Trading-Day, and Market-Time Contract
```
