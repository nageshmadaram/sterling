# Sterling — Family Runbook

This is everything you need to run Sterling day to day. You do not need to know
Python, Git, Docker, SQLite or options trading to follow it.

No passwords, broker credentials or bot tokens appear in this file. They are kept
where section 12 says.

---

## 1. What Sterling does

Sterling watches a fixed list of Indian stocks and indices once a day, after the
market closes. When its one strategy — **Snapback 1.0.5** — sees its setup, Sterling
records the opportunity and, the next morning, records what buying that option would
actually have cost at real market prices. It also records a hedge against the index.

The strategy is frozen. Nobody should change how it decides anything.

## 2. What PAPER means

**PAPER is the normal state today.** Sterling records what would have happened using
real prices, but sends no orders to the broker. No money can be lost or made.

Sterling is collecting evidence about whether the strategy actually works. That takes
months, not days.

## 3. What LIVE means

LIVE means real orders with real money.

**Sterling will refuse to trade live until it has proved itself.** It needs at least
60 separate trading days and 300 completed trades before the decision can even be
made, and the result must be positive after costs. Until then the screen shows
`EVIDENCE: INCONCLUSIVE` and live trading is blocked.

Nobody can switch this on with a button. That is deliberate.

## 4. How to log into the broker

Sterling needs a Zerodha Kite login each trading day, before **09:15 IST**.

1. Open Sterling in the browser.
2. Go to the KITE tab.
3. Click **Login** and complete the Zerodha login in the window that opens.
4. Return to the Family Operations screen and check that **BROKER** says
   `CONNECTED`.

The login expires daily. This is Zerodha's rule, not a fault.

## 5. How to tell Sterling is healthy

Open the **Family Operations** screen. Read the top line.

| What it says | What it means | What to do |
|---|---|---|
| `HEALTHY` | Everything is working | Nothing |
| `DEGRADED` | Something is missing, usually the broker login | See sections 6 and 7 |
| `HALTED` | Sterling has stopped itself on purpose | See section 8 |
| `RECOVERY_REQUIRED` | Sterling cannot trust its own records | Contact the person in section 12 |

`DEGRADED` while the market is closed, with only "broker login required", is normal
in the evening.

## 6. "Broker login required"

The Zerodha session expired, which happens daily.

Do section 4. If the login fails repeatedly, Zerodha may be down — check whether you
can log in at kite.zerodha.com directly. If you cannot, it is not Sterling.

## 7. "Market data stale"

Sterling is connected but is not receiving fresh prices.

1. Check your internet connection.
2. Do the broker login again (section 4).
3. If it persists for more than 15 minutes during market hours, contact section 12.

**Sterling will not act on stale prices.** It stops instead of guessing. Seeing this
message means the protection is working.

## 8. "HALTED"

Sterling stopped itself. It does this when something could make its records wrong —
for example its database is unreachable, or its frozen strategy settings do not match
what was approved.

Do not try to restart it repeatedly. Contact the person in section 12 and tell them
the exact words on the screen.

Existing positions are still watched and can still be exited while halted.

## 9. STOP ALL NEW TRADES

There is one red button on the Family Operations screen.

Press it if anything looks wrong and you want Sterling to stop opening anything new.

What it does:
- Stops all new entries immediately.

What it does **not** do:
- It does not delete anything.
- It does not abandon positions Sterling already holds.
- It does not stop Sterling from exiting or protecting those positions.

It is always safe to press. Releasing it later is a deliberate action.

## 10. Never increase quantity after losses

If Sterling ever goes live, it trades **one lot**.

Do not increase the size because it lost money and you want to recover it. Do not
increase the size because it won and it feels like it is working. Both are how people
lose far more than they planned.

Quantity increases require new evidence, not confidence.

## 11. Never bypass the evidence gate

If the screen says `EVIDENCE: INCONCLUSIVE` or `FAILED`, Sterling must not trade live.

There is no toggle that overrides this, and one should never be added. A system that
has not proved it makes money is not made safer by someone believing in it.

## 12. Who to contact for technical help

