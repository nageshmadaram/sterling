# Start here

This file assumes you have never written software. Everything below is a command
you type, or something you read on a screen.

Sterling is a research system that watches the Indian options market, records
what it sees, and decides whether a trading idea called **Snapback** actually
works. It is not currently allowed to trade meaningful money, and it should not
be given that permission until the evidence says so. See
[WHAT_NOT_TO_CHANGE.md](WHAT_NOT_TO_CHANGE.md).

## The one command that matters

```bash
cd ~/Sterling && ./scripts/sterling-status
```

Read it top to bottom. It tells you whether Sterling is healthy, whether it is
allowed to open new positions, and if not, why.

## What the words mean

**Safe mode ON** — Sterling will not open any new position. It will still close
positions it already has, repair their protection, and talk to the broker. Safe
mode is a brake on new risk, never on getting out of existing risk.

**BLOCKED** — Sterling is refusing to do something. This is normal and usually
correct. The status screen prints the reason on the next line.

**PASS / FAIL / INCONCLUSIVE** — the verdict on whether Snapback works.

- *PASS* means the evidence says the strategy made money after real costs. This
  has not happened and may never happen.
- *FAIL* means the evidence says it does not work. That is a useful, honest
  answer, and the correct response is to stop, not to adjust it until it passes.
- *INCONCLUSIVE* means there is not enough evidence yet. **This is the current
  state and will remain so for a long time** — it needs at least 300 completed
  real trades across at least 60 separate trading days, and Snapback trades
  roughly once every three days. That is years, not weeks.

## Starting Sterling

```bash
cd ~/Sterling && ./scripts/sterling-status
```

If the backend says NOT RUNNING, start it, then check the status again. If the
status screen says `Blocked by` something, fix that thing first — do not try to
work around it.

## Logging in to the broker

Kite (Zerodha) logs you out every morning. Sterling cannot log in for you, on
purpose: no password or token is stored anywhere in this system. See
[BROKER_REAUTH.md](BROKER_REAUTH.md).

## The drive

Sterling's recorded market data lives on a USB drive. If the status screen says
the lake is MISSING, plug it in. Data that is not recorded on a given day cannot
ever be recovered later — the market data vendor does not sell the past for
expired option contracts. See [BACKUP_AND_RESTORE.md](BACKUP_AND_RESTORE.md).

## If something looks wrong

Turn off new trading first, ask questions second:

```bash
./scripts/sterling-safe-mode on "describe what looked wrong"
```

That is always safe. It never prevents closing a position. Then read
[INCIDENT_RESPONSE.md](INCIDENT_RESPONSE.md).

## If you have taken over from someone else

Sterling is designed to be handed over. Two things have to be settled, and
neither is a code change:

- **Which broker account it trades through.** That is a recorded binding, not a
  shared password. See [BROKER_ACCOUNT_HANDOFF.md](BROKER_ACCOUNT_HANDOFF.md).
- **Who holds access to everything it depends on** — the developer console, the
  static IP, the encrypted secrets, the alert channels. See
  [ACCESS_CONTINUITY.md](ACCESS_CONTINUITY.md), and run:

```bash
./scripts/sterlingctl continuity
```

To see why no strategy is allowed to spend real money yet:

```bash
./scripts/sterlingctl permission
```

## What Sterling is not

It is not an income machine, and nobody should plan around money from it. It is
an instrument for finding out whether a specific idea has an edge, built so that
the answer will be honest whichever way it comes out, and so that the account
survives while the question is still open.
