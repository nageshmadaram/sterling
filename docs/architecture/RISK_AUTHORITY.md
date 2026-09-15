# Canonical Risk Authority Specification

## 1. Single Risk Authority

`RiskEngine` is the sole decision authority for approving or denying trading signals.

Mandatory risk rules are evaluated in the following strict order:

1. **Durable Halt State**: Rejects if system is in `HALTED` or `RECOVERY_REQUIRED` state.
2. **System Health**: Rejects if execution database or broker store is unavailable.
3. **Account Identity**: Rejects if account credentials or user session are unauthenticated.
4. **Market Session**: Rejects if outside market hours or session is closed.
5. **Data Freshness**: Rejects if market data feed is stale.
6. **Daily Loss Breaker**: Rejects if per-user or global daily loss threshold is breached.
7. **Drawdown Breaker**: Rejects if peak-to-trough drawdown threshold is breached.
8. **Unresolved Orders**: Rejects if account has un-reconciled `UNKNOWN` orders.
9. **Portfolio Caps**: Rejects if capital or lot limits are exceeded.
10. **Correlation Gate**: Rejects if sector/index correlation limit is breached.
11. **Greeks Budget**: Rejects if net Delta/Gamma/Vega budget is exceeded.
12. **Microstructure**: Rejects if spread or volume liquidity criteria fail.
13. **Strategy Rules**: Rejects if strategy-specific setup criteria fail.

## 2. Invariants

- In **LIVE** mode, any infrastructure exception during risk evaluation must fail-closed (**DENY/UNKNOWN**).
- Risk rules are mandatory and cannot be bypassed via feature flags like `wire_risk_infra`.