Primary contact: **Nagesh Madaram**

Tell them:
- the exact words on the Family Operations screen,
- the time it started,
- whether the broker shows CONNECTED.

Credentials and recovery information are **not** in this document or anywhere in the
code repository. They are kept in the household password manager. Ask the contact
above for access.

## 13. Backup location

Sterling backs up its evidence automatically every day after the market closes.

```
~/Sterling/backups/snapback/YYYY-MM-DD/
```

Each folder holds the database, a checksum proving it is undamaged, and a record of
which frozen version produced it. At least 14 days are kept, plus weekly copies.

**One copy should live outside this laptop** — an external drive or cloud folder.
Copy the newest folder there weekly.

## 14. Restore procedure

If the laptop is replaced or the evidence is damaged:

1. Install Sterling on the new machine.
2. Copy the most recent backup folder from the backup location.
3. Ask the contact in section 12 to run the restore. It is one command and it refuses
   to run if the backup is damaged, so a bad backup cannot overwrite a good database.
4. Sterling will start in `RECOVERY_REQUIRED` until it has checked its records. That
   is correct — it is verifying, not broken.

## 15. Credentials and recovery information

Not in this document. Not in the code. Not in GitHub.

They are in the household password manager, together with:
- the Zerodha login and its two-factor recovery,
- the machine login,
- the location of the off-laptop backup copy.

---

## Installing the backend as a service (one time)

The backend must come back by itself after a reboot or a crash. Without this,
a machine that restarts overnight produces a silent gap in the evidence.

```bash
mkdir -p ~/.config/systemd/user ~/.config/sterling
install -m 600 /dev/null ~/.config/sterling/sterling.env
cp deploy/systemd/sterling-backend.service ~/.config/systemd/user/
cp deploy/systemd/sterling-backend.watchdog.service ~/.config/systemd/user/
cp deploy/systemd/sterling-backend.watchdog.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now sterling-backend sterling-backend.watchdog.timer
loginctl enable-linger "$USER"
```

`loginctl enable-linger` is what keeps the service running after you log out.
Without it the service stops when the desktop session ends.

### The environment file

`~/.config/sterling/sterling.env` holds the settings and secrets. It is mode 600
and is never committed. Put the Kite API key and secret, the Telegram token, the
family account identifiers and the allocation capital there — not in the unit
file, and not in the repository.

The unit file sets `STERLING_BIND_HOST=127.0.0.1`. Leave it. Changing it to
`0.0.0.0` puts an authenticated broker session on every device on the home
network.

### Checking it

```bash
systemctl --user status sterling-backend
curl -fsS http://127.0.0.1:8000/api/v1/health/live
curl -sS http://127.0.0.1:8000/api/v1/health/ready | head -40
```

`/health/live` answering means the process is up. `/health/ready` may answer 503
for ordinary reasons — a non-trading day, a pending morning Kite login, an
incomplete session — and that is not a fault. The watchdog timer deliberately
probes only liveness, because restarting on a false readiness would loop the
service all weekend without fixing anything.

### Stopping it

```bash
systemctl --user stop sterling-backend
```

Stopping sends SIGTERM and waits up to 90 seconds. That window exists so the
runtime can finish the transaction it is in and drain queued alerts. Do not
`kill -9` a running session: a process killed mid-write leaves evidence that
cannot be told apart from a crash.

---

## Unattended morning start-up

The goal is that nothing needs typing except the Kite login itself.

### What is automatic

With the environment file in place, a boot does all of this on its own:

1. The config store opens and the Snapback config loads (a failure here halts
   loudly rather than running a disabled strategy).
2. The Kite account is provisioned from `KITE_API_KEY` / `KITE_API_SECRET` — no
   retyping credentials into the UI, even on a wiped database.
3. The evidence database is created and stamped with its experiment identity.
4. Preflight runs its twelve checks; the runner starts only if they pass.
5. The ops scheduler starts, and the session keeper renews any token Zerodha
   issued a `refresh_token` for.

### What is not, and cannot be

