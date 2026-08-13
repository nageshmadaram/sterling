# A127 — Implementation Notes

## Status

IMPLEMENTED — boundary and adversarial contract tests added.

## Scope

The implementation establishes the immutable order-intent boundary, canonical execution-event validation, and idempotent broker submission seam defined by A127.

## Fail-closed invariants

```text
invalid OrderIntent       -> no broker call
reused idempotency key    -> prior logical result
changed intent + same key -> hard conflict
invalid fill evidence     -> rejection
unknown status            -> UNKNOWN
invalid evidence class    -> rejection
non-fill with fill data   -> rejection
```

## Important limitation

The current `normalize_event` method validates a canonical event. It does not claim to be a provider-specific status mapper. Provider-specific translation must be implemented by a concrete broker adapter before production integration.

This is intentional: no undocumented broker status mapping is invented.

## Remaining A127 implementation work

Before A127 can be declared production-complete, the execution boundary still requires:

```text
provider status -> canonical status mapping
duplicate execution-event detection
stale/out-of-order event handling
partial-fill position projection
cancellation/fill race handling
replacement-order lineage
A126 lifecycle handoff
```

These must be implemented against authoritative provider contracts or deterministic test fixtures. No broker behavior is inferred from undocumented assumptions.
