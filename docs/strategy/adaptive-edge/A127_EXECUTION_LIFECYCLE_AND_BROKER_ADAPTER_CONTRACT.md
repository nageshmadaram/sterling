# A127 — Execution Lifecycle and Broker Adapter Contract

**Status:** IMPLEMENTATION CONTRACT  
**Depends on:** A75 execution/event contracts and A126 lifecycle contract  
**Scope:** canonical boundary between immutable order intent, broker submission, broker events, fills, and position lifecycle.

## 1. Purpose

A127 prevents the strategy engine from treating an order request as an execution fact.

```text
Decision
  -> Authorized intent
  -> Instrument selection
  -> OrderIntent
  -> Broker adapter
  -> Broker acknowledgement/events
  -> Canonical ExecutionEvent
  -> Position projection
  -> A126 lifecycle supervision
```

The adapter is infrastructure. Strategy policy must not depend on broker-specific request objects, session semantics, response payloads, or error types.

## 2. Execution invariants

```text
BUY_SIGNAL != ORDER_SUBMITTED
ORDER_SUBMITTED != ORDER_ACKNOWLEDGED
ORDER_ACKNOWLEDGED != FILLED
FILLED <=> actual executed quantity > 0
```

Position quantity changes only from canonical execution evidence.

An adapter must never manufacture a fill because an order was accepted.

## 3. Identity model

The following identities remain distinct:

```text
DecisionID
TradeID
OrderIntentID
BrokerOrderID
ExecutionEventID
FillID
PositionID
```

Canonical parentage is:

```text
Decision
  -> OrderIntent
  -> BrokerOrder
  -> ExecutionEvent
  -> Position
```

A broker order may produce multiple execution events and fills. A replacement order remains a distinct broker-order identity while retaining the originating trade/order lineage.

## 4. Immutable OrderIntent

`OrderIntent` is the only strategy-owned object crossing into the execution infrastructure boundary.

It must contain at least:

```text
order_intent_id
selection_id
instrument_id
side
quantity
intent_version
idempotency_key
created_at
```

The adapter must not mutate the intent. Broker-specific translation is one-way:

```text
OrderIntent -> BrokerRequest
```

## 5. Idempotency

Submission must be idempotent by the canonical `idempotency_key`.

For the same adapter scope:

```text
same key + same intent fingerprint
    -> same logical submission
```

A reused key with a different intent fingerprint is a hard conflict and must fail closed.

Idempotency state must distinguish:

```text
UNKNOWN
SUBMITTED
ACKNOWLEDGED
REJECTED
COMPLETED
```

It must never create a second broker order merely because the first response was lost.

## 6. Broker lifecycle normalization

Provider-specific statuses are normalized into canonical execution events. The canonical event vocabulary is:

```text
SUBMITTED
ACKNOWLEDGED
REJECTED
CANCEL_REQUESTED
CANCELLED
PARTIALLY_FILLED
FILLED
EXPIRED
AMENDED
UNKNOWN
```

Unknown broker states must remain `UNKNOWN`; they must not be mapped optimistically to `FILLED`.

## 7. ExecutionEvent

A canonical execution event records at minimum:

```text
execution_event_id
order_intent_id
event_type
event_time
broker_reference
filled_quantity
fill_price
```

A fill event must contain positive executed quantity and an execution price. Non-fill lifecycle events must not invent either value.

If broker timestamps and receipt timestamps differ, both must be retained where available.

## 8. Partial fills

If:

```text
requested = 100
filled = 40
```

then:

```text
actual_exposure = 40
remaining_order = 60
```

The pending 60 and active 40 are separate state objects.

A cancelled remainder is not an exit of the existing exposure.

## 9. Fill accounting

For fills `q_i` at prices `p_i`:

```text
average_execution_price = sum(q_i * p_i) / sum(q_i)
```

The implementation retains individual fills; the weighted average is derived state.

Signal price, decision price, order price, fill price, and mark price are distinct quantities.

## 10. Trigger versus execution

Lifecycle protection creates an obligation; it does not create a fill.

```text
PROTECTION_TRIGGER
    -> EXIT_OBLIGATION
    -> EXIT_ORDER
    -> BROKER_EVENT
    -> ACTUAL_FILL
```

A gap through a protection level records the trigger and the actual fill separately. The implementation must not backfill the trigger price as the execution price.

## 11. Execution uncertainty

Historical or simulated execution evidence is classified explicitly:

```text
OBSERVED
RECONSTRUCTED
MODELED
ASSUMED
UNKNOWN
```

Critical unknown execution information must not silently become an optimistic assumption.

## 12. Position projection

Position state is derived only from canonical execution events.

```text
execution event
    -> position projection
```

Acknowledgements, submissions, cancellation requests, and strategy decisions do not directly alter position quantity.

The projector must be able to handle:

```text
partial entry
multiple fills
partial exit
replacement orders
cancelled remainder
full flattening
```

## 13. Lifecycle handoff

Once actual exposure exists, the position enters A126 supervision.

```text
ExecutionEvent
    -> PositionState
    -> A126 LifecycleEngine
```

The lifecycle engine owns horizon/thesis/protection semantics. The broker adapter does not decide whether to promote, downgrade, hold, or exit.

## 14. Emergency behavior

Emergency/risk exits may bypass normal strategy continuation, but not the execution boundary.

Even an emergency exit must produce:

```text
exit obligation
-> order intent
-> broker interaction
-> canonical execution evidence
```

unless the infrastructure itself is unavailable, in which case the system records the unresolved operational state and continues fail-closed reconciliation.

## 15. Broker errors

Broker-specific exceptions are translated into canonical adapter outcomes. Error text must not be used as strategy state.

Examples of canonical outcomes include:

```text
RETRYABLE_UNAVAILABLE
DUPLICATE_CONFLICT
REJECTED
INVALID_REQUEST
AUTHORIZATION_FAILURE
RATE_LIMITED
UNKNOWN
```

Retry policy is infrastructure-owned and must be idempotency-safe.

## 16. No broker leakage

Forbidden dependencies from strategy/domain code:

```text
broker SDK request types
broker SDK response types
broker session objects
broker-specific order enums
broker-specific exception classes
```

Only the adapter may translate those objects.

## 17. Causal timestamps

At minimum, where available:

```text
T_decision
T_order_creation
T_submission
T_acknowledgement
T_fill
T_receipt
```

must remain distinguishable. The adapter must not collapse timestamps merely for convenience.

## 18. Contract tests

The implementation must attack:

```text
lost acknowledgement
lost submission response
replayed submission
same idempotency key + changed intent
partial fill
fill after cancellation request
replacement order
broker rejection after partial fill
unknown broker status
gap through protection
stale event
out-of-order event
duplicate execution event
```

Every case must preserve causal identity and fail closed where the state cannot be established safely.

## 19. Explicit non-goals

A127 does not invent:

```text
slippage parameters
transaction-fee values
position-sizing rules
lifecycle thresholds
stop-loss formulas
trailing formulas
option-selection mathematics
broker-specific undocumented behavior
```

Those require their respective authoritative specifications or external evidence.

## 20. Completion criterion

A127 is complete when:

```text
OrderIntent
    -> adapter
    -> canonical execution events
    -> position projection
    -> A126 lifecycle
```

is executable in a deterministic test harness, while all provider-specific details remain behind the infrastructure adapter and every execution fact remains traceable to its originating order intent.
