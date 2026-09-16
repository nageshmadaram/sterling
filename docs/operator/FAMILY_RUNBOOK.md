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
