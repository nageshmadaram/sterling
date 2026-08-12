# Adaptive Edge V2 — TrueData Endpoint Field Mapping and Response Contract

**Artifact:** A73  
**Status:** SPECIFICATION / PROVIDER-CONTRACT RECONCILIATION  
**Version:** 2.1.0

## 1. Purpose

A73 maps the TrueData endpoints actually present in the repository's `truedata-docs` Postman collection to their canonical roles without inventing undocumented response semantics.

The repository README identifies the TrueData Market Data API documentation as the reference documentation for the REST API. The repository also contains the exported REST Postman collection used as concrete request evidence.

## 2. Source evidence hierarchy

For this artifact:

```text
1. TrueData documentation stored in truedata-docs
2. TrueData Postman request definitions stored in truedata-docs
3. Captured provider responses, when available
4. External TrueData documentation only for verification
```

A request definition proves endpoint/request shape. It does not, by itself, prove the complete response-field semantics.

Therefore any field not established by documentation or a captured response remains UNKNOWN.

## 3. Historical tick endpoint — getticks

Repository request evidence:

```text
GET https://history.truedata.in/getticks

Parameters observed:
    symbol
    bidask
    from
    to
    response
    comp
```

The stored example uses:

```text
symbol=RELIANCE
bidask=1
from=221014T09:00:00
to=221014T18:30:00
response=csv
comp=false
```

Canonical role:

```text
historical tick acquisition for a specified symbol/time range
```

Known from request evidence:

```text
symbol       = requested instrument identifier
from/to      = requested historical time range
bidask       = provider request option; exact response semantics require documentation
response     = representation selector; csv demonstrated
comp         = provider request option; exact semantics require documentation
```

Unknown until provider response/schema evidence is captured:

```text
exact tick columns
trade/quote event semantics
LTP field name
LTQ field name
TTQ field name
OI field name
bid/ask field names
sequence field
provider timestamp precision
```

## 4. Historical all-symbol tick endpoint — getAllTicks

Repository request evidence:

```text
GET https://history.truedata.in/getAllTicks

Parameters observed:
    segment
    timestamp
    response
    interval
```

Stored example:

```text
segment=eq
timestamp=240328T11:10:05
response=csv
interval=1sec
```

Canonical role:

```text
historical segment-wide tick/second-resolution acquisition endpoint
```

Important:

The request's `interval=1sec` proves that the repository's concrete request targets a 1-second interval representation. It does NOT prove that the response is identical to raw tick events.

Therefore:

```text
getAllTicks(interval=1sec)
    != assumed raw tick stream
```

until the provider response schema establishes the semantics.

## 5. Historical bars — getbars

Repository request evidence:

```text
GET https://history.truedata.in/getbars
```

Parameters observed:

```text
symbol
from
to
response
interval
delivery (disabled in stored example)
```

The stored example demonstrates `interval=eod`.

Canonical role:

```text
historical symbol-specific bar acquisition
```

The exact set of accepted intervals is not frozen by this artifact merely because one example contains `eod`.

## 6. All-symbol bars — getAllBars

Repository request evidence:

```text
GET https://history.truedata.in/getAllBars
```

Parameters observed:

```text
segment
timestamp
delivery
response
lotsize
```

Stored example:

```text
segment=fo
timestamp=240212T09:15
delivery=true
response=csv
lotsize=true
```

Canonical role:

```text
segment-wide historical bar acquisition
```

`lotsize=true` is a provider request option. Its economic interpretation must come from the provider documentation; the adapter must not silently use it as a contract multiplier unless that mapping is documented.

## 7. Minute/all-symbol endpoint — getAllBarsforMin

Repository request evidence:

```text
GET https://history.truedata.in/getAllBarsforMin
```

Parameters observed:

```text
segment
timestamp
delivery
response
lotsize
```

Stored example:

```text
segment=fo
timestamp=230828T11:10
delivery=true
response=csv
lotsize=true
```

Canonical role:

```text
segment-wide minute-bar acquisition
```

The exact interval represented by the endpoint is provider-defined and must not be generalized beyond the documented endpoint semantics.

## 8. Last N bars — getlastnbars

Repository request evidence:

```text
GET https://history.truedata.in/getlastnbars
```

Stored example:

```text
symbol=SBIN-I
interval=1min
response=csv
bidask=0
comp=false
nbars=150
```

Canonical role:

```text
recent-bar retrieval for a specified symbol
```

This endpoint is useful for live/recent-state initialization but is not automatically equivalent to historical archival acquisition.

## 9. Last N ticks — getlastnticks

Repository request evidence:

```text
GET https://history.truedata.in/getlastnticks
```

Stored example:

```text
symbol=ACC
bidask=1
response=csv
nticks=2000
interval=tick
```

Canonical role:

```text
recent tick retrieval for a specified symbol
```

The stored collection also uses the same endpoint for an LTP-style request with:

```text
symbol=NIFTY-I
bidask=1
nticks=1
interval=tick
```

Therefore `getlastnticks` must not be implemented as a special LTP endpoint. It is a recent-tick retrieval endpoint whose single-row use case may provide the latest observation.

## 10. Instrument/reference endpoints

The collection contains endpoints including:

```text
gettradedsymbols
getSymbolExpiryList
getSymbolOptionChain
getsymbolchangehistory
getindexcomponents
getbhavcopy
getbhavcopystatus
getcorpaction
getcorpactionrange
```

These are not market-event streams. They belong to instrument/reference metadata or corporate-action boundaries.

They may be consumed by research dataset construction only through explicit contracts.

## 11. Canonical mapping table

