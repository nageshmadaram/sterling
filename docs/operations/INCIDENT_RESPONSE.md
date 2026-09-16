# When something goes wrong

## First, always

```bash
cd ~/Sterling && ./scripts/sterling-safe-mode on "describe what you saw"
```

This stops new positions. It does not stop closing existing ones. There is no
situation in which turning it on makes things worse, and doing it before you
understand the problem is correct.

Then read the status:

```bash
./scripts/sterling-status
```

---

## Specific situations

### The broker shows a position Sterling does not know about

**Do not close it manually without recording what you saw first.** Note the
symbol, quantity and time. Then check whether Sterling has an unresolved order.
Sterling will already be in safe mode. The position is real money and takes
priority over tidy records — if it is large or unhedged, closing it is more
important than any of this.

### Sterling has a position the broker does not show

Usually an order that never actually reached the exchange. Sterling should be in
safe mode. Do not assume the position exists and do not assume it does not —
check the Kite order book directly, which is the authority.

### A position has no stop-loss

Sterling blocks new entries automatically. Getting protection back on the
existing position is the priority. If it cannot be established, closing the
position is a reasonable response — an unprotected option position is an
unbounded risk that nobody chose.

### The market data feed disconnected

Sterling will not open new positions without fresh data, automatically. Existing
positions continue to be managed. Reconnecting is the fix; there is nothing
urgent to do beyond that.

### The drive is unplugged or full

Sterling cannot record. New entries block; existing positions keep being
managed. Plug it back in, or free space. Any opportunity that occurred while it
was unavailable is lost evidence — note the time, since it affects the
denominator in the evidence report.

### An order's outcome is unknown

Never resubmit. A duplicate order is a second real position. Check the Kite
order book, establish what actually happened, and let Sterling reconcile.

### The running code is not the released version

Stop. Sterling records which version produced each piece of evidence, and
evidence from an unreleased build cannot be mixed with the rest. Get back to the
released version before trading again.

---

## What to preserve, always

Whatever happened, do not delete logs, evidence files, or the safe-mode file
while investigating. They are how anyone later works out what occurred. A bad
day with complete records is recoverable; a bad day with deleted records is not.
