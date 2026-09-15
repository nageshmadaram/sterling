# Snapback scalping and intraday research

Snapback now has three explicit modes. `swing` retains the daily rule. `scalp`
and `intraday` share a new reversal and premium-management engine with separately
configurable candle and holding periods. The settings page supplies a one-minute
scalp preset and a five-minute intraday preset. Changing modes does not enable
execution or change account capital.

The new modes have **no demonstrated profitability**. Existing daily research
does not validate minute entries, five-point targets, or large-quantity fills.
The archived quote-data assessment is in [SCALPING_RESEARCH.md](SCALPING_RESEARCH.md).

## Entry and contract plan

An underlying must first close outside a Bollinger band, then produce a completed
reversal candle back inside it with EMA9 confirmation. ADX14 rejects strong trends;
relative volume is optional because index volume may be unavailable. The engine
requires 30 contiguous bars in the current session, resets after gaps, ignores
forming bars, and refuses stale signals outside the entry window. With five-minute
bars, the 30-bar warmup means entries begin no earlier than 11:45 IST.

Long puts represent an upward stretch reversal. Calls represent the mirror and
require `allow_fade_down`. Open interest and quoted spread are contract-liquidity
filters. PCR, change in OI and Fibonacci are not added as predictive signals:
there is no synchronized held-out evidence here that they improve this rule.

The live plan requires a recent two-sided quote and references the ask for a buy.
It never substitutes Black–Scholes prices for missing intraday option quotes.
Contract delta selection still uses a labeled realized-volatility proxy, so it is
an approximation, not a measured live option Greek.

All target, stop, lock and trailing distances are **option premium points**, not
underlying points. A requested five-point target can be too small after costs.
The effective target is raised to clear configured net reward/risk:

```
unit_cost = max(configured_variable_cost, observed_spread) + fixed_cost / quantity
minimum_target = unit_cost + minimum_net_RR * (stop_distance + unit_cost)
```

The variable cost is an operator estimate including spread, slippage and variable
fees. The fixed cost is charged once per completed trade. These defaults are
assumptions, not a verified broker fee schedule. Lot rounding, available cash,
requested lots or premium allocation, maximum lots, and estimated stop risk all
limit quantity. Larger quantities do not turn a negative per-unit edge positive.
Risk bounds assume stop execution; gaps can exceed them.

For example, at premium ₹100, a 50-unit lot, four-point stop, one-point variable
cost and ₹40 fixed cost, estimated loss at the stop is ₹290. At minimum net RR 1,
the requested five-point target becomes 7.6 points, giving an estimated ₹290 net
target. This is arithmetic for a plan, not an expected return or a fill guarantee.

## Exits and automatic upgrades

The replay enters at the next option candle's open after a completed signal.
Existing stops are evaluated first; a gap through a stop exits at the observed
open. A candle touching both stop and target cannot be counted as a winner.

The target is a **soft continuation trigger**. A strong completed close above it
promotes the position to a runner. Otherwise a target touch exits at that candle's
observed close; the engine does not retrospectively claim a fill at the target.
Cost-aware locks and trailing stops take effect on the next bar and never loosen.
Runner promotion extends the time allowance; it never adds quantity or averages
down. Position timeouts, session square-off, cooldown, entry-count and daily-loss
limits apply in replay.

Only completed exits enter realized P&L. Truncated option tapes report unresolved
positions explicitly and do not value missing exits or claim a completed result.
Reported drawdown is realized equity drawdown; it excludes open-position marks.

## Use and validation

Select **Scalp** or **Intraday** in Snapback settings, review costs, risk, expiry
and session times, then scan to see quote-referenced plans. The board does not
arm intraday plans for broker execution: broker trailing protection is not wired
to this engine. Automatic management currently operates in the pure replay
lifecycle. Daily history is hidden for these modes.

Research endpoint: `POST /api/v1/config/snapback/replay-intraday` (authenticated).
It does not save settings or send orders. Supply:

```json
{
  "symbol": "NIFTY",
  "option_symbol": "ACTUAL_LISTED_OPTION_SYMBOL",
  "option_type": "PE",
  "lot_size": 50,
  "settings": {"trading_mode": "scalp", "scalp_timeframe_minutes": 1},
  "underlying": [{"time": 1789357500, "open": 25000, "high": 25001, "low": 24999, "close": 25000, "volume": 0}],
  "option_candles": [{"time": 1789357500, "open": 100, "high": 101, "low": 99, "close": 100, "volume": 100}]
}
```

The illustrative single candle produces no signal. Use actual contract metadata
and full synchronized historical tapes in practice. Times are bar-open epoch
seconds in the regular IST session; both tapes must have identical timestamps,
with no duplicate or missing bars inside a session. Up to 10,000 bars per tape
are accepted. Caller-supplied provenance is explicitly unverified.

The legacy simulation uses daily synthetic option valuation and refuses a
scalp-only request rather than treating its results as minute performance. The
research API requires actual option candles. A future performance claim needs
chronologically held-out contracts/sessions, costs at larger sizes, adverse
spread/slippage tests and enough independent trading days. Indicators and exit
thresholds must not be tuned on the final holdout.

The implementation version is A501.0. Earlier promotion records are stale after
this change. Daily runner replay also now respects calendar expiry and adverse
opening gaps through a trailing stop; old daily figures need rerunning before
being attributed to this version.
