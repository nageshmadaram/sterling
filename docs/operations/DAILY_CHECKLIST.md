# Daily checklist

Ten minutes before the market opens (09:15 IST). Do these in order; stop at the
first one that fails and fix it before continuing.

## Before the open

1. **Plug in the drive.** Sterling's data lives on it.
2. **Check the status.**
   ```bash
   cd ~/Sterling && ./scripts/sterling-status
   ```
3. **Log in to Kite.** See [BROKER_REAUTH.md](BROKER_REAUTH.md). The token
   expires every morning; this is normal.
4. **Confirm the status screen is clean.** Specifically:
   - Lake: MOUNTED
   - Backend: RUNNING, Ready: READY
   - Safe mode: OFF (if it is ON, read why before turning it off)
   - Unresolved orders: 0
5. **If anything is not clean, do not trade.** An unclear state is the one
   condition under which Sterling should stay out of the market.

## During the session

Nothing. Sterling runs itself. Do not intervene in a position because it is
losing — the whole point of the experiment is to see what the strategy does
without help.

If something alarming happens:

```bash
./scripts/sterling-safe-mode on "what you saw"
```

## After the close

6. **Run the evidence audit.**
   ```bash
   cd ~/Sterling/backend && .venv/bin/python -m study.snapback_evidence_audit --date $(date +%F)
   ```
   Read it. See [EVIDENCE_INTERPRETATION.md](EVIDENCE_INTERPRETATION.md).
7. **Check that nothing is unresolved.** `Blocked` counts are fine and expected.
   `Inconclusive` trades are worth looking at — they mean Sterling traded but
   cannot fully prove what happened.
8. **Back up the new evidence.** See
   [BACKUP_AND_RESTORE.md](BACKUP_AND_RESTORE.md). Today's recording cannot be
   recreated tomorrow.

## Weekly

- Verify one backup actually restores, rather than assuming it does.
- Confirm the number of completed authoritative trades is growing. If it is not,
  something is blocking every opportunity and that is worth investigating.
