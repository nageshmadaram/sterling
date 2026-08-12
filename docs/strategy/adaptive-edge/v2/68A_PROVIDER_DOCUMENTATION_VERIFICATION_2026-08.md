# A68A — Provider Documentation Verification

**Status:** VERIFIED FROM PROVIDER DOCUMENTATION  
**Verification date:** 2026-08-13

## 1. TrueData — verified facts

Source: TrueData Market Data API support documentation.

Verified:

- TrueData provides real-time streaming for tick, 1-minute bars and 5-minute bars.
- TrueData supports tick + 1-minute and tick + 5-minute streaming combinations.
- TrueData provides Level 1 data with best bid/ask as part of the feed.
- Historical data availability is timeframe-dependent.
- Default historical availability documented by TrueData is last 5 trading days for tick data and last 6 months for 1/2/3/5/10/15/30/60-minute bars.
- Historical REST request limits differ between tick and minute-bar requests.
- TrueData documents Full Market Feed Replay for active Market Data API subscribers.

Source references:

- https://www.truedata.in/products/marketdataapi
- https://feedback.truedata.in/knowledge-base/article/getting-started-with-truedata-market-data-api
- https://feedback.truedata.in/knowledge-base/article/historical-real-time-data-availability-through-market-data-api
- https://feedback.truedata.in/knowledge-base/article/requestscalls-limit-via-historical-and-real-time-api

## 2. Consequence for Adaptive Edge

The raw-data boundary must not be described as "1-second bars" merely because TrueData advertises L1 data at 1-second frequency on its product page.

The provider documentation separately identifies **tick streaming** and **1-minute bar streaming**. Therefore:

```text
TrueData tick stream
    !=
TrueData 1-minute bar
```

Whether the user's actual entitlement provides a historical tick archive sufficient for the intended research period remains an external dependency.

## 3. Zerodha Kite — verified facts

Source: official Kite Connect v3 documentation.

Verified:

- Order placement returns an order ID but does not guarantee execution.
- Order history can be retrieved by order ID.
- The API exposes all orders for the day.
- The API exposes all executed trades for the day.
- An order may execute in multiple chunks, represented as separate trades.
- Trade records include trade ID, order ID, quantity, average execution price and fill timestamp.
- Order records expose filled quantity, pending quantity and average price for completed orders.
- Positions are exposed through the positions API.
- The positions API exposes `net` and `day` position views.
- Exiting a position is performed by placing the opposite BUY or SELL order; there is no separate generic position-exit API.
- Order updates can be received asynchronously through the documented postback mechanism; the documentation recommends WebSocket order updates for individual developers.

Source references:

- https://kite.trade/docs/connect/v3/orders/
- https://kite.trade/docs/connect/v3/portfolio/
- https://kite.trade/docs/connect/v3/postbacks/
- https://kite.trade/docs/connect/v3/

## 4. Boundary conclusions

The following are now documentation-supported architecture decisions:

```text
RESEARCH MARKET DATA
    -> TrueData

TRADING ORDER SUBMISSION
    -> Kite

EXECUTION/FILL EVIDENCE
    -> Kite trades/order state

POSITION TRUTH
    -> Kite position state, reconciled against confirmed trades

SQUARE-OFF
    -> opposite Kite order
```

The following remain unresolved because provider documentation alone does not establish the user's account entitlement or a strategy-specific semantic:

```text
TrueData historical tick depth for the user's account
TrueData exact tick payload available to the user's plan
TrueData historical replay coverage for the research period
TrueData provider availability timestamp semantics for every research endpoint
Adaptive Edge's final prediction resolution
Adaptive Edge's final execution simulation resolution
```

## 5. Corrections to previous wording

The earlier phrase "TrueData second-level data" is replaced by:

```text
TrueData tick stream / L1 feed
```

A derived one-second bar may be constructed only if the raw tick/event payload and aggregation semantics support it. It must not be treated as a provider-native one-second OHLC bar unless TrueData documentation explicitly establishes that semantics.

## 6. Security boundary

TrueData credentials and Kite API secrets/access tokens are runtime secrets and must never be committed to the repository or dataset manifests.
