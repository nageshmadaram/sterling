# A127 — Execution Adversarial Cases

These cases define the minimum hostile scenarios for the execution boundary.

```text
same idempotency key + same intent
    -> return prior broker result; do not resubmit

same idempotency key + different intent
    -> hard conflict; do not resubmit

invalid OrderIntent
    -> reject before broker call

non-fill event carrying fill fields
    -> reject

fill event with missing/invalid fill data
    -> reject

unknown provider status
    -> map to UNKNOWN; never guess

duplicate execution_event_id + identical event
    -> idempotent no-op

duplicate execution_event_id + conflicting event
    -> hard conflict

out-of-order event
    -> must not overwrite newer canonical state

partial fill
    -> preserve cumulative causal evidence; do not assume completion

cancel/fill race
    -> retain both observed events; downstream state machine resolves precedence

replacement order
    -> preserve parent order lineage; never reuse the original identity
```

The provider-specific mapping and ordering semantics remain implementation obligations of the concrete broker adapter and canonical lifecycle contract. This artifact does not invent broker behavior.
