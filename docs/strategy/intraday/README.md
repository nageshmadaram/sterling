# Intraday pack

Three strategies on one 5-minute tape. They share a universe, a session window,
a contract picker and a config object, and differ only in what makes them fire.

Nothing here has been through the walk-forward harness. Every threshold below
is a judgement call, `calibrated_fields` is empty, and `auto_execute` defaults
off and should stay off until a study exists.

| | Entry | Stop | Target |
|---|---|---|---|
| **Pivot Break** (`pivot_break`) | A strong candle closing through both EMA9 and a Fibonacci pivot | That candle's low (long) or high (short) | 1:2 banks half, the rest runs to 1:3 behind a breakeven stop |
| **MA Ribbon** (`ma_ribbon`) | The 55 EMA crossing *every* one of 8 / 13 / 21 | The slow line, offset by an ATR multiple | The opposite full cross, capped at 1:3 |
| **VWAP SuperTrend** (`vwap_supertrend`) | A SuperTrend(18, 1.46) flip whose bar closes on the matching side of session VWAP | VWAP | 20 points, trailing along VWAP after 12 |

A break up buys the CE, a break down buys the PE, in all three.

## Where the code is

| | |
|---|---|
| Rules (pure, no broker) | `backend/app/engines/intraday/` |
| The position, its two ladders, the trail | `backend/app/engines/intraday/position.py` |
| Runtime, config store, scan | `backend/app/services/intraday.py` |
| Durable position registry | `backend/app/services/intraday_positions.py` |
| Entry, protection, exits, the auto loop | `backend/app/services/intraday_runner.py` |
| Routes | `/config/intraday` (GET/PUT), `/snapshot`, `/scan`, `/arm`, `/adopt`, `/exit`, `/square-off`, `/reconcile`, `/positions` |
| Replay | `_intraday_signals_from_bars` in `backend/app/services/simulation.py` |
| Board | `frontend/src/components/kite/board/IntradayBoard.tsx` |
| Settings | `frontend/src/components/IntradaySettings.tsx` |

The simulation calls the same `evaluate_symbol` the live scan calls, so a signal
that appears in one and not the other is a data difference, never a second
implementation drifting.

## What was added to the stated rules, and why

Each of the three arrived as a few sentences. The sentences are implemented
verbatim; these are the gaps that had to be filled to make them testable, each
one a setting that can be turned off.

**"Green (possibly strong) candle"** is not a condition a computer can evaluate.
It became two: body as a share of the candle's range (`pb_min_body_pct`), and
body against ATR (`pb_min_body_atr`). Either alone is gameable — the first by a
tiny bar in a dead tape, the second by a long wick with no body.

**A break that stays broken is not a new break.** Without `pb_require_fresh_break`
every subsequent bar above the level is also "a candle closing above the pivot",
and the engine re-enters an old move at a worse price on every bar.

**The stop is the candle, so the candle can make the trade untakeable.**
`pb_max_stop_pct` rejects a setup whose own range is too wide, rather than
shrinking the stop to fit — which would make the engine claim a risk it is not
taking.

**A braided ribbon crosses constantly.** `rb_min_spread_pct` requires the four
EMAs to be meaningfully apart. Without it a "full cross" happens several times an
hour in a flat tape with no trend behind any of them.

**The ribbon strategy has no stop at all.** Its stated exit is the opposite full
cross, which can be days away. `rb_stop_atr_mult` puts a price stop below the
slow line; set it to 0 for the literal rule (the stop then sits on the line).

**A VWAP stop is whatever distance VWAP happens to be.** On a wide bar that is a
1:0.2 trade against a fixed 20-point target, and on a bar that closes right at
VWAP the next tick takes it out. `vs_min_stop_points` / `vs_max_stop_points` skip
those rather than resize them.

**Index spot has no volume.** Kite reports zero volume on NIFTY and BANKNIFTY, and
a volume-weighted average of zero weights collapses onto the typical price — so
"closed below VWAP" becomes close-vs-itself and is true or false essentially at
random. `vs_require_volume_vwap` refuses to trade that, and the row says so.
This exact failure silenced an earlier engine in this repo for its whole life
(`docs/` — ORB index blackout).

**A flip is an event, not a state.** "Whenever SuperTrend changes to red" means
the bar it changed on, not every red bar after it. `vs_require_fresh_flip` and
`vs_confirm_within_bars` make that explicit.

