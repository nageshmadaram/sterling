# Safe mode

Safe mode is one switch that means **take no new risk**.

## What it does and does not do

Blocks:
- opening any new position

Never blocks:
- closing a position
- repairing a stop-loss
- adjusting or repairing a hedge
- reconciling with the broker
- recording market data
- anything you need in order to inspect the system

That distinction is deliberate. A safety feature that prevented you from getting
out of a position would be more dangerous than whatever triggered it.

## Checking it

```bash
cd ~/Sterling && ./scripts/sterling-safe-mode status
```

## Turning it on

```bash
./scripts/sterling-safe-mode on "unexplained position in the Kite app"
```

Always safe to do. If you are unsure whether something is wrong, turn it on.
Nothing is lost by sitting out a day; the strategy trades roughly once every
three days anyway.

## Turning it off

```bash
./scripts/sterling-safe-mode off --ack
```

`--ack` is required, and it means: *I looked at the problem and it is resolved.*
Sterling refuses without it. If safe mode was triggered automatically by a real
condition, Sterling will also refuse until you acknowledge it explicitly — a
script cannot clear it in order to keep trading.

## What turns it on automatically

| Condition | What it means |
|---|---|
| `UNKNOWN_BROKER_EXPOSURE` | The broker shows a position Sterling does not know about |
| `POSITION_MISMATCH` | Sterling and the broker disagree about what is held |
| `RECONCILIATION_FAILURE` | The two could not be reconciled at all |
| `PROTECTION_MISSING` | A position has no stop-loss |
| `PROTECTION_FAILURE` | A stop-loss was requested and did not become active |
| `EVIDENCE_WRITER_FAILURE` | Sterling cannot record what it is doing |
| `MARKET_DATA_INTEGRITY` | Market data is missing, stale, or nonsensical |
| `RUNTIME_IDENTITY_MISMATCH` | The running code is not the released version |
| `UNRESOLVED_ORDER` | An order's outcome is unknown |
| `EXECUTION_DISCREPANCY` | Fills differ from expectations by an abnormal amount |

## It survives restarts

The state is a small file on disk. Restarting Sterling, rebooting the machine,
or reinstalling will not clear it. This is on purpose: restarting is the first
thing people try when something looks wrong, and a safety switch that a restart
silently cancelled would be worse than no switch at all.

If you ever need to read it without starting Sterling:

```bash
cat ~/Sterling/data/safe_mode.json
```
