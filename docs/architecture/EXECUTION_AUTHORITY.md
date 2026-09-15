# Canonical Execution Authority Specification

## 1. Single Execution Authority Principle

All broker order submissions across Sterling—including Kite Engine, Opening Leaders, Intraday, Gamma Move, Adaptive Edge, Navigator, and manual orders—must route exclusively through the canonical `ExecutionService`.

No strategy or runner module may invoke broker REST/WebSocket APIs (`place_order`, `modify_order`, `cancel_order`) directly.

## 2. Order State Machine Invariants

```text
                    reserve()
                       │
                       ▼
                  ┌──────────┐
                  │ RESERVED │
                  └────┬─────┘
                       │ atomic CAS
                       ▼
                 ┌────────────┐
                 │ SUBMITTING │
                 └──┬─────┬───┘
                    │     │
             ACK    │     │ transport uncertainty
                    │     ▼
                    │  ┌─────────┐
                    │  │ UNKNOWN │
                    │  └────┬────┘
                    │       │ broker reconciliation
                    ▼       ▼
                 ┌────────────┐
                 │ SUBMITTED  │
                 └─────┬──────┘
                       │
                 broker evidence
                  ┌────┴─────┐
                  ▼          ▼
              PARTIAL      FILLED
                  │
                  ├─────────► FILLED
                  │
                  └─────────► CANCELLED
```

### Invariants:
1. `SUBMITTING` → `UNKNOWN` upon transport exception or timeout.
2. `UNKNOWN` **NEVER** transitions back to `RESERVED` or triggers an automatic re-submission.
3. Order recovery repeats observation and projection, never broker submission.
