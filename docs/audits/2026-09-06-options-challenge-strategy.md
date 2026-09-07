# Indian long-options auction strategy: specification and evidence

Research date: 6 September 2026. Status: **UNVALIDATED CANDIDATE; no demonstrated option profitability or 20x result.** All numerical trading thresholds below are proposed starting values, not fitted, optimized, or proven. No engine settings or live orders were changed.

**Latest user-selected objective: 2x option premium per winner.** The final section, "Selected revision: 2x option premium", governs the current proposed contract selection, sizing and exits. Earlier +2R and 100x–500x sections document the compared alternatives; they are not simultaneous exit rules.

The proposed system buys a liquid directional option after opening-auction price discovery is confirmed by continuous trading. A separate closing-auction experiment looks for residual price pressure that derivatives have not already incorporated. The reason to test this family is its observable trigger, short exposure, limited number of decisions, and falsifiable economic explanation. There is no evidence that it is the most profitable family.

## Attack the proposal before coding

| Attack | What would invalidate the idea | Response |
| --- | --- | --- |
| Auction information is public | Futures and options reprice before our order arrives | Measure future executable option P&L after actual receipt time and latency; a big auction gap alone is insufficient |
| Indications can reverse | Final matching price differs from earlier indicative price | Opening module waits for confirmed auction outcome; closing module tracks sign changes and expires signals |
| Breakouts fail in sideways markets | Costs and repeated stops exceed trend profits | One attempt per direction, strict daily cap, separate regime results |
| Retests select weak moves | Winners never pull back; losing breakouts do | Test the frozen retest rule against a simple breakout baseline on the same dates |
| Correct direction still loses | IV contraction, decay, spread, or entry price outweigh delta gains | Evaluate actual option bid/ask returns, not underlying direction accuracy |
| More indicators overfit | OI/PCR filters only improve the development sample | Require incremental holdout benefit; leave OI/PCR out of baseline entries |
| More trades amplify losses | Net edge is zero or negative after costs | No daily trade quota; high-frequency order submission is not an edge |
| Small account cannot express safe size | A single lot exceeds risk/cash/depth limits | Skip the trade; never round quantity up to one lot |
| Cash auction and option fill confused | Backtest exits the option at the stock's auction price | Every entry and exit uses that exact option's executable book |
| Attractive historical 20x month is selected | Best month or tuned parameters do not repeat | Publish all contiguous monthly windows and untouched results |

## What changed at the exchanges

All times below are IST; load a date-effective exchange calendar and actual phase messages. The clock alone cannot identify randomized auction closure.

