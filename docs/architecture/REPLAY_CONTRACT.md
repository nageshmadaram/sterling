# Canonical Replay Contract Specification

## 1. Causal Point-in-Time Replay Rule

Every historical simulation or replay engine must strictly satisfy:

$$\text{Observed Data}(T) = \{ D \mid \text{timestamp}(D) \le T \}$$

No quote, tick, bar, IV, daily high/low, or signal metric timestamped $> T$ may be consumed when evaluating a decision at timestamp $T$.

## 2. Matcher Rules

- **Future Quotes**: Strictly forbidden. `quote.timestamp_ms > decision_ts` must result in no match.
- **Quote Age Limit**: Quotes older than `max_age_ms` (e.g. 60,000 ms) are rejected as stale.
- **Fill Price Fabrication**: Synthetic ask/bid defaults (e.g., hardcoded 100/99) are strictly forbidden for validation.
- **Unresolved Positions**: Any position still open at the end of the simulation dataset must be reported explicitly as `OPEN`/`UNRESOLVED`.
