# Sterling — Durable Execution Control & Universal ExecutionService Specification

## 1. Single Execution Authority Principle

All broker order submissions across Sterling—including Kite Engine, Opening Leaders, Intraday, Gamma Move, Adaptive Edge, Navigator, and manual orders—must route exclusively through the canonical `ExecutionService`.

No strategy module, background runner, or API endpoint may invoke broker REST/WebSocket APIs (`place_order`, `modify_order`, `cancel_order`) directly.

---

## 2. Durable Execution Control Architecture (Phase 1)

### 2D Orthogonal State Model

Execution control tracks two independent, orthogonal dimensions to eliminate state collisions:

1. **`operator_state`**: `RUNNING` | `HALTED`
   - Controls operator/risk permission to initiate exposure-increasing execution.
   - Set by manual operator commands or safety circuit breakers.

2. **`recovery_state`**: `CLEAN` | `RECOVERY_REQUIRED`
   - Controls technical reconciliation state following transport uncertainty or un-projected broker fills.
   - Updated automatically by execution recovery workflows and cleared via authenticated CAS reconciliation.

```text
               OPERATOR DIMENSION                       RECOVERY DIMENSION
           ┌────────────────────────┐                ┌───────────────────────┐
           │        RUNNING         │                │         CLEAN         │
           └───────────┬────────────┘                └───────────┬───────────┘
                       │ operator halt                           │ transport uncertainty
                       ▼                                         ▼
           ┌────────────────────────┐                ┌───────────────────────┐
           │         HALTED         │                │   RECOVERY_REQUIRED   │
           └────────────────────────┘                └───────────────────────┘
```

### Invariable Rule

> **No live submission may depend solely on process memory for permission to trade.**

### Database Schema (`execution_control`)

```sql
CREATE TABLE IF NOT EXISTS execution_control (
    scope                TEXT NOT NULL DEFAULT 'global',
    uid                  TEXT NOT NULL DEFAULT 'default',
    account_id           TEXT NOT NULL DEFAULT 'default',
    operator_state       TEXT NOT NULL DEFAULT 'RUNNING',  -- RUNNING | HALTED
    recovery_state       TEXT NOT NULL DEFAULT 'CLEAN',    -- CLEAN | RECOVERY_REQUIRED
    reason_code          TEXT NOT NULL DEFAULT '',
    reason               TEXT NOT NULL DEFAULT '',
    revision             INTEGER NOT NULL DEFAULT 1,
    actor                TEXT NOT NULL DEFAULT 'system',
    last_reconciled_ms   INTEGER NOT NULL DEFAULT 0,
    created_ms           INTEGER NOT NULL DEFAULT 0,
    updated_ms           INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (scope, uid, account_id)
);
```

### Pre-Submission Execution Control Check Pipeline

Every live order request must pass `assert_safe_to_trade`:

```text
Strategy Signal
      │
      ▼
RiskAuthority.evaluate(...) [Requires explicit risk_approved=True]
      │
      ▼
ExecutionControl.assert_safe_to_trade(...) [Reads durable DB 2D state + unresolved journal]
      │
      ▼
ExecutionService.submit_order(...)
      │
      ▼
Durable Order Journal (RESERVED)
      │
      ▼
Submission Claim (SUBMITTING)
      │
      ▼
Broker Send → ACK / UNKNOWN (RECOVERY_REQUIRED on transport error)
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
| **1** | **Sterling Canonical Execution Engine** | Implemented (Slice 1 & 2) | `order_journal` + `CanonicalExecutionService` |
| **2** | **Manual Kite Orders API** | Open (Target #1) | `CanonicalExecutionService.submit_order(...)` |
| **3** | **Opening Volume Leaders** | Open (Target #2) | Replace direct `place_order` with `ExecutionService` |
| **4** | **Intraday Runner** | Open (Target #3) | Route via `ExecutionService` |
| **5** | **Gamma Move** | Open (Target #4) | Route via `ExecutionService` |
| **6** | **Adaptive Edge** | Open (Target #5) | Route via `ExecutionService` |
| **7** | **Navigator & Other Runners** | Open (Target #6) | Route via `ExecutionService` |

---

## 5. Domain Separation Framework

```text
Signal Authority:     "What opportunity exists?"
Risk Authority:       "May this account take it and with what quantity?"
Execution Authority:  "How is one broker effect safely created and recovered?"
Accounting Authority: "What did the broker actually fill and what exposure exists?"
```

These four domains are strictly decoupled across the system architecture.