| Provider endpoint | Canonical role | Source resolution | Safe to assume raw tick? |
|---|---|---|---|
| getticks | historical symbol tick retrieval | tick | YES as endpoint class; exact payload semantics UNKNOWN |
| getAllTicks | historical segment-wide tick/second retrieval | provider-defined; 1sec demonstrated | NO |
| getbars | historical symbol bars | provider-defined | NO |
| getAllBars | historical segment-wide bars | provider-defined | NO |
| getAllBarsforMin | historical minute bars | minute | NO |
| getlastnbars | recent symbol bars | requested interval | NO |
| getlastnticks | recent symbol ticks | tick | YES as endpoint class; exact payload semantics UNKNOWN |
| gettradedsymbols | traded-symbol reference | n/a | NO |
| getSymbolExpiryList | expiry metadata | n/a | NO |
| getSymbolOptionChain | option-chain reference | n/a | NO |
| getsymbolchangehistory | symbol identity history | n/a | NO |
| getindexcomponents | index membership/reference | n/a | NO |
| getbhavcopy | end-of-day reference | daily | NO |
| getbhavcopystatus | end-of-day status | daily | NO |
| getcorpaction | corporate actions | event/reference | NO |
| getcorpactionrange | corporate actions over range | event/reference | NO |

## 12. Canonical response normalization

The adapter must preserve the provider response before normalization.

```text
provider_response
    -> raw artifact
    -> schema validation
    -> endpoint-specific parser
    -> canonical observation
```

The parser must be endpoint-specific.

A single generic CSV parser must not assume that all TrueData endpoints have identical columns.

## 13. Timestamp contract

The request examples establish provider timestamp formats such as:

```text
YYMMDDTHH:MM:SS
YYMMDDTHH:MM
```

This demonstrates request formatting only.

The canonical event timestamp semantics, timezone, precision, and availability semantics remain UNKNOWN until the response documentation establishes them.

The adapter must preserve the original provider timestamp representation alongside any parsed timestamp.

## 14. Response-format contract

The repository examples use:

```text
response=csv
```

Therefore CSV is a demonstrated response representation.

The adapter must not assume CSV is the only supported provider response format unless the provider contract freezes that choice.

The raw response must be retained sufficiently to reproduce parsing and validation.

## 15. Delivery and lot-size options

The endpoints demonstrate:

```text
delivery=true
lotsize=true
```

These options must remain explicit request configuration.

They cannot silently become universal accounting semantics.

In particular:

```text
lotsize
    !=
canonical contract multiplier
```

unless provider documentation explicitly establishes that relationship for the endpoint and instrument class.

## 16. Data authority rules

The following are frozen:

```text
TrueData responses -> research market data
Kite responses     -> trading/execution state
```

Kite data cannot repair a missing TrueData observation.

TrueData data cannot confirm a Kite execution.

## 17. Failure conditions

Endpoint acquisition must fail closed when:

1. authentication fails;
2. provider rejects entitlement;
3. response schema is unknown;
4. required provider fields are absent;
5. timestamp cannot be parsed without assumption;
6. instrument identity cannot be resolved;
7. response is truncated or malformed;
8. endpoint semantics conflict with the registered contract;
9. duplicate/reordered observations cannot be reconciled under provider semantics.

No fallback to Kite is permitted.

## 18. Security finding

The repository's exported Postman collection contains bearer-token values embedded in request definitions.

Those values must be treated as compromised credentials if they are real/current.

The canonical implementation must use environment/secret storage and must not persist provider tokens in source control.

This is an operational security blocker independent of market-data semantics.

## 19. Frozen architecture

```text
Endpoint-specific provider adapter
        |
        v
raw response artifact
        |
        v
endpoint-specific schema validation
        |
        v
canonical provider observation
        |
        v
research dataset
```

No endpoint-specific parser may perform strategy calculations.

## 20. Learned/configurable values

None are learned by A73.

Configuration that requires validation:

```text
endpoint selection
request interval
historical chunk size
response representation
bid/ask option
delivery option
lot-size option
```

## 21. UNKNOWN / TODO

```text
Exact response columns for each endpoint
Exact field units
Exact tick sequence semantics
Exact LTP/LTQ/TTQ/OI semantics
Exact bid/ask semantics
Exact timestamp timezone
Exact availability semantics
Exact historical retention by endpoint
Exact entitlement requirements
Exact request limits
Exact correction/revision semantics
Exact interval vocabulary
```

## 22. Adversarial review

### Attack: one parser for all CSV responses

Rejected. Endpoint families can have different schemas and optional fields.

### Attack: interpret `1sec` as raw ticks

Rejected. The request proves a one-second interval request, not semantic equivalence with tick events.

### Attack: use `lotsize=true` as multiplier

Rejected until provider documentation establishes the mapping.

### Attack: use `getlastnticks` as the historical dataset

Rejected. It is a recent-N retrieval mechanism and cannot silently replace range-based historical acquisition.

### Attack: fall back to Kite

Rejected by the provider authority contract.

### Attack: parse request timestamps as UTC

Rejected. The stored request format does not establish timezone semantics.

## ARCHITECTURE STATUS

Endpoint families and their canonical roles are frozen from repository evidence. The adapter must remain endpoint-specific and provider-authoritative.

## UNRESOLVED

Response-field semantics and provider-specific operational limits listed in Section 21.

## BLOCKERS

Exact response schemas and semantics must be verified from the TrueData documentation/captured responses before implementation of endpoint parsers.

## NEXT ARTIFACT

A74 — TrueData Response-Schema Verification and Fixture Contract.
