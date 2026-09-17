# Logging in to Kite

Zerodha invalidates the access token every morning. This is their rule, not a
fault, and Sterling cannot work around it.

## Why Sterling cannot log in for you

No password, PIN, API secret or access token is stored anywhere in this system,
and none should ever be added to it — not in a file, not in the code, not in the
GitHub repository. A trading system that can log itself in is a trading system
that anyone who copies its files can log in as.

This means a person has to log in each morning before Sterling can see the
market. That is the cost of not having credentials sitting on a disk, and it is
worth paying.

## Each morning

1. Log in to Kite the normal way.
2. Complete the Sterling connection flow in the browser.
3. Confirm it worked:
   ```bash
   cd ~/Sterling && ./scripts/sterling-status
   ```
   The service section should not report a broker problem.

## If login fails

- Check the Kite web app works at all — if Zerodha is down, nothing here helps.
- Do not trade while the broker connection is uncertain. Sterling will refuse
  anyway; do not try to work around that refusal.

## Never

- Never write a password or token into a file in this repository.
- Never paste one into a chat, an issue, or a commit message.
- Never leave instructions for someone else to reuse your credentials. If
  somebody else must operate Sterling, they connect their own account — see the
  succession process in the family runbook.