**The daily login.** Zerodha requires a human at the TOTP/2FA step every trading
day. No setting changes that. Perform the Kite login yourself before 09:15 IST.

Until you do, `/health/ready` and the Family screen will show the broker as not
connected. That is the system reporting the truth, not a fault. The alert outbox
sends a WARNING at 08:45 IST and a CRITICAL at 09:10 IST if the login has not
happened.

### One-time setup

```bash
mkdir -p ~/.config/sterling
install -m 600 deploy/sterling.env.example ~/.config/sterling/sterling.env
python3 -c "import secrets; print(secrets.token_urlsafe(48))"   # STERLING_SECRET_KEY
${EDITOR:-nano} ~/.config/sterling/sterling.env
```

Fill in the Kite key and secret, the family user and account identifiers, the
generated `STERLING_SECRET_KEY`, and the paths. Then follow the systemd install
above.

`STERLING_SECRET_KEY` is not optional in practice. Leave it unset and the Kite
API secret and access token are encrypted in the local database under a dev key
that is published in the source, which is no protection at all.

Note that changing this key later makes already-stored secrets undecryptable —
you would re-enter the Kite credentials once. Set it before first use.

### Checking the unattended path worked

```bash
curl -sS http://127.0.0.1:8000/api/v1/health/ready | head -40
curl -sS http://127.0.0.1:8000/api/v1/snapback/prospective/health
```

`ready: true` with `failed_checks: []` means everything except the login is done.
`broker_connected: false` before you log in is expected.

---

## If the operator dies, or hands Sterling on

Sterling is built to outlive whoever set it up. It is deliberately **not** built
to keep trading an account after its holder has died — transactions in a deceased
person's account are not lawful, and no amount of working software makes that
acceptable.

The division that matters:

- **the strategy's evidence history belongs to Sterling.** It describes the
  strategy, not the account, and stays valid across a handover.
- **broker inventory belongs to the legal account holder.** It does not transfer
  because a piece of software was reconfigured.

### The procedure

1. **Stop the service.** `systemctl --user stop sterling-backend`. Place no
   further orders on the previous account.
2. **Flatten and reconcile.** A handover is refused while any position holds
   exposure or any order has an unknown outcome. Rebinding then would leave real
   positions owned by an account Sterling has stopped watching.
3. **Legal transmission.** The nominee or legal heir claims the assets through
   the broker's own transmission process, into **their own account**. Sterling
   plays no part in this.
4. **New credentials.** The new operator opens or uses their own broker account
   and generates their own API key and secret. **Never reuse the previous
   holder's password, API key or access token** — they identify a person, not a
   role, and reusing them is both unlawful and untraceable.
5. **Rebind.** Set `STERLING_FAMILY_USER_ID` and `STERLING_FAMILY_ACCOUNT_ID` to
   the new account and put the new credentials in the environment file.
6. **Start.** Preflight refuses to run until the new account reconciles clean
   and flat, with no unknown orders or positions.

The evidence database is preserved read-only. All account-specific live state is
reset, because it described somebody else's book.

### What is NOT a succession

Reusing the old login to "keep things running" is not a succession. It is trading
in another person's account. If you are ever tempted because the alternative
looks like paperwork, stop the service and leave it stopped — a halted Sterling
loses nothing, and there is no deadline that makes this the right call.

---

## What Sterling is, and is not

Sterling carries **one** strategy for family use: Snapback, and only in paper
mode until its evidence says otherwise. Every other engine in this repository is
research: some are unvalidated, one is measured as economically negative, others
are calibration projects. Family Mode hides and refuses all of them, so there is
no screen where an unvalidated strategy can be armed by mistake.

Even if Snapback eventually passes its gate, it should not be treated as a
salary. Its measured history is concentrated: a small number of large winners
paid for many small losers. A strategy with that shape can have long, discouraging
stretches while still being sound over years. **Sterling should never be the only
thing a household lives on**, and the appropriate first live size is the smallest
one the broker permits — scaled only against evidence milestones decided in
advance, never because a few trades went well.