The opening revision starts **7 September 2026**: market and limit order collection 09:00–09:05, limit-only collection thereafter until random closure 09:08–09:10, matching to 09:12, then buffer to 09:15. There is no live history of this revision as of the research date. [Dated broker announcement](https://zerodha.com/z-connect/business-updates/changes-to-the-pre-open-session-and-etf-price-bands)

Futures pre-open covers eligible current-month index/stock futures, with next-month inclusion in the last five trading days before current-month expiry. Options are excluded. Therefore option entries start in continuous trading, after 09:15. [NSE futures pre-open specifications](https://www.nseindia.com/static/products-services/equity-derivatives-pre-open-session)

CAS is live from 3 August; [NSE's archive](https://www.nseindia.com/static/reports/closing-auction-session-historical-data) lists actual sessions. Eligible cash stocks stop continuous trading at 15:15. Reference calculation runs to 15:20; market/limit collection to 15:25; limit-only collection then closes randomly during 15:28–15:30. Matching completes by 15:35. NFO trades to 15:40. The reference is cash VWAP over 15:00–15:15. Options trade separately from the cash auction. [NSE CAS specifications](https://www.nseindia.com/static/products-services/closing-auction-session)

Zerodha MIS F&O square-off currently begins at/after **15:26**, subject to RMS changes. The proposed late CAS experiment therefore requires a permitted product such as NRML, adequate funds, and a verified self-managed exit deadline. If these conditions are absent, skip the entire CAS module. [Broker square-off policy](https://support.zerodha.com/category/trading-and-markets/trading-faqs/market-sessions/articles/intraday-auto-square-off-timings)

The September 5 Kite staff update distinguishes pre-open fields from CAS: pre-open `ohlc.open`, `total_imbalance_qty`, `volume`, `buy_quantity`, and `sell_quantity`; CAS indicative volume is **not** streamed in `volume`, and a dedicated field is being developed. CAS price/imbalance fields have appeared as `indicative_close_price` / `total_imbalance`. Validate exact current payloads, timestamp semantics and signed imbalance decoding. Do not infer them from ordinary OHLCV or apply undocumented integer conversions. [Kite staff updates](https://kite.trade/forum/discussion/comment/52719/)

## Data contract and shared filters

Persist exchange event time, local receipt time, venue, phase, source version, and validity for every observation. Replay on receipt time: no record may influence a decision before it arrived. Never carry yesterday's auction value into today's session or backward-fill missing records.

At entry, underlying/futures and option books must each have a verified source age <=1 second and their event timestamps must differ by <=1 second. Dynamic auction inputs may be at most two seconds old and must lie within two seconds of the compared futures/option observations. A recent HTTP receipt does not prove that a contained market value is current. Static confirmed opening/reference prices retain their valid session identity instead of a live-quote age test. Missing source timestamps, stale dependencies or a sequence gap cancel pending setups; reconnect requires fresh state and cannot revive a prior setup.

| Input | Required contents |
| --- | --- |
| Daily instrument master | Venue, symbol, underlying, option type, strike, expiry, lot size, tick size; retain daily versions and avoid token reuse ambiguity |
| Underlying tape | Actual trades, quantities and completed one-minute bars, with auction/continuous phase labels |
| Option tape | Best bid/ask, quoted sizes, available depth, trade/book timestamps, exact contract identity |
| Opening auction | Confirmed opening price, match status, prior indications with receipt times; missing auction outcome means this module skips |
| Closing auction | Reference price, indicative price, signed equilibrium imbalance and executable indicative quantity, with verified meanings |
| Optional chain features | Expiry-specific OI, OI changes, IV/Greeks, timestamp and calculation conventions; absent features stay absent |
| Broker/order log | Submit, acknowledge, reject, cancel, partial fill, fill price/quantity and fees; unknown order outcome blocks another entry |

For index signals use actual near-month NIFTY or BANKNIFTY **futures** prices and traded volume consistently for auction anchor, range and VWAP. A cash index has no directly traded volume; do not invent index VWAP from tick counts. Expiry/roll changes require an explicit contract transition, not a spliced tradable price. For stock signals use the stock's cash trades and cash auction anchor.

Research universe: NIFTY and BANKNIFTY, evaluated separately; for stocks, select at most ten F&O-eligible names by median option premium turnover over the previous 20 completed sessions. Use contemporaneous eligibility and corporate-action history. This universe rule itself requires real option data; the inspected lake cannot supply it. An index and its stock constituents share the same account risk budget.

Option selection: buy CE for positive signals, PE for negative signals; choose a valid option with absolute delta 0.55–0.70, targeting 0.60. Among candidates use lowest executable spread, then greater depth, then smaller distance to target delta. Delta is derived from contemporaneous valid quotes and a documented model; it is not a win probability. Use the nearest index expiry with at least two remaining trading sessions. For stocks use expiry with at least five remaining trading sessions and outside broker delivery-margin restrictions; otherwise roll to the next eligible expiry or skip. Refresh actual expiry and lot size daily. [Instrument metadata](https://kite.trade/docs/connect/v3/market-quotes/), [stock physical-settlement policy](https://support.zerodha.com/category/trading-and-markets/trading-faqs/f-otrading/articles/policy-on-physical-settlement)

Greek conventions must be explicit. The 0.55–0.70 filter refers to spot-equivalent delta with respect to the option's underlying. During CAS, cash LTP may be frozen after 15:15: do not use it as a fresh pricing input. Derive a contemporaneous forward for the option's own expiry from valid market quotes and documented carry/dividend assumptions, then consistently convert forward delta to spot-equivalent delta. A weekly option and a monthly futures contract have different maturities; raw monthly futures price is not automatically the weekly forward. If the required forward/carry inputs are absent, skip. Pricing models select exposure; their theoretical prices never replace observed fills or establish profits.

Entry requires a positive uncrossed bid/ask, book age no more than one second, and spread/mid no more than 0.5% for index options or 1.0% for stocks. Proposed quantity must be at most 20% of both visible buyable depth within the entry cap and sellable depth within the stop-slippage budget. These are research filters, not guarantees of future liquidity. Missing usable timestamps or depth means skip, not assume zero cost.

## Opening module: auction-confirmed range break and retest

Use completed continuous one-minute bars only. Exclude auction prints and indicative volume from range, volume and VWAP calculations. VWAP is sum(trade price × quantity) / sum(quantity), resetting at 09:15. The first range contains trades with times in [09:15, 09:20); its high is H, low L, width W=H−L. Skip if W is zero or required bars are absent. P0 is the confirmed auction opening price of the same signal instrument.

Only consider breakouts during 09:20–10:30. RVOL is continuous cumulative traded quantity from 09:15 to the completed signal bar, divided by the median quantity over the same elapsed interval of the preceding 20 valid sessions. Exclude the current day from the denominator. Require RVOL >= 1.5; insufficient history means skip.

For a CE candidate:

1. A completed bar closes above H+0.05W, above P0 and above current VWAP; VWAP exceeds its value three completed bars earlier.
2. During the next three completed bars, accept the first retest with low in [H−0.15W, H+0.05W], close above H, close above open, and close above VWAP and P0. Cancel the setup if any earlier retest-window bar closes below H−0.15W. Expire if no valid retest occurs.
3. At the first subsequent fresh executable option quote, provided the current underlying still exceeds H, P0 and current VWAP, submit the bounded buy. The finalized retest bar must arrive within one second of its exchange close, and the entry signal expires two seconds after that close, regardless of delayed processing. Retest must complete and entry must occur by 10:30. Do not fill at the retest bar's historic close or revive an expired signal after a reconnect.
4. Underlying invalidation is the retest low minus 0.05W. Exit on its first subsequent observed touch, not a retrospectively convenient candle price.

For PE mirror every inequality: break below L−0.05W, below P0/VWAP, falling VWAP; retest high in [L−0.05W,L+0.15W], bearish close below L/P0/VWAP. Invalidation is retest high plus 0.05W. Never move invalidation farther away after entry.

At most one submitted entry attempt per direction per underlying per day. This is a hypothesis about continuation after price discovery, not a prediction that the auction must cause a trend. Missing auction data does not silently switch it into an unrelated plain breakout strategy.

## Closing module: residual auction pressure, research-disabled initially

Start with **single-stock options only**. A single stock's auction imbalance does not establish an index signal. An index CAS extension needs contemporaneous constituent weights, coverage and properly aggregated indicative values, plus separate validation; leave it disabled in version 1.

This module requires synchronized stock-auction, same-stock futures and option books, plus the permitted broker product described above. Set `cas_enabled=false` until the actual feed is recorded and the module independently passes the validation gate below. The absence of CAS indicative tradable quantity in the currently described broker field is a concrete data blocker, not a value to fabricate.

Define at each auction update:

```text
P_ref = published CAS reference cash price
F_ref = time-weighted futures mid over [15:00,15:15), frozen at 15:15
a_t = log(indicative_equilibrium_price_t / P_ref)
f_t = log(current_futures_mid_t / F_ref)
u_t = a_t - f_t
I_t = signed_equilibrium_imbalance_t / (2 * indicative_matched_quantity_t
                                      + abs(signed_equilibrium_imbalance_t))
```

I_t is usable only when the feed verifies that imbalance is buy demand minus sell supply **at the same equilibrium price**, and matched quantity is in identical units. Total book buy/sell quantities are different concepts and cannot be substituted. Zero denominator or missing quantity means skip.

u_t is a **relative-move feature, not arbitrage or fair value**: cash/futures references differ, basis/dividends can move, and information may already be in the option book. Keep actual futures bid/ask as well as mid for validation. For a new entry during 15:21–15:27 require all of the following over the entire preceding 30 seconds, with at least one fresh valid observation per two seconds:

```text
CE: a_t >= 0.0015, u_t >= 0.0010, I_t >= 0.20
PE: a_t <= -0.0015, u_t <= -0.0010, I_t <= -0.20
```

The 0.15% / 0.10% / 0.20 thresholds are untested defaults. They cannot establish that the remaining move pays the spread. The mandatory empirical gate is positive expected **future executable option return** conditional on these features after observed receipt latency and all costs, learned only on earlier data. For a given module and index/stock bucket, use a one-sided 95% lower confidence bound on mean net R, clustered by trading day; require it above zero on the untouched validation described below. Until enough real observations exist to estimate this, emit `NO_EDGE_EVIDENCE` and never submit.

Exit at the shared net stop/target, when signed u or I reaches/crosses zero, or three minutes after the first confirmed entry fill, whichever occurs first. Cancel stale pending entries when collection ends or feed validity fails. No new entry after 15:27, and cancel/flatten by 15:37 regardless of phase; confirm actual flat position. One CAS entry attempt per stock per day, sharing the whole-account three-trade limit. Do not assume the final auction outcome at the time of an earlier entry.

## Sizing, stops, targets and compounding

The following are proposed research limits. They do not turn an unvalidated strategy into a live recommendation.

Let E be current reconciled account equity after fees, E0 today's starting equity, E_high the campaign's reconciled high-water equity, and L the valid lot size. Maintain only one open strategy position/account at a time and at most three filled trades/day across both modules. No averaging down or doubling after losses.

For entry ask A, define option premium distance d=0.10A. For each integer n>=1 evaluate q=nL, prospective entry costs, adverse exit costs, spread/latency slippage reserve and available capital. Choose the largest n satisfying all:

```text
planned_R(q) = q*d + fee_estimate(entry, stop_exit, q) + exit_slippage_reserve(q)
planned_R(q) <= 0.01 * E
entry_premium(q) + entry_fees <= min(0.20 * E, available_cash_after_reservations)
planned_R(q) <= remaining_daily_loss_budget
q passes both entry and exit depth limits
```

Calculate costs for the actual integer order count: flat brokerage makes a simple fractional-lot formula inexact. If n=0, skip. The slippage reserve must come from recorded executable data; no calibration means no live-size approval. Use the entry price cap for reservation, then freeze R using confirmed filled quantity and actual entry price. Reconcile partial fills and cancel remainders; do not top up to force an intended R.

Exit when conservative liquidation P&L at available bids, after all paid/estimated exit fees, reaches **−(R − remaining exit slippage reserve)** or **+2R**, on underlying invalidation (opening module), or on the module-specific time/invalidation condition. Reserve anticipated post-trigger execution loss instead of consuming that reserve before sending the exit. Opening maximum hold is ten minutes from first fill, with hard flat by 10:40. Targets use actual net INR P&L, not a Greek estimate. Record fills to determine realized R; stops may execute below −R because of gaps, spread widening, feed failure or rejections. Full premium loss can greatly exceed planned 1% account risk.

Daily breaker: disable entries, cancel pending entries and close exposure if liquidation equity falls to 0.97E0 or lower. Campaign breaker: pause the strategy at equity <=0.90E_high. An unresolved order, disconnected feed, or position mismatch also blocks new entries and invokes reconciled recovery. Do not rely on the broker square-off timer to prove flatness.

Recompute allowed whole lots from reconciled current equity after every closed trade. That is the compounding mechanism; profits only increase size when cash, risk and depth support another lot. Do not size from unrealized targets or assume settlement proceeds are immediately available for every product.

Entry uses a tick-rounded marketable limit no more than one tick above the observed ask and within the cost budget; expire after two seconds if unfilled, reconcile, and do not chase. Exit deadlines trigger exit attempts and reconciliation; a bounded limit order cannot guarantee either a fill or a maximum loss. Implement the strategy through Sterling's existing execution lifecycle, not a second submission path.

## Maths and why Greeks/OI/PCR cannot win every time

For a local change in option value:

```text
dV ≈ delta*dS + 0.5*gamma*(dS)^2 + vega*dIV + theta*dt
net account P&L = quantity*(actual_exit_price - actual_entry_price) - all_costs
```

Use consistent units; omitted cross/higher-order terms and discrete jumps matter. Greeks quantify exposure; they do not predict dS or dIV. Delta 0.60 is not a 60% intraday win probability. IV contraction can offset a favorable directional move. Purchased gamma must be paid for through premium and decay. [OIC Greeks explanation](https://www.optionseducation.org/news/march-webinar-key-takeaways-understanding-options-greeks-and-their-role-in-pricing-and-risk)

Every open option contract has a long and a short side. Higher put OI can come from protection buying, put writing or a multi-leg hedge; aggregate OI alone cannot identify the informed participant's direction. PCR is the put/call OI ratio for a **fixed expiry and defined strike universe**, not a unique forecast. Greeks and a public chain permit many future price paths, including losing ones, so they cannot logically guarantee every trade wins. [OIC OI explanation](https://www.optionseducation.org/referencelibrary/faq/general-information)

Log expiry-specific OI/PCR as optional features. Test the baseline, then baseline plus one feature, using the same chronological splits and a multiple-comparison correction. Use only fields actually available at decision time. Keep the simpler strategy if improvement does not survive holdout, costs and latency.

For gross average win bR, gross loss R and average cost cR, arithmetic expectancy is p*b-(1-p)-c. Geometric growth additionally depends on position risk and the full return distribution: g=mean(log(1+net_account_return)). At constant fractional risk f and already-net binary +bR/−R outcomes, g=p*log(1+bf)+(1-p)*log(1-f). Positive arithmetic expectancy alone is not a guarantee of healthy compounded growth.

Required growth is (20**(1/N))-1 per period: over 20 trading sessions, **16.1586% net per day**. The count of sessions depends on the actual target month. At 1% risk with exact net +2R/−1R outcomes:

```text
ending_equity = 100000 * 1.02**wins * 0.99**losses
60 trades, all wins:  ₹328,103
100 trades, all wins: ₹724,465
200 trades:           at least 168 wins to reach ₹2,000,000
```

These are feasibility calculations, not market simulations. At 5% risk and 60 trades, 42 wins suffice under the same idealized binary payoffs, but ten consecutive losses reduce equity by 40.13%. Neither a 70% win rate nor those fills have been established. The proposed three-trades/day limit means this specification is **not a credible plan for the requested 20x month** under its +2R target. Changing the risk limit to make the target fit does not create evidence.

## Real evidence checked now

Fresh read-only audits completed successfully. [Derivative inventory recheck](2026-09-06-options-challenge-derivative-recheck.json): 12,246 instruments / 231,143,717 manifest bar rows; **zero NFO/BFO derivative historical rows** and no files under the lake's tick directory. Contract catalogues contain option/futures identifiers but not their prices. This is the inspected lake's coverage, not a claim about all commercial datasets.

[CAS recheck](2026-09-06-options-challenge-cas-recheck.json): 136,980 selected NIFTY 50/SBIN/LT minute bars; 366 instrument-session checks; 9,855 bars on nine post-CAS dates, 3–13 August. Selected file hashes, OHLC, timestamps and session checks passed. The selected history ends 13 August. Minute OHLCV does not reveal auction imbalance history or executable option fills.

**Strategy trade count, win rate, net P&L, drawdown, and ending equity: NOT MEASURED.** Do not display zeros as if a backtest had run. Neither the opening module nor the CAS module has proven profitability. There is no actual ₹1 lakh→₹20 lakh result to report.

Reproduce the evidence from the repository root:

```sh
PYTHONPATH=backend backend/.venv/bin/python backend/study/kite_cas_realdata_audit.py --output docs/audits/2026-09-06-options-challenge-cas-recheck.json
backend/.venv/bin/python backend/study/kite_derivative_coverage_audit.py --out docs/audits/2026-09-06-options-challenge-derivative-recheck.json
backend/.venv/bin/python docs/audits/2026-09-06-options-challenge-math.py
```

The last command prints arithmetic JSON only; it does not generate market data. The saved output is [compounding arithmetic](2026-09-06-options-challenge-math.json).

## What would count as actual proof

Acquire licensed historical option bid/ask/depth with retained contract metadata and synchronized underlying/auction observations, or record these forward through an authorized feed. Do not manufacture option premiums from index returns or Black–Scholes. Do not treat a present-day chain, EOD CAS file, screenshot, instrument catalogue or OHLC high-touch as a historical fill.

Freeze this specification before evaluating untouched dates. A proposed minimum screening design is 60 development sessions, 20 validation sessions and 20 untouched final sessions, with at least 100 final completed trades **per module and index/stock bucket**; extend the collection period when trade counts are insufficient. This count is a screening floor, not automatic statistical sufficiency. Since revised pre-open trading starts September 7, this history cannot already exist under those rules. Post-August-3 CAS must be evaluated separately from older closes.

Replay buys at available asks and sells at available bids after measured signal-to-order delay, respecting depth, partial fills, cancellation races and per-request broker limits. Option LTP-only bars cannot establish scalping/HFT execution. Apply the fee schedule effective on each trade date. For current NSE options, Zerodha lists brokerage ₹20/executed order, sale STT 0.15% of premium, exchange charges 0.03553%, SEBI ₹10/crore, GST 18% on applicable charges, and buy stamp duty 0.003%; reconcile all contract-note charges and applicable IPFT rather than silently omitting them. [Current fees](https://zerodha.com/charges), [April 2026 STT change](https://zerodha.com/marketintel/bulletin/445377/revision-in-stt-securities-transaction-tax-from-1st-april-2026)

Report all trades, skipped signals, attempted/rejected orders, slippage, total fees, average realized net R, profit factor, win/loss distribution, day-cluster confidence interval, worst day, maximum drawdown, time underwater and compounded equity. Separate NIFTY, BANKNIFTY, stocks, opening, CAS, expiry distance and market regime. Include consecutive 20-session windows and the full period; never select just the best month.

Reject promotion if the final day-cluster lower 95% bound on mean net R is <=0, results disappear after removing the best day, or results turn negative under double observed spread/slippage and double measured latency. Record each stress assumption; stressed outputs are sensitivity analysis, not realized fills. Check nearby parameters and point-in-time symbol holdouts without retuning the final test. A successful replay supports further forward observation; actual broker execution proof requires reconciled fills and contract notes from a separately authorized live trial.

The next necessary input is executable derivatives and auction history. Without it, the honest result of the challenge remains **unproven**, regardless of how attractive the formulas look.

## Addendum: the user's observed 100x–500x options

The user clarified that the intended edge is capturing rare enormous option moves around CAS. That changes the payoff being investigated. The earlier +2R exit would dispose of a winner long before a 100x premium increase, so that baseline is unsuitable for the specific rare-payoff objective. The following is a separate, disabled research variant, not a claim that the objective has been achieved.

First distinguish 100x (e.g. premium 1 to 100) from +100% (1 to 2). A very small initial premium can create a huge multiple from a modest absolute change. Illustrative arithmetic only: 0.05 to 5 is 100x, to 10 is 200x, to 25 is 500x. NSE lists a 0.05 NIFTY-option tick. A single-tick entry spread at that price is material. [NSE option specification](https://www.nseindia.com/static/products-services/equity-derivatives-nifty50)

I have not verified a particular contract's 100x–500x move or the claimed every-other-day frequency from primary records. A requested contract/date example would allow targeted verification. A daily high/low ratio does not establish that the low occurred first or that the move followed the CAS signal. The opportunity must be measured using a subsequent sellable bid divided by the earlier buyable ask, for the proposed quantity, after the signal's receipt time. Record settlement payouts separately from traded option exits.

Finding a winner somewhere in a large chain is different from a preselected strategy's hit rate. Pure arithmetic: buy 100 equally sized/equally priced contracts; if one returns 100x and 99 become worthless, the basket only breaks even before costs. No assumption is made that real contracts are independent or equally priced.

At 1% of current equity spent as premium per attempt, a 100x premium winner multiplies the account by 1.99; a total premium loss multiplies it by 0.99. Over 60 sequential attempts:

```text
E = 100000 * (1 + 0.01*(M-1))**W * 0.99**(60-W)
```

| Exact premium multiple M on every winner | Minimum wins W to reach 20x | Equity with those wins |
| --- | --- | --- |
| 100x | 6 | 3,609,267 INR |
| 200x | 4 | 4,552,560 INR |
| 500x | 2 | 2,003,064 INR |

**Cost-free arithmetic only**, assuming every other attempt loses all its premium, every winner realizes the entire stated multiple, immediate compounding, unlimited executable sizing and no daily/campaign breaker. These are not strategy results. Two 500x winners barely pass before costs: 0.15% sale STT alone reduces that last result to approximately 1,998,052 INR, before brokerage, spreads and other charges. The same calculations and the isolated STT sensitivity are in the arithmetic script/JSON.

The required arithmetic break-even hit rate is 1/M before costs. At 1% premium allocation, the break-even hit rates for expected log growth are higher: 1.4395% for 100x, 0.9093% for 200x, 0.5583% for 500x. A break-even hit rate is not a hit rate that reliably reaches 20x within one month. Sixty complete losses would leave 54,716 INR in the unconstrained model; the actual proposed campaign breaker would stop earlier. Do not apply the unconstrained payoff table to a strategy with breakers or early exits.

The simplest rare-payoff research variant changes the contract and exit rules, while keeping the observed auction trigger:

1. For opening trades use the completed opening confirmation above. For stock CAS use the measured residual u and equilibrium imbalance I above, with the same entry windows and fresh-data requirements. Index CAS remains blocked without a validated aggregate index-auction signal; a single constituent's imbalance is insufficient.
2. Among valid directional OTM contracts use absolute spot-equivalent delta 0.05–0.20, targeting 0.10, with the maturity conventions already specified. Select nearest target delta among contracts that pass live depth/spread filters; tie-break by smaller spread, greater depth, then strike. Select before future outcomes are known. The liquidity filters may reject most very cheap contracts; that rejection is a result, not grounds to assume a fill.
3. Budget **the entire premium plus entry/estimated exit costs within 1% of reconciled equity**. Use whole lots, skip if one lot does not fit, one position at a time, and retain daily/campaign breakers. This replaces stop-distance sizing for this module: losses are expected to include full premiums. The cost-free 1% premium table above is an optimistic simplification of this smaller all-in budget.
4. Remove the +2R profit cap and the 10%-premium stop. Exit the opening version on the defined underlying invalidation or its 10-minute/10:40 deadline. Exit CAS when u or I reverses sign or at 15:37; for this variant remove the three-minute timer to permit a longer auction reaction. Feed failure invokes reconciled protective exit. Never exit at the historical maximum unless that was the actual rule-triggered executable fill.
5. Version 1 remains an intraday, pre-expiry experiment using the earlier expiry eligibility. Analyze expiry-day strike-crossing as a separate experiment with exact settlement rules. Do not assume pre-expiry results prove an expiry effect, or promise 100x moves with expiry days excluded. Stock expiry delivery requires its own funded treatment; no stock-expiry position is silently carried to obtain a theoretical cash payoff.
6. Measure achieved multiples under these exits: fractions reaching 2x, 10x, 100x, 200x and 500x; also report all lost premiums, quote rejects, capacity and total compounded equity. Keep the module disabled without demonstrated positive net log growth and the earlier independent holdout gate. A 100x high on a contract sold earlier is not a 100x strategy win.

On an expiry day, the economically relevant hypothesis is a strike crossing: a final settlement above K gives a call intrinsic value max(S_settle−K,0), and below K gives a put max(K−S_settle,0). An indicative auction price is uncertain and is not yet that final settlement. Greeks measure the sensitivity to the crossing; the option chain may already price its probability. The testable question is whether information available in time predicts a sufficiently favorable **future executable payoff** relative to the current ask, including all failures.

SEBI announced on **3 September 2026** that it would review derivative settlement-price methodology in light of CAS. A historical CAS effect therefore needs a versioned rule regime and cannot be assumed permanent. This is a review announcement, not proof of either a rule change or a profitable strategy. [SEBI announcement](https://www.sebi.gov.in/media-and-notifications/press-releases/sep-2026/sebi-to-review-settlement-price-methodology-for-derivative-contracts-in-the-light-of-cas-rollout_104260.html)

## Selected revision: 2x option premium

The user clarified that "starting 2x" means a winning option doubles in premium, for example 100 to 200. It does not mean the whole account doubles on each winner. The original account ambition of 1 lakh to 20 lakh in a month remains unproven; the user has not replaced it with a guaranteed 2 lakh account target.

Retain the precise opening confirmation and stock-CAS residual/imbalance triggers above, including data availability gates, clocks, one-position limit, trade limits and breakers. This revision changes option selection and exits. All its parameters are initial research hypotheses, and no new market-performance evidence has been obtained.

Use a liquid directional CE/PE with absolute spot-equivalent delta 0.35–0.55, targeting 0.45. Select the closest delta among contracts passing the documented expiry, spread, depth, funds and freshness checks; break ties by narrower spread, then greater depth, then strike. This favors sensitivity to the directional move while keeping the rule deterministic. It does not establish that doubling will occur within the holding window. Use the documented expiry-matched forward/carry convention during CAS; frozen cash LTP is not a pricing update. Index CAS still requires a separately validated aggregate auction input and remains disabled without it.

Let A be actual volume-weighted entry fill price, q filled quantity and L the valid lot size. The target is a **gross premium multiple of 2**, before fees. At a candidate entry ask/cap A_cap, enumerate integer lots and reserve:

```text
premium_stop_distance = 0.20 * A_cap
planned_R(q) = q * premium_stop_distance
             + estimated_entry_and_stop_exit_fees(q)
             + remaining_exit_slippage_reserve(q)

choose the greatest integer q/L satisfying:
    planned_R(q) <= 0.01 * reconciled_equity
    planned_R(q) <= remaining_daily_loss_budget
    q*A_cap + entry_fees <= available_unreserved_cash
    q*A_cap <= 0.05 * reconciled_equity
    q passes both entry and exit depth/capacity checks
```

Fees/slippage and integer lots normally reduce the allocation below the ideal 5% of equity. If even one lot exceeds the budget, skip; do not increase the risk limit to force a trade. After a partial fill, cancel/reconcile the remainder and recompute using actual A and filled q without increasing quantity. The 1% is planned stop risk, not guaranteed maximum loss: a complete premium loss could approach 5% of equity plus fees.

For exit evaluation use depth-weighted liquidation proceeds for q, not the top bid on one lot. Trigger the earliest of:

```text
TARGET: executable liquidation value before fees >= 2 * A * q
STOP:   executable liquidation value before fees <= 0.80 * A * q
RISK:   net liquidation P&L <= -(R - remaining_exit_slippage_reserve)
SIGNAL: opening underlying invalidation, or CAS residual/imbalance sign reversal
TIME:   opening first-fill + 10 minutes or 10:40, whichever comes first;
        CAS first-fill + 3 minutes or 15:37, whichever comes first
```

Maintain the earlier entry windows (opening through 10:30, CAS 15:21–15:27), short signal TTL, data-gap handling and order reconciliation. Quote-triggered target/stop prices are not guaranteed fills. Actual net return is computed from fills and all costs; a trigger at 2A does not mean net proceeds are 2A. Time/signal exits mean that many real outcomes will be neither +100% nor −20% of premium. Retuning the time window to force more doubles would require a new untouched test.

The arithmetic behind this hypothesis is straightforward: +100% premium target / 20% premium stop = **5:1 gross reward-to-risk**, not 2R. At the idealized 5% equity premium allocation, a full winner adds 5% to the account and a normal stop loses 1%, before fees. A 1 lakh account would allocate at most about 5,000 INR to premium before costs/lot rounding, with about 1,000 INR planned premium loss.

Under exact binary outcomes with fractional reinvestment:

```text
E_N = 100000 * 1.05**W * 0.99**(N-W)
```

For 60 attempts, the arithmetic script computes the minimum winners for a 2x account milestone and whether 20x is even attainable. Even 60 straight full winners reach only about **18.68 lakh before costs**. A lower 2x premium target therefore does not by itself validate the original 20x monthly ambition. The binary arithmetic break-even hit rate is 1/6 (16.67%) before costs; this is not a measured hit rate or a safe deployment threshold. Log growth, actual payoff distributions, time exits and costs must be measured from real records.

Validation must record which happens first after each actual entry: doubled executable premium, stopped premium, signal invalidation or time exit. Replay real option books with receipt-time latency and sufficient size. Report target-hit rate, actual mean net win/loss, net expectancy, maximum drawdown, and compounded equity after whole-lot sizing. Reject the proposal if the earlier chronological/independent validation gates fail. Current strategy results remain **NOT MEASURED**.
