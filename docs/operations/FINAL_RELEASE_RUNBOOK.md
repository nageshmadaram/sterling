# Final release runbook

This is the sequence that turns an engineering-complete checkout into a tagged
build running in live-market shadow production. Nothing here authorises real
money: capital is earned lane by lane, later, through
[EVIDENCE_INTERPRETATION.md](EVIDENCE_INTERPRETATION.md) and the promotion gate.

**Central rule: engineering readiness authorises observation, not capital.**

## 0. Preconditions

Do not start while anything is still changing the repository. Every step below
is evidence about one exact SHA, and a single commit voids all of it.

```bash
git status --porcelain     # must print nothing
scripts/sterlingctl doctor # must exit 0
```

## 1. Cut the release candidate

```bash
git switch -c release/sterling-family-v1
```

From this point, only release-blocking fixes. No opportunistic cleanup, no
strategy tuning. Any commit creates a new SHA and invalidates every
certification attestation recorded against the old one.

## 2. Run the suites on a clean checkout

```bash
cd backend && PYTHONWARNINGS=ignore .venv/bin/python -m pytest -q
```

Then the frontend, browser/E2E, Kitelake and failure-drill suites as those
projects document them.

## 3. Live Kite acceptance during a moving market

Run the acceptance harness while the market is actually moving. A quiet-market
pass proves the code runs, not that the vendor assumptions hold.

## 4. Record what a machine cannot check

Three gates are checked automatically — source identity, release manifest,
backup/restore. The rest are human observations and are attested against the
exact SHA:

```bash
scripts/sterlingctl certify attest remote_ci PASS "Your Name" "https://…/runs/123"
scripts/sterlingctl certify attest test_suites PASS "Your Name" "logs/2026-09-18-suites.txt"
scripts/sterlingctl certify attest kite_live_acceptance PASS "Your Name" "logs/…"
scripts/sterlingctl certify attest reconnect PASS "Your Name" "logs/…"
scripts/sterlingctl certify attest persistence PASS "Your Name" "logs/…"
scripts/sterlingctl certify attest failure_drills PASS "Your Name" "logs/…"
scripts/sterlingctl certify attest open_exposure PASS "Your Name" "reconcile output"
```

A `PASS` must name who observed it. An unrecorded gate reads `UNKNOWN`, and
`UNKNOWN` is not a pass.

## 5. Freeze and verify

```bash
scripts/sterlingctl freeze     # writes data/manifests/release.json
scripts/sterlingctl verify     # must report no drift
scripts/sterlingctl backup
scripts/sterlingctl restore-check
scripts/sterlingctl certify    # must print RELEASE_READY
```

The manifest records all ten lane identities, both schema versions, every
lane's `execution_vehicle`, and every declared challenger with its own
identity and a zero sample.

## 6. Tag, and never move the tag

```bash
git tag -a sterling-family-v1 -m "Family release candidate"
git push origin sterling-family-v1
```

Deploy only that tag. Set `STERLING_RELEASE_TAG` on the host to the same
string, or new evidence is written unattributed and excluded from every gate.

## 7. Start a fresh authoritative evidence store

The new release is a new identity. Its forward sample starts at zero; older
rows keep their own identity and are never re-attributed to it.

## 8. Shadow production

Entry submission stays off. The runtime observes the live market, selects real
contracts, reads real depth and real margin, builds real intents, and records
what would have happened:

```bash
scripts/sterlingctl shadow          # per-lane fillability, slippage, refusals
scripts/sterlingctl permission      # why no lane may send a real entry
```

A no-fill, an unlisted contract, an unavailable margin, a thin book and a stale
quote are all **outcomes**. They are recorded as outcomes and never converted
into a synthetic fill.

## 9. What is still blocked, deliberately

- Real-money entry for every lane (`LIVE_EXECUTION_ENABLED = False`).
- The SuperTrend long-option wrapper, which is research-only on current
  evidence: 0/60 configurations were OOS net-positive.
- `supertrend_directional_v1`, which exists as a Track-C challenger with zero
  trades and `live_orders = DISABLED`.

## 10. Before any real order, separately

See [BROKER_ACCOUNT_HANDOFF.md](BROKER_ACCOUNT_HANDOFF.md) for the account
binding and [ACCESS_CONTINUITY.md](ACCESS_CONTINUITY.md) for the static-IP and
ownership checklist. Both must be clean before a lane can be promoted, and
neither is a code change.
