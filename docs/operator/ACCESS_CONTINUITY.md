# Sterling — Access and Credential Continuity

**No password, API secret, OTP seed or recovery code appears in this file, or
anywhere in this repository.** This page records *where* each credential lives
and *who* may reach it. Nothing else.

Fill in the blanks below on paper or in the password manager, not here.

---

## 1. Why this page exists

An incident is the worst possible moment to discover that the only person who
can log into the broker is unreachable, or that the backup drive is encrypted
with a key nobody wrote down. Everything here must be tested **while the system
is healthy**.

## 2. What must be reachable, and by whom

| Thing | Where it is managed | Who may access it | Recovery path | Last tested |
|---|---|---|---|---|
| Broker account (Zerodha Kite) | broker's own app / password manager | | broker's account-recovery procedure | |
| Broker API key & secret | password manager entry | | regenerate in the broker developer console | |
| The machine Sterling runs on | | | | |
| Repository (GitHub) | | | GitHub account recovery | |
| Notification channel (Telegram bot) | password manager entry | | recreate bot via BotFather | |
| Backup location | | | | |
| Backup encryption key, if any | password manager entry | | **none — losing this loses the backups** | |
| Domain / hosting, if any | | | | |

## 3. Roles

Keep these separate where you can. One person may hold both, but then the
handover plan below matters more, not less.

| Role | Can | Cannot |
|---|---|---|
| **Operator** | Start, stop, SAFE_MODE, reconcile, back up, restore-check, read reports, exit a position by hand at the broker | Change strategy rules, promote a lane, raise capital limits |
| **Developer** | Change code, freeze a release, change lane states | Approve real capital alone |
| **Owner** | Approve promotion to real money, approve capital increases | — |

## 4. Rules

- Credentials are never committed to the repository, never pasted into a
  runbook, and never sent over chat.
- The operator needs **broker access**, not developer access, to reach a safe
  state. If reaching safety requires editing code, the handoff is not complete.
- Test each access path while the system is healthy, and record the date in the
  table above. An untested recovery path is an assumption.
- When a person's role ends, rotate every credential they could reach, and
  record the rotation date.

## 5. Technical escalation

| Situation | Who | How |
|---|---|---|
| Unexplained real exposure | | |
| Sterling will not reach a safe state | | |
| Evidence database cannot be restored | | |
| Suspected duplicate order | | |

Fill these in with a name and a contact method that works outside working
hours. "Ask in the group chat" is not an escalation path.

## 6. If the operator hands Sterling on, or cannot continue

1. New operator reads [START_HERE.md](START_HERE.md), then completes every drill
   in [FAILURE_DRILLS.md](FAILURE_DRILLS.md) §"Operator sign-off" **without
   editing code**.
2. Access table above is updated and each path is tested, with dates.
3. Old operator's credentials are rotated.
4. Until both are done, Sterling stays in SAFE_MODE.

See also [FAMILY_RUNBOOK.md](FAMILY_RUNBOOK.md) §12 and §15, which hold the
existing contact and credential-location arrangements.
