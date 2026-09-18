# Access continuity

The system can be handed over. Access to the things it depends on is what
usually cannot be, and none of it lives in the code. This page is the list, and
`scripts/sterlingctl continuity` is the same list with answers recorded against
it.

**No password, PIN, TOTP seed or API secret belongs in this repository, in a
backup bundle, or in any answer recorded below.** Record *where* a secret lives
and *how* the authorised operator recovers it — never the secret.

## The checklist

```bash
scripts/sterlingctl continuity
```

| Item | Question |
|---|---|
| `nominee` | Are the broker/depository nominee details current, and the transmission requirements known? |
| `authorised_operator` | Who is the authorised future operator, and which account will they use? |
| `developer_console` | Who owns the broker developer console, and how is access recovered? |
| `static_ip` | Who owns the static IP / VPS / ISP arrangement, and when does it renew? |
| `secrets` | Where are the encrypted secrets stored, and how does the authorised operator recover them? |
| `code_and_infra` | Who holds GitHub, domain, server and alert-channel access? |
| `no_secrets_in_repo` | Is it confirmed that no password, TOTP seed or PIN is in the repository or backup bundle? |

An item without an answer reads `OUTSTANDING`. A blank answer is not an answer.
The handoff is not complete while any item is outstanding.

## Static egress

Zerodha requires API order placement to come from a static IP registered
against the developer app. Market data, orderbook and positions endpoints are
reachable more broadly, which is why shadow production can run before this is
settled and real orders cannot.

Sterling never asks the internet what its own address is — that would make a
safety check depend on a third party being up. The deployment records the
observation; the application reads it:

```bash
export STERLING_STATIC_EGRESS_IP=203.0.113.7     # the address registered with the broker
export STERLING_OBSERVED_EGRESS_IP=203.0.113.7   # what the host's egress actually is
export STERLING_EGRESS_VERIFIED_AT=2026-09-18T09:00:00+05:30
```

A verification older than seven days is stale and refuses: a lease renewal or a
reboot can move the address. `scripts/sterlingctl doctor` reports this as
`static_egress`.

`ORDER_PLACEMENT_READY` requires all six of: an authorised account binding, a
current static-egress verification, a valid broker session, verified API order
permission, a `NORMAL` safety supervisor, and a lane already in `LIVE_MINIMUM`
or `LIVE_SCALED`.

## Re-verify at release time

Broker and regulator rules change. Before enabling any real order, re-read:

- Zerodha support: transacting in a deceased person's account.
- Zerodha support: static IP for Kite Connect API order placement.
- SEBI: modified nomination norms for demat accounts and mutual fund folios.

Treat all three as operational dependencies, not as strategy evidence.
