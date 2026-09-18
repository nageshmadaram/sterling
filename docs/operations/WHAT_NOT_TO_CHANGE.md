# What not to change

Each line below exists because changing it would either destroy the experiment
or destroy money. They are not style preferences.

## Do not lower the 300-trade gate

Snapback must complete at least 300 real trades across at least 60 separate
sessions before anyone concludes it works. At roughly one trade every three
days, that is years away. Lowering it does not make the evidence arrive sooner;
it converts *not knowing* into *risking money as though you knew*.

## Do not change Snapback's parameters during the experiment

Not the delta, the days to expiry, the assumed volatility, the position size,
the entry rule or the exit rule. The running experiment measures one specific
frozen strategy. Changing anything mid-way means the trades before and after are
not the same experiment, and neither half has enough data on its own.

If you have a better idea, it becomes a **new** strategy with its own name and
its own evidence, run alongside. It never replaces the running one.

## Do not turn NOT_LISTED into the nearest strike

Sterling calculates which option contract to buy. Sometimes that exact contract
does not exist on the exchange, and Sterling records NOT_LISTED and skips the
trade. Making it buy the nearest one instead would be a different strategy
wearing the same name, and every trade after that point would be measuring
something other than what the trades before it measured.

## Do not turn UNKNOWN into a default

Everywhere Sterling does not know something — a missing price, a missing
timestamp, a missing contract, a missing broker confirmation — it says UNKNOWN
and stops. Replacing any of those with a guess, a last-known value, or a model
estimate makes the system confident instead of correct.

## Do not label modelled results as real

The historical Snapback results used *estimated* option prices, because real
historical option prices for those dates do not exist and cannot be bought. They
are a reason to run the experiment, not evidence that it works.

## Do not delete evidence because a trade lost

Losing trades are data. A record that keeps only the good days measures nothing.

## Do not disable safe mode to force a trade

Safe mode turns on when Sterling cannot establish its own state — an unexplained
broker position, a missing stop-loss, a failed reconciliation. Those are exactly
the moments when opening another position is most dangerous. Fix the condition,
then release it.

## Do not move release tags

`runtime-1.1` through `runtime-1.6` each point at one exact version of the code.
Moving one makes every piece of evidence recorded under it unverifiable, because
nobody can tell which code produced it.

## Do not read a good week as proof

Snapback loses on most individual trades by design, and makes its money on a
small number of large winners. A profitable month means very little, and so does
a losing one. Only the full sample answers the question.

## Do not deploy meaningful capital before the gate passes

And if it passes, start at the smallest size that is real, not at the size you
would like to be trading.

## Do not turn the SuperTrend long-option wrapper into a family-capital lane

Sterling's own 7.5-year study over real underlyings found 0 of 60 long-option
configurations net-positive out-of-sample, and none with a profit factor above
1.0. It stays research. The stronger hypothesis — the same signal expressed
delta-1 — exists as `supertrend_directional_v1`, a Track-C challenger with its
own identity, a sample of zero and live orders disabled.

## Do not change the execution vehicle of a lane that is collecting evidence

Bought options and futures are two different economic propositions for the same
signal. A vehicle change is a new `vehicle_contract_hash`, a new identity, and a
sample that restarts at zero. It is never an edit to a running lane.

## Do not pool paper, shadow and broker results into one number

They answer three different questions, and the most permissive of them is
paper. Regimes are reported separately unless a lane's predeclared promotion
policy names the pooling in advance.

## Do not reassign a fill from one broker account to another

After an account migration the strategy identity is unchanged, but the
execution-evidence segment is not. Old fills stay with the old account forever.
