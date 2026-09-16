# Same-Day Shadow Replay — 2026-09-16

**Not prospective evidence.** Today's data is already known, so nothing here counts
toward the authoritative gate. Written to `data/snapback/replay_2026-09-16.db`, every
row stamped `source = SAME_DAY_SHADOW_REPLAY`, `authoritative = 0`. The clean
`prospective_freeze_1.db` was not touched — the replay script refuses to run against
any path containing `prospective_freeze`.

Classification: **REPLAY_INCOMPLETE**
Gate adapter: `INCONCLUSIVE` (`authoritative: false`)

---

## 1. Did the frozen scan generate signals today?

Yes — one, across a 200-name universe, with zero symbol failures.

The engine was genuinely running: config `enabled: true`, lookback 20,
`min_stretch_atr` 1.5, `max_rv_pct` 70, market filter `bearish`, EMA 50, target delta
0.70, DTE 40–60, hedge `index_futures`.

## 2. The signal

| Field | Value |
|---|---|
| symbol | LAURUSLABS |
| signal bar | 2026-09-11 (not today's session) |
| side | fade_up |
| direction | BEARISH |
| stretch ATR | 1.89 |
| mean target | 1879.19 |
| market regime | bearish gate satisfied |
| RV filter | passed (below the 70% cap) |
| board state | `watching` — "live hedge execution is not supported by the manual ticket path" |

The board reason is expected: the manual ticket cannot place a hedged trade. The
prospective collector, not the board, is the execution path.

## 3. Contract selection

Selection ran and produced a contract from the real chain:

| Field | Value |
|---|---|
| contract | LAURUSLABS26OCT2100PE |
| expiry | 2026-10-27 |
| DTE | 41 (inside 40–60) |
| strike | 2100.0 |
| option type | PE |
| actual delta | −0.6868 (target 0.70) |
| moneyness | ITM |
| lot size | 850 |
| exchange | NFO |
| OI / bid / ask / spread % | not recorded — the entry phase stopped before quote capture |

## 4. Entry

**INCONCLUSIVE — refused at the quote-quality gate.**

Recorded reason: `Stale or invalid futures quote: ['future_exchange_timestamp']`.

The chain was fetched, 25 put strikes were quoted, the futures contract
(NIFTY26SEPFUT) was resolved and quoted. The gate then rejected the futures quote
because its exchange timestamp sits after the presented entry instant — the quotes are
post-close snapshots. No fill was written. No synthetic price was substituted.

That is the correct outcome and the single most valuable result of this rehearsal:
**with stale data the pipeline refuses rather than fabricating an entry.**

## 5. Hedge

Futures resolution worked (contract picked, quoted, lot size read). Causal beta
computed successfully after the fix in section "Defects found". Hedge lots and
executable price were never reached, because entry was refused first.

## 6. Lifecycle

Not exercised. With no fill there is no position, so premium stop, EXIT_PENDING, EOD
MTM, sessions-held and runner transitions had nothing to act on.

`intraday_risk_processed: 0`, `eod_processed: 0` — both correct for an empty book.

## 7. Economics

Nothing to report, and nothing invented. No option P&L, no futures P&L, no brokerage,
STT, exchange charges, GST or stamp duty — because no trade exists. The evidence
summary reports these as UNKNOWN, not zero.

## 8. Evidence quality

| Metric | Value |
|---|---|
| option quote observations | 0 persisted |
| quote coverage | UNKNOWN (no persisted observations) |
| stale quotes | the futures quote was rejected as stale |
| invalid books | none reached |
| missing contracts | none — the option and the future both resolved |
| missing futures quotes | the quote was present but failed freshness |

## 9. Classification

**REPLAY_INCOMPLETE.** The pipeline ran end to end and every stage either produced
evidence or refused with a recorded reason, but the fill, hedge, lifecycle and
economics stages could not be exercised, because after the close there is no quote
that can pass an honest freshness check.

Not REPLAY_FAILED: nothing fabricated a price, and no stage raised.
Not REPLAY_VALID: no fill was produced, so the downstream path is unproven here.

---

## Defects found by this rehearsal

### P0-EVIDENCE — causal beta could never be computed (fixed)

`process_prospective_pending_entries()` imported `get_daily_bars` from
`app.services.ohlcv_store`. **That function does not exist**, so the import raised on
every entry, `causal_beta` stayed `None`, and every non-NIFTY signal was skipped with
`missing causal rolling beta` → `INCONCLUSIVE`.

The daily OHLCV store is also empty for the universe (`get_symbol_coverage` returns
`None` for both LAURUSLABS and NIFTY), so simply correcting the name would not have
helped.

Replaced with `_causal_daily_bars()`, which fetches daily candles from the broker and
**truncates them at the signal date**, so beta uses only sessions that had closed when
the signal fired. Where the signal date itself carries no beta value, the most recent
prior session is used — never a later one. Pinned by
`tests/unit/test_snapback_causal_beta.py`.

Impact had this not been caught: every stock signal would have been skipped for the
whole month. Only NIFTY, which hardcodes beta 1.0, would have traded. The evidence
report would have shown honest zeros and nobody would have known why.

### Latent — one decision row per opportunity

`decisions.opportunity_id` is unique, so a second decision for the same opportunity
raises `UNIQUE constraint failed`. Reached only when an opportunity is re-decided,
which the frozen flow does not normally do. **Backlog, not P0.**

---

## Replay devices used (disclosed)

Three, none of which changed a decision rule:

1. **Signal re-dated** to the previous session's close, so the T+1 entry phase could
   run inside one day.
2. **`decisions` cleared** before the entry attempt — `scan_once()` runs the collector
   cycle itself and had already written a "missed T+1 window" decision.
3. **Fixed clock of 09:20 IST** for the entry phase only. Prices, books and provider
   timestamps were left exactly as the broker returned them, which is precisely why
   the freshness gate still refused.

## What this rehearsal cannot answer

The 15-session horizon and the runner path cannot be exercised in one day. For those,
use the existing historical harness `study/snapback_observed_replay.py` with the same
frozen rules, kept separately labelled as replay evidence.

Fill, hedge execution and cost accounting remain unproven end to end. They will be
exercised for real tomorrow inside the 09:15–09:45 window, when quotes are live and
the freshness gate can actually pass.

## Reproduce

```bash
cd backend
STERLING_OBSERVATIONS_DB_PATH=/home/nageshmadaram/Sterling/data/snapback/replay_2026-09-16.db \
STERLING_RUNTIME_SHA=9e989dd910995deb5e77385b983e5992c58883c0 \
python3 study/snapback_shadow_replay.py
```
