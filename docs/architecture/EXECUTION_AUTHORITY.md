# Sterling — Durable Execution Control & Universal ExecutionService Specification

## 1. Single Execution Authority Principle

All broker order submissions across Sterling—including Kite Engine, Opening Leaders, Intraday, Gamma Move, Adaptive Edge, Navigator, and manual orders—must route exclusively through the canonical `ExecutionService`.

No strategy module, background runner, or API endpoint may invoke broker REST/WebSocket APIs (`place_order`, `modify_order`, `cancel_order`) directly.

---

## 2. Durable Execution Control Architecture (Phase 1)

### State Machine

```text
                 ┌───────────────┐
                 │    RUNNING    │
                 └───────┬───────┘
                         │ operator halt / risk breach
                         ▼
                 ┌───────────────┐
                 │    HALTED     │
                 └───────┬───────┘
                         │ restart / transport uncertainty
                         ▼
              ┌─────────────────────┐
              │  RECOVERY_REQUIRED  │
              └──────────┬──────────┘
                         │ authenticated reconciliation +
                         │ explicit operator reset
                         ▼
                 ┌───────────────┐
                 │    RUNNING    │
                 └───────────────┘
```

### Invariable Rule

> **No live submission may depend solely on process memory for permission to trade.**

### Database Schema (`execution_control`)

```sql
CREATE TABLE IF NOT EXISTS execution_control (
    scope                TEXT NOT NULL DEFAULT 'global',
    uid                  TEXT NOT NULL DEFAULT 'default',
    account_id           TEXT NOT NULL DEFAULT 'default',
    state                TEXT NOT NULL DEFAULT 'RUNNING',  -- RUNNING | HALTED | RECOVERY_REQUIRED
    reason               TEXT NOT NULL DEFAULT '',
    revision             INTEGER NOT NULL DEFAULT 0,
    actor                TEXT NOT NULL DEFAULT 'system',
    created_ms           INTEGER NOT NULL,
    updated_ms           INTEGER NOT NULL,
    PRIMARY KEY (scope, uid, account_id)
);
```

### Pre-Submission Execution Control Check Pipeline

Every live order request must pass `assert_live_allowed`:

```text
Strategy Signal
      │
      ▼
RiskAuthority.evaluate(...)
      │
      ▼
ExecutionControl.assert_live_allowed(...) [Reads durable DB]
      │
      ▼
ExecutionService.submit(...)
      │
      ▼
Durable Order Journal (RESERVED)
      │
      ▼
Submission Claim (SUBMITTING)
      │
      ▼
Broker Send → ACK / UNKNOWN
```

---

## 3. Order Journal State Machine & Invariants (Phase 2)

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
1. `SUBMITTING` $\rightarrow$ `UNKNOWN` upon transport exception or HTTP timeout.
2. `UNKNOWN` **NEVER** transitions back to `RESERVED` or triggers an automatic re-submission.
3. Order recovery repeats observation and projection, never broker submission.

---

## 4. Universal ExecutionService Migration Roadmap

| Order | Component / Runner | Migration Status | Target Execution Pipeline |
|---:|---|---|---|
| **1** | **Manual Kite Orders API** | Migrated | `CanonicalExecutionService.submit(...)` |
| **2** | **Sterling Kite Engine** | Baseline Canonical | `order_journal` + `execution_lifecycle` |
| **3** | **Opening Volume Leaders** | Migration Target #1 | Replace direct `place_order` with `ExecutionService` |
| **4** | **Intraday Runner** | Migration Target #2 | Route via `ExecutionService` |
| **5** | **Gamma Move** | Migration Target #3 | Route via `ExecutionService` |
| **6** | **Adaptive Edge** | Migration Target #4 | Route via `ExecutionService` |
| **7** | **Navigator & Other Runners** | Migration Target #5 | Route via `ExecutionService` |

---

## 5. Domain Separation Framework

```text
Signal Authority:     "What opportunity exists?"
Risk Authority:       "May this account take it and with what quantity?"
Execution Authority:  "How is one broker effect safely created and recovered?"
Accounting Authority: "What did the broker actually fill and what exposure exists?"
```

These four domains are strictly decoupled across the system architecture.
