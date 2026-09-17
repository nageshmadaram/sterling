# Sterling — Emergency Card

**Print this. Keep it where you can read it without a working Sterling.**

---

## Stop everything, right now

```bash
./sterlingctl safe on "emergency"
```

New trades stop immediately. Exits, protection repair and reconciliation keep
working — that is deliberate, because a blocked exit is more dangerous than
whatever made you reach for this card.

Then open the broker app directly and look at what it actually holds.

---

## If you see ...

| You see | Do this first | Do **NOT** |
|---|---|---|
| `SAFE_MODE` | Leave it on. Read the trigger. Protect open positions. | Turn it off to get a trade in. |
| `BROKER_ERROR` | Open the broker directly. Compare positions and orders. Run `./sterlingctl reconcile`. | Resubmit an order whose state you do not know. |
| `DATA_ERROR` | Block new entries. Check the market session and the feed. | Trade on stale prices. |
| `EVIDENCE_ERROR` | Block new entries. Protect positions. Repair or restore storage. | Keep going while records are not durable. |
| `RECOVERY_REQUIRED` | Run `./sterlingctl reconcile`. Confirm zero unknown exposure. | Start new activity before it is clean. |
| A real fill with no protection | Repair protection now, or exit the position per policy. | Assume protection exists. |
| Exposure nobody can explain | The broker is the source of truth. SAFE_MODE, then reconcile. | Guess the position state. |
| `doctor` exits **2** | Treat it as a failure. A check that could not run is not a pass. | Read it as "probably fine". |

---

## The order of operations, always

1. **Block new risk.** `./sterlingctl safe on "<reason>"`
2. **Find out what is real.** The broker app, not Sterling's screen.
3. **Reconcile.** `./sterlingctl reconcile`
4. **Protect or exit** anything real and unprotected.
5. **Only then** think about why.

Diagnosing before blocking is how a small problem becomes a position.

---

## Exiting by hand

If Sterling cannot exit a real position and you must:

1. Confirm the exact instrument and quantity **in the broker app**.
2. Exit there, directly.
3. Write down what you did and when.
4. Tell the technical contact in [ACCESS_CONTINUITY.md](ACCESS_CONTINUITY.md).
5. Leave SAFE_MODE on until someone has reconciled the records.

A hand exit that nobody records becomes an unexplained mismatch tomorrow.

---

## What never justifies overriding a block

- A trade that looks like it is about to be missed.
- A losing open position you want to "fix".
- A run of winners.
- Being in a hurry.

Sterling is recording evidence, not chasing income. A missed trade costs
nothing. An override during an unknown state can cost real money.