## The live path

Bars in, signals out is only half of it. Everything below exists because real
money is involved.

**Sizing.** `RISK_PCT` sizes so the distance to the premium stop costs a set
share of capital, and **blocks** when even one lot breaks the budget rather than
flooring to one and turning the percentage into a suggestion. `max_lots` is the
ceiling either way. After `descale_after_losses` consecutive losers the ceiling
halves.

**Two ladders.** The *spot* ladder is the thesis — a candle's low, the slow EMA,
VWAP. The *premium* ladder is the money. They are tracked separately because the
premium can round-trip while the spot rule is still perfectly intact, and that
gap is where an open drawdown builds. `side` is always `long`: every one of
these three BUYS an option, whichever way the thesis points, and reading the
thesis as the order side is the bug that once sold every PE at entry.

**Stops.** The spot stop becomes a premium stop by delta — and Kite quotes carry
no greeks, so delta is backed out of the traded premium (IV first, then
Black-Scholes) using the same machinery every other option path here uses. When
it will not solve — a premium below intrinsic, a contract that has not traded, an
expiry that has passed — the fallback is a fixed percentage, deliberately rather
than a guessed delta: a guessed delta that is too high puts the stop below zero
and the position can then never be stopped out at all. Of the two candidates the
*nearer* is taken, because the delta estimate ignores theta and vega, both of
which work against a bought option. The stop rests at the broker as a GTT
(`stop_mode`), so it survives this process dying.

**Trailing.** Two stages, on ticks, not on a UI being open:

1. at `trail_activate_r` the stop goes to what was actually paid;
2. after that it rides `premium_trail_pct` below the best premium seen.

It only ever ratchets up, and every move is pushed to the resting GTT.

**Dynamic stops and targets.** A structural stop can land two ticks from the
close on a quiet bar — that is not a stop, it is a fee — so `stop_atr_floor_mult`
pushes it out to a distance the instrument actually moves. A fixed target can sit
inside one bar's range on a fast day, so `target_atr_mult` lifts it. Both only
ever move a level **away** from entry: tightening a rule's own stop would be
trading a different strategy than the one on the board.

**The runner.** `pivot_break` banks half at 1:2 and runs the rest to 1:3 behind a
breakeven stop. A single lot cannot be halved, so it keeps the runner target and
rides the trail instead — trying to sell half a lot gets a rejection at the exact
moment it was trying to bank a win.

**Rule exits.** `check_rules` re-evaluates the thesis on the scan cadence and
closes a position whose reason for existing has gone. For the ribbon strategy
that IS the stated exit, and nothing else evaluates it.

**Gates, all in `entry_blocker`.** Engine off, outside the entry window, position
cap, already holding, daily trade cap, this engine's own daily loss limit — plus
the account-wide `assert_safe_to_trade` (kill switch, account daily loss,
duplicate order), which fails closed. The board asks the same function BEFORE a
click that the click itself would.

**Auto-execution needs both switches.** The shared engine's `auto_execute` AND
this engine's, ANDed. Neither alone is enough, and the default is off.

**Reconcile.** The broker is right and we are wrong: a position Zerodha does not
have is closed, not re-protected. Re-protecting it would rest a SELL against lots
that are not there.

## Invariants

* Every rule is evaluated on a **CLOSE**. The live scan drops a forming bar
  (`_drop_forming`); evaluating one makes a signal appear and disappear inside
  the same five minutes.
* The stop is checked **before** the target on every managed bar. A bar that
  trades through both is a loss. Assuming the good fill is the most common way a
  replay engine flatters itself.
* The stop only ever moves in the trade's favour.
* Levels are the **underlying's** points, never premium. The board says so.
* Pivots come from the **prior** period. Computed from the session in progress,
  the level moves under the trade and reads as a break that un-breaks itself.
* A zero-range prior session yields **no** pivots rather than seven identical
  levels that every tick "breaks".
* Every exit path goes through `_exit_position`, which claims `pos.exiting`
  before anything is sent. The tick loop, the session-end sweep and a manual
  square-off can all decide at the same moment, and two sells for one position
  is a naked short.
* An exit that fails re-places the protection it cancelled. An unprotected
  position is worse than a missed exit.
* There is ONE trailing implementation (`position.update_trail`). A second one
  that nothing calls is a second one that drifts.
