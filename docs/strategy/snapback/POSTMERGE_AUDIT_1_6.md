# Runtime 1.6 post-merge audit

**Status: OPEN — do not create `snapback-prospective-runtime-1.6` yet.**

Runtime 1.6 was merged to `main` before the live Kite acceptance gate ran. This
audit exists to correct engineering/evidence defects discovered after that merge
without changing the frozen Snapback strategy, config hash or rule hash.

## Confirmed findings

1. **SAFE_MODE admission gap (P0-OPERABILITY).** The durable safe-mode service and
   operator scripts existed, but the prospective entry admission path did not
   consult them. Fixed on this audit branch at the shared capacity choke point;
   SAFE_MODE now blocks new exposure while position management remains untouched.
2. **Broker evidence audit was fail-open (P0-EVIDENCE).** Read failures were
   converted to empty partitions, and a final `RECONCILED` state plus selection /
   hedge rows could be counted without proving the critical lifecycle milestones.
   Fixed: corrupt evidence makes the audit fail, and market evidence, option fill,
   hedge filled/declared waiver, protection active, exit fill and reconcile are
   required for broker-chain authority.
3. **Live acceptance persistence was not rerun-idempotent (P0-ACCEPTANCE).** A
   second same-day run could read prior acceptance parts and fail row-count
   equality. Fixed by using a unique round-trip store for each invocation.
4. **`NOT_LISTED` coupled strategy reality to vendor-schema acceptance
   (P0-ACCEPTANCE).** The resolver correctly treated `NOT_LISTED` as a finding,
   then returned `SKIP`, making engineering acceptance unable to finish. Fixed:
   the finding remains unchanged and a clearly labelled real listed probe contract
   is used only for websocket/depth/timestamp/reconnect schema checks.
5. **Economic robustness metrics were computed but not gated (P1-ECONOMICS).**
   Before authoritative forward outcomes begin, promotion is now predeclared to
   require positive expectancy under 3x observed costs and after removing the
   most profitable 1% of trades (minimum three once N>=300), in addition to the
   existing gates.

## Important correction to the runtime-1.6 release record

The release record says the broker lifecycle recorder is wired into the
`SnapbackProspectiveCollector`. It is not. The collector is explicitly a paper
observation engine; manufacturing broker submission/fill/protection lifecycle
states for it would fabricate evidence. The authoritative forward economic gate
correctly reads the prospective observation warehouse. The broker lifecycle
recorder remains infrastructure for actual broker evidence and must be integrated
only when real broker/shadow execution exists.

## Release boundary

The tag remains withheld. The next releasable commit must pass the complete test /
regression gates and then the live Kite acceptance run against that exact commit.
Any FAIL, SKIP, harness incompatibility, or moved SHA blocks the tag.

**Economic verdict remains INCONCLUSIVE.** These fixes improve what Sterling can
prove and how safely it fails; they do not establish that Snapback is profitable.
