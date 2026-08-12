# Adaptive Edge V2 — TrueData / Kite Provider Boundary Conformance Test Matrix

**Artifact:** A68  
**Status:** SPECIFICATION  
**Version:** 2.1.0

## 1. Purpose

A68 proves that market-data authority and trading authority remain separate throughout the system.

```text
TrueData -> research / market state
Kite    -> orders / fills / positions / trading costs
```

No component may silently substitute one provider for the other.

## 2. Provider ownership matrix

| Domain | Authority | Forbidden substitute |
|---|---|---|
| Historical research market data | TrueData | Kite |
| Live research market state | TrueData | Kite |
| Tick/bar construction | TrueData | Kite |
| Feature source observations | TrueData | Kite |
| Label source observations | TrueData | Kite |
| Order submission | Zerodha Kite | TrueData |
| Order status | Zerodha Kite | TrueData |
| Trade/fill confirmation | Zerodha Kite | TrueData |
| Position state | Zerodha Kite | TrueData |
| Square-off | Zerodha Kite | TrueData |
| Execution charges | Zerodha Kite | TrueData |
| Research accounting assumptions | Canonical contract | provider inference |

## 3. Boundary invariants

1. A TrueData market event cannot directly submit an order.
2. A Kite fill cannot modify a historical TrueData observation.
3. Kite market quotes cannot repair missing TrueData research data.
4. TrueData ticks cannot be treated as execution confirmation.
5. A submitted Kite order is not a fill until Kite trade evidence confirms execution.
6. Position state is derived from confirmed execution evidence, not order intent.
7. Square-off is an execution action through Kite, not a state mutation in the research layer.
8. Research labels are generated from the declared TrueData target source, not from realized Kite P&L.
9. Realized P&L is downstream of execution/accounting and cannot alter the original prediction.
10. Provider identifiers and timestamps must remain auditable end-to-end.

## 4. Conformance scenarios

### C1 — Missing TrueData observation

Input:

```text
TrueData observation unavailable
Kite quote available
```

Expected:

```text
research observation = MISSING
Kite quote            = NOT ACCEPTED AS SUBSTITUTE
```

### C2 — Kite order accepted but not filled

Expected:

```text
order state != position state
position quantity remains unchanged
```

### C3 — Partial Kite fill

Expected:

```text
position effect = confirmed filled quantity only
remaining order quantity = not a position
```

### C4 — Multiple fills for one order

Expected:

```text
each trade retained
position reconstructed from fills
average execution price computed from confirmed fills
```

### C5 — TrueData price differs from Kite execution price

Expected:

```text
TrueData = research observation
Kite = execution observation
```

Neither is overwritten by the other.

The difference becomes execution slippage/cost evidence only under the declared execution-cost contract.

### C6 — Kite position differs from locally reconstructed position

Expected:

```text
RECONCILIATION_FAILURE
```

No automatic mutation of historical evidence.

### C7 — TrueData reconnect

Expected:

```text
source gap = explicit gap
```

No synthetic ticks are inserted.

### C8 — Kite reconnect

Expected:

Execution state enters the declared recovery state. Outstanding orders and positions are reconciled against Kite before further authorization.

### C9 — Provider timestamp mismatch

Expected:

The event remains provider-specific and cannot be reordered using an undocumented timezone conversion.

### C10 — Research replay

Expected:

The replay uses the versioned TrueData dataset and never calls Kite to fill missing historical observations.

## 5. State-machine separation

Research state:

```text
RAW_TRUE_DATA
    -> CANONICAL_EVENT
    -> FEATURE_STATE
    -> PREDICTION
    -> ECONOMIC_DECISION
```

Trading state:

```text
AUTHORIZED
    -> KITE_ORDER_SUBMITTED
    -> KITE_ORDER_ACCEPTED
    -> KITE_PARTIAL_FILL
    -> KITE_FILLED
    -> POSITION
    -> SQUARE_OFF_ORDER
    -> EXIT_FILL
    -> FLAT
```

The two state machines communicate only through explicit contracts.

## 6. Forbidden shortcuts

The following implementations are invalid:

```text
if TrueData missing:
    use Kite quote

if Kite order accepted:
    create position

if position expected:
    fabricate fill

if historical TrueData unavailable:
    use current Kite data

if Kite position differs:
    overwrite local accounting without reconciliation
```

## 7. Test evidence

Every conformance test must record:

```text
provider
provider version/API contract
request/reference ID where available
input timestamp
availability timestamp
expected authority
actual authority
result
failure code
```

## 8. Failure policy

Boundary violations are fail-closed.

A provider-boundary violation must not be downgraded to a warning when it can affect:

- feature construction;
- target construction;
- prediction;
- order authorization;
- position reconstruction;
- accounting;
- risk reconciliation.

## 9. Frozen versus external

### Frozen architecture

- TrueData owns research observations.
- Kite owns execution observations.
- Position state derives from confirmed Kite fills.
- Research labels remain independent of realized Kite P&L.
- Provider substitution is forbidden.
- Boundary failures are fail-closed.

### External dependencies

```text
TrueData exact payload semantics             = external
TrueData entitlement                         = external
Kite account/execution contract              = external
Kite order/trade/position API availability   = external
```

## 10. Adversarial review

The strongest attack is an apparently convenient reconciliation shortcut: using Kite because it contains a price when TrueData has a gap. This is explicitly prohibited because it changes the research population and can introduce availability and selection bias.

The second attack is treating an accepted order as an execution. Kite's order lifecycle explicitly separates order status from actual trades; the system therefore waits for execution evidence.

The third attack is using realized P&L to redefine a historical label. This is prohibited because outcome and execution are downstream of the original decision.

## ARCHITECTURE STATUS

Frozen: provider ownership and cross-provider boundary invariants.

## UNRESOLVED

Provider-specific runtime details require entitlement/API verification.

## BLOCKERS

No live conformance test can pass until actual TrueData and Kite credentials/configuration are available in the execution environment.

## NEXT ARTIFACT

A69 — Canonical Research Dataset Ingestion and Provenance Contract.
