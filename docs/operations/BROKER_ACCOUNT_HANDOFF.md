# Broker account handoff and migration

Sterling must never depend on continuing to transact in an account that is no
longer available. Zerodha's guidance is explicit that transactions must not
continue in a deceased person's account — the nominee or legal heir claims the
holdings through transmission and trades from their own account.

So the account is a **binding**: a named, replaceable, recorded identity that
the strategy mathematics does not depend on. Handing over the original
credentials is not a migration, and it is not permitted.

## The two rules

1. **The strategy identity does not change when the account changes.** A frozen
   lane keeps its identity and its forward sample across a migration, because
   nothing about its rules moved.
2. **The execution evidence segment does change.** A fill in account A is not a
   fill in account B. Old fills keep the old segment forever; reassigning one
   would be inventing a broker fact.

## What a binding records

Metadata only. No password, PIN, OTP, TOTP seed or API secret ever enters this
record — the store refuses any field whose name looks like a credential.

| Field | Meaning |
|---|---|
| `binding_id` | Stable identifier, derived from broker + client id + date |
| `broker` | e.g. `zerodha` |
| `legal_account_holder` | The legal owner. The continuity question is a legal one |
| `client_id` | Broker client code. Identifying, not secret |
| `account_scope` | `read_only`, `shadow`, `live_minimum` — narrower than the broker's own permissions |
| `deployment_id` | Which Sterling deployment holds it |
| `static_egress_profile` | Name of the registered egress profile, not the address |
| `status` | `DECLARED` → `VERIFIED` → `ACTIVE` → `DEACTIVATED` |

## Declaring the first binding

```bash
scripts/sterlingctl bind declare zerodha "Account Holder Name" AB1234 shadow prod-1 vps-mumbai
scripts/sterlingctl bindings
```

A new binding is `DECLARED`: it cannot trade. Promote it only after the
read-only doctor and reconciliation pass against that account:

```bash
scripts/sterlingctl bind verify   zerodha-ab1234-20260918
scripts/sterlingctl bind activate zerodha-ab1234-20260918
```

Sterling refuses to have two `ACTIVE` bindings at once, and refuses to activate
one while another is still active. Silently retiring the incumbent is the one
action that could send money to the wrong account, so the operator does it
explicitly.

## Migrating to another authorised account

```bash
scripts/sterlingctl migrate    # prints the procedure, and who must do each step
```

1. `scripts/sterlingctl safe on` — block every new-exposure path.
2. Close or reconcile Sterling-managed positions in the old account, as is
   legally and operationally appropriate. **(operator / legal)**
3. `scripts/sterlingctl bind deactivate <old-binding-id>`.
4. `scripts/sterlingctl bind declare …` for the new authorised account, and
   configure its Kite app and client credentials outside the repository.
5. Verify API access, static-IP registration, order permission and margin
   endpoints with the broker. **(operator)**
6. `scripts/sterlingctl doctor` and `scripts/sterlingctl reconcile`.
7. Run market-data and shadow-only acceptance on the new account.
8. `scripts/sterlingctl bind activate <new-binding-id>` — this starts a new
   execution-account evidence segment. The strategy identity is unchanged.
9. Do **not** enable `LIVE_MINIMUM` until shadow, reconciliation and protection
   drills pass on the new account. **(operator)**

A deactivated binding can never be reactivated. Create a new one instead: the
history of which account did what has to stay unambiguous.

## Outside the code

Nominee details, transmission requirements and who the authorised future
operator is are recorded in [ACCESS_CONTINUITY.md](ACCESS_CONTINUITY.md) and in
the continuity checklist (`scripts/sterlingctl continuity`).
